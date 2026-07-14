"""The closed loop (v2.2 §6-W8): performance -> decision -> variation -> re-gate -> DRAFT bundle
delta. Hermetic: the variation is injected; no network, no spend. Governance closes the loop but
nothing publishes (PD-7)."""
from __future__ import annotations

from decision_engine import CampaignPolicy, DecisionKind, simulate
from launch_bundle.loop import run_closed_loop
from media_plan.schemas import (
    CampaignObjective, Destination, FunnelStage, GeoMode, GeoTargeting, KPITarget, MediaPlan,
    MetaObjective, Persona,
)


def _policy():
    return CampaignPolicy(kpi=KPITarget(metric="cost_per_lead", target_value=50.0, unit="EGP"),
                          test_budget=1000.0)


def _plan():
    obj = CampaignObjective(objective=MetaObjective.LEADS, destination=Destination.WHATSAPP,
                            funnel_stage=FunnelStage.BOFU, rationale="r", budget_allocation_pct=100.0,
                            kpi_target=KPITarget(metric="cost_per_lead", target_value=50.0, unit="EGP"))
    return MediaPlan(run_id="r1", market_definition_ref="m1", objectives=[obj],
                     base_persona=Persona(), base_geo=GeoTargeting(mode=GeoMode.NATIONAL))


_PROFILE = {"category": "clinic"}


def test_fatigue_rotates_and_a_clean_variant_is_bundled_as_draft():
    res = run_closed_loop(
        policy=_policy(), snapshots=simulate("fatigue", days=14, seed=7), profile=_PROFILE,
        media_plan=_plan(), current_creative={"headline": "قديم"},
        variation_fn=lambda old: {"headline": "جرّب خدمتنا الجديدة", "cta": "راسلنا"}, created_at="t")
    assert res.decision.kind is DecisionKind.ROTATE_CREATIVE
    assert res.action == "variant_bundled"
    assert res.new_bundle is not None and res.new_bundle.status == "DRAFT"     # never auto-published
    assert res.delta and res.delta["decision"] == "rotate_creative"


def test_a_violating_variant_is_rejected_by_the_re_gate():
    res = run_closed_loop(
        policy=_policy(), snapshots=simulate("fatigue", days=14, seed=7), profile=_PROFILE,
        media_plan=_plan(), variation_fn=lambda old: {"headline": "نتيجة مضمونة 100%", "cta": "احجز"},
        created_at="t")
    assert res.action == "variant_rejected" and res.new_bundle is None
    assert res.policy_lint.verdict == "VIOLATIONS"
    assert any("policy" in r or "unsourced" in r for r in res.reasons)        # both gates can veto


def test_a_healthy_campaign_needs_no_new_creative():
    res = run_closed_loop(
        policy=_policy(), snapshots=simulate("winner", days=14, seed=7), profile=_PROFILE,
        media_plan=_plan(), variation_fn=lambda old: {}, created_at="t")
    assert res.decision.kind is DecisionKind.SCALE and res.action == "no_change"
    assert res.new_bundle is None


def test_kill_and_replace_also_triggers_a_variant():
    res = run_closed_loop(
        policy=_policy(), snapshots=simulate("loser", days=14, seed=7), profile=_PROFILE,
        media_plan=_plan(), variation_fn=lambda old: {"headline": "عرض جديد", "cta": "اطلب"},
        created_at="t")
    assert res.decision.kind is DecisionKind.KILL_AND_REPLACE and res.action == "variant_bundled"
