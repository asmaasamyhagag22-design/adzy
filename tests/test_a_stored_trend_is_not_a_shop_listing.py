"""Rows stored as "market signals" before the commerce filter existed must not become Opportunities.

MEASURED 2026-08-02 across the stored brands: of 22 rows carried as market signals, **3** were
market signals. The rest were an Amazon product page, a Carrefour category page, «Dried Fruits»
twice as a bare label, a supplements shop's collection page, two Instagram posts and a Facebook
video — and each one had become a line in the Opportunities column of a client's SWOT.

`trends.sources` already knew how to reject these. The filter ran at FETCH time only, so every row
saved before it shipped went straight from the store into `synthesize_swot(trends=…)` untouched.
The fix moves the same test to the point where stored rows are handed over.

The second test here guards the write path that carries those rows: an empty list is a WRITE, not
an absence, and `BrandStore`'s one-level merge will happily let one replace real content.
"""
from __future__ import annotations

from api.webapp_analysis import market_signals_for

_SHOP = "https://www.amazon.eg/-/en/Liposomal-Nutritional-Compound/dp/B0XYZ"
_REPORT = "https://www.statista.com/outlook/cmo/beauty-personal-care/egypt/"


def _brand(rows):
    return {"domain_key": "b_com", "trends": rows}


def test_a_product_page_stored_as_a_trend_is_not_handed_to_the_swot():
    rows = [{"title": "Liposomal Nutritional Compound Complex Capsules", "url": _SHOP,
             "matched_terms": ["liposomal"]}]
    assert market_signals_for(_brand(rows)) == []


def test_a_bare_category_label_is_not_a_market_statement():
    """«Dried Fruits» was stored twice for abuauf and became two Opportunities. A category label
    asserts nothing about where a market is going."""
    rows = [{"title": "Dried Fruits", "url": "https://shop.example/c/dried-fruits",
             "matched_terms": ["dried", "fruits"]}]
    assert market_signals_for(_brand(rows)) == []


def test_a_real_market_report_still_reaches_the_swot():
    """The filter must not empty the quadrant it was built to clean. This is one of the three rows
    that survived on the real data."""
    rows = [{"title": "Beauty & Personal Care - Egypt | Statista Market Forecast", "url": _REPORT,
             "matched_terms": ["beauty"]}]
    out = market_signals_for(_brand(rows))
    assert len(out) == 1
    assert out[0].title.startswith("Beauty & Personal Care")
    assert out[0].url == _REPORT


def test_the_rows_arrive_as_objects_not_dicts():
    """`trend_signals` reads title/url/matched_terms with getattr — a dict yields "" for every one
    of them and each trend is silently discarded as off-topic. Handing dicts over made every
    opportunity vanish, and the report blamed the code that filtered them."""
    out = market_signals_for(_brand([{"title": "Beauty & Personal Care - Egypt | Statista Market Forecast",
                                      "url": _REPORT, "matched_terms": ["beauty"]}]))
    assert getattr(out[0], "title", "") and getattr(out[0], "matched_terms", None) == ("beauty",)


def test_trends_stored_only_under_the_swot_are_still_found():
    """Two brands hold their rows only at `swot.trends`; `trends_of` exists for exactly that. A
    reader that forgot the second location would blank a working tab."""
    brand = {"domain_key": "b_com",
             "swot": {"trends": [{"title": "Beauty & Personal Care - Egypt | Statista Market Forecast",
                                  "url": _REPORT, "matched_terms": ["beauty"]}]}}
    assert len(market_signals_for(brand)) == 1


# --- the write guard --------------------------------------------------------------------------

def test_the_apply_refuses_to_empty_something_the_record_had():
    """MEASURED on the first write: `_swot_to_frontend` emits every key it knows, so a value the
    apply tool forgot to carry shipped as `[]` and four brands went from 6/5/5/6 stored trends to
    zero. Nobody would have seen it until a Trend tab rendered blank in front of a client."""
    from info.swot_apply import _would_empty
    stored = {"trends": [{"title": "t"}], "notes": ["n"], "site_readiness": [{"title": "s"}]}
    assert _would_empty(stored, {"trends": [], "notes": ["n"], "site_readiness": [{"title": "s"}]}) == ["trends"]
    assert _would_empty(stored, {"trends": [], "notes": [], "site_readiness": []}) == [
        "trends", "site_readiness", "notes"]


def test_the_guard_allows_a_record_that_never_had_it():
    from info.swot_apply import _would_empty
    assert _would_empty({"trends": [], "notes": [], "site_readiness": []},
                        {"trends": [], "notes": [], "site_readiness": []}) == []


def test_the_guard_allows_growth():
    from info.swot_apply import _would_empty
    assert _would_empty({"notes": ["a"]}, {"notes": ["a", "b"], "trends": [{"t": 1}]}) == []
