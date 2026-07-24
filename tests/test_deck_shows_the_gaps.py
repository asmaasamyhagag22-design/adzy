# -*- coding: utf-8 -*-
"""What the system admits internally must reach the page.

MEASURED (2026-07-21): the run recorded honest explanations and the deck showed none of them —
`swot.notes` and `discovery_notes` are referenced NOWHERE in `dashboard/`, and an empty quadrant
rendered as an em-dash with a `0` badge. `discovery_notes` for Volume Qatar literally reads
"thin local tier: only 1 of 4 peers cleared the floor ... Not padding with sub-floor candidates"
and "Consider a larger radius" — a client-grade explanation, kept private.

The profile also said `ready_for_swot: false` (`swot_blockers: ["low_evidence_coverage"]`) and the
SWOT shipped with no badge. Owner's ruling: ship it, but say so. Hermetic."""
from __future__ import annotations

from dashboard.war_room import _wr_swot_quad, _evidence_notes_html, _readiness_badge


def test_an_empty_quadrant_shows_its_reason_instead_of_a_dash():
    html = _wr_swot_quad([], "t", reason="التهديدات: فاضية — محتاجة قرين له موقع قابل للفحص")
    assert "قابل للفحص" in html
    assert ">—<" not in html                       # the bare dash is gone when we can explain


def test_a_full_quadrant_is_unchanged():
    html = _wr_swot_quad([{"text": "بند حقيقي"}], "s", reason="لا ينبغي أن يظهر")
    assert "بند حقيقي" in html and "لا ينبغي" not in html


def test_an_empty_quadrant_without_a_reason_keeps_the_old_dash():
    assert "—" in _wr_swot_quad([], "o")


def test_discovery_and_swot_notes_reach_the_page():
    html = _evidence_notes_html(
        swot_notes=["التهديدات: فاضية — محتاجة إشارة سوق"],
        discovery_notes=["thin local tier: only 1 of 4 peers cleared the floor",
                         "Consider a larger radius if you need scraped-dimension comparison"])
    assert "thin local tier" in html
    assert "larger radius" in html
    assert "فاضية" in html


def test_no_notes_renders_nothing():
    assert _evidence_notes_html(swot_notes=[], discovery_notes=[]) == ""


def test_a_blocked_readiness_is_badged_with_its_blocker():
    html = _readiness_badge({"ready_for_swot": False,
                             "swot_blockers": ["low_evidence_coverage"]})
    assert "low_evidence_coverage" in html
    assert "أولية" in html or "تغطية" in html      # says the result is preliminary, and why


def test_a_ready_profile_gets_no_badge():
    assert _readiness_badge({"ready_for_swot": True}) == ""
    assert _readiness_badge({}) == ""
