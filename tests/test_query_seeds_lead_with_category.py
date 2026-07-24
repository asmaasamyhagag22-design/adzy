# -*- coding: utf-8 -*-
"""Competitor search queries must name the MARKET, not the menu.

MEASURED (Volume Qatar, 2026-07-21): `market_definition.py:138` reads
`for svc in subs[:5] or ([category] if category else [])` — the category is used ONLY when the
brand has no offerings at all. This café's offerings are individual dishes, so the three seeds
`competitor/discovery.py:242` actually issued were:

    "Volume Catering Qanat Quartier"
    "best Volume Catering in Qanat Quartier"
    "Tomato Basil Soup Qanat Quartier"

Two of three carry the brand's OWN product name, and not one says "cafe". Google returned two
places, one of which was the brand itself. The same shape hit nanocare, whose seeds searched for
its own SKUs ("أفضل نانو كير ليبوسومال حديد في Cairo") — nobody else sells those, by definition.

Rules: the category seeds come FIRST, the brand's own name never appears in a seed, and the place
token prefers something searchable (city/country) over a micro-district. Hermetic."""
from __future__ import annotations

from competitor.market_definition import build_market_definition


def _volume():
    dishes = ["Volume Catering", "Tomato Basil Soup", "Baked Salmon With Lemon Couscous"]
    return {"name": {"value": "Volume Cafe"}, "category": {"value": "cafe"},
            "source_url": {"value": "https://volumeqatar.com/"},
            "languages": [{"code": "en"}],
            "service_areas": [{"value": "Qanat Quartier"}, {"value": "The Pearl Qatar"},
                              {"value": "Qatar"}],
            "offerings": [{"name": {"value": d}} for d in dishes]}


def test_category_seeds_come_first():
    seeds = build_market_definition(_volume()).query_seeds
    assert seeds, "a brand with a category must always get seeds"
    assert any("cafe" in s.lower() for s in seeds[:2]), seeds[:4]


def test_no_seed_carries_the_brands_own_name():
    seeds = build_market_definition(_volume()).query_seeds
    assert not any("volume" in s.lower() for s in seeds), \
        [s for s in seeds if "volume" in s.lower()]


def test_the_place_token_is_searchable():
    seeds = build_market_definition(_volume()).query_seeds
    lead = " ".join(seeds[:2]).lower()
    assert "qatar" in lead, seeds[:4]        # not only the micro-district


def test_product_seeds_are_kept_after_the_market_ones():
    seeds = build_market_definition(_volume()).query_seeds
    joined = " ".join(seeds).lower()
    assert "soup" in joined or "salmon" in joined     # still available, just not leading


def test_a_brand_with_no_offerings_still_gets_category_seeds():
    md = build_market_definition({"name": {"value": "X"}, "category": {"value": "clinic"},
                                  "service_areas": [{"value": "Cairo"}]})
    assert any("clinic" in s.lower() for s in md.query_seeds)
