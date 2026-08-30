# -*- coding: utf-8 -*-
"""The pull must prefer the advertiser-accurate query and disclose which one it used.

Order: resolve the competitor's page (by advertiser NAME) -> pull that page's ads. Only when the
page can't be identified does it fall back to the keyword search, whose results are ad-copy matches
and therefore need the relevance gate. Which path produced the data is recorded, because "5 ads by
this competitor" and "5 ads mentioning this competitor" are different facts. Hermetic."""
from __future__ import annotations

from ads_intel.live import iter_competitor_ads


class _T:
    def __init__(self, by_page=None, by_keyword=None, resolve=None):
        self.by_page = by_page or []
        self.by_keyword = by_keyword or []
        self.resolve = resolve if resolve is not None else []
        self.calls: list[str] = []

    def fetch(self, url):
        if "search_page_ids" in url:
            self.calls.append("page")
            return {"data": self.by_page}
        if "page_id" in url and "page_name" in url:
            self.calls.append("resolve")
            return {"data": self.resolve}
        self.calls.append("keyword")
        return {"data": self.by_keyword}


def _run(t):
    rows, basis = [], {}
    for page, b in iter_competitor_ads("NTI", country="EG", token="t", fields="id",
                                       transport=t, batch_size=5, hard_cap=5):
        rows.extend(page)
        basis = b
    return rows, basis


def test_resolved_page_is_used_and_disclosed():
    t = _T(resolve=[{"page_id": "222", "page_name": "NTI"}],
           by_page=[{"id": "a", "page_id": "222"}],
           by_keyword=[{"id": "stranger"}])
    rows, basis = _run(t)
    assert [r["id"] for r in rows] == ["a"]            # the advertiser's own ad, not the stranger
    assert basis["basis"] == "page_id" and basis["page_id"] == "222"
    assert "keyword" not in t.calls                    # no need to widen at all


def test_unresolvable_page_falls_back_and_says_so():
    t = _T(resolve=[{"page_id": "999", "page_name": "Totally Unrelated Co"}],
           by_keyword=[{"id": "k"}])
    rows, basis = _run(t)
    assert [r["id"] for r in rows] == ["k"]
    assert basis["basis"] == "keyword"                 # disclosed as the weaker evidence
    assert basis.get("page_id") in (None, "")


def test_an_advertiser_with_no_active_ads_is_not_re_searched_by_keyword():
    # Their page WAS identified and it is running nothing. That is a real answer — widening to a
    # keyword search here would replace a true "they don't advertise" with strangers' ads.
    t = _T(resolve=[{"page_id": "222", "page_name": "NTI"}], by_page=[],
           by_keyword=[{"id": "stranger"}])
    rows, basis = _run(t)
    assert rows == [] and basis["basis"] == "page_id"
    assert "keyword" not in t.calls
