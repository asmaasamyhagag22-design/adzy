"""Offline A/B structure for the frozen Gate 2 positioning fixtures."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from api.brand_translate import profile_to_brand

from .contracts import BusinessObjective, ObjectiveType
from .service import build_positioning_strategy


FIXTURES = (
    ("buffalo_stronger", "buffaloburger_com_result.json", ObjectiveType.AWARENESS_GROWTH, ["Egypt"]),
    ("buffalo_conflicting_counts", "buffaloburger_com_result.json", ObjectiveType.AWARENESS_GROWTH, ["Egypt"]),
    ("marasim_thin", "marasimltd_com_result.json", ObjectiveType.QUALIFIED_LEADS_GROWTH, []),
    ("volume_objective_awareness", "volumeqatar_com_result.json", ObjectiveType.AWARENESS_GROWTH, ["Qatar"]),
    ("volume_objective_trial", "volumeqatar_com_result.json", ObjectiveType.TRIAL_GROWTH, ["Qatar"]),
    ("volume_wrong_scope", "volumeqatar_com_result.json", ObjectiveType.NEW_MARKET_ENTRY, ["Egypt"]),
    ("nanocare_self_claim", "nanocareegypt_com_result.json", ObjectiveType.REVENUE_GROWTH, ["Egypt"]),
)


def _brand_from_result(result: Mapping[str, Any], filename: str) -> dict:
    profile = result.get("profile") if isinstance(result.get("profile"), Mapping) else {}
    domain = filename.removesuffix("_result.json")
    brand = profile_to_brand(dict(profile), source_url=str(result.get("subject_url") or ""),
                             domain_key=domain)
    brand.update({"_profile": dict(profile), "_full_result": dict(result),
                  "_swot_raw": dict(result.get("swot") or {}), "domain_key": domain})
    return brand


def run_offline_benchmark(root: str | Path | None = None) -> dict:
    """Run structural A/B with provider calls forcibly disabled; reports no quality fiction."""
    base = Path(root) if root else Path(__file__).resolve().parents[2] / "outputs"
    rows = []
    for name, filename, objective_type, geography in FIXTURES:
        result = json.loads((base / filename).read_text(encoding="utf-8"))
        if name == "buffalo_stronger":
            # Fixed stronger-evidence variant: remove the one conflicting “45 branches” assertion;
            # the conflict case below retains the untouched snapshot and must expose it.
            for field_name in ("value_propositions", "trust_signals", "other_unique_insights"):
                result.get("profile", {})[field_name] = [
                    row for row in (result.get("profile", {}).get(field_name) or [])
                    if "45 branches" not in str(row.get("value") if isinstance(row, dict) else row)]
        brand = _brand_from_result(result, filename)
        objective = BusinessObjective(
            objective_id=f"obj_{name}", origin="BENCHMARK_EXPLICIT",
            business_type=objective_type,
            description=f"Benchmark objective: {objective_type.value}",
            geography=tuple(geography))

        # A: execute the existing function with its provider factory disabled. This exercises the
        # real current serializer/TOWS fallback while guaranteeing zero provider calls and cost.
        from api import webapp_analysis
        original = webapp_analysis._caller
        started = time.perf_counter()
        try:
            webapp_analysis._caller = lambda strong: None
            legacy = webapp_analysis.generate_strategy(brand)
        finally:
            webapp_analysis._caller = original
        a_ms = round((time.perf_counter() - started) * 1000, 3)

        started = time.perf_counter()
        build = build_positioning_strategy(
            brand, objective=objective,
            requested_scope={"geography": geography, "market": ""})
        b_ms = round((time.perf_counter() - started) * 1000, 3)
        canonical = build.output["canonical_strategy"]
        decision = canonical["decision"]
        verification = canonical["verification"]
        assessment = canonical["research_sufficiency_assessment"]
        evidence = canonical["evidence"]
        claims = canonical["claims"]
        evidence_ids = {e["evidence_id"] for e in evidence}
        complete = sum(bool(c.get("supporting_evidence_ids")) and
                       all(e in evidence_ids for e in c.get("supporting_evidence_ids", []))
                       for c in claims if c.get("epistemic_type") != "HYPOTHESIS")
        factual = sum(c.get("epistemic_type") != "HYPOTHESIS" for c in claims)
        inspected_option_id = (
            decision.get("selected_option_id")
            or decision.get("provisional_option_id")
        )
        selected = next((o for o in decision.get("options") or []
                         if o.get("option_id") == inspected_option_id), {})
        requirement_results = assessment.get("requirement_results") or []
        legacy_strategy = legacy.get("marketing_strategy") or {}
        rows.append({
            "case": name,
            "A": {
                "alternatives": 1 if legacy.get("marketing_strategy") else 0,
                "selected_positioning": legacy_strategy.get("positioning_statement") or "",
                "lineage_exposed": False,
                "unsupported_premises": "NOT_ASSESSABLE_WITHOUT_LINEAGE",
                "unknowns_exposed": len((legacy.get("marketing_strategy") or {}).get("notes") or []),
                "human_marketer_preference": "NOT_COLLECTED",
                "latency_ms": a_ms, "provider_calls": 0, "cost_usd": 0,
            },
            "B": {
                "alternatives": len(decision.get("options") or []),
                "selected_positioning": selected.get("positioning_statement") or "",
                "lineage_complete_claims": complete, "factual_claims": factual,
                "lineage_completeness": "COMPLETE" if complete == factual else "INCOMPLETE",
                "unknowns_exposed": len(decision.get("unresolved_unknowns") or []),
                "contradictions_exposed": len(decision.get("contradictions") or []),
                "unsupported_premises": sum(
                    d.get("code") == "UNSUPPORTED_PREMISE"
                    for d in verification.get("defects") or []),
                "customer_evidence": sum(e.get("source_class") == "CUSTOMER_VOICE"
                                         and e.get("availability") == "AVAILABLE" for e in evidence),
                "competitor_evidence": sum(e.get("source_class") == "COMPETITOR_ANALYSIS"
                                           and e.get("availability") == "AVAILABLE" for e in evidence),
                "tradeoffs": sum(bool(o.get("trade_offs")) for o in decision.get("options") or []),
                "verification_status": verification.get("status"),
                "differentiation_rubric": (verification.get("rubric") or {}).get("differentiation"),
                "reason_to_believe_rubric": (
                    verification.get("rubric") or {}).get("reason_to_believe_credibility"),
                "human_marketer_preference": "NOT_COLLECTED",
                "defects": [d.get("code") for d in verification.get("defects") or []],
                "requirement_coverage": {
                    "total": len(requirement_results),
                    "satisfied": sum(
                        item.get("current_status") == "SATISFIED"
                        for item in requirement_results
                    ),
                    "unmet": sum(
                        item.get("current_status") in {"UNMET", "UNKNOWN"}
                        for item in requirement_results
                    ),
                    "unverified": sum(
                        item.get("current_status") == "UNVERIFIED"
                        for item in requirement_results
                    ),
                },
                "blocking_gaps": list(
                    assessment.get("blocking_requirement_ids") or []
                ),
                "resolution_actions": [
                    {
                        "requirement_id": item.get("requirement_id"),
                        "action": item.get("resolution_action"),
                        "reason_code": item.get("reason_code"),
                    }
                    for item in requirement_results
                ],
                "policy_id": assessment.get("policy_id"),
                "policy_version": assessment.get("policy_version"),
                "unknown_count": len(assessment.get("unknowns") or []),
                "eligible_for_approval": bool(
                    verification.get("eligible_for_approval")
                ),
                "selection_status": decision.get("selection_status"),
                "provisional_options": sum(
                    option.get("option_status") == "PROVISIONAL"
                    for option in decision.get("options") or []
                ),
                "final_options": sum(
                    option.get("option_status") in {"SELECTED", "ALTERNATIVE"}
                    for option in decision.get("options") or []
                ),
                "verifier_defects": [
                    defect.get("code")
                    for defect in verification.get("defects") or []
                ],
                "compatibility": "ADDITIVE_CANONICAL_RUN_JSON",
                "latency_ms": b_ms, "provider_calls": 0, "cost_usd": 0,
            },
        })
    awareness = next(r for r in rows if r["case"] == "volume_objective_awareness")
    trial = next(r for r in rows if r["case"] == "volume_objective_trial")
    return {
        "mode": "STRUCTURAL_OFFLINE", "provider_backed": False,
        "quality_preference_measured": False,
        "objective_sensitivity": {
            "same_brand_cases_compared": True,
            "selected_positioning_changed": (
                awareness["B"].get("selected_positioning")
                != trial["B"].get("selected_positioning")),
            "note": "Structural metric only; no provider-backed quality preference was measured.",
        },
        "cases": rows,
    }
