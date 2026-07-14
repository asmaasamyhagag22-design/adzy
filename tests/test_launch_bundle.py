"""U6 Launch Bundle — the Ads-Manager-ready, human-gated deliverable (v2.2 §6-W8 gate). Hermetic;
drives real MediaPlan objects, no network."""
from __future__ import annotations

from pathlib import Path

from launch_bundle import LaunchBundle, assemble_launch_bundle
from launch_bundle.bundle import approval_token
from media_plan.schemas import (
    CampaignObjective, Destination, FunnelStage, GeoMode, GeoTargeting, KPITarget, MediaPlan,
    MetaObjective, Persona,
)


def _obj(objective, destination, funnel, pct, target=50.0):
    return CampaignObjective(objective=objective, destination=destination, funnel_stage=funnel,
                             rationale="grounded rationale", budget_allocation_pct=pct,
                             kpi_target=KPITarget(metric="cost_per_lead", target_value=target, unit="EGP"))


def _plan(objectives, geo=None):
    return MediaPlan(run_id="r1", market_definition_ref="m1", objectives=objectives,
                     base_persona=Persona(),
                     base_geo=geo or GeoTargeting(mode=GeoMode.RADIUS, center_address="Nasr City",
                                                  radius_km=10.0))


def _clean_creative():
    return {"headline": "احجز استشارتك", "primary_text": "خبرة 15 سنة في زراعة الشعر",
            "cta": "راسلنا واتساب", "asset_refs": ["poster.png"]}


def test_campaign_plan_mirrors_the_meta_hierarchy():
    b = assemble_launch_bundle(profile={"category": "clinic"},
                               media_plan=_plan([_obj(MetaObjective.LEADS, Destination.WHATSAPP, FunnelStage.BOFU, 100.0)]),
                               creative=_clean_creative(), created_at="t")
    c = b.campaign_plan.campaigns[0]
    assert c.objective == "OUTCOME_LEADS" and c.status == "PAUSED"          # exact Meta string, never live
    aset = c.ad_sets[0]
    assert aset.destination == "whatsapp" and aset.placement == "advantage_plus"
    assert aset.geo.mode == "radius" and aset.geo.radius_km == 10.0
    assert aset.ads[0].headline == "احجز استشارتك" and aset.ads[0].asset_refs == ["poster.png"]


def test_funnel_plan_yields_one_campaign_per_objective():
    b = assemble_launch_bundle(profile={"category": "ecommerce"}, created_at="t",
                               media_plan=_plan([
                                   _obj(MetaObjective.AWARENESS, Destination.WEBSITE, FunnelStage.TOFU, 40.0),
                                   _obj(MetaObjective.SALES, Destination.ONLINE_STORE, FunnelStage.BOFU, 60.0)]))
    objs = [c.objective for c in b.campaign_plan.campaigns]
    assert objs == ["OUTCOME_AWARENESS", "OUTCOME_SALES"]


def test_policy_lint_catches_a_violating_creative_in_the_bundle():
    b = assemble_launch_bundle(profile={"category": "clinic"}, created_at="t",
                               media_plan=_plan([_obj(MetaObjective.LEADS, Destination.WHATSAPP, FunnelStage.BOFU, 100.0)]),
                               creative={"headline": "علاج نهائي للصلع", "primary_text": "نتيجة مضمونة 100%", "cta": "احجز"})
    assert b.policy_lint.verdict == "VIOLATIONS"
    assert {f.category for f in b.policy_lint.findings} >= {"guaranteed_results", "medical_claims"}


def test_policy_lint_also_reads_the_compliance_sheet_shipped_copy():
    sheet = {"shipped_copy": [{"copy_field": "headline", "copy_text": "نتيجة مضمونة"}]}
    b = assemble_launch_bundle(profile={"category": "beauty"}, created_at="t", compliance_sheet=sheet,
                               media_plan=_plan([_obj(MetaObjective.TRAFFIC, Destination.WEBSITE, FunnelStage.TOFU, 100.0)]))
    assert b.policy_lint.verdict == "VIOLATIONS" and b.compliance == sheet


def test_approval_is_draft_and_token_is_content_bound():
    b = assemble_launch_bundle(profile={"category": "clinic"}, created_at="t", creative=_clean_creative(),
                               media_plan=_plan([_obj(MetaObjective.LEADS, Destination.WHATSAPP, FunnelStage.BOFU, 100.0)]))
    assert b.approval.status == "DRAFT" and b.approval.approved_by is None and b.status == "DRAFT"
    assert b.approval.approval_token == approval_token(b.campaign_plan)
    b.campaign_plan.campaigns[0].name = "MUTATED"
    assert approval_token(b.campaign_plan) != b.approval.approval_token   # approval can't carry over


def test_prereqs_derive_from_destinations_and_creative():
    # whatsapp + no creative -> whatsapp_business_verified + creative_copy pending
    b = assemble_launch_bundle(profile={"category": "clinic"}, created_at="t",
                               media_plan=_plan([_obj(MetaObjective.LEADS, Destination.WHATSAPP, FunnelStage.BOFU, 100.0)]))
    items = {p.item for p in b.prerequisites.items}
    assert {"page_access", "whatsapp_business_verified", "creative_copy"} <= items
    # sales/website -> pixel required
    b2 = assemble_launch_bundle(profile={"category": "ecommerce"}, created_at="t", creative=_clean_creative(),
                                media_plan=_plan([_obj(MetaObjective.SALES, Destination.ONLINE_STORE, FunnelStage.BOFU, 100.0)]))
    assert "pixel_installed" in {p.item for p in b2.prerequisites.items}


def test_writes_all_bundle_files_to_its_own_subdir(tmp_path):
    b = assemble_launch_bundle(profile={"category": "clinic"}, created_at="t", creative=_clean_creative(),
                               out_dir=tmp_path / "bundle",
                               media_plan=_plan([_obj(MetaObjective.LEADS, Destination.WHATSAPP, FunnelStage.BOFU, 100.0)]))
    names = {p.name for p in (tmp_path / "bundle").iterdir()}
    assert names == {"campaign_plan.json", "compliance.json", "approval.json", "ads_manager.csv", "launch_bundle.json"}
    assert Path(b.ads_export_path).name == "ads_manager.csv"
    # the CSV has the Ads-Manager header + a data row
    csv_text = (tmp_path / "bundle" / "ads_manager.csv").read_text(encoding="utf-8")
    assert "Campaign Objective" in csv_text and "OUTCOME_LEADS" in csv_text


def test_bundle_is_strict_and_serializable():
    b = assemble_launch_bundle(profile={"category": "clinic"}, created_at="t", creative=_clean_creative(),
                               media_plan=_plan([_obj(MetaObjective.LEADS, Destination.WHATSAPP, FunnelStage.BOFU, 100.0)]))
    assert LaunchBundle.model_validate(b.model_dump()).run_id == b.run_id
