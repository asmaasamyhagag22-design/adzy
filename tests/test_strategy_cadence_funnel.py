# -*- coding: utf-8 -*-
"""P-14 + P-15 (2026-07-18): the calendar posted a FLAT 4/week for every vertical (a clinic ≠ a
restaurant) and had NO funnel intent — every item was a shapeless 'post'. P-14 adds per-category
cadence to media_plan.config; P-15 adds funnel_stage + cta to the plan/schema so the calendar is a
funnel plan, biased toward BOFU when the objective is leads/sales. Hermetic — MockCaller / config."""
from __future__ import annotations

from business_profile.llm.caller import MockCaller
from strategy import build_strategy
from strategy.builder import _PlanItem, _PlanResponse


def test_cadence_is_per_category():
    from media_plan.config import cadence_for
    assert cadence_for("restaurant") == 7                 # high-velocity feed
    assert cadence_for("clinic") == 3                     # trust vertical posts less
    assert cadence_for("legal") == 2
    assert cadence_for("unknown-vertical") == 4           # universal default
    assert cadence_for(None) == 4


def test_build_strategy_uses_category_cadence_when_not_overridden():
    # a 28-day window: clinic (3/wk) => ~12 items; restaurant (7/wk) => ~28. No explicit cadence.
    clinic = build_strategy({"name": {"value": "Baseera Clinic"}, "category": {"value": "clinic"},
                             "offerings": [{"name": {"value": "Laser"}}]},
                            None, days=28, start_date="2026-06-01")
    resto = build_strategy({"name": {"value": "Zooba"}, "category": {"value": "restaurant"},
                            "offerings": [{"name": {"value": "Koshari"}}]},
                           None, days=28, start_date="2026-06-01")
    assert len(clinic.items) < len(resto.items)           # the clinic posts less often, by config
    assert len(clinic.items) == 12 and len(resto.items) == 28


def test_funnel_stage_and_cta_flow_through_and_are_validated():
    plan = _PlanResponse(items=[
        _PlanItem(day_offset=0, platform="instagram", content_type="post", topic="Laser",
                  angle="a", hook="h", funnel_stage="bofu", cta="Book your session"),
        _PlanItem(day_offset=2, platform="instagram", content_type="reel", topic="Clinic tour",
                  angle="a", hook="h", funnel_stage="TOFU", cta="Follow us"),
        _PlanItem(day_offset=4, platform="facebook", content_type="post", topic="Tips",
                  angle="a", hook="h", funnel_stage="garbage", cta="[Client Name] call now"),
    ])
    cal = build_strategy({"name": {"value": "Baseera"}, "category": {"value": "clinic"},
                          "offerings": [{"name": {"value": "Laser"}}]},
                         MockCaller({"content_strategy": plan}), days=14, start_date="2026-06-01")
    by_topic = {i.topic: i for i in cal.items}
    assert by_topic["Laser"].funnel_stage == "bofu" and by_topic["Laser"].cta == "Book your session"
    assert by_topic["Clinic tour"].funnel_stage == "tofu"          # normalized to lowercase
    assert by_topic["Tips"].funnel_stage == ""                     # invalid stage dropped
    assert by_topic["Tips"].cta == ""                              # placeholder CTA scrubbed (P-18)
