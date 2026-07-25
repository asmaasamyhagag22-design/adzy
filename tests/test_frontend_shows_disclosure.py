# -*- coding: utf-8 -*-
"""The honesty fields must reach the SCREEN, not just the JSON body.

MEASURED (2026-07-25): after the backend was taught to ship `is_preview`, `degraded_reason`,
`trust_violation` and `trust_note_ar`, a grep of `frontend/index.html` for all four returned ZERO
matches. The disclosure was produced, serialised, sent — and then dropped by the only consumer that
exists. My own earlier fix was therefore incomplete in exactly the way this project keeps naming: it
made a true statement ("the API discloses it") that answers a different question from the one that
matters ("does the person see it?").

Worse, `/api/edit/inpaint` answers `applied: false` because region editing has no implementation,
and the client announced `toast('Region edited', 'success')` unconditionally — the UI actively
asserted the opposite of what the response said.

These assertions read the shipped HTML because that IS the client; there is no build step and no
component tree to mount. They are written to fail if the rendering is removed, not merely if a
string is renamed."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_HTML = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


@pytest.fixture(scope="module")
def html() -> str:
    return _HTML.read_text(encoding="utf-8")


def test_every_disclosure_field_the_api_sends_is_read_by_the_client(html):
    for field in ("is_preview", "degraded_reason", "trust_violation", "trust_note_ar"):
        assert field in html, f"the client never reads `{field}` — it is disclosed to nobody"


def test_there_is_a_place_on_the_page_to_render_it(html):
    assert 'id="gen-disclosure"' in html, "no element holds the disclosure"


def test_the_generate_handler_calls_the_renderer(html):
    """Reading the fields is not enough; the banner has to be invoked after a generation."""
    assert html.count("showDisclosure(") >= 3, (
        "showDisclosure is defined but not called on every path that puts an image on screen")


def test_the_inpaint_result_no_longer_claims_success_unconditionally(html):
    """The success toast must be reachable ONLY when `applied` is not false.

    CORRECTED while writing this file: the first version scanned a fixed 700-character window
    BEFORE the toast, and failed on correct code simply because an explanatory comment pushed the
    guard out of the window — a test measuring comment length, not behaviour. Anchoring on the
    guard and requiring the toast to sit after it says the same thing without the brittleness."""
    guard = html.find("data.applied === false")
    assert guard > 0, "the inpaint response's `applied` flag is not checked at all"
    toast = html.find("'Region edited'", guard)
    assert toast > guard, (
        "'Region edited' is announced before/without the check — it must be the else branch")
    between = html[guard:toast]
    assert "else" in between, "the success toast is not gated behind the applied check"


def test_the_untranslated_note_from_the_backend_is_what_gets_shown(html):
    """The backend already localises the note; the client must show THAT, not its own English."""
    idx = html.find("data.applied === false")
    assert idx > 0
    assert "data.note" in html[idx:idx + 400], "the localised backend note is ignored"


def test_a_trust_violation_is_visually_distinct_from_a_plain_preview(html):
    """A fabricated element and a free placeholder must not look like the same notice."""
    fn = html[html.find("function showDisclosure"):]
    fn = fn[:fn.find("\n}")]
    assert "trust_violation" in fn and "warn" in fn, (
        "the banner does not distinguish a grounding violation from an ordinary preview")


def test_the_banner_is_hidden_when_there_is_nothing_to_disclose(html):
    fn = html[html.find("function showDisclosure"):]
    fn = fn[:fn.find("\n}")]
    assert re.search(r"display\s*=\s*['\"]none['\"]", fn), (
        "a clean paid render would still show a notice")
