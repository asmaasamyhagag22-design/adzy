"""U6 Launch Bundle CLI (`python -m launch_bundle`) — assembles a DRAFT bundle from the
media_plan.json a run writes. Hermetic: drives a real MediaPlan through the real assembler; the
conftest network guard proves it does no I/O beyond the given files."""
from __future__ import annotations

import json

from launch_bundle.__main__ import main
from media_plan.schemas import (
    CampaignObjective, Destination, FunnelStage, GeoMode, GeoTargeting, KPITarget, MediaPlan,
    MetaObjective, Persona,
)

_FILES = ["launch_bundle.json", "campaign_plan.json", "approval.json", "ads_manager.csv",
          "compliance.json"]


def _plan() -> MediaPlan:
    obj = CampaignObjective(
        objective=MetaObjective.LEADS, destination=Destination.WHATSAPP, funnel_stage=FunnelStage.BOFU,
        rationale="grounded rationale", budget_allocation_pct=100.0,
        kpi_target=KPITarget(metric="cost_per_lead", target_value=50.0, unit="EGP"))
    return MediaPlan(run_id="r1", market_definition_ref="m1", objectives=[obj], base_persona=Persona(),
                     base_geo=GeoTargeting(mode=GeoMode.RADIUS, center_address="Nasr City", radius_km=10.0))


def test_cli_from_media_plan_json_writes_a_draft_bundle(tmp_path, capsys):
    mp = tmp_path / "media_plan.json"
    mp.write_text(_plan().model_dump_json(indent=2), encoding="utf-8")
    out = tmp_path / "bundle"
    assert main([str(mp), "--out", str(out)]) == 0
    for f in _FILES:
        assert (out / f).exists(), f
    doc = json.loads((out / "launch_bundle.json").read_text(encoding="utf-8"))
    assert doc["status"] == "DRAFT"                                   # never APPROVED by the tool
    assert doc["campaign_plan"]["campaigns"][0]["status"] == "PAUSED"  # never live (PD-7)
    assert "DRAFT" in capsys.readouterr().out


def test_cli_from_run_dir_finds_media_plan(tmp_path):
    (tmp_path / "media_plan.json").write_text(_plan().model_dump_json(), encoding="utf-8")
    # a sibling result JSON supplies the business name + competitor count (best-effort enrichment)
    (tmp_path / "result.json").write_text(json.dumps(
        {"profile": {"name": {"value": "Acme Clinic"}, "category": "clinic"},
         "competitors": [{"x": 1}, {"x": 2}]}), encoding="utf-8")
    out = tmp_path / "b2"
    assert main([str(tmp_path), "--out", str(out)]) == 0
    assert (out / "launch_bundle.json").exists()


def test_cli_from_result_json_uses_embedded_media_plan(tmp_path):
    rj = tmp_path / "result.json"
    rj.write_text(json.dumps({"profile": {"category": "clinic"},
                              "media_plan": _plan().model_dump(mode="json"),
                              "competitors": []}), encoding="utf-8")
    out = tmp_path / "b3"
    assert main([str(rj), "--out", str(out)]) == 0
    assert (out / "campaign_plan.json").exists()


def test_cli_default_out_dir_is_slugged_from_business_name(tmp_path, monkeypatch):
    (tmp_path / "media_plan.json").write_text(_plan().model_dump_json(), encoding="utf-8")
    (tmp_path / "result.json").write_text(json.dumps(
        {"profile": {"name": {"value": "Acme Clinic"}, "category": "clinic"}}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main([str(tmp_path)]) == 0
    assert (tmp_path / "outputs" / "launch_bundle" / "acme-clinic" / "launch_bundle.json").exists()
