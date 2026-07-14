"""Assemble a Launch Bundle from a grounded MediaPlan + optional creative — the human-gated deliverable
(v2.2 §6-W8). Reuses U1 (media plan), grounding.compliance (sheet), U4 (policy lint), U5 (advisor).
Pure/deterministic given its inputs (timestamp passed in); writes files only when out_dir is set."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, List, Optional

from advisor import build_missing_info_report
from policy_linter import lint_campaign
from policy_linter.linter import surfaces_from_compliance_sheet

from launch_bundle.ads_export import campaign_plan_to_csv
from launch_bundle.campaign_plan import flatten_media_plan
from launch_bundle.schemas import (
    ApprovalDraft, CampaignPlanJSON, LaunchBundle, Prereq, PrereqChecklist,
)


def _fv(profile: Any, key: str) -> str:
    if not isinstance(profile, dict):
        return ""
    v = profile.get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return str(v).strip() if v else ""


def approval_token(campaign_plan: CampaignPlanJSON) -> str:
    """sha256 of the EXACT campaign plan — the human sign-off is content-bound, so approving plan A
    never carries over to a mutated plan B (mirrors reel/render3 approval_token)."""
    return hashlib.sha256(campaign_plan.model_dump_json().encode("utf-8")).hexdigest()


def _prereqs(media_plan: Any, has_creative: bool) -> PrereqChecklist:
    dests, objs = set(), set()
    for o in (getattr(media_plan, "objectives", None) or []):
        dests.add(getattr(getattr(o, "destination", None), "value", ""))
        objs.add(getattr(getattr(o, "objective", None), "value", ""))
    items: List[Prereq] = [Prereq(item="page_access", note="admin access to the Facebook Page")]
    if "OUTCOME_SALES" in objs or dests & {"website", "online_store"}:
        items.append(Prereq(item="pixel_installed", note="Meta pixel/CAPI on the site for conversions"))
    if "whatsapp" in dests:
        items.append(Prereq(item="whatsapp_business_verified", note="a verified WhatsApp Business number"))
    if "lead_form" in dests:
        items.append(Prereq(item="lead_form_configured", note="an instant lead form + follow-up path"))
    if not has_creative:
        items.append(Prereq(item="creative_copy", required=True,
                            note="approved headline/primary-text/CTA + assets are pending"))
    return PrereqChecklist(items=items)


def assemble_launch_bundle(*, profile: Any, media_plan: Any, creative: Optional[dict] = None,
                           compliance_sheet: Optional[dict] = None, competitor_count: int = 0,
                           swot_mode: str = "", n_reviews: int = 0, ads_available: bool = False,
                           created_at: str = "", out_dir: "str | Path | None" = None) -> LaunchBundle:
    """Package everything a media buyer needs. `creative` = {headline, primary_text, cta, asset_refs};
    `compliance_sheet` = a grounding.compliance.build_compliance_sheet output for the shipped copy."""
    plan = flatten_media_plan(media_plan, creative=creative)

    # U4 — lint the copy that will actually run (the creative + any shipped-copy rows).
    copy_surfaces = {k: str(v) for k, v in (creative or {}).items()
                     if k in ("headline", "primary_text", "cta") and str(v).strip()}
    copy_surfaces.update(surfaces_from_compliance_sheet(compliance_sheet))
    policy_lint = lint_campaign(category=_fv(profile, "category"), copy_surfaces=copy_surfaces)

    # U5 — the honesty/gaps report.
    advisor = build_missing_info_report(
        media_plan=media_plan, profile=profile, swot_mode=swot_mode,
        competitor_count=competitor_count, n_reviews=n_reviews, ads_available=ads_available)

    prereqs = _prereqs(media_plan, has_creative=bool(copy_surfaces))
    approval = ApprovalDraft(status="DRAFT", approved_by=None,
                             approval_token=approval_token(plan), created_at=str(created_at))
    bundle = LaunchBundle(
        run_id=getattr(media_plan, "run_id", ""), status="DRAFT", campaign_plan=plan,
        compliance=compliance_sheet, policy_lint=policy_lint, advisor=advisor,
        prerequisites=prereqs, approval=approval, ads_export_path=None,
        assets_dir=str(Path(out_dir) / "assets") if out_dir else None)

    if out_dir:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "campaign_plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        (d / "compliance.json").write_text(json.dumps(compliance_sheet or {}, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
        (d / "approval.json").write_text(approval.model_dump_json(indent=2), encoding="utf-8")
        csv_path = d / "ads_manager.csv"
        csv_path.write_text(campaign_plan_to_csv(plan), encoding="utf-8")
        bundle.ads_export_path = str(csv_path)
        (d / "launch_bundle.json").write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    return bundle
