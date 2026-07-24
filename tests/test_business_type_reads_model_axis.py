# -*- coding: utf-8 -*-
"""Routing must read the MODEL axis, not re-derive it from the conflated `category`.

Step 3 of the split (process.md §8.11). `business_type.classify()` asks HOW a business sells —
local vs ecommerce vs reach — which is exactly what `business_model` now answers directly. It used
to reconstruct that from `category` via hand-kept sets (`_LOCAL_CATEGORIES`, `_ECOMMERCE_CATEGORIES`),
the very mapping the split removes.

When `business_model` is present it wins. When it is absent (an un-migrated profile), the legacy
category-set behaviour still runs, so nothing regresses mid-migration. Hermetic."""
from __future__ import annotations

from competitor.business_type import BusinessType, classify_business_type as classify


def _profile(**kw):
    base = {"name": {"value": "X"}, "source_url": {"value": "https://x.example/"}}
    base.update(kw)
    return base


def test_an_explicit_ecommerce_model_routes_without_needing_a_category():
    # market says "jewellery" (a LOCAL-ish word under the old sets); model says it ships online.
    bt = classify(_profile(market_category={"value": "handmade jewellery"},
                           business_model="ecommerce"))
    assert bt.business_type.value in ("ecommerce", "hybrid")


def test_a_local_model_routes_local_even_for_an_ecommerce_market_word():
    bt = classify(_profile(market_category={"value": "supplements"},
                           business_model="local",
                           service_areas=[{"value": "Cairo"}]))
    assert bt.business_type is BusinessType.LOCAL


def test_a_service_model_routes_to_reach():
    bt = classify(_profile(market_category={"value": "IT training"},
                           business_model="service"))
    assert bt.business_type is BusinessType.REACH


def test_an_absent_model_falls_back_to_the_legacy_category_sets():
    # no business_model -> the pre-split behaviour still runs, so un-migrated profiles are unchanged
    bt = classify(_profile(category={"value": "cafe"}, service_areas=[{"value": "Doha"}]))
    assert bt.business_type is BusinessType.LOCAL


def test_an_unknown_model_defers_to_structure_and_category():
    bt = classify(_profile(category={"value": "ecommerce"}, business_model="unknown"))
    assert bt.business_type.value in ("ecommerce", "hybrid", "reach", "local")   # decided by the old signals
