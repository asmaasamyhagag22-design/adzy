# -*- coding: utf-8 -*-
"""«الترند مش بيطلع» — because the Trend tab never showed trends.

MEASURED on the shipped frontend. `#strat-view-trend` renders exactly four things, all read from
`swot.strategic_context`:

    quadrant_counts        how many items are in each SWOT box
    benchmark_quality      "competitive" | "standalone"
    low_benchmark_items    which comparisons were thin
    watchout_items

None of that is a market trend. It is bookkeeping ABOUT the SWOT, shown under a tab labelled
"Trend" — and the code says so itself: "surfaced from swot.strategic_context ... there's no
separate trend.json".

MEANWHILE the backend has been fetching real trends all along. `_trends_for(profile)` calls
`top_trends_bounded`, and `synthesize_swot(trends=...)` turns them into Opportunities and Threats
with citable URLs. The trends are paid for, fetched, used — and the LIST ITSELF (title, source
URL, which of the brand's terms matched) is dropped before the payload. So the one tab named after
them could not show them even in principle.

Same shape as every other defect found today: produced · consumed · never shown.

WHAT THIS LOCKS: the trends travel to the client with the source URL that makes each one checkable,
and the tab has a renderer for them. The analysis-quality chips are not deleted — they are honest
and useful — they just stop being the only thing under that name.

Hermetic: no network, no model.
"""
from __future__ import annotations

import pytest


class _Trend:
    def __init__(self, title, url, terms=("مكملات",), score=0.8):
        self.title = title
        self.url = url
        self.matched_terms = terms
        self.score = score


@pytest.fixture
def _profile():
    return {"name": "Example", "category": "ecommerce", "source_url": "https://example.test/",
            "offerings": [{"name": "Thing"}]}


# --- the trends must reach the payload ----------------------------------------------------------

def test_the_fetched_trends_are_serialized_for_the_client(monkeypatch, _profile):
    import api.webapp_analysis as wa
    monkeypatch.setattr(wa, "_trends_for",
                        lambda p: [_Trend("نمو سوق المكملات في مصر", "https://reuters.test/a")])
    out = wa.generate_swot({"_profile": _profile, "domain_key": "example_test"})
    trends = (out.get("swot") or {}).get("trends")
    assert trends, f"the trends never reached the payload: {sorted(out.get('swot') or {})}"
    assert trends[0]["title"] == "نمو سوق المكملات في مصر"


def test_each_trend_carries_the_source_that_makes_it_checkable(monkeypatch, _profile):
    import api.webapp_analysis as wa
    monkeypatch.setattr(wa, "_trends_for",
                        lambda p: [_Trend("Supplement market grows", "https://reuters.test/a")])
    out = wa.generate_swot({"_profile": _profile, "domain_key": "example_test"})
    t = (out["swot"]["trends"] or [{}])[0]
    assert t.get("url", "").startswith("http"), t
    assert t.get("matched_terms"), "nothing says WHY this trend is on-topic for this brand"


def test_no_trends_ships_an_empty_list_not_a_missing_key(monkeypatch, _profile):
    """The tab has to distinguish "we looked and found none" from "the field does not exist"."""
    import api.webapp_analysis as wa
    monkeypatch.setattr(wa, "_trends_for", lambda p: [])
    out = wa.generate_swot({"_profile": _profile, "domain_key": "example_test"})
    assert (out.get("swot") or {}).get("trends") == []


def test_a_trend_source_failure_never_breaks_the_swot(monkeypatch, _profile):
    import api.webapp_analysis as wa

    def _boom(_p):
        raise RuntimeError("serper down")
    monkeypatch.setattr(wa, "_trends_for", _boom)
    out = wa.generate_swot({"_profile": _profile, "domain_key": "example_test"})
    assert out.get("has_swot") is True


# --- and the tab must render them ----------------------------------------------------------------

def test_the_trend_tab_reads_the_trends():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "swot.trends" in html, "the Trend tab still shows only SWOT bookkeeping"


def test_the_trend_tab_has_somewhere_to_put_them():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "trend-signals" in html, "no container for the market signals"
