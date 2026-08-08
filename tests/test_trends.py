"""Trend engine — hermetic (injected fake sources, no network)."""
from __future__ import annotations

import time

from trends import (
    TrendItem, fetch_trends, rank_trends, match_to_keywords, top_trends, top_trends_bounded,
    keywords_from_profile,
)


def test_top_trends_bounded_abandons_a_slow_source(monkeypatch):
    # a hung/slow trend source must NEVER block analyze — bounded fetch returns [] fast
    import trends.engine as eng

    def _slow(*a, **k):
        time.sleep(5)
        return ["should-not-appear"]

    monkeypatch.setattr(eng, "top_trends", _slow)
    t0 = time.monotonic()
    out = eng.top_trends_bounded(["ai"], timeout_s=0.3)
    assert out == [] and (time.monotonic() - t0) < 2.0     # returned ~immediately, not after 5s


def test_top_trends_bounded_returns_items_when_fast(monkeypatch):
    import trends.engine as eng
    monkeypatch.setattr(eng, "top_trends", lambda *a, **k: ["a", "b"])
    assert eng.top_trends_bounded(["x"], timeout_s=2.0) == ["a", "b"]


class _FakeSource:
    def __init__(self, name, items):
        self.name = name
        self._items = items

    def fetch(self, limit=30):
        return list(self._items)


class _BoomSource:
    name = "boom"

    def fetch(self, limit=30):
        raise RuntimeError("source down")


def test_fetch_trends_aggregates_and_a_failing_source_is_skipped():
    a = _FakeSource("a", [TrendItem("X", "u", "a", score=10)])
    items = fetch_trends([a, _BoomSource()])
    assert [it.title for it in items] == ["X"]          # boom contributed nothing, no raise


def test_rank_normalizes_popularity_per_source_and_adds_recency():
    now = 1_000_000.0
    items = [
        TrendItem("hn big", "u", "hn", score=100, created_ts=now - 3600),
        TrendItem("hn small", "u", "hn", score=10, created_ts=now - 3600),
    ]
    ranked = rank_trends(items, now=now)
    assert ranked[0].title == "hn big"                  # higher popularity ranks first
    assert ranked[0].trend_score > ranked[1].trend_score


def test_match_is_wholeword_and_drops_stopwords():
    items = [TrendItem("The best business services online", "u", "s")]
    match_to_keywords(items, ["business", "services"])   # both are stopwords
    assert items[0].matched_terms == ()

    art = [TrendItem("New AI art generator", "u", "s")]
    match_to_keywords(art, ["art"])
    assert art[0].matched_terms == ("art",)

    cart = [TrendItem("Smart cartwheel trick", "u", "s")]
    match_to_keywords(cart, ["art"])
    assert cart[0].matched_terms == ()                  # 'art' inside 'cartwheel' != a match


def test_top_trends_puts_on_topic_first_then_ranks():
    now = 1_000_000.0
    src = _FakeSource("hn", [
        TrendItem("Generic cooking tips", "u1", "hn", score=100, created_ts=now - 3600),
        TrendItem("AI jewelry design goes viral", "u2", "hn", score=50, created_ts=now - 3600),
        TrendItem("Diamond market shift", "u3", "hn", score=10, created_ts=now - 3600),
    ])
    items = top_trends(["jewelry", "diamond"], sources=[src], now=now, top_k=3)
    assert items[0].matched_terms == ("jewelry",)       # matched + strongest score
    assert items[1].matched_terms == ("diamond",)       # matched + weaker score
    assert "cooking" in items[2].title.lower()          # unmatched generic last


def test_require_match_filters_unrelated_trends():
    now = 1_000_000.0
    src = _FakeSource("hn", [
        TrendItem("Cooking tips", "u1", "hn", score=100, created_ts=now - 3600),
        TrendItem("Jewelry trends 2026", "u2", "hn", score=5, created_ts=now - 3600),
    ])
    items = top_trends(["jewelry"], sources=[src], now=now, require_match=True)
    assert [it.title for it in items] == ["Jewelry trends 2026"]


def test_keywords_from_profile_extracts_topical_terms():
    profile = {
        "category": {"value": "jewelry_ecommerce"},
        "offerings": [{"name": {"value": "Diamond Rings"}}, {"name": "Gold Necklaces"}],
        # value_propositions are EvidencedFields -> text under 'value' (the REAL shape).
        "value_propositions": [{"value": "Handcrafted luxury pieces"}],
        "tagline": {"value": "Timeless elegance"},
    }
    kws = keywords_from_profile(profile)
    assert "jewelry" in kws and "diamond" in kws and "handcrafted" in kws
    assert "the" not in kws and "business" not in kws   # stopwords excluded


# ---- vertical-aware source selection (consumer brands must not get TECH trends) ----

