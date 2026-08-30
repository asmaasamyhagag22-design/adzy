"""Ad Compliance Sheet — the CLIENT-FACING deliverable derived from an asset's audit trail.

The audit sidecar (`build_poster_audit` / `build_calendar_audit`) is the raw proof object;
this module turns it into the sheet an agency hands its client (the shape promised in the
concept doc): one row per claim, each row exactly one of:

  - verified   : the claim traces to REAL evidence (source URL + verbatim matched quote)
  - softened   : the gate caught an unverifiable claim and it was reworded before publish
  - dropped    : the gate caught a fabricated claim and it was removed before publish
  - no_checkable_claims : the copy line carries no falsifiable claim (subjective/creative
                          copy — passes by the falsifiability policy)
  - unverified : an unsourced hard claim survived to the shipped copy. Post-gate this
                 should never happen; when it does the sheet says so honestly (verdict
                 NEEDS_REVIEW) instead of hiding it.

Transparency rule (same as the audit trail): remediated claims are SHOWN, marked handled —
the client sees the system caught and fixed a fabrication, not merely a clean result.

Pure/deterministic, no I/O, never raises on ordinary input (a malformed audit -> None).
"""
from __future__ import annotations

from typing import Any, Optional

SHEET_TITLE = "Ad Compliance Sheet"

# claim-kind -> short human label for the sheet's "claim" column
_KIND_LABELS = {
    "number": "numeric claim",
    "superlative": "superlative/ranking claim",
    "first": "first/only claim",
    "award": "award claim",
    "certification": "certification claim",
    "guarantee": "guarantee claim",
    "free": "free-offer claim",
}


def _claim_label(kind: str, token: str) -> str:
    label = _KIND_LABELS.get(kind or "", f"{kind} claim" if kind else "claim")
    return f"{label}: “{token}”" if token else label


def _verified_basis(tier: Optional[str]) -> str:
    if tier == "brand":
        return "Verified against the brand's own website (verbatim evidence)"
    return "Verified against a sourced web result"


def build_compliance_sheet(audit: Any) -> Optional[dict]:
    """Transform one asset's audit-trail dict into the client-facing compliance sheet.

    Returns None when `audit` isn't a usable audit dict (the caller ships without a sheet
    rather than shipping a fabricated one).
    """
    if not isinstance(audit, dict):
        return None
    final = audit.get("final_copy")
    if not isinstance(final, dict):
        # CS-121: the CALENDAR audit (strategy/audit.py) ships items[].shipped_copy.fields —
        # same per-field claim rows, different envelope. Aggregate them into one sheet so the
        # calendar finally gets the (claim -> source) trail the poster always had.
        fields = []
        for it in (audit.get("items") or []):
            sc = it.get("shipped_copy") if isinstance(it, dict) else None
            if isinstance(sc, dict):
                fields.extend(f for f in (sc.get("fields") or []) if isinstance(f, dict))
        if fields:
            final = {"fields": fields}
        else:
            return None

    rows: list[dict] = []
    verified = unverified = clean_fields = 0
    # A field/claim we cannot READ is a claim we did not CHECK (P6 #2). It used to `continue` and
    # vanish from every count, leaving the sheet free to say PASS about copy it never inspected.
    unreadable = 0

    for fa in final.get("fields") or []:
        if not isinstance(fa, dict):
            unreadable += 1
            continue
        field_name = fa.get("field") or ""
        copy_text = fa.get("text") or ""
        claims = fa.get("claims") or []
        if not claims:
            clean_fields += 1
            rows.append({
                "copy_field": field_name,
                "copy_text": copy_text,
                "claim": None,
                "status": "no_checkable_claims",
                "basis": "No falsifiable claim — subjective/creative copy (passes by policy)",
                "source_url": None,
                "matched_quote": None,
                "source_tier": None,
                "lang_mismatch": False,
            })
            continue
        for c in claims:
            if not isinstance(c, dict):
                unreadable += 1          # unchecked, not clean — forces NEEDS_REVIEW below
                continue
            sourced = bool(c.get("sourced"))
            if sourced:
                verified += 1
            else:
                unverified += 1
            rows.append({
                "copy_field": field_name,
                "copy_text": copy_text,
                "claim": _claim_label(c.get("kind") or "", c.get("token") or ""),
                "status": "verified" if sourced else "unverified",
                "basis": (_verified_basis(c.get("source_tier")) if sourced
                          else "No supporting evidence found for this claim in the shipped copy"),
                "source_url": c.get("source_url"),
                "matched_quote": c.get("matched_quote"),
                "source_tier": c.get("source_tier"),
                "lang_mismatch": bool(c.get("lang_mismatch")),
            })

    remediation_rows: list[dict] = []
    for rec in audit.get("remediation") or []:
        if not isinstance(rec, dict):
            continue
        action = rec.get("action") or "softened"
        dropped = action == "dropped"
        kinds = rec.get("unsourced_claims") or []
        remediation_rows.append({
            "copy_field": rec.get("field") or "",
            "original_text": rec.get("original_text") or "",
            "claim": ", ".join(_KIND_LABELS.get(k, k) for k in kinds) or "unverifiable claim",
            "status": "dropped" if dropped else "softened",
            "basis": ("No supporting evidence — claim removed before publish" if dropped
                      else "No supporting evidence — claim reworded before publish"),
            "note": rec.get("note"),
        })

    # FAIL-CLOSED (P6 #2): PASS is a POSITIVE statement — it requires that we actually reviewed
    # something and found every claim sourced. `unverified == 0` alone was the all([]) vacuity:
    # an audit with zero rows (a changed copy path, a serialisation miss) certified itself clean,
    # and unreadable rows silently dropped out of the counts. Nothing checked = nothing certified.
    if not rows or unreadable:
        verdict = "NEEDS_REVIEW"
    else:
        verdict = "PASS" if unverified == 0 else "NEEDS_REVIEW"
    return {
        "title": SHEET_TITLE,
        "asset_type": audit.get("asset_type") or "asset",
        "generated_by": audit.get("gate") or "evidence_ledger",
        "verdict": verdict,
        "policy": audit.get("policy"),
        "coverage": audit.get("coverage"),
        "evidence_count": audit.get("evidence_count"),
        "summary": {
            "rows": len(rows),
            "claims_verified": verified,
            "claims_unverified": unverified,
            "claims_unreadable": unreadable,      # >0 => we could not check it => NEEDS_REVIEW
            "fields_without_claims": clean_fields,
            "claims_remediated": len(remediation_rows),
        },
        "shipped_copy": rows,
        "remediation": remediation_rows,
    }
