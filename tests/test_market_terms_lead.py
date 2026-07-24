# -*- coding: utf-8 -*-
"""The market comes before the menu.

MEASURED (Volume Qatar, 2026-07-21):
  - the trend query was `"catering tomato basil Qatar trends 2026"` — three ingredient words off
    ONE dish, because seeds are emitted in profile order and `trends/sources.py:188` takes `[:3]`;
  - the brand's own category, `"cafe"`, was **deleted** as a brand word (`engine.py:218`) because
    the trade name is "Volume **Cafe**". That breaks a whole class: "X Pharmacy", "Y Jewelry",
    "Z Bakery" all lose their category;
  - the real market words (`coffee`, `cuisine`) sat at indices 43-45, unreachable.

Two rules, both universal:
  1. the evidenced CATEGORY always survives brand-token exclusion — it is what the business IS;
  2. market-level terms are ORDERED FIRST. Deliberately ordering, not filtering: product terms
     carry real signal (`مكملات` and `جلوتاثيون` are both useful) and `tests/test_trends_batch3.py`
     rightly pins them present. Ordering fixes the truncation without losing anything."""
from __future__ import annotations

from trends.engine import keywords_from_profile


def _volume_profile():
    dishes = ["Volume Catering", "Tomato Basil Soup", "Baked Salmon With Lemon Couscous",
              "Marinated Burata Toast", "Quinoa Kale Garden Greens"]
    # The description is quoted from the REAL profile: every scraped brand has one, and it is the
    # field that corroborates a value-prop word into a market term. A fixture without it would be
    # thinner than anything production ever sees.
    return {"name": {"value": "Volume Cafe"},
            "category": {"value": "cafe"},
            "source_url": {"value": "https://volumeqatar.com/"},
            "description": {"value": "Volume Cafe is a two-floor cafe, library and event space "
                                     "serving high-quality food, coffee and catering."},
            "offerings": [{"name": {"value": d}} for d in dishes],
            "value_propositions": [{"value": "specialty coffee and a two-floor library"}]}


def test_the_category_survives_being_part_of_the_brand_name():
    kws = keywords_from_profile(_volume_profile())
    assert "cafe" in kws, "the brand IS a cafe — its category is not a brand word"
    assert "volume" not in kws                  # the actual brand word still goes


def test_market_terms_come_before_menu_items():
    kws = keywords_from_profile(_volume_profile())
    top3 = kws[:3]
    assert "cafe" in top3, kws[:8]
    # the ingredient words must not occupy the query — they may exist further down
    assert "tomato" not in top3 and "basil" not in top3, kws[:8]


def test_product_terms_are_kept_not_deleted():
    kws = keywords_from_profile(_volume_profile())
    assert "tomato" in kws and "salmon" in kws   # ordered later, never discarded


def test_a_brand_whose_name_is_unrelated_is_unaffected():
    p = {"name": {"value": "Nano Care"}, "category": {"value": "supplements"},
         "offerings": [{"name": {"value": "Liposomal Glutathione"}}]}
    kws = keywords_from_profile(p)
    assert kws[:2] == ["supplements", "liposomal"] or "supplements" == kws[0], kws[:5]
    assert "nano" not in kws and "care" not in kws


def test_no_category_still_returns_offering_terms():
    p = {"name": {"value": "X"}, "offerings": [{"name": {"value": "Laser Hair Removal"}}]}
    kws = keywords_from_profile(p)
    assert "laser" in kws
