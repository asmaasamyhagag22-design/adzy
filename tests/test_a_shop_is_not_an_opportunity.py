# -*- coding: utf-8 -*-
"""Five of Abu Auf's six "opportunities" were places to buy the category.

MEASURED on the stored trends: 6/6 of the URLs this source returned for Abu Auf were retailer
category pages — carrefouregypt, gourmetegypt, jumia, amazon.eg, nutzzi, cafelax — and the SWOT
presented every one as an OPPORTUNITY. Including Carrefour, which stocks Abu Auf's own products: a
distribution channel filed as a market opening for the brand it distributes.

TWO CAUSES, and the first is the one that matters.

1. THE QUESTION WAS A SHOPPING QUESTION. `_query` built three product nouns + the market + a seed
   + the year, and the seed was "trends" / «الأكثر رواجًا» — literally "the most in-demand", which
   is what a customer types before buying. Google answered it the way it answers shoppers.

2. THERE WAS NO FILTER AT ALL. Not on host, not on shape, not on content. Whatever came back became
   an opportunity.

The filter's positive half is what actually works. Excluding storefronts by URL shape alone caught
2 of 5: Carrefour's category path is `/mafegy/en/c/FEGY1500000` and Gourmet's is
`/en/nuts-dried-fruits` — neither says "shop". But their TITLES are category LABELS, and a market
signal makes a CLAIM. That is the discriminator.

AND THE TEST CAUGHT A REGRESSION IN THE FIX: «حظر استيراد المكملات الغذائية» — an import ban —
carries no word like "market" or "report", so the positive test alone dropped it. The repo keeps a
bilingual headwind list precisely because a ban was once misfiled as an opportunity. Dropping it
here would have destroyed the Threats path in order to tidy the Opportunities one.
"""
from __future__ import annotations

import pytest

from trends.sources import SerperTrendSource, is_commerce_result


# =====================================================================
# the question
# =====================================================================

def test_the_query_asks_about_the_market_not_the_shelf():
    q = SerperTrendSource(keywords=["nuts", "dried fruits", "dates"], gl="eg", hl="en")._query()
    assert "market trends report" in q
    assert "trends 2026" not in q.replace("market trends report", "")


def test_the_arabic_seed_is_analysis_not_demand():
    """«الأكثر رواجًا» is "the most in-demand" — a shopper's phrase. The per-language seed STRUCTURE
    is the D-8 fix (CS-108) and is kept; only the word changes."""
    q = SerperTrendSource(keywords=["مكسرات", "تمور"], gl="eg", hl="ar")._query()
    assert "اتجاهات السوق" in q
    assert "الأكثر رواجًا" not in q


def test_shopping_intent_is_excluded_at_the_query():
    q = SerperTrendSource(keywords=["nuts", "dates"], gl="eg", hl="ar")._query()
    for neg in ("-buy", "-shop", "-price", "-اشتري", "-تسوق"):
        assert neg in q


def test_the_language_is_still_never_inferred_from_the_country():
    """A Doha cafe whose own site is English gets the English seed. `hl` is not derived from `gl`."""
    q = SerperTrendSource(keywords=["coffee", "roastery"], gl="qa", hl="")._query()
    assert "market trends report" in q and "اتجاهات" not in q


def test_no_keywords_still_means_no_query():
    assert SerperTrendSource(keywords=[], gl="eg", hl="en")._query() == ""


# =====================================================================
# the five real storefronts
# =====================================================================

@pytest.mark.parametrize("title,url", [
    ("Nuts , Dates & Dried Fruits Online | Carrefour Egypt",
     "https://www.carrefouregypt.com/mafegy/en/c/FEGY1500000"),
    ("Nuts & Dried Fruits | Gourmet Food Stores",
     "https://www.gourmetegypt.com/en/nuts-dried-fruits"),
    ("Nuts & Dried Fruits - Best Prices in Egypt", "https://www.jumia.com.eg/nuts-dried-fruits/"),
    ("Dried Fruits: Buy Online at Best Prices in Egypt", "https://www.amazon.eg/-/en/b?node=21"),
    ("Dried Fruits", "https://nutzzi-eg.com/product-category/dried-fruits/"),
])
def test_a_storefront_is_not_a_market_signal(title, url):
    assert is_commerce_result(title, url) is True


def test_a_channel_that_stocks_the_brand_is_not_an_opening_for_it():
    """The owner's own correction: Carrefour is not Abu Auf's competitor — it is where Abu Auf's
    products are sold. It is not an opportunity either; it is a channel, and this system has no
    box for that. Until it does, the honest thing is not to file it in the wrong one."""
    assert is_commerce_result("Nuts , Dates & Dried Fruits Online | Carrefour Egypt",
                              "https://www.carrefouregypt.com/mafegy/en/c/F1") is True


# =====================================================================
# what must survive
# =====================================================================

@pytest.mark.parametrize("title", [
    "Egypt Nuts Market Size & Outlook 2030",
    "Egypt Beauty and Personal Care Market Size & Outlook 2030",
    "Beauty trends 2026: innovation, care and longevity",
    "Consumer demand for healthy snacks is growing",
    "تقرير: نمو سوق المكسرات في مصر 2026",
    "دراسة عن اتجاهات استهلاك التمور",
])
def test_a_real_market_signal_survives(title):
    assert is_commerce_result(title, "https://example.com/a") is False


@pytest.mark.parametrize("title", [
    "حظر استيراد المكملات الغذائية",
    "Egypt supplement import ban hits importers",
    "أزمة نقص التمور تضرب السوق",
    "New regulation on food labelling",
])
def test_a_headwind_survives_even_with_no_market_word(title):
    """THE REGRESSION THIS FILE CAUGHT IN ITS OWN FIX. A ban carries no word like "market" or
    "report", so the positive market test alone dropped it — and `_TREND_THREAT_KEYS` exists
    because a ban was once shown to a client as an OPENING. Tidying Opportunities must not empty
    Threats."""
    assert is_commerce_result(title, "https://example.com/news") is False


def test_the_headwind_list_is_the_repos_own_one():
    """Not a second copy that can drift from the canonical list."""
    import ast
    import inspect

    import trends.sources as s
    tree = ast.parse(inspect.getsource(s.is_commerce_result))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.Module)) and ast.get_docstring(node):
            node.body = node.body[1:]
    assert "_TREND_THREAT_KEYS" in ast.unparse(tree)


# =====================================================================
# it is wired into the fetch, and it fails safe
# =====================================================================

def test_the_filter_runs_on_every_hit():
    import inspect
    src = inspect.getsource(SerperTrendSource.fetch)
    assert "is_commerce_result(title, link)" in src


def test_a_missing_threat_list_does_not_crash_the_source(monkeypatch):
    """The lazy import exists so `trends` need not depend on `competitor` at load. If it fails the
    source must still answer, not raise."""
    import builtins
    real = builtins.__import__

    def _boom(name, *a, **k):
        if name.startswith("competitor"):
            raise ImportError("no competitor package")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert is_commerce_result("Dried Fruits", "https://x.com/a") is True
    assert is_commerce_result("Egypt Nuts Market Size", "https://x.com/b") is False


def test_empty_input_is_not_a_market_signal():
    assert is_commerce_result("", "") is True
    assert is_commerce_result(None, None) is True
