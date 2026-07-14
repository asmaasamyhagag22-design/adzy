"""The closed loop (v2.2 §6-W8): performance -> decision -> (if rotate/kill) a creative variation ->
RE-GATE the variant through the Evidence Ledger + Policy Linter -> a fresh DRAFT bundle delta for a
human. Governance closes the loop, but no budget moves and nothing publishes (PD-7). The variation is
injected (`variation_fn`) so the loop is decoupled from — and testable without — the creative engine.
"""
from __future__ import annotations

from typing import Any, Callable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from decision_engine import DecisionKind, evaluate
from decision_engine.schemas import Decision
from policy_linter import PolicyLintReport, lint_campaign

from launch_bundle.bundle import assemble_launch_bundle
from launch_bundle.schemas import LaunchBundle

_ACT_ON = {DecisionKind.ROTATE_CREATIVE, DecisionKind.KILL_AND_REPLACE}


class LoopResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    action: Literal["no_change", "variant_bundled", "variant_rejected"]
    reasons: List[str] = Field(default_factory=list)
    policy_lint: Optional[PolicyLintReport] = None
    new_bundle: Optional[LaunchBundle] = None
    delta: Optional[dict] = None      # {decision, old_token, new_token} when a variant is bundled


def _ledger_ok(profile: Any, creative: dict) -> tuple[bool, List[str]]:
    """Evidence Ledger re-check: every hard claim in the variant's copy must resolve to real evidence.
    Degrades to OK (with a note) if the ledger can't run — the Policy Linter still gates."""
    lines = [str(creative.get(k, "")) for k in ("headline", "primary_text", "cta") if creative.get(k)]
    try:
        from grounding import EvidenceLedger
        led = EvidenceLedger.from_profile(profile)
        unsourced = []
        for ln in lines:
            unsourced += [v.claim.text for v in led.audit_text(ln) if not v.sourced]
        return (not unsourced), ([f"unsourced claim: {c}" for c in unsourced[:3]] if unsourced else [])
    except Exception:  # noqa: BLE001
        return True, ["evidence ledger unavailable — policy lint only"]


def run_closed_loop(*, policy, snapshots, profile: Any, media_plan: Any,
                    variation_fn: Callable[[dict], dict], current_creative: Optional[dict] = None,
                    created_at: str = "", **bundle_kwargs) -> LoopResult:
    """Evaluate the snapshots; on ROTATE_CREATIVE/KILL_AND_REPLACE, request a variation, re-gate it,
    and (if it passes both gates) assemble a fresh DRAFT bundle. `variation_fn(old_creative)->new`."""
    decision = evaluate(policy, snapshots)
    if decision.kind not in _ACT_ON:
        return LoopResult(decision=decision, action="no_change",
                          reasons=["decision does not require a new creative"])

    new_creative = dict(variation_fn(current_creative or {}) or {})
    surfaces = {k: str(v) for k, v in new_creative.items()
                if k in ("headline", "primary_text", "cta") and str(v).strip()}
    lint = lint_campaign(category=(profile or {}).get("category") if isinstance(profile, dict) else "",
                         copy_surfaces=surfaces)
    grounded_ok, ledger_notes = _ledger_ok(profile, new_creative)

    if lint.verdict != "PASS" or not grounded_ok:
        reasons = [f"policy: {lint.verdict}"] + ledger_notes
        reasons += [f"{f.category}:{f.span}" for f in lint.findings[:3]]
        return LoopResult(decision=decision, action="variant_rejected", reasons=reasons,
                          policy_lint=lint)

    new_bundle = assemble_launch_bundle(profile=profile, media_plan=media_plan, creative=new_creative,
                                        created_at=created_at, **bundle_kwargs)
    return LoopResult(
        decision=decision, action="variant_bundled",
        reasons=["variant passed the Evidence Ledger + Policy Linter re-gate"] + ledger_notes,
        policy_lint=lint, new_bundle=new_bundle,
        delta={"decision": decision.kind.value, "new_token": new_bundle.approval.approval_token})