def test_subreddits_are_vertical_aware():
    from trends.sources import _subreddits_for
    assert "jewelry" in _subreddits_for(["rings", "earrings", "jewellery"])
    assert "food" in _subreddits_for(["burger", "restaurant"])
    assert "SkincareAddiction" in _subreddits_for(["makeup", "skincare"])
    assert "technology" in _subreddits_for(["saas", "platform"])
    # unknown vertical -> a broad CONSUMER mix, never tech-only
    assert "femalefashionadvice" in _subreddits_for(["widget"])


def test_tech_feeds_only_for_tech_brands():
    from trends.sources import default_trend_sources
    # a jewelry brand: web search + Reddit consumer subs, NO Hacker News / Dev.to
    assert [s.name for s in default_trend_sources(["rings", "earrings"])] == ["web", "reddit"]
    # a software brand: keeps the tech feeds
    names = [s.name for s in default_trend_sources(["saas", "developer"])]
    assert "hackernews" in names and "devto" in names


def test_serper_trend_source_builds_a_vertical_query_and_is_offline_safe(monkeypatch):
    from trends.sources import SerperTrendSource
    src = SerperTrendSource(["ecommerce", "rings", "earrings", "necklaces"])
    q = src._query()
    assert q.startswith("rings earrings necklaces")       # drops the generic 'ecommerce'
    # UPDATED 2026-08-02. This asserted the seed was "trends", which made the whole thing a
    # SHOPPING query — MEASURED, 6/6 of Abu Auf's returned URLs were retailer category pages and
    # the SWOT shipped them as opportunities, Carrefour (a channel that stocks Abu Auf) among them.
    # The seed now asks a market question and the shopping surface is excluded at the query.
    assert "market trends report" in q
    assert "2026" in q
    assert "-buy" in q and "-shop" in q
    # No provider configured / any failure -> [] (never raises).
    import competitor.search_providers as sp
    monkeypatch.setattr(sp, "get_default_search_provider", lambda: None)
    assert src.fetch(10) == []


class _CapturingProvider:
    """Records exactly what reaches the SERP provider. The seam these tests assert at."""
    name = "capture"

    def __init__(self):
        self.calls: list[dict] = []

    def search(self, query, *, num=10, gl=None, hl=None):
        self.calls.append({"query": query, "num": num, "gl": gl, "hl": hl})
        return []


def test_serper_trend_source_sends_the_brands_country_to_the_serp(monkeypatch):
    """D-8: a Qatari brand's "market signals" must come from QATAR's SERP.

    THE BUG THIS PINS (found 2026-07-15 while shipping a real Doha deliverable):
    `SerperTrendSource.fetch` called `prov.search(q, num=...)` with NEITHER `gl` nor `hl`.
    `SerperProvider.search` only adds them when truthy (search_providers.py:78-81), so the
    payload was `{q, num}` and Serper served its **US/English default** — which the dashboard
    then labelled "إشارات السوق الحيّة · Live market signals" for a Doha cafe.

    Same family as the 42 and the "Rising interest" else-branch: the citation TRACES (a real
    URL, a real article) while the claim — "this is a signal for YOUR market" — does not,
    because the search asked about a different market. A true link answering a question
    nobody asked.
    """
    import competitor.search_providers as sp
    from trends.sources import SerperTrendSource

    cap = _CapturingProvider()
    monkeypatch.setattr(sp, "get_default_search_provider", lambda: cap)

    SerperTrendSource(["coffee", "roastery"], gl="qa", hl="ar").fetch(10)

    assert len(cap.calls) == 1
    assert cap.calls[0]["gl"] == "qa"      # the country reaches the SERP
    assert cap.calls[0]["hl"] == "ar"      # so does the language


def test_serper_trend_source_omits_gl_and_hl_when_there_is_no_country_evidence(monkeypatch):
    """The honest default: no evidence of a country -> NO `gl`. Never a guessed one, and
    never a silent US default dressed up as the brand's market.

    This also keeps the no-country request byte-identical to the pre-D-8 behaviour: both
    values stay None, so `SerperProvider` adds neither key to the payload.
    """
    import competitor.search_providers as sp
    from trends.sources import SerperTrendSource

    cap = _CapturingProvider()
    monkeypatch.setattr(sp, "get_default_search_provider", lambda: cap)

    SerperTrendSource(["coffee", "roastery"]).fetch(10)          # no gl/hl supplied at all

    assert len(cap.calls) == 1
    assert cap.calls[0]["gl"] is None
    assert cap.calls[0]["hl"] is None


def test_serper_trend_source_never_sends_an_empty_string_as_a_country(monkeypatch):
    """`country_of` returns "" (not None) when the evidence carries no country
    (poster/locale.py:103). An empty string must degrade to None, never reach the payload as
    `gl=""` — an empty `gl` is a malformed request, not an honest absence."""
    import competitor.search_providers as sp
    from trends.sources import SerperTrendSource

    cap = _CapturingProvider()
    monkeypatch.setattr(sp, "get_default_search_provider", lambda: cap)

    SerperTrendSource(["coffee"], gl="", hl="").fetch(10)         # the country_of("") shape

    assert cap.calls[0]["gl"] is None
    assert cap.calls[0]["hl"] is None
