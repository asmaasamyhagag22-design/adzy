"""U5 Missing-Info & Confidence Advisor — concrete gaps/risks/per-axis grounding, NO composite %
(v2.2 §6-W7 gate). Hermetic; drives the real MediaPlan/Persona objects."""
from __future__ import annotations

from advisor import MissingInfoReport, build_missing_info_report
from business_profile.schemas import EvidenceItem
from media_plan.schemas import (
    CampaignObjective, Destination, EvidenceRef, FunnelStage, GeoMode, GeoTargeting, KPITarget,
    MediaPlan, MetaObjective, Persona,
)


def _ref(grounded: bool) -> EvidenceRef:
    if grounded:
        return EvidenceRef(claim="25-40 professionals", resolved=True,
                           evidence=[EvidenceItem(page_url="https://x/about", quote="for professionals 25-40",
                                                  extractor="llm")])
    return EvidenceRef(claim="25-40", evidence=[], resolved=False)


def _plan(*, kpi_target_value=None, age_grounded=None) -> MediaPlan:
    persona = Persona(age_range=_ref(age_grounded)) if age_grounded is not None else Persona()
    obj = CampaignObjective(
        objective=MetaObjective.LEADS, destination=Destination.WHATSAPP, funnel_stage=FunnelStage.BOFU,
        rationale="local clinic drives WhatsApp leads", budget_allocation_pct=100.0,
        kpi_target=KPITarget(metric="cost_per_lead", target_value=kpi_target_value, unit="EGP"))
    return MediaPlan(run_id="r1", market_definition_ref="m1", objectives=[obj],
                     base_persona=persona, base_geo=GeoTargeting(mode=GeoMode.NATIONAL))


def test_weak_plan_surfaces_every_gap_class():
    r = build_missing_info_report(
        media_plan=_plan(kpi_target_value=None, age_grounded=False), swot_mode="standalone",
        competitor_count=0, n_reviews=0, ads_available=False,
        profile={"offerings": [], "trust_signals": [], "value_propositions": []})
    fields = {g.field for g in r.gaps}
    assert fields == {"competitors", "customer_reviews", "competitor_ads", "kpi_target", "audience_demographics"}
    assert r.ungrounded_axes == ["age_range"] and r.grounded_axes == []
    assert r.risks and len(r.missing_elements) == 3


def test_no_composite_confidence_number():
    r = build_missing_info_report(media_plan=_plan())
    assert "confidence_pct" not in r.model_dump() and "confidence" not in r.model_dump()  # §10


def test_healthy_plan_has_no_gaps():
    r = build_missing_info_report(
        media_plan=_plan(kpi_target_value=45.0, age_grounded=True), swot_mode="competitive",
        competitor_count=5, n_reviews=12, ads_available=True,
        profile={"offerings": [{"price_text": "1000 EGP"}], "trust_signals": ["accredited"],
                 "value_propositions": ["free consult"]})
    assert r.gaps == [] and r.missing_elements == []
    assert r.grounded_axes == ["age_range"] and r.ungrounded_axes == []


def test_empty_competitor_set_flagged_even_when_mode_competitive():
    r = build_missing_info_report(media_plan=_plan(), swot_mode="competitive",
                                  competitor_count=0, n_reviews=5, ads_available=True)
    assert any(g.field == "competitors" for g in r.gaps)


def test_kpi_target_set_removes_the_kpi_gap():
    r = build_missing_info_report(media_plan=_plan(kpi_target_value=50.0), swot_mode="competitive",
                                  competitor_count=3, n_reviews=5, ads_available=True)
    assert not any(g.field == "kpi_target" for g in r.gaps)


def test_report_is_strict_and_serializable():
    r = build_missing_info_report(media_plan=_plan())
    assert MissingInfoReport.model_validate(r.model_dump()).gaps == r.gaps


def test_no_media_plan_degrades_to_market_gaps_only():
    r = build_missing_info_report(media_plan=None, swot_mode="standalone", competitor_count=0)
    assert {g.field for g in r.gaps} >= {"competitors", "customer_reviews", "competitor_ads"}
    assert r.grounded_axes == [] and r.ungrounded_axes == []
