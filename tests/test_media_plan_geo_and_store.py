# -*- coding: utf-8 -*-
"""A3 (2026-07-18): media_plan grounding integrity.
* CS-84: GeoTargeting had no `country`, so a national plan / a radius / a city list never named
  WHICH country (root of P-51 — a bare "national" is ambiguous). Add it, from country_of evidence.
* CS-06: an e-commerce CATEGORY alone locked an online_store destination as RESOLVED with the
  category name as its own "evidence" — circular. A category class is a HYPOTHESIS (resolved=False),
  not proof; only a real store URL / priced catalog / ordering link grounds it.
* CS-20: a brand WITH an address but a national-reach category (MarketDefinition radius_km=None)
  was given a fabricated 15km radius around its HQ. Respect national.
Hermetic — pure builders, no LLM/network."""
from __future__ import annotations

from media_plan.builder import build_geo, conversion_signals
from media_plan.schemas import Destination, GeoMode


def _online_store_ref(signals):
    for name, dest, ref in signals:
        if dest is Destination.ONLINE_STORE:
            return ref
    return None


# ---- CS-84: geo carries the country -------------------------------------------------

def test_geo_names_the_country_from_evidence():
    sa = build_geo({"source_url": "https://brand.sa/", "contact_channels": {}})
    assert sa.country == "Saudi Arabia"
    eg = build_geo({"source_url": "https://brand.com.eg/",
                    "contact_channels": {"physical_addresses": [{"value": "Nasr City, Cairo"}]}})
    assert eg.country == "Egypt"                      # even a radius plan names the country


# ---- CS-06: a category class is a hypothesis, not resolved proof ---------------------

def test_ecommerce_category_alone_is_an_unresolved_hypothesis():
    prof = {"source_url": "https://shop.example/", "category": {"value": "ecommerce"},
            "offerings": [{"name": {"value": "Mystery Widget"}}]}      # no price, no store URL
    ref = _online_store_ref(conversion_signals(prof))
    assert ref is not None
    assert ref.resolved is False, "category-only online_store must be a hypothesis, not a fact"
    assert ref.is_grounded is False


def test_a_real_priced_catalog_still_grounds_the_store():
    src = "https://shop.example/"
    prof = {"source_url": src, "category": {"value": "ecommerce"},
            "offerings": [{"name": {"value": f"Item {i}"}, "price_text": "100 EGP",
                           "page_url": f"{src}products/item-{i}"} for i in range(3)]}
    ref = _online_store_ref(conversion_signals(prof))
    assert ref is not None and ref.resolved is True and ref.is_grounded is True


# ---- CS-20: an address + national-reach category stays NATIONAL ----------------------

def test_national_reach_category_with_an_address_is_not_forced_to_a_radius():
    # an e-commerce brand ships nationally: MarketDefinition returns radius_km=None even with an
    # HQ address — build_geo must NOT fabricate a 15km radius around the warehouse.
    prof = {"source_url": "https://shop.eg/", "category": {"value": "ecommerce"},
            "contact_channels": {"physical_addresses": [{"value": "6th of October, Giza"}]}}
    geo = build_geo(prof)
    assert geo.mode is GeoMode.NATIONAL and geo.country == "Egypt"


def test_local_category_with_an_address_still_gets_its_radius():
    prof = {"source_url": "https://clinic.eg/", "category": {"value": "clinic"},
            "contact_channels": {"physical_addresses": [{"value": "Nasr City, Cairo"}]}}
    geo = build_geo(prof)
    assert geo.mode is GeoMode.RADIUS and geo.radius_km and geo.radius_km > 0
    assert geo.country == "Egypt"
