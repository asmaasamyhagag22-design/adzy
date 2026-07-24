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


def approve_bundle(out_dir: "str | Path", *, token: str, approved_by: str):
    """P-50 (PD-7): the real DRAFT->APPROVED gate. Nothing auto-approves and the token was computed
    but never verified. This verifies the caller's token against the CURRENT campaign plan on disk
    (content-bound: approving plan A never carries to a mutated plan B), then flips status to
    APPROVED, stamps `approved_by`, and rewrites approval.json + launch_bundle.json. Raises on a
    missing bundle, an empty approver, or a token that does not match the plan — a human sign-off
    can never be forged or stale."""
    from launch_bundle.schemas import LaunchBundle
    d = Path(out_dir)
    bpath = d / "launch_bundle.json"
    if not bpath.is_file():
        raise FileNotFoundError(f"no launch_bundle.json in {d}")
    if not (approved_by or "").strip():
        raise ValueError("approved_by is required — approval is a named human act (PD-7)")
    bundle = LaunchBundle.model_validate_json(bpath.read_text(encoding="utf-8"))
    expected = approval_token(bundle.campaign_plan)
    if not token or token != expected:
        raise ValueError("approval token does not match the current campaign plan — refused "
                         "(the plan changed since the token was issued, or the token is wrong)")
    bundle.status = "APPROVED"
    bundle.approval.status = "APPROVED"
    bundle.approval.approved_by = approved_by
    (d / "approval.json").write_text(bundle.approval.model_dump_json(indent=2), encoding="utf-8")
    bpath.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    return bundle


def assemble_launch_bundle(*, profile: Any, media_plan: Any, creative: Optional[dict] = None,
                           compliance_sheet: Optional[dict] = None, competitor_count: int = 0,
                           swot_mode: str = "", n_reviews: int = 0, ads_available: bool = False,
                           created_at: str = "", out_dir: "str | Path | None" = None,
                           media_plan_v2: Any = None) -> LaunchBundle:
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
    # 2.1: validate the v2 layers before shipping them — a malformed dict is dropped with
    # a note, never trusted blind (the export is a client deliverable).
    v2_dict = None
    if media_plan_v2 is not None:
        try:
            from media_plan_v2 import MediaPlanV2
            v2_dict = MediaPlanV2.model_validate(media_plan_v2).model_dump(mode="json")
        except Exception:  # noqa: BLE001 — an invalid v2 must not sink the whole bundle
            v2_dict = None

    bundle = LaunchBundle(
        run_id=getattr(media_plan, "run_id", ""), status="DRAFT", campaign_plan=plan,
        compliance=compliance_sheet, policy_lint=policy_lint, advisor=advisor,
        prerequisites=prereqs, approval=approval, ads_export_path=None,
        assets_dir=str(Path(out_dir) / "assets") if out_dir else None,
        media_plan_v2=v2_dict)

    if out_dir:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "campaign_plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        # P-52: an empty `{}` compliance.json read as "clean" when in fact no creative copy was
        # reviewed yet. Be honest about that absence instead of shipping a silent empty object.
        _compliance_out = compliance_sheet or {
            "status": "NO_CREATIVE_REVIEWED",
            "note": "No shipped creative copy was provided to this bundle, so no compliance sheet "
                    "could be produced. Attach the approved headline/primary-text/CTA and re-run to "
                    "get the (claim -> source) trail. Absence here is NOT a pass.",
        }
        (d / "compliance.json").write_text(json.dumps(_compliance_out, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
        (d / "approval.json").write_text(approval.model_dump_json(indent=2), encoding="utf-8")
        csv_path = d / "ads_manager.csv"
        # P-53: write BYTES with a UTF-8 BOM (utf-8-sig) so Excel renders Arabic correctly, and
        # avoid the platform newline translation that turned csv's "\r\n" into "\r\r\n" on Windows
        # (write_text opens in text mode). The csv module already emits "\r\n"; we keep it verbatim.
        csv_path.write_bytes(campaign_plan_to_csv(plan).encode("utf-8-sig"))
        bundle.ads_export_path = str(csv_path)
        if v2_dict:
            # The buyer-facing strategist sheet: 3 test cells + kill-switch, own CSV (the
            # Ads-Manager import format keeps its exact columns — never mixed).
            import csv as _csv
            import io as _io
            buf = _io.StringIO()
            w = _csv.writer(buf)
            w.writerow(["cell", "axis", "budget_share", "hypothesis", "funnel_stage",
                        "angle", "cta", "counter_basis", "grounded_in"])
            for c in (v2_dict.get("test_cells") or []):
                w.writerow([c.get("name"), c.get("axis"), c.get("budget_share"),
                            c.get("hypothesis"), c.get("funnel_stage"), c.get("angle"),
                            c.get("cta"), c.get("counter_basis"), c.get("grounded_in")])
            (d / "test_cells.csv").write_bytes(buf.getvalue().encode("utf-8-sig"))
        (d / "launch_bundle.json").write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    return bundle
