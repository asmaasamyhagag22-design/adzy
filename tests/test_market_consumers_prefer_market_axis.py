# -*- coding: utf-8 -*-
"""Market-side consumers must prefer the free-text market over the conflated `category`.

Step 3 of the split (process.md §8.11). The trend seeds and the competitor query seeds ask WHAT the
business sells — which `market_category` answers precisely ("liposomal supplements") where
`category` only gave a coarse, often-wrong label ("ecommerce", "services_b2c"). When
`market_category` is present it leads; when absent, the legacy `category` still seeds, so
un-migrated profiles are unchanged. Hermetic."""
from __future__ import annotations

from trends.engine import keywords_from_profile
from competitor.market_definition import build_market_definition


def _profile(**kw):
    base = {"name": {"value": "Nano Care"}, "source_url": {"value": "https://nanocareegypt.com/"},
            "offerings": [{"name": {"value": "Liposomal Iron"}}]}
    base.update(kw)
    return base


def test_trend_seeds_lead_with_the_market_when_present():
    kws = keywords_from_profile(_profile(category={"value": "ecommerce"},
                                         market_category={"value": "liposomal supplements"}))
    # the real market words lead — not the business model "ecommerce"
    assert "supplements" in kws[:3] or "liposomal" in kws[:3], kws[:6]
    assert "ecommerce" not in kws[:2]


def test_trend_seeds_fall_back_to_category_when_market_absent():
    kws = keywords_from_profile(_profile(category={"value": "cafe"}))
    assert "cafe" in kws                          # un-migrated profile still seeds from category


def test_query_seeds_lead_with_the_market_when_present():
    md = build_market_definition(_profile(category={"value": "ecommerce"},
                                          market_category={"value": "supplements"},
                                          service_areas=[{"value": "Cairo"}]))
    lead = " ".join(md.query_seeds[:2]).lower()
    assert "supplements" in lead
    assert "ecommerce" not in lead                # the model word never seeds a market search


def test_query_seeds_fall_back_to_category():
    md = build_market_definition({"name": {"value": "X"}, "category": {"value": "clinic"},
                                  "source_url": {"value": "https://x.eg/"},
                                  "service_areas": [{"value": "Cairo"}]})
    assert any("clinic" in s.lower() for s in md.query_seeds)


def test_a_business_model_word_is_not_treated_as_a_market():
    # "services_b2c" (Coca-Cola's mislabel) must never become a trend seed
    kws = keywords_from_profile(_profile(category={"value": "services_b2c"},
                                         market_category={"value": "soft drink beverages"}))
    assert "services" not in kws and "b2c" not in kws
    assert "beverages" in kws or "drink" in kws
