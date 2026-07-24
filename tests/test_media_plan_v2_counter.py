# -*- coding: utf-8 -*-
"""ش4 — الهجوم المضاد (2026-07-19): the competitor cell in Media Plan v2 becomes a COUNTER-play
seeded by real ad-presence facts: rival dominant format image→we bet Reel (video→static/carousel);
rival plays price/discount→we play quality from our OWN evidenced strength — never a discount war.
Provenance in `counter_basis` ("أب القرار"). And the honesty ruling: we hold longevity evidence,
NOT competitor CPL — so NO invented kpi/target number anywhere; a rationale note instead. Hermetic."""
from __future__ import annotations

from media_plan_v2 import build_media_plan_v2

_PROFILE = {"source_url": "https://clinic.eg/", "name": {"value": "Baseera Clinic"},
            "category": {"value": "clinic"}, "audience_type": {"value": "نساء 25-45"},
            "offerings": [{"name": {"value": "Laser"}, "price_text": {"value": "800 EGP"}}]}
_SWOT = {"strengths": [{"text": "أطباء جلدية معتمدون"}], "opportunities": []}


def _presence(fmt="image", cta="whatsapp", winners=1, fatigue=0, active=4, longest=43):
    return [{"brand_ref": "rival_a", "page_name": "Rival A", "active_count": active,
             "longest_days": longest, "proven_winners": 0, "formats": {}, "platforms": {},
             "winners_30d": winners, "fatigue_groups": fatigue,
             "top_cta": cta, "top_format": fmt}]


def _cell3(plan):
    return next(c for c in plan.test_cells if c.axis == "competitor")


def test_counter_cell_image_rival_bets_reel():
    plan = build_media_plan_v2(_PROFILE, swot=_SWOT, ad_presence=_presence(fmt="image"))
    c = _cell3(plan)
    assert "Reel" in c.hypothesis or "ريل" in c.hypothesis
    assert "Rival A" in c.counter_basis and "43" in c.counter_basis
    assert "Meta Ad Library" in c.counter_basis


def test_counter_cell_video_rival_bets_static():
    plan = build_media_plan_v2(_PROFILE, swot=_SWOT, ad_presence=_presence(fmt="video"))
    c = _cell3(plan)
    assert ("ثابت" in c.hypothesis) or ("كاروسيل" in c.hypothesis) or ("carousel" in c.hypothesis.lower())


def test_price_angle_countered_with_own_quality():
    intel = {"rival_a": {"brand_ref": "rival_a", "offers": [
        {"value": "خصم 25% على الليزر", "evidence": [{"page_url": "https://fb/1",
         "quote": "خصم 25%", "extractor": "ig_snapshot"}],
         "confidence": "high", "source_type": "extracted"}],
        "usps": [], "pain_points": [], "message_angles": [], "rejected": 0}}
    plan = build_media_plan_v2(_PROFILE, swot=_SWOT, ad_presence=_presence(),
                               competitor_intel=intel)
    c = _cell3(plan)
    assert "أطباء جلدية معتمدون" in c.hypothesis          # OUR quality edge leads
    assert "خصم" not in c.cta                              # never a discount war on our side


def test_no_ads_keeps_legacy_cell3():
    base = build_media_plan_v2(_PROFILE, swot=_SWOT)
    withnone = build_media_plan_v2(_PROFILE, swot=_SWOT, ad_presence=None, competitor_intel=None)
    assert _cell3(base).hypothesis == _cell3(withnone).hypothesis
    assert _cell3(base).counter_basis == "" and base.competitor_rationale == []


def test_no_invented_kpi_target_anywhere():
    plan = build_media_plan_v2(_PROFILE, swot=_SWOT, ad_presence=_presence(longest=43))
    # the rationale exists and says we will NOT invent the rival's CPL
    joined = " ".join(plan.competitor_rationale)
    assert "43" in joined and ("لن نخترعه" in joined or "لا نملك" in joined)
    assert any("43" in n for n in plan.reality_check.notes)   # surfaced beside the kill-switch
    # and the kill-switch number is still OUR price-derived one, untouched by rival data
    assert plan.reality_check.kill_switch.basis == "price_margin"


def test_dominant_rival_selection_ties():
    from media_plan_v2.engine import _dominant_rival
    two = [{"page_name": "A", "active_count": 3, "longest_days": 10},
           {"page_name": "B", "active_count": 3, "longest_days": 90}]
    assert _dominant_rival(two)["page_name"] == "B"           # tie on count -> longest wins
    assert _dominant_rival([]) is None
