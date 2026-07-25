# -*- coding: utf-8 -*-
"""The SWOT the client LOOKS at — the last hop, where three of today's fixes were dying.

The pattern this file exists to break is the one that also hid the image-provenance defect that
morning: PRODUCED · SERIALIZED · NEVER SHOWN. Two greps over `frontend/index.html` settled it:

    notes            -> 0 hits
    site_readiness   -> 0 hits

Every disclosure sentence written into `swot.notes` — "استُبعد N عنصر", "أضيفي منافساً واحداً على
الأقل", "نُقل N عنصر إلى «جاهزية الموقع»" — was composed carefully, stored faithfully, and
rendered nowhere. And `site_readiness`, the box that site mechanics were MOVED to on the owner's
ruling, had no renderer at all, so for the client those items were not moved, they were deleted.

The priority was the third: the quadrant renderer read `title`, `reasoning` and one evidence ref,
and nothing else, so the entire Pickton & Wright apparatus — importance, performance, the written
basis, `focus` — stopped one step short of the screen.

Static assertions on the shipped file, the same approach as `test_frontend_shows_disclosure.py`:
this repo has no JS test runner, and a guard that reads the file is worth far more than no guard.
Each assertion anchors on the BEHAVIOUR (the field being read) rather than on a character window,
after an earlier version of that file failed on correct code because a comment moved a line.
"""
from __future__ import annotations

from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
    encoding="utf-8")


# --- 1) the disclosure notes must have a renderer -------------------------------------------

def test_the_swot_notes_are_rendered_somewhere():
    """`swot.notes` carries every honest sentence the analysis writes about its own limits."""
    assert "swot.notes" in HTML or "swot['notes']" in HTML, (
        "swot.notes has no reader in the frontend — every disclosure sentence dies here")


def test_the_notes_have_a_container_to_render_into():
    assert "swot-notes" in HTML, "no element to render the notes into"


# --- 2) site readiness must have a renderer -------------------------------------------------

def test_site_readiness_is_rendered():
    """Site mechanics were MOVED here, not deleted. Without a renderer the move IS a deletion."""
    assert "site_readiness" in HTML, (
        "site_readiness has no reader in the frontend — moved items vanish for the client")


def test_a_swot_with_only_readiness_items_still_renders():
    """MEASURED shape: nanocare's strategic quadrants can be empty while four readiness items
    exist. The render gate must not treat that as "no SWOT" and show a blank page."""
    assert "site_readiness" in HTML.split("const hasSwot")[1][:600], (
        "hasSwot ignores site_readiness — a readiness-only SWOT renders as nothing at all")


# --- 3) the priority must be visible on the item ---------------------------------------------

def test_the_priority_is_read_by_the_item_renderer():
    assert "item.priority" in HTML, "the priority never reaches the rendered item"


def test_the_written_basis_is_available_to_the_client():
    """A rank with no stated measurement is an assertion. The basis is how a client checks it."""
    assert ".basis" in HTML, "the measured basis is not rendered anywhere"


def test_the_focus_flag_is_marked():
    """`focus` is the paper's "pay particular attention" verdict — the one thing the ranking is
    for. It must be visible, not merely present in the payload."""
    assert "focus" in HTML, "the focus verdict is computed and never shown"


# --- 4) every item says where it came from ----------------------------------------------------

def test_each_item_declares_its_evidence_source():
    """The owner's fourth ask: peer comparison / customer comment / self-claim / market signal.
    A self-claim reading like a measured advantage is the defect the SWOT rebuild removed at the
    scoring layer; it must not come back at the rendering layer."""
    assert "swotItemSource" in HTML, "no per-item source label is derived"


def test_a_self_claim_is_labelled_as_the_brands_own_words():
    assert "peers_measured" in HTML, (
        "nothing distinguishes a compared item from an uncompared self-claim on screen")


# --- the rendering must stay safe --------------------------------------------------------------

def test_swot_text_is_escaped_before_being_put_into_html():
    """Item text comes from scraped pages and from customer comments — attacker-influenced input
    going into `innerHTML`. It must pass through an escape helper."""
    assert "function escHtml" in HTML, "no escape helper for SWOT text"
    # Anchor on the renderer that OWNS the interpolation, not on a byte window near the caller —
    # the same mistake this suite already made once, where a correct implementation failed because
    # a comment pushed the guard out of a fixed-size slice.
    body = HTML.split("function swotItemHtml")[1].split("\n}")[0]
    for field in ("item.title", "item.reasoning"):
        assert f"escHtml({field})" in body, f"{field} is interpolated into innerHTML unescaped"
