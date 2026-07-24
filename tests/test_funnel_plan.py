# -*- coding: utf-8 -*-
"""خطة الـFunnel (owner ruling 2026-07-20 — automatic-by-evidence): a NASCENT brand gets TWO
objectives (TOFU Awareness with the maturity disclosure as rationale + the deduced conversion
objective) split per config; established/unknown keeps today's single objective BYTE-IDENTICAL;
v2 grows a 4th awareness cell summing to 1.0; the war room shows the whole funnel. Hermetic."""
from __future__ import annotations

from media_plan.builder import build_media_plan
from media_plan.config import funnel_split_for
from media_plan.maturity import MaturityVerdict
from media_plan_v2 import build_media_plan_v2

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
    """Deterministic fake deduction caller: always LEADS/whatsapp/bofu."""

    def __call__(self, system, user, schema, group_name=""):
        from media_plan.schemas import Confidence, Destination, FunnelStage, MetaObjective
        return schema(objective=MetaObjective.LEADS, destination=Destination.WHATSAPP,
                      funnel_stage=FunnelStage.BOFU, rationale="whatsapp evidenced",
                      confidence=Confidence.HIGH, alternatives=[]), None


def test_nascent_gets_a_two_objective_funnel_plan():
    plan = build_media_plan(_PROFILE, caller=_Caller(), maturity=_NASCENT)
    assert plan is not None and len(plan.objectives) == 2
    top, bottom = plan.objectives
    assert top.objective.value == "OUTCOME_AWARENESS" and top.funnel_stage.value == "tofu"
    assert "براند ناشئ" in top.rationale                        # أب القرار معلن
    assert top.kpi_target and top.kpi_target.target_value is None   # no invented CPM target
    assert bottom.objective.value == "OUTCOME_LEADS"            # the deduced objective survives
    assert abs(top.budget_allocation_pct + bottom.budget_allocation_pct - 100.0) < 0.01
    split = funnel_split_for("nascent")
    assert abs(top.budget_allocation_pct - split["tofu"] * 100) < 0.01


def test_established_and_unknown_stay_single_objective():
    for level in ("established", "unknown"):
        v = MaturityVerdict(level=level, signals=[], disclosure="x")
        plan = build_media_plan(_PROFILE, caller=_Caller(), maturity=v)
        assert plan is not None and len(plan.objectives) == 1
        assert plan.objectives[0].budget_allocation_pct == 100.0
    baseline = build_media_plan(_PROFILE, caller=_Caller())        # no maturity at all
    assert len(baseline.objectives) == 1


def test_v2_nascent_grows_awareness_cell_summing_to_one():
    v2 = build_media_plan_v2(_PROFILE, swot={"strengths": [{"text": "س"}]}, maturity=_NASCENT)
    assert len(v2.test_cells) == 4
    assert v2.test_cells[0].axis == "awareness"
    assert "brand maturity signals" in v2.test_cells[0].grounded_in
    assert abs(sum(c.budget_share for c in v2.test_cells) - 1.0) < 0.001
    v2b = build_media_plan_v2(_PROFILE, swot={"strengths": [{"text": "س"}]})
    assert len(v2b.test_cells) == 3                              # today's shape untouched


def test_war_room_shows_the_whole_funnel(tmp_path):
    from tests.test_war_room import _build, _result
    doc = _result()
    doc["media_plan"]["objectives"] = [
        {"objective": "OUTCOME_AWARENESS", "destination": "website", "funnel_stage": "tofu",
         "budget_allocation_pct": 30.0},
        {"objective": "OUTCOME_LEADS", "destination": "lead_form", "funnel_stage": "bofu",
         "budget_allocation_pct": 70.0}]
    doc["brand_maturity"] = _NASCENT.model_dump(mode="json")
    h = _build(tmp_path, result=doc)
    assert "Funnel:" in h and "30%" in h and "70%" in h          # both stages, both shares
    assert "نضج البراند: ناشئ" in h                              # the maturity chip
    h2 = _build(tmp_path)                                        # single-objective plan
    assert "نضج البراند" not in h2
