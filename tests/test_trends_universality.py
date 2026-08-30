# -*- coding: utf-8 -*-
"""Batch I / A6 (2026-07-18): trends must not privilege one country.
* CS-107: `_STOPWORDS` contained the literal 'egypt', so an Egyptian brand's own market keyword was
  silently unmatchable (dropped before matching) while a Qatari brand's 'qatar' matched — a D-8
  asymmetry baked into a word list.
* CS-108: the SERP seed hardcoded the English word 'trends' + the literal year '2026' and never put
  the MARKET in the query TEXT (the owner MEASURED that `gl` alone can't fix relevance). Localize the
  seed by language, name the market in the text, and derive the year.
Hermetic — pure string logic, no network."""
from __future__ import annotations

from datetime import datetime


def test_egypt_is_not_a_stopword_and_matches_like_any_market():
    from trends.engine import match_to_keywords
    from trends.sources import TrendItem
    items = [TrendItem(title="Best cafes in Egypt this year", url="", source="x", score=1.0),
             TrendItem(title="Best cafes in Qatar this year", url="", source="x", score=1.0)]
    match_to_keywords(items, ["egypt", "qatar", "cafes"])
    # both country keywords match their own market — Egypt is no longer silently dropped
    assert "egypt" in items[0].matched_terms
    assert "qatar" in items[1].matched_terms


def test_serper_query_names_the_market_localizes_the_seed_and_derives_the_year():
    from trends.sources import SerperTrendSource
    q_ar = SerperTrendSource(keywords=["coffee"], gl="qa", hl="ar")._query()
    assert "coffee" in q_ar
    assert "Qatar" in q_ar                                   # the MARKET is in the query TEXT (D-8)
    assert str(datetime.now().year) in q_ar                 # current year, not a frozen 2026
    assert "trends" not in q_ar                              # Arabic query is localized, not English
    # an English-market brand keeps the English seed word
    q_en = SerperTrendSource(keywords=["coffee"], gl="us", hl="en")._query()
    assert "trends" in q_en and str(datetime.now().year) in q_en
    # no country evidence -> no market token, but still a valid seeded query
    q_none = SerperTrendSource(keywords=["coffee"], gl="", hl="en")._query()
    assert "coffee" in q_none and "trends" in q_none
