"""Trend-driven Opportunities/Threats (SWOT-quality Slice 3).

Origin (owner): the SWOT had no market-shift signal — an ecommerce brand with no Places peers and
no reachable reviews got EMPTY Opportunities/Threats. Now on-topic market TRENDS become brand-level
O/T, each citing the trend URL, `directional_not_validated`, with junk/aggregator hosts dropped and
every line Ledger-gated. Hermetic: synthetic TrendItems, no network/LLM.
"""
from __future__ import annotations

import dataclasses

from competitor.brand_signals import opportunities_threats_from_trends
from competitor.matrix import build_matrix
from competitor.swot import synthesize_swot
from trends.sources import TrendItem


def _profile():
    return {"name": {"value": "Skin Co"}, "category": {"value": "beauty"}}


def _trends():
    return [
        TrendItem(title="Clean vegan skincare surges in 2026", url="https://www.vogue.com/a",
                  source="web", matched_terms=("skincare", "vegan")),
        TrendItem(title="New regulation restricts some cosmetic ingredients",
                  url="https://www.reuters.com/b", source="web", matched_terms=("cosmetic",)),
        TrendItem(title="Junk skincare tips", url="https://www.pinterest.com/pin/9",  # denylisted host
                  source="web", matched_terms=("skincare",)),
        TrendItem(title="Off-topic crypto rally", url="https://news.example/c",       # no matched_terms
                  source="web", matched_terms=()),
    ]


def test_trends_become_grounded_opportunities_and_threats():
    opps, threats = opportunities_threats_from_trends(_profile(), _trends())
    otexts = [o.text for o in opps]
    ttexts = [t.text for t in threats]
    # a COVERAGE opportunity + a headwind threat, both on-topic. NOT "Rising interest": nothing
    # upstream measures direction (trends/engine.py is "no time-series"), so the line may only
    # assert that coverage on the brand's terms exists.
    assert any(t.startswith("Coverage on") and "skincare" in t for t in otexts)
    assert any(t.startswith("Category headwind on") and "cosmetic" in t for t in ttexts)
    assert not any("Rising interest" in t for t in otexts + ttexts)   # the manufactured claim is gone
    # junk aggregator host (pinterest) dropped; off-topic (no matched_terms) dropped
    assert not any("Junk skincare tips" in t for t in otexts + ttexts)
    assert not any("crypto" in t.lower() for t in otexts + ttexts)
    # provenance + honest tier
    for it in opps + threats:
        assert it.citation[0].startswith("http")
        assert it.claim_strength == "directional_not_validated"


def test_synthesize_swot_prepends_trend_ot_and_fills_empty_quadrants():
    m = build_matrix(_profile(), [])
    before = synthesize_swot(m, profile=_profile())
    after = synthesize_swot(m, profile=_profile(), trends=_trends())
    assert len(before.opportunities) == 0 and len(before.threats) == 0     # empty without a feed
    assert len(after.opportunities) >= 1 and len(after.threats) >= 1       # trends fill them
    assert after.opportunities[0].text.startswith("Coverage on")           # trends LEAD


def test_a_trend_without_a_url_is_dropped_not_cited_as_a_placeholder():
    """The citation must always resolve. A url-less trend used to ship with the literal string
    "market trends" as its citation — a citation that pointed at nothing but RENDERED as one."""
    urlless = [TrendItem(title="Vegan skincare everywhere", url="", source="web",
                         matched_terms=("skincare",))]
    opps, threats = opportunities_threats_from_trends(_profile(), urlless)
    assert (opps, threats) == ([], [])
    # and nothing that does survive may carry a non-URL citation
    all_items = sum(opportunities_threats_from_trends(_profile(), _trends()), [])
    assert all(it.citation and it.citation[0].startswith("http") for it in all_items)
    assert not any("market trends" in (it.citation[0] or "") for it in all_items)


def test_no_trends_is_regression_safe():
    m = build_matrix(_profile(), [])
    base = dataclasses.asdict(synthesize_swot(m, profile=_profile()))
    assert dataclasses.asdict(synthesize_swot(m, profile=_profile(), trends=None)) == base
    assert dataclasses.asdict(synthesize_swot(m, profile=_profile(), trends=[])) == base
    assert opportunities_threats_from_trends(_profile(), []) == ([], [])
