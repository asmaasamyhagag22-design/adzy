"""Campaign-plan CSV export. Emits one row per Ad in a CONVENTIONAL bulk-import column order.

⚠ P-49 (honesty): these columns are a reasonable media-buying layout, but they are NOT verified
against a live Meta Ads Manager bulk-import template — some (Funnel Stage, Placement, Asset Refs, KPI
Metric/Target) are our own planning fields, not guaranteed Meta import headers. Treat this as a
buyer-facing PLANNING sheet to map by hand, not a guaranteed one-click import, until the columns are
reconciled with a real Ads Manager template. The JSON stays the load-bearing artifact."""
from __future__ import annotations

import csv
import io
from typing import Any

# One row per Ad, in a conventional Ads-Manager bulk-import field order.
COLUMNS = ["Campaign Name", "Campaign Objective", "Campaign Status", "Ad Set Name", "Funnel Stage",
           "Destination", "Placement", "Budget Allocation %", "Geo Mode", "Geo Detail",
           "Audience", "KPI Metric", "KPI Target", "Ad Headline", "Ad Primary Text", "Ad CTA",
           "Asset Refs"]


def _csv_safe(v: Any) -> str:
    """P-53: neutralize CSV/formula injection. Excel & Sheets EXECUTE a cell that starts with
    = + - @ (or a tab/CR), so a headline like '=cmd|...' becomes a live formula on import. Prefix
    such a cell with a guard apostrophe (the standard OWASP mitigation) so it renders as text."""
    s = "" if v is None else str(v)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def _geo_detail(g: Any) -> str:
    if g.radius_km is not None:
        return f"{g.center_address or ''} r={g.radius_km}km"
    return ", ".join(g.cities or g.regions) or ("national" if g.mode == "national" else "")


def campaign_plan_to_csv(plan: Any) -> str:
    """Serialize a CampaignPlanJSON to an Ads-Manager import CSV string."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    for c in plan.campaigns:
        for aset in c.ad_sets:
            for ad in (aset.ads or [None]):
                # every value passes through _csv_safe (P-53) — the copy fields especially, since
                # a headline/primary-text is attacker-influenceable scraped/LLM text.
                w.writerow({k: _csv_safe(val) for k, val in {
                    "Campaign Name": c.name, "Campaign Objective": c.objective,
                    "Campaign Status": c.status, "Ad Set Name": aset.name,
                    "Funnel Stage": aset.funnel_stage, "Destination": aset.destination,
                    "Placement": aset.placement, "Budget Allocation %": aset.budget_allocation_pct,
                    "Geo Mode": aset.geo.mode, "Geo Detail": _geo_detail(aset.geo),
                    "Audience": " | ".join(aset.audience.summary),
                    "KPI Metric": aset.kpi.metric, "KPI Target": aset.kpi.target_value or "",
                    "Ad Headline": getattr(ad, "headline", ""),
                    "Ad Primary Text": getattr(ad, "primary_text", ""),
                    "Ad CTA": getattr(ad, "cta", ""),
                    "Asset Refs": ", ".join(getattr(ad, "asset_refs", []) or []),
                }.items()})
    return buf.getvalue()
