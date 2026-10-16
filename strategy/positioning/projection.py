"""Read-only compatibility projection from an approved canonical Run."""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping


def project_approved_strategy(
    output: Mapping[str, Any], *, run_id: str, approval_state: str,
) -> dict[str, Any] | None:
    canonical = output.get("canonical_strategy") if isinstance(output, Mapping) else None
    if not isinstance(canonical, Mapping) or approval_state != "approved":
        return None
    decision = canonical.get("decision") if isinstance(canonical.get("decision"), Mapping) else {}
    verification = (canonical.get("verification")
                    if isinstance(canonical.get("verification"), Mapping) else {})
    options = [o for o in (decision.get("options") or []) if isinstance(o, Mapping)]
    selected = next((o for o in options
                     if str(o.get("option_id") or "") == str(decision.get("selected_option_id") or "")), None)
    if selected is None:
        return None
    objective = output.get("_requested_objective") if isinstance(output.get("_requested_objective"), Mapping) else {}
    # The persisted envelope intentionally does not duplicate requested_config. The read model may
    # supply it under this private transient key when available; otherwise goals stay empty.
    business = objective.get("business") if isinstance(objective.get("business"), Mapping) else objective
    unknowns = [str(v) for v in (decision.get("unresolved_unknowns") or [])]
    source_counts = Counter(
        str(e.get("source_class") or "UNKNOWN")
        for e in (canonical.get("evidence") or []) if isinstance(e, Mapping)
    )
    notes = list(unknowns)
    if verification.get("status") == "PASS_WITH_DISCLOSURE":
        notes.insert(0, "Approved with disclosed uncertainty.")
    return {
        "positioning_statement": str(selected.get("positioning_statement") or ""),
        "value_proposition": str(selected.get("differentiated_value") or ""),
        "audience": [str(selected.get("target") or "")] if selected.get("target") else [],
        "goals": [str(business.get("description") or "")] if business.get("description") else [],
        "notes": notes,
        "strategy_version_id": str(run_id),
        "approval_state": "approved",
        "decision_type": "POSITIONING",
        "verification_status": str(verification.get("status") or ""),
        "unresolved_unknowns": unknowns,
        "evidence_summary": {"total": sum(source_counts.values()),
                             "by_source_class": dict(sorted(source_counts.items()))},
        # Deliberate unknowns: this slice did not decide them.
        "strategic_posture": None,
        "posture_rationale": None,
        "tone_of_voice": None,
        "content_pillars": [],
        "channels": [],
        "tows_matrix": {},
        "priority_action_plan": [],
        "campaign_brief_feed": [],
    }

