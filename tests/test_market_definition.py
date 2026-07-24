"""MarketDefinition projection (competitor/market_definition.py) — U8b slice, hermetic.

The projection must be PURE (profile-in, definition-out), evidence-honest (missing fields
stay empty, never invented), config-by-category with a universal default (D-8), and produce
bilingual query seeds for Discovery v2.
"""
from __future__ import annotations

from competitor.market_definition import MarketDefinition, build_market_definition


def _clinic_profile():
    return {
        "source_url": "https://clinic.example.eg/",
        "category": {"value": "clinic"},
        "audience_type": {"value": "b2c"},
        "description": {"value": "A dermatology clinic in Cairo."},
        "offerings": [{"name": {"value": "Laser Hair Removal"}},
                      {"name": "Acne Treatment"}],
        "contact_channels": {"physical_addresses": [
            {"value": "12 Tahrir St, Dokki, Giza"}]},
        "service_areas": [{"value": "Egypt"}],
        # An Egyptian clinic's audience searches in Arabic -> the localized "best …" query seed is
        # correct here. (P-83: that seed is now keyed on this measured language, not hardcoded.)
        "languages": [{"code": "ar"}, {"code": "en"}],
    }


def test_projection_grounds_geo_category_and_services():
    md = build_market_definition(_clinic_profile())
    assert md.category == "clinic" and md.is_grounded
    assert md.geo.country == "Egypt"            # ccTLD .eg via poster.locale
    assert md.geo.city == "Giza" and "Dokki" in md.geo.districts
    assert md.geo.radius_km == 10.0             # clinic-class default (config, not logic)
    assert "Laser Hair Removal" in md.sub_services and "Acne Treatment" in md.sub_services
    assert md.audience.audience_type == "b2c"


def test_query_seeds_are_bilingual_services_x_places():
    md = build_market_definition(_clinic_profile())
    assert any("Laser Hair Removal" in q and "Giza" in q for q in md.query_seeds)
    assert any(q.startswith("أفضل") for q in md.query_seeds)      # Arabic seed
    assert len(md.query_seeds) <= 20


def test_ecommerce_is_geography_free_and_tier_detects_tokens():
    p = {"source_url": "https://shop.example/", "category": {"value": "ecommerce"},
         "description": {"value": "Premium luxury jewelry."},
         "service_areas": [{"value": "Egypt"}]}
    md = build_market_definition(p)
    assert md.geo.radius_km is None             # national reach
    assert md.tier == "premium"


def test_empty_profile_is_honest_not_invented():
    md = build_market_definition({})
    assert isinstance(md, MarketDefinition)
    assert not md.is_grounded
    assert md.sub_services == [] and md.query_seeds == []
    assert md.tier == "mid"                     # neutral default, no fabricated positioning
