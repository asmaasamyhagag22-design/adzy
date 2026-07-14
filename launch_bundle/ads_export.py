"""Ads-Manager import export. openpyxl isn't bundled, so this emits a CSV in Ads-Manager column order
(Ads Manager imports CSV as well as xlsx); the JSON stays the load-bearing artifact. Upgrading to a
true .xlsx is a follow-up once openpyxl is available."""
from __future__ import annotations

import csv
import io
from typing import Any

# One row per Ad, in a conventional Ads-Manager bulk-import field order.
COLUMNS = ["Campaign Name", "Campaign Objective", "Campaign Status", "Ad Set Name", "Funnel Stage",
           "Destination", "Placement", "Budget Allocation %", "Geo Mode", "Geo Detail",
           "Audience", "KPI Metric", "KPI Target", "Ad Headline", "Ad Primary Text", "Ad CTA",
           "Asset Refs"]


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
                w.writerow({
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
                })
    return buf.getvalue()
