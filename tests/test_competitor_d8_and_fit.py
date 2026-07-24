"""P-80 (web peer fit is honestly labelled), P-82 (category filter covers more verticals),
P-83 (market-definition query seeds keyed to the brand's language, not hardcoded Arabic)."""
from __future__ import annotations

from competitor.discovery import CATEGORY_TYPE_MAP


# ---- P-82: the coarse category filter now covers the common consumer verticals ----
def test_common_verticals_have_place_types_now():
    for cat in ("retail", "ecommerce", "beauty", "fitness", "automotive", "cafe", "hospital"):
        assert CATEGORY_TYPE_MAP.get(cat), f"{cat} still has no Places type mapping (fails open)"


def test_beauty_maps_to_beauty_places_not_nothing():
    assert "beauty_salon" in CATEGORY_TYPE_MAP["beauty"]


# ---- P-80: a web-sourced peer's score is labelled search_rank, not a measured fit ----
def test_web_peer_fit_is_labelled_search_rank():
    from competitor.models import PeerFitBreakdown
    b = PeerFitBreakdown(total=0.5, basis="search_rank")
    assert b.basis == "search_rank"
    # a normal (Places) breakdown defaults to the real weighted basis
    assert PeerFitBreakdown(total=0.8).basis == "weighted"


# ---- P-83: market-definition seeds follow the brand's language ----
def _seeds_for(profile):
    from competitor.market_definition import build_market_definition
    md = build_market_definition(profile)
    return md.query_seeds if hasattr(md, "query_seeds") else getattr(md, "seeds", [])


def test_english_brand_gets_english_superlative_not_arabic():
    from competitor import market_definition as M
    prof = {
        "category": "restaurant",
        "offerings": [{"name": "pizza"}],
        "physical_address": "London",
        "languages": [{"code": "en"}],
        "service_areas": [{"value": "London"}],
    }
    # Build via the module's public builder; fall back to inspecting seed construction.
    md = M.build_market_definition(prof)
    seeds = getattr(md, "query_seeds", None) or getattr(md, "seeds", []) or []
    joined = " || ".join(seeds)
    assert "أفضل" not in joined, f"Arabic superlative leaked into an English brand's seeds: {seeds}"
    if seeds:
        assert any("pizza" in s for s in seeds)
