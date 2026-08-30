# -*- coding: utf-8 -*-
"""The owner's typed budget must be CONSUMED, not just recorded.

Owner (2026-07-20): "حساب التكلفة بكتب مش بيحسب غير الديفلت بس." She types a monthly budget in the
vault; the plan still showed "الميزانية: مطلوب رقمك" and every test cell showed a bare percentage,
because `test_budget` was never set by the builder — the number went in and nothing downstream read
it.

Two honest rules, both universal:
  - her monthly budget IS the first month's test budget, split across objectives by the funnel
    allocation the plan already computed (no invented fraction), and
  - when a share cannot buy a readable test (below the LEARNING FLOOR, 3 x target cost x ad sets)
    the plan neither silently accepts it nor crashes — it says so in `budget_note`.
Hermetic (no model calls)."""
from __future__ import annotations

from media_plan.builder import build_media_plan
from media_plan.maturity import MaturityVerdict

_PROFILE = {
    "source_url": {"value": "https://clinic.example/"},
    "name": {"value": "New Clinic"},
    "category": {"value": "clinic"},
    "offerings": [{"name": {"value": "Laser"}, "price_text": {"value": "800 EGP"}}],
    "contact_channels": {"whatsapp_numbers": ["+201000000000"]},
}

_NASCENT = MaturityVerdict(level="nascent",
                           signals=["حضور سوشيال: لا منصات", "لا مراجعات عملاء متاحة"],
                           disclosure="براند ناشئ (يحتاج بناء وعي قبل التحويل) — تجريبي")


class _Caller:
    def __call__(self, system, user, schema, group_name=""):
        from media_plan.schemas import Confidence, Destination, FunnelStage, MetaObjective
        return schema(objective=MetaObjective.LEADS, destination=Destination.WHATSAPP,
                      funnel_stage=FunnelStage.BOFU, rationale="whatsapp evidenced",
                      confidence=Confidence.HIGH, alternatives=[]), None


def _plan(**kw):
    return build_media_plan(_PROFILE, caller=_Caller(), run_id="test", **kw)


def test_budget_stays_unknown_without_the_owner_number():
    mp = _plan()
    assert mp is not None
    assert all(o.test_budget is None for o in mp.objectives)      # honest TBD, unchanged behaviour
    assert all(not o.budget_note for o in mp.objectives)


def test_owner_monthly_budget_becomes_the_test_budget():
    mp = _plan(monthly_budget=30000.0)
    assert mp is not None
    obj = mp.objectives[0]
    assert obj.test_budget == 30000.0                             # her number, actually used
    assert "ميزانيتك" in obj.budget_note                          # disclosed as HERS, not a default


def test_a_funnel_plan_splits_her_budget_by_allocation():
    mp = _plan(monthly_budget=30000.0, maturity=_NASCENT)
    assert mp is not None and len(mp.objectives) == 2
    total = sum(o.test_budget or 0.0 for o in mp.objectives)
    assert abs(total - 30000.0) < 1.0                             # the whole budget, nothing invented
    for o in mp.objectives:
        assert abs((o.test_budget or 0) - 30000.0 * o.budget_allocation_pct / 100.0) < 1.0


def _objective_with_cost_target(target: float, ad_sets: int = 2):
    from media_plan.schemas import (
        CampaignObjective, Confidence, Destination, FunnelStage, KPITarget, MetaObjective,
    )
    return CampaignObjective(
        objective=MetaObjective.LEADS, destination=Destination.WHATSAPP,
        funnel_stage=FunnelStage.BOFU, rationale="r", budget_allocation_pct=100.0,
        num_ad_sets=ad_sets, confidence=Confidence.HIGH,
        kpi_target=KPITarget(metric="cost_per_lead", target_value=target, unit="EGP"))


def test_a_budget_below_the_learning_floor_is_refused_not_crashed():
    from media_plan.builder import _apply_owner_budget
    # floor = 3 x 200 x 2 = 1200; her 600 cannot buy a readable test.
    [obj] = _apply_owner_budget([_objective_with_cost_target(200.0)], 600.0)
    assert obj.test_budget is None                                # not silently accepted
    assert "أرضية التعلّم" in obj.budget_note or "أرضية التعلم" in obj.budget_note
    assert "1,200" in obj.budget_note                             # the floor is SHOWN, not asserted
    # a budget that clears the same floor is taken as-is
    [ok] = _apply_owner_budget([_objective_with_cost_target(200.0)], 5000.0)
    assert ok.test_budget == 5000.0


def test_budget_without_a_cost_target_is_accepted_without_claiming_readability():
    # No target value => there is no learning floor to check. Honest behaviour is to use her
    # number and NOT pretend it was validated.
    from media_plan.builder import _apply_owner_budget
    [obj] = _apply_owner_budget([_objective_with_cost_target(None)], 5.0)
    assert obj.test_budget == 5.0 and "أرضية" not in obj.budget_note


def test_garbage_budget_is_ignored():
    for bad in (0, -100, None, "abc"):
        mp = _plan(monthly_budget=bad)
        assert mp is not None and all(o.test_budget is None for o in mp.objectives)
