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
_POSTER_EXCLUDED = {"background_image": "design domain (text-free scene); not a factual claim"}


def build_poster_audit(profile: dict, concept: Any, brief: Any = None) -> dict:
    """Assemble the poster's audit trail.

    `concept` carries the final copy + the gate's `remediation` log. `brief` (optional) is
    what actually renders — when given, its fields are the authoritative shipped copy.
    """
    from grounding import EvidenceLedger
    ledger = EvidenceLedger.from_profile(profile)

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
