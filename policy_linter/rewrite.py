"""U4 suggested-rewrite re-gate (v2.2 §6-W7): an LLM may PROPOSE a rewrite of a policy-violating copy
line, but it can only replace the original after it RE-PASSES both gates — the Policy Linter AND the
Evidence Ledger (no new unsourced claim). A rewrite that still violates, or that smuggles in an
unsourced claim, is REJECTED (PD-1: the gate is code, never the model's say-so; PD-3: evidence or it
doesn't ship). Hermetic: the caller is injected."""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from policy_linter.linter import lint_campaign

_REWRITE_SYSTEM = (
    "You rewrite ONE line of ad copy to remove a Meta ad-policy violation while keeping the brand's "
    "REAL offer. Rules: address the need, not the person; never guarantee a result/outcome; no "
    "before/after; no cure claims; ADD NO new facts, numbers, awards, or claims that weren't already "
    "there. Return only the rewritten line."
)


class RewriteOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool                       # True only if the rewrite passes BOTH gates
    original: str
    rewritten: str = ""
    reasons: List[str] = Field(default_factory=list)


def _ledger_clean(profile: Any, text: str) -> tuple[bool, List[str]]:
    """Every hard claim in the rewrite must resolve to real evidence.

    FAIL-CLOSED (P6 #4): if the ledger cannot run we CANNOT certify the rewrite is grounded, so we
    reject it. It used to `return True` here — reporting CLEAN exactly when it had checked nothing,
    which is the inverse of PD-3 ("evidence or it doesn't ship") that this module cites. The Policy
    Linter alone is a regex over 4 rules; it cannot see a fabricated superiority claim.
    Note: `profile=None` does NOT reach this path — from_profile(None) yields an EMPTY ledger, so
    every hard claim is unsourced and the gate already fails closed on the normal route.
    """
    try:
        from grounding import EvidenceLedger
        led = EvidenceLedger.from_profile(profile)
        unsourced = [v.claim.text for v in led.audit_text(text or "") if not v.sourced]
        return (not unsourced), [f"unsourced claim: {c}" for c in unsourced[:3]]
    except Exception as exc:  # noqa: BLE001
        return False, [f"evidence ledger unavailable ({type(exc).__name__}) — cannot certify the "
                       f"rewrite carries no unsourced claim; rejecting rather than shipping it"]


def suggest_and_regate(*, surface: str, text: str, category, caller: Any,
                       profile: Any = None) -> RewriteOutcome:
    """Ask `caller` for a compliant rewrite of `text`, then re-gate it. Accept only if the rewrite
    PASSes the Policy Linter AND carries no unsourced hard claim. `caller` is the shared LLM caller
    (injected for tests); None -> no rewrite attempted."""
    if caller is None:
        return RewriteOutcome(accepted=False, original=text, reasons=["no caller available"])

    class _Rewrite(BaseModel):
        model_config = ConfigDict(extra="ignore")
        rewritten: str = ""

    try:
        out, _u = caller(_REWRITE_SYSTEM, f"CATEGORY: {category}\nVIOLATING LINE:\n{text}",
                         _Rewrite, group_name="policy_rewrite")
    except Exception as exc:  # noqa: BLE001
        return RewriteOutcome(accepted=False, original=text,
                              reasons=[f"rewrite call failed: {type(exc).__name__}"])
    new_text = (getattr(out, "rewritten", "") or "").strip() if out else ""
    if not new_text:
        return RewriteOutcome(accepted=False, original=text, reasons=["empty rewrite"])

    lint = lint_campaign(category=category, copy_surfaces={surface: new_text})
    ledger_ok, ledger_notes = _ledger_clean(profile, new_text)
    if lint.verdict == "PASS" and ledger_ok:
        return RewriteOutcome(accepted=True, original=text, rewritten=new_text,
                              reasons=["passed the Policy Linter + Evidence Ledger re-gate"] + ledger_notes)
    reasons = [f"policy: {lint.verdict}"] + ledger_notes
    reasons += [f"{f.category}:{f.span}" for f in lint.findings[:3]]
    return RewriteOutcome(accepted=False, original=text, rewritten=new_text, reasons=reasons)
