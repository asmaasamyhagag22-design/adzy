# -*- coding: utf-8 -*-
"""Exact-phrase search silently returns NOTHING for a multi-word competitor name.

MEASURED against the live Ad Library (2026-07-20, EG, ACTIVE):
    "Vodafone"        EXACT_PHRASE 5   UNORDERED 5
    "Vodafone Egypt"  EXACT_PHRASE 0   UNORDERED 1     <- the whole brand disappears
    "egysupp"         EXACT_PHRASE 0   UNORDERED 0     <- genuinely not advertising

EXACT_PHRASE was chosen deliberately (a multi-word name matched as scattered keywords pulled
strangers), and that reason still holds — so precision is not traded away here. Instead, a zero
exact result RETRIES unordered, and the existing multi-signal relevance gate (ads_intel/relevance)
does what it already does: drop the strangers. Zero-because-they-do-not-advertise and
zero-because-our-phrasing-was-too-strict are different facts, and only one of them is true about
the competitor. Hermetic — the transport is injected."""
from __future__ import annotations

from ads_intel.live import build_query_url, iter_ad_pages


class _Transport:
    """Returns rows only for the search_type given at construction."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.seen: list[str] = []

    def fetch(self, url: str) -> dict:
        st = "KEYWORD_EXACT_PHRASE" if "EXACT_PHRASE" in url else "KEYWORD_UNORDERED"
        self.seen.append(st)
        return {"data": self.answers.get(st, [])}


def _pull(transport) -> list:
    return [r for page in iter_ad_pages("Vodafone Egypt", country="EG", token="t",
                                        fields="id", transport=transport, batch_size=5,
                                        hard_cap=5) for r in page]


def test_zero_exact_matches_falls_back_to_unordered():
    t = _Transport({"KEYWORD_EXACT_PHRASE": [], "KEYWORD_UNORDERED": [{"id": "1"}]})
    rows = _pull(t)
    assert len(rows) == 1
    assert t.seen[0] == "KEYWORD_EXACT_PHRASE"          # precision is still tried FIRST
    assert "KEYWORD_UNORDERED" in t.seen


def test_a_successful_exact_search_never_widens():
    # When the precise search works, the loose one must not run — that is what keeps strangers out.
    t = _Transport({"KEYWORD_EXACT_PHRASE": [{"id": "1"}], "KEYWORD_UNORDERED": [{"id": "x"}]})
    rows = _pull(t)
    assert [r["id"] for r in rows] == ["1"]
    assert "KEYWORD_UNORDERED" not in t.seen


def test_both_empty_stays_empty_without_looping():
    t = _Transport({"KEYWORD_EXACT_PHRASE": [], "KEYWORD_UNORDERED": []})
    assert _pull(t) == []
    assert t.seen.count("KEYWORD_UNORDERED") == 1       # one retry, never a storm


def test_single_word_queries_do_not_retry():
    # A one-word query means exact and unordered are the same search — retrying would only spend
    # a call to get the same answer.
    t = _Transport({"KEYWORD_EXACT_PHRASE": [], "KEYWORD_UNORDERED": [{"id": "1"}]})
    rows = [r for page in iter_ad_pages("egysupp", country="EG", token="t", fields="id",
                                        transport=t, batch_size=5, hard_cap=5) for r in page]
    assert rows == [] and t.seen == ["KEYWORD_EXACT_PHRASE"]


def test_query_url_still_defaults_to_exact_phrase():
    assert "KEYWORD_EXACT_PHRASE" in build_query_url(
        search_terms="a b", country="EG", fields="id", limit=5, token="t")
