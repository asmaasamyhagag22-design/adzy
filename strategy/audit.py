"""Brand-safety AUDIT TRAIL for a content calendar — Evidence-Ledger Step 3c (calendar).

The SAME shape as the verified poster trail (`poster/audit.py`), per item:
  * `shipped_copy` — the (claim -> REAL source URL) trail of the item's surviving
    hook/angle/topic/CTA, via `EvidenceLedger.audit_fields(...).export()`. Resolution is the
    SAME language-aware resolver the poster uses (Arabic claim cites Arabic evidence; an
    unavoidable cross-language match is flagged) — so the calendar does not reintroduce the
    visual-match defect fixed on the poster.
  * `remediation` — what the strategy gate BLANKED on this item (recorded at build time), so
    a reader sees which generated field was fabricated and remediated — not that it silently
    vanished.
And one top-level `coverage` scoping block (shared with the poster): covered = poster +
calendar copy; excluded = reel (NOT yet gated). Pure/deterministic, never raises.
"""
from __future__ import annotations

from typing import Any

from grounding.audit import POLICY, coverage_block


def build_calendar_audit(profile: dict, calendar: Any) -> dict:
    """Assemble the content calendar's per-item brand-safety trail."""
    from grounding import EvidenceLedger
    ledger = EvidenceLedger.from_profile(profile)

    items = []
    remediated = 0
    for it in getattr(calendar, "items", []):
        fields = {k: v for k, v in (("hook", it.hook), ("angle", it.angle),
                                    ("topic", it.topic), ("cta", it.cta))
                  if (v or "").strip()}
        report = ledger.audit_fields(fields)
        rem = list(getattr(it, "remediation", []) or [])
        if rem:
            remediated += 1
        items.append({
            "date": it.date,
            "platform": it.platform,
            "content_type": it.content_type,
            "remediated": bool(rem),
            "remediation": rem,                 # what the gate blanked on this item (handled)
            "shipped_copy": report.export(),    # the (claim -> real source URL) trail
        })

    return {
        "asset_type": "content_calendar",
        "gate": "evidence_ledger",
        "gate_active": True,
        "policy": POLICY,
        "coverage": coverage_block(),
        "business_name": getattr(calendar, "business_name", ""),
        "evidence_count": len(ledger.entries),
        "items_total": len(items),
        "items_remediated": remediated,
        "items": items,
    }
