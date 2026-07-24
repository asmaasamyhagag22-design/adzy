# -*- coding: utf-8 -*-
"""الباتش 3 — الترند وحُرّاس الإنفاق: CS-63 dedup (crossposts stop eating top_k slots),
CS-44 salience (repeated-across-fields keywords outrank insertion order), the measured-noise
disclosure surfaces, and the Serper monthly meter (fail-closed, the C3 gap). Hermetic."""
from __future__ import annotations

from trends.engine import NOISE_DISCLOSURE, fetch_trends
from trends.sources import SerperTrendSource, TrendItem


class _Src:
    def __init__(self, name, items):
        self.name = name
        self._items = items

    def fetch(self, limit):
        return self._items[:limit]


def _it(url, title="t"):
    return TrendItem(source="x", title=title, url=url, score=1.0)


def test_cs63_duplicate_urls_keep_one_slot():
    a = _Src("a", [_it("https://Site.com/post/"), _it("https://other.com/x")])
    b = _Src("b", [_it("https://site.com/post?utm_source=reddit"),   # same page, tracked + cased
                   _it("https://site.com/post"), _it("https://third.com/y")])
    items = fetch_trends([a, b])
    urls = [i.url for i in items]
    assert len(urls) == 3                                   # 5 in -> 3 unique out
    assert sum("site.com/post" in u.lower() for u in urls) == 1


def test_cs44_salience_beats_insertion_order():
    # 'جلدية' appears in 3 profile fields (3 duplicates in the list); 'استشارات' once but FIRST
    src = SerperTrendSource(["استشارات", "جلدية", "ليزر", "جلدية", "جلدية", "ليزر"])
    q = src._query()
    assert q.index("جلدية") < q.index("استشارات")           # frequency outranks position
    assert q.index("ليزر") < q.index("استشارات")


def test_noise_disclosure_reaches_the_market_page(tmp_path):
    from tests.test_war_room import _build, _result
    doc = _result()
    doc["trends"] = [{"title": "Signal", "url": "https://news.example/a"}]
    h = _build(tmp_path, result=doc)
    assert "تكرار نفس البحث غيّر نحو ثلثي الروابط" in h      # measured, dated, no bare number
    h2 = _build(tmp_path)                                    # no trends -> no disclosure needed
    assert NOISE_DISCLOSURE not in h2


def test_serper_meter_caps_and_records(tmp_path, monkeypatch):
    from competitor.search_providers import SerperProvider
    led = tmp_path / "serper.jsonl"
    monkeypatch.setenv("SERPER_LEDGER", str(led))
    monkeypatch.setenv("SERPER_MONTHLY_CAP_TEST", "x")
    monkeypatch.setenv("SERPER_MONTHLY_CALL_CAP", "2")
    calls = {"n": 0}

    def fake_post(url, payload, headers):
        calls["n"] += 1
        return {"organic": [{"link": "https://a.example/1", "title": "T", "snippet": ""}]}

    import competitor.search_providers as sp
    monkeypatch.setattr(sp, "_post_json", fake_post)
    prov = SerperProvider(api_key="k")
    assert len(prov.search("q1")) == 1 and len(prov.search("q2")) == 1
    assert prov.search("q3") == [] and "refused" in (prov.last_error or "")   # cap blocks call #3
    assert calls["n"] == 2                                   # the refused call NEVER hit the net
    led.write_text(led.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
    assert prov.search("q4") == [] and "corrupt" in (prov.last_error or "")   # fail-closed


def test_brand_tokens_excluded_market_kept():
    # owner ask 2026-07-20 ("عايزاها للمجال كله مش منتج بعينه"): the brand's own name must never
    # ride the trend seed — the pulse reads the CATEGORY, not the brand's mirror.
    from trends.engine import brand_identity_tokens, keywords_from_profile
    profile = {"name": {"value": "نانو كير"},
               "source_url": {"value": "https://nanocareegypt.com/"},
               "category": {"value": "supplements"},
               "offerings": [{"name": {"value": "نانو كير ليبوسومال جلوتاثيون"}},
                             {"name": {"value": "مكملات غذائية فيتامينات"}}]}
    toks = brand_identity_tokens(profile)
    assert {"نانو", "كير", "نانوكير", "nanocareegypt"} <= toks
    kws = keywords_from_profile(profile)
    for banned in ("نانو", "كير", "نانوكير", "nanocareegypt"):
        assert banned not in kws, banned
    for market in ("جلوتاثيون", "ليبوسومال", "مكملات", "فيتامينات", "supplements"):
        assert market in kws, market
