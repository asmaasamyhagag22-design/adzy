# -*- coding: utf-8 -*-
"""The derived threats and the per-quadrant notes must reach the real SWOT.

A helper nobody calls is a comment. `synthesize_swot` is the single production entry
(`competitor/full_run.py:455`), so the wiring is pinned here: derived threats fill an EMPTY
Threats quadrant only, and the empty-quadrant explanation always ships. Hermetic."""
from __future__ import annotations

from competitor.matrix import ComparativeGapMatrix
from competitor.swot import SWOTItem, synthesize_swot


class _Trend:
    def __init__(self, title):
        self.title = title
        self.url = "https://x.example/report"


def _subject():
    from competitor.matrix import MatrixColumn
    return MatrixColumn(name="Volume Cafe", is_subject=True, values={})


def test_a_market_growth_signal_fills_an_otherwise_empty_threats_quadrant():
    swot = synthesize_swot(
        ComparativeGapMatrix(columns=[_subject()], gaps=[]), themes=[],
        trends=[_Trend("Qatar Catering Services Market Size & Forecast 2026")])
    assert swot.threats, "a real market signal was available and the quadrant shipped empty"
    assert all(t.claim_strength == "directional_not_validated" for t in swot.threats)


def test_derived_threats_never_displace_measured_ones():
    """A brand WITH a real peer-gap threat must keep exactly that — inference is a fallback,
    never an addition that dilutes measured evidence."""
    swot = synthesize_swot(ComparativeGapMatrix(columns=[_subject()], gaps=[]), themes=[],
                           trends=[_Trend("Qatar Catering Market Growth")])
    measured = SWOTItem(text="Competitors lead on CTAs", citation=["peer"], evidence="cta 3v17",
                        claim_strength="validated")
    swot.threats = [measured]
    from competitor.swot import derived_threats
    assert measured in swot.threats
    assert derived_threats(trends=[], weaknesses=[], maturity=None) == []


def test_every_run_explains_an_empty_quadrant():
    swot = synthesize_swot(ComparativeGapMatrix(columns=[_subject()], gaps=[]), themes=[])
    joined = " ".join(swot.notes)
    assert "فاضية" in joined, swot.notes


def test_no_trends_no_invented_threats():
    swot = synthesize_swot(ComparativeGapMatrix(columns=[_subject()], gaps=[]), themes=[])
    assert swot.threats == []                    # honest emptiness, now explained in notes
