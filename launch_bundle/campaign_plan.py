"""Flatten a grounded U1 MediaPlan into the Meta Campaign -> AdSet -> Ad hierarchy (one campaign per
objective; a funnel plan yields several). Objectives serialize as the exact Meta API strings, so the
plan imports into Ads Manager with no translation layer."""
from __future__ import annotations

from typing import Any, List, Optional

from launch_bundle.schemas import (
    AdBlock, AdSetPlan, AudienceBlock, CampaignPlanJSON, CampaignSpec, GeoBlock, KpiBlock,
)

_AXES = ("age_range", "gender", "income_level", "location_summary")


def _audience(persona: Any, refinement: Optional[str]) -> AudienceBlock:
    summary: List[str] = []
    for axis in _AXES:
        ref = getattr(persona, axis, None)
        if ref is not None and getattr(ref, "is_grounded", False) and getattr(ref, "claim", ""):
            summary.append(f"{axis}: {ref.claim}")
    for ir in (getattr(persona, "interests", None) or []):
        if getattr(ir, "is_grounded", False) and getattr(ir, "claim", ""):
            summary.append(f"interest: {ir.claim}")
    return AudienceBlock(summary=summary, refinement=refinement or None)


def _geo(base_geo: Any) -> GeoBlock:
    return GeoBlock(mode=getattr(getattr(base_geo, "mode", None), "value", str(getattr(base_geo, "mode", ""))),
                    center_address=getattr(base_geo, "center_address", None),
                    radius_km=getattr(base_geo, "radius_km", None),
                    cities=list(getattr(base_geo, "cities", None) or []),
                    regions=list(getattr(base_geo, "regions", None) or []))


def _kpi(obj: Any) -> KpiBlock:
    k = getattr(obj, "kpi_target", None)
    if k is None:
        return KpiBlock(metric="unset")
    return KpiBlock(metric=k.metric, target_value=k.target_value, unit=k.unit)


def flatten_media_plan(media_plan: Any, *, creative: Optional[dict] = None) -> CampaignPlanJSON:
    """`creative` (optional): {primary_text, headline, cta, asset_refs} — the approved creative that
    fills each Ad. When absent, ads carry empty copy (a prereq flags 'creative copy pending')."""
    persona = getattr(media_plan, "base_persona", None)
    base_geo = getattr(media_plan, "base_geo", None)
    creative = creative or {}
    campaigns: List[CampaignSpec] = []
    for obj in (getattr(media_plan, "objectives", None) or []):
        dest = getattr(getattr(obj, "destination", None), "value", str(getattr(obj, "destination", "")))
        funnel = getattr(getattr(obj, "funnel_stage", None), "value", str(getattr(obj, "funnel_stage", "")))
        meta_obj = getattr(getattr(obj, "objective", None), "value", str(getattr(obj, "objective", "")))
        label = getattr(getattr(obj, "objective", None), "label", meta_obj)
        ad = AdBlock(primary_text=str(creative.get("primary_text", "")),
                     headline=str(creative.get("headline", "")),
                     cta=str(creative.get("cta", "")), destination=dest,
                     asset_refs=list(creative.get("asset_refs", []) or []))
        ad_set = AdSetPlan(
            name=f"{label} · {funnel.upper()}", funnel_stage=funnel, destination=dest,
            daily_budget=None, budget_allocation_pct=float(getattr(obj, "budget_allocation_pct", 0.0)),
            geo=_geo(base_geo), audience=_audience(persona, getattr(obj, "audience_refinement", None)),
            kpi=_kpi(obj), ads=[ad])
        campaigns.append(CampaignSpec(name=f"{label} campaign", objective=meta_obj, ad_sets=[ad_set]))
    return CampaignPlanJSON(run_id=getattr(media_plan, "run_id", ""),
                            market_definition_ref=getattr(media_plan, "market_definition_ref", ""),
                            campaigns=campaigns)
