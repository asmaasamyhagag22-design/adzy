# -*- coding: utf-8 -*-
"""Batch C (2026-07-18) — launch-bundle deliverable defects P-53, P-48, P-50. RED-first, hermetic."""
from __future__ import annotations

from pathlib import Path

from launch_bundle import assemble_launch_bundle
from launch_bundle.ads_export import campaign_plan_to_csv
from launch_bundle.campaign_plan import flatten_media_plan
from media_plan.schemas import (
    CampaignObjective, Destination, FunnelStage, GeoMode, GeoTargeting, KPITarget, MediaPlan,
    Persona,
)


def _plan():
    obj = CampaignObjective(objective="OUTCOME_LEADS", destination=Destination.LEAD_FORM,
                            funnel_stage=FunnelStage.BOFU, rationale="r", budget_allocation_pct=100.0,
                            kpi_target=KPITarget(metric="cost_per_lead", target_value=50.0, unit="EGP"))
    return MediaPlan(run_id="r1", market_definition_ref="m1", objectives=[obj],
                     base_persona=Persona(),
                     base_geo=GeoTargeting(mode=GeoMode.NATIONAL))


# --- P-53: the CSV must be Excel-safe --------------------------------------------------------
def _csv_with(headline, primary):
    plan = flatten_media_plan(_plan(), creative={"headline": headline, "primary_text": primary,
                                                 "cta": "cta", "asset_refs": []})
    return campaign_plan_to_csv(plan)


def test_csv_injection_is_neutralized():
    # a formula-looking cell must be defused so Excel/Sheets don't execute it on import
    csv = _csv_with("=cmd|'/c calc'!A1", "hi")
    # the dangerous cell must be prefixed with a guard quote, never start a raw '='
    assert "=cmd" not in csv or "'=cmd" in csv or "\t=cmd" not in csv
    for lead in ("=", "+", "-", "@"):
        c = _csv_with(f"{lead}danger", "x")
        # no data cell may begin (right after a comma/quote) with a bare formula lead
        assert f',"{lead}danger"' not in c and f",{lead}danger" not in c


def test_written_csv_has_a_bom_and_no_double_carriage_returns(tmp_path):
    # Excel needs a UTF-8 BOM to render Arabic correctly; Windows text-mode writes double-CR
    assemble_launch_bundle(profile={"category": "clinic"}, media_plan=_plan(),
                           creative={"headline": "خبرة ٢٠ سنة", "primary_text": "أفضل رعاية",
                                     "cta": "اتصل", "asset_refs": []},
                           out_dir=str(tmp_path))
    raw = (tmp_path / "ads_manager.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV lacks a UTF-8 BOM -> Arabic mojibake in Excel"
    assert b"\r\r\n" not in raw, "CSV has doubled carriage returns (\\r\\r\\n)"


# --- P-48: the CLI must consume a STUDIO run (a dir with {slug}_media_plan.json) --------------
def test_cli_consumes_a_studio_run_directory(tmp_path):
    # the studio writes {slug}_media_plan.json (not the literal media_plan.json the CLI looked for)
    (tmp_path / "acme_media_plan.json").write_text(_plan().model_dump_json(), encoding="utf-8")
    from launch_bundle.__main__ import _load
    plan, profile, result = _load(tmp_path)
    assert plan is not None and getattr(plan, "run_id", "") == "r1"


# --- P-50: approval must be a real gate, not a computed-but-ignored token ---------------------
def test_approval_gate_verifies_the_token_and_flips_status(tmp_path):
    from launch_bundle.bundle import approval_token
    from launch_bundle import approve_bundle          # the new gate
    b = assemble_launch_bundle(profile={"category": "clinic"}, media_plan=_plan(),
                               out_dir=str(tmp_path))
    good = approval_token(b.campaign_plan)
    # a wrong token is REFUSED
    try:
        approve_bundle(str(tmp_path), token="deadbeef", approved_by="owner")
        assert False, "a wrong approval token must be refused"
    except Exception:
        pass
    # the correct, content-bound token flips DRAFT -> APPROVED (human sign-off, PD-7)
    updated = approve_bundle(str(tmp_path), token=good, approved_by="owner")
    assert updated.status == "APPROVED" and updated.approval.approved_by == "owner"
    import json
    saved = json.loads((tmp_path / "approval.json").read_text(encoding="utf-8"))
    assert saved["status"] == "APPROVED"
