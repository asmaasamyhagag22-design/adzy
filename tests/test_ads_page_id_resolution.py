# -*- coding: utf-8 -*-
"""Pull a competitor's ads BY ADVERTISER, not by whoever mentions their name.

MEASURED against the live Ad Library (2026-07-20, EG, ACTIVE):
  - `search_terms="NTI"` returned 10 ads whose advertisers were AMOpportunities, Alibaba.com,
    Apps Reviewing Studio, Codigo Divino — strangers. `search_terms` matches AD COPY, so the
    result answers "who mentioned this word", not "what is this competitor running".
  - `search_page_ids=["103181025890144"]` returned 5 ads, ALL from that one advertiser.

So the relevance gate the owner complained about ("1 related out of many") was patching a query
that could never be right. The fix is to resolve the competitor to a PAGE first — matching on
`page_name`, the advertiser's own name, never on ad text — then pull that page's ads.

`resolve_page_id` returns None rather than a weak guess: a wrong page would attribute a stranger's
ad budget to a competitor, which is worse than saying "we could not identify their page".
Hermetic — the transport is injected."""
from __future__ import annotations

from ads_intel.live import resolve_page_id


class _T:
    def __init__(self, rows):
        self.rows = rows
        self.urls: list[str] = []

    def fetch(self, url):
        self.urls.append(url)
        return {"data": self.rows}


def _rows(*pairs):
    return [{"id": str(i), "page_id": pid, "page_name": nm}
            for i, (pid, nm) in enumerate(pairs)]


def test_resolves_the_page_whose_NAME_matches_the_competitor():
    t = _T(_rows(("111", "AMOpportunities"), ("222", "NTI Egypt"), ("333", "Alibaba.com")))
    assert resolve_page_id("NTI", country="EG", token="t", transport=t) == "222"


def test_ignores_advertisers_that_merely_mention_the_name():
    # every advertiser is a stranger -> no page, and NOT the first row as a consolation prize
    t = _T(_rows(("111", "AMOpportunities"), ("333", "Alibaba.com")))
    assert resolve_page_id("NTI", country="EG", token="t", transport=t) is None


def test_exact_name_beats_a_partial_one():
    t = _T(_rows(("111", "Nano Care Egypt Store"), ("222", "Nano Care")))
    assert resolve_page_id("Nano Care", country="EG", token="t", transport=t) == "222"


def test_matching_is_case_and_whitespace_insensitive():
    t = _T(_rows(("222", "  vodafone   EGYPT ")))
    assert resolve_page_id("Vodafone Egypt", country="EG", token="t", transport=t) == "222"


def test_no_results_resolves_to_none():
    assert resolve_page_id("x y", country="EG", token="t", transport=_T([])) is None


def test_transport_failure_degrades_to_none_not_an_exception():
    class _Boom:
        def fetch(self, url):
            raise RuntimeError("network")
    assert resolve_page_id("NTI", country="EG", token="t", transport=_Boom()) is None


def test_the_lookup_asks_for_page_id_and_page_name():
    t = _T(_rows(("222", "NTI")))
    resolve_page_id("NTI", country="EG", token="t", transport=t)
    assert "page_id" in t.urls[0] and "page_name" in t.urls[0]
