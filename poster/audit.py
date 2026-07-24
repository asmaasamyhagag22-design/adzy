"""Brand-safety AUDIT TRAIL for a generated poster — Evidence-Ledger Step 3c.

The agency-facing PROOF object. For ONE poster it carries:
  1. `final_copy`  — the (claim -> REAL source URL) trail of the SHIPPED copy, via
     `EvidenceLedger.audit_fields(...).export()`. Source URLs are the actual scraped
     `page_url`s the evidence came from (not placeholders).
  2. `remediation` — what the grounding gate CAUGHT and how it was HANDLED (softened /
     dropped / softened_to_fallback). This is the transparency that makes the proof
     HONEST: the agency sees the system caught and remediated a fabrication, not merely
     that the result looks clean. The original fabricated claim is shown ONLY here, marked
     handled — never in `final_copy` as if it were sourced, and never hidden as if it
     never existed.
  3. `coverage`   — an EXPLICIT scoping statement: which surfaces this trail covers vs which
     are excluded (the reel is NOT yet gated, so no brand-safety claim is made for it).
     Claiming coverage for an ungated surface would be the overselling that breaks the moat.

Pure/deterministic, never raises for ordinary input.
"""
from __future__ import annotations

from typing import Any

from grounding.audit import POLICY, coverage_block

# Poster-specific exclusion on top of the shared scope (the generated background is design,
# not a factual claim).
# P-33: do NOT assert the scene is "text-free" — generated backgrounds frequently bake in
# incidental text (shop signs, pseudo-glyphs, even a third-party mark). What is true is that the
# COPY audit does not certify the imagery; any text/marks the vision model saw are recorded in the
# `vision_qa` block (P-42), not silently blessed as sourced copy.
_POSTER_EXCLUDED = {"background_image":
                    "generated design/imagery — not certified as sourced copy; "
                    "incidental text or marks in the scene are recorded in vision_qa, not blessed"}


def attach_vision_qa(audit: dict | None, qa: Any) -> "dict | None":
    """P-42: persist the SHIPPED poster's vision-QA verdict into its audit sidecar.

    Before this the tri-state verdict (`third_party_mark` / `invented_product` / `score` /
    `overall_pass`) lived ONLY in the in-memory `PosterGenResult.qa` and was never written next to
    the poster — so a counterfeit swoosh could ship under a copy-only `passes: true` with no record
    of whether QA even ran. This records it. `overall_pass` is tri-state (True/False/None=not
    checked); `checked` says whether a vision model actually inspected the image."""
    if audit is None or qa is None:
        return audit
    audit["vision_qa"] = {
        "checked": bool(getattr(qa, "checked", False)),
        "overall_pass": getattr(qa, "overall_pass", None),   # True | False | None (not checked)
        "score": getattr(qa, "score", None),
        "third_party_mark": bool(getattr(qa, "third_party_mark", False)),
        "invented_product": bool(getattr(qa, "invented_product", False)),
        "logo_ok": bool(getattr(qa, "logo_ok", True)),
        "script_wellformed": bool(getattr(qa, "script_wellformed", True)),
        "has_latin_text": bool(getattr(qa, "has_latin_text", False)),
        "reason": str(getattr(qa, "reason", "") or ""),
    }
    return audit


def build_poster_audit(profile: dict, concept: Any, brief: Any = None) -> dict:
    """Assemble the poster's audit trail.

    `concept` carries the final copy + the gate's `remediation` log. `brief` (optional) is
    what actually renders — when given, its fields are the authoritative shipped copy.
    """
    from grounding import EvidenceLedger, make_subject_judge
    # CS-46/CS-75 (owner ruling 2026-07-18): the AUDIT ledger must carry the same subject judge the
    # concept gate uses (poster/concept.py:233) — without it a fabricated superlative ("أفضل قهوة")
    # or a fabricated issuer ("معتمد من هارفارد") is certified brand-tier in the sidecar (measured on
    # 12/18 real profiles). The judge is a cheap Flash call, invoked ONLY on the disjoint-subject
    # residual; guarded so no caller / an offline env degrades to the lenient deterministic gate
    # (never blocks a real claim, never crashes the audit).
    judge = None
    try:
        from business_profile.llm import default_caller
        judge = make_subject_judge(default_caller(strong=False))
    except Exception:  # noqa: BLE001
        judge = None
    ledger = EvidenceLedger.from_profile(profile, subject_judge=judge)

    def _shipped(concept_val: str, *brief_attrs: str) -> str:
        if brief is not None:
            for a in brief_attrs:
                val = getattr(brief, a, None)
                if val:
                    return str(val)
        return concept_val or ""

    fields = {
        "headline": _shipped(concept.headline, "headline"),
        "subheadline": _shipped(concept.subheadline, "subheadline"),
        "cta": _shipped(concept.cta, "cta_text"),
    }
    chips = (getattr(brief, "offerings", None) if brief is not None else None) \
        or list(concept.proof_points or [])
    for i, chip in enumerate(list(chips)[:3], 1):
        fields[f"chip_{i}"] = chip

    report = ledger.audit_fields({k: v for k, v in fields.items() if v})
    remediation = list(getattr(concept, "remediation", []) or [])
    return {
        "asset_type": "poster",
        "gate": "evidence_ledger",
        # SCOPE (P6 #3): this describes THE COPY AUDIT only, and it is honest because
        # `EvidenceLedger.from_profile` above is deliberately UNGUARDED — a gate failure raises
        # and no trail is emitted, so there is no path that reports a gate which did not run.
        # It does NOT speak for the H6 headline gate: that one records its own outcome in
        # `remediation` (unsourced_claims=["gate_unavailable"]) when it could not verify an
        # external override. Do not widen this flag's meaning without deriving it.
        "gate_active": True,
        "policy": POLICY,
        "coverage": coverage_block(_POSTER_EXCLUDED),
        "evidence_count": len(ledger.entries),
        "remediated": bool(remediation),
        "remediation": remediation,
        "final_copy": report.export(),
    }
