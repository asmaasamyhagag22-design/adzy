from __future__ import annotations

import json
import subprocess
from pathlib import Path

from strategy.positioning.benchmark import run_offline_benchmark
from strategy.positioning.objective import objective_from_mapping
from strategy.positioning.projection import project_approved_strategy
from strategy.positioning.service import build_positioning_strategy

from test_gate2b_positioning_contracts import _objective, _strong_brand


ROOT = Path(__file__).resolve().parents[1]


def test_approved_run_projects_without_inventing_legacy_strategy_fields():
    build = build_positioning_strategy(
        _strong_brand(), objective=objective_from_mapping(_objective()),
        requested_scope={"geography": ["Qatar"]})
    output = dict(build.output)
    output["_requested_objective"] = build.requested_config["objective"]
    projected = project_approved_strategy(
        output, run_id="f17cc2f4-8adc-4b35-abbb-79a758633900", approval_state="approved")
    assert projected["strategy_version_id"] == "f17cc2f4-8adc-4b35-abbb-79a758633900"
    assert projected["approval_state"] == "approved"
    assert projected["positioning_statement"]
    assert projected["strategic_posture"] is None
    assert projected["posture_rationale"] is None
    assert projected["tone_of_voice"] is None
    assert projected["content_pillars"] == projected["channels"] == []
    assert project_approved_strategy(output, run_id="x", approval_state="pending") is None


def test_frontend_strategy_state_uses_exact_approved_version_not_fake_posture():
    script = ROOT / "frontend" / "assets" / "strategy-state.js"
    javascript = """
      const state = require(process.argv[1]);
      const out = {
        canonical: state.exists({strategy_version_id:'run-1', approval_state:'approved', strategic_posture:null}),
        pending: state.exists({strategy_version_id:'run-1', approval_state:'pending', strategic_posture:'balanced'}),
        empty: state.exists({strategic_posture:null}),
        legacy: state.exists({strategic_posture:'balanced'})
      };
      process.stdout.write(JSON.stringify(out));
    """
    result = subprocess.run(["node", "-e", javascript, str(script)], check=True,
                            capture_output=True, text=True)
    assert json.loads(result.stdout) == {
        "canonical": True, "pending": False, "empty": False, "legacy": True}


def test_offline_benchmark_runs_all_frozen_cases_without_provider_or_quality_claims():
    result = run_offline_benchmark()
    assert result["mode"] == "STRUCTURAL_OFFLINE"
    assert result["provider_backed"] is False
    assert result["quality_preference_measured"] is False
    assert len(result["cases"]) == 7
    assert all(row["A"]["provider_calls"] == row["B"]["provider_calls"] == 0
               for row in result["cases"])
    assert all(row["A"]["cost_usd"] == row["B"]["cost_usd"] == 0
               for row in result["cases"])
    assert all(row["B"]["alternatives"] >= 2 for row in result["cases"])
    wrong_scope = next(row for row in result["cases"] if row["case"] == "volume_wrong_scope")
    assert "SCOPE_MISMATCH" in wrong_scope["B"]["defects"]
    conflict = next(row for row in result["cases"] if row["case"] == "buffalo_conflicting_counts")
    assert conflict["B"]["contradictions_exposed"] > 0
    nano = next(row for row in result["cases"] if row["case"] == "nanocare_self_claim")
    assert nano["B"]["verification_status"] == "NEEDS_REPAIR"
    assert "WEAK_REASON_TO_BELIEVE" in nano["B"]["defects"]
    assert result["objective_sensitivity"]["same_brand_cases_compared"] is True
    assert result["objective_sensitivity"]["selected_positioning_changed"] is True
