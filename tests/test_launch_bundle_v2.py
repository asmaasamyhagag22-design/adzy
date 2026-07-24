# -*- coding: utf-8 -*-
"""2.1 (LB-MPV2-GAP): the War Room's Export button pointed at a bundle that silently DROPPED
every media_plan_v2 layer (test cells, kill-switch, counter-strategy). Pins: the v2 layers travel
with the bundle (validated, never trusted blind), test_cells.csv ships beside the Ads-Manager CSV
(whose import columns stay untouched), and a v2-less doc bundles exactly as before."""
from __future__ import annotations

import json
from pathlib import Path

from launch_bundle.bundle import assemble_launch_bundle
from media_plan_v2 import build_media_plan_v2

_PROFILE = {"source_url": "https://clinic.eg/", "name": {"value": "Baseera Clinic"},
            "category": {"value": "clinic"},
            "offerings": [{"name": {"value": "Laser"}, "price_text": {"value": "800 EGP"}}]}


def _mp():
    from media_plan.schemas import (CampaignObjective, Destination, FunnelStage, GeoMode,
                                    GeoTargeting, KPITarget, MediaPlan, MetaObjective, Persona)
    obj = CampaignObjective(objective=MetaObjective.LEADS, destination=Destination.WHATSAPP,
                            funnel_stage=FunnelStage.BOFU, rationale="grounded rationale",
                            budget_allocation_pct=100.0,
                            kpi_target=KPITarget(metric="cost_per_lead", target_value=50.0,
                                                 unit="EGP"))
    return MediaPlan(run_id="r1", market_definition_ref="m1", objectives=[obj],
                     base_persona=Persona(),
                     base_geo=GeoTargeting(mode=GeoMode.RADIUS, center_address="Nasr City",
                                           radius_km=10.0))


def test_v2_layers_travel_with_the_bundle(tmp_path):
    v2 = build_media_plan_v2(_PROFILE, swot={"strengths": [{"text": "أطباء معتمدون"}]})
    b = assemble_launch_bundle(profile=_PROFILE, media_plan=_mp(),
                               media_plan_v2=v2.model_dump(mode="json"), out_dir=tmp_path)
    assert b.media_plan_v2 and len(b.media_plan_v2["test_cells"]) == 3
    doc = json.loads((tmp_path / "launch_bundle.json").read_text(encoding="utf-8"))
    assert doc["media_plan_v2"]["reality_check"]["kill_switch"]["max_cpl"]  # the vault number ships
    cells_csv = (tmp_path / "test_cells.csv").read_text(encoding="utf-8-sig")
    assert "audience" in cells_csv and "competitor" in cells_csv
    # the Ads-Manager import CSV keeps its own columns — never mixed with cell rows
    ads_csv = Path(tmp_path / "ads_manager.csv").read_text(encoding="utf-8-sig")
    assert "hypothesis" not in ads_csv


def test_v2_less_doc_bundles_exactly_as_before(tmp_path):
    b = assemble_launch_bundle(profile=_PROFILE, media_plan=_mp(), out_dir=tmp_path)
    assert b.media_plan_v2 is None
    assert not (tmp_path / "test_cells.csv").exists()


def test_calendar_audit_shape_yields_a_compliance_sheet():
    # CS-121: the calendar audit (items[].shipped_copy.fields) used to yield None ALWAYS —
    # the shape mismatch starved every calendar of its (claim -> source) trail.
    from grounding.compliance import build_compliance_sheet
    audit = {"business_name": "B", "items": [{
        "date": "2026-07-01", "platform": "instagram",
        "shipped_copy": {"passes": True, "fields": [{
            "field": "topic", "text": "جلسة ليزر بأجهزة معتمدة", "grounded": True,
            "claims": [{"kind": "certification", "token": "معتمدة", "sourced": True,
                        "source_url": "https://b.example/about",
                        "matched_quote": "أجهزتنا معتمدة"}]}]}}]}
    sheet = build_compliance_sheet(audit)
    assert sheet is not None and sheet.get("verdict")
    assert sheet.get("shipped_copy")                          # the per-claim trail is populated
    assert build_compliance_sheet({"items": []}) is None      # nothing checked -> honest None


def test_d6_human_approval_gate(tmp_path):
    # D-6 (owner ruling): DRAFT -> APPROVED only via an explicit named human sign-off, and the
    # sign-off is CONTENT-BOUND — a tampered plan makes approval refuse.
    import json

    import pytest

    from launch_bundle.approve import approve_bundle
    assemble_launch_bundle(profile=_PROFILE, media_plan=_mp(), out_dir=tmp_path)
    with pytest.raises(SystemExit):
        approve_bundle(tmp_path, approved_by="")                 # anonymous -> refused
    a = approve_bundle(tmp_path, approved_by="أسماء")
    assert a["status"] == "APPROVED" and a["approved_by"] == "أسماء"
    doc = json.loads((tmp_path / "launch_bundle.json").read_text(encoding="utf-8"))
    assert doc["status"] == "APPROVED"
    # tamper AFTER assembly -> the token no longer matches -> refuse (never half-approve)
    cp = tmp_path / "campaign_plan.json"
    plan = json.loads(cp.read_text(encoding="utf-8"))
    plan["campaigns"][0]["objective"] = "OUTCOME_SALES"
    cp.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(SystemExit, match="REFUSED"):
        approve_bundle(tmp_path, approved_by="أسماء")


def test_malformed_v2_is_dropped_not_trusted(tmp_path):
    b = assemble_launch_bundle(profile=_PROFILE, media_plan=_mp(),
                               media_plan_v2={"garbage": True}, out_dir=tmp_path)
    assert b.media_plan_v2 is None                      # fail-closed on a client deliverable
