# -*- coding: utf-8 -*-
"""The Places text query must name the category first, like the seeds already do.

MEASURED — the Volume Qatar RE-RUN (2026-07-21), after the market-first seeds landed. Discovery
recovered (1 → 4 real cafés) on the strength of the v2 seeds, but the saved log still shows the v1
query unchanged:

    text search 'tomato basil soup baked salmon with cafe' biased to (25.3760,51.5450) r=15000m

`discovery.py` builds it as `crit.offering_keywords[:6] + [category]` — offering words in document
order, category LAST and usually pushed out. Half of item 2 reached `market_definition.query_seeds`
and never reached this line. Hermetic."""
from __future__ import annotations

from competitor.discovery import build_text_query, build_match_criteria


def _crit():
    return build_match_criteria({
        "name": {"value": "Volume Cafe"}, "category": {"value": "cafe"},
        "service_areas": [{"value": "The Pearl Qatar"}],
        "offerings": [{"name": {"value": n}} for n in
                      ["Volume Catering", "Tomato Basil Soup", "Baked Salmon With Lemon Couscous"]],
    })


def test_the_category_opens_the_query():
    q = build_text_query(_crit())
    assert q.split()[0] == "cafe", q


def test_the_brands_own_name_never_enters_the_query():
    assert "volume" not in build_text_query(_crit()).lower()


def test_product_words_may_follow_but_do_not_dominate():
    q = build_text_query(_crit()).split()
    assert "cafe" in q[:1]
    assert len(q) <= 7                       # a query, not a menu dump


def test_a_brand_with_no_offerings_still_queries_its_category():
    crit = build_match_criteria({"name": {"value": "X"}, "category": {"value": "clinic"}})
    assert "clinic" in build_text_query(crit)


def test_no_category_falls_back_to_offering_terms():
    crit = build_match_criteria({"name": {"value": "X"},
                                 "offerings": [{"name": {"value": "Laser Hair Removal"}}]})
    assert "laser" in build_text_query(crit).lower()
