"""U6 — Launch Bundle (v2.2 §5, §6-W8): the Ads-Manager-ready, human-gated deliverable.

`assemble_launch_bundle` packages a media plan into what a media buyer hands off: a
`campaign_plan.json` mirroring the Meta Campaign -> AdSet -> Ad hierarchy (objectives as the exact
Meta API strings, campaigns PAUSED — never live, PD-7), an Ads-Manager import CSV, the creative
COMPLIANCE sheet, the U4 policy-lint verdict, the U5 missing-info report, a prerequisite checklist,
and a DRAFT `approval.json` whose token is sha256-bound to the exact plan (a human promotes
DRAFT -> APPROVED; nothing auto-approves).
"""
from launch_bundle.bundle import approve_bundle, assemble_launch_bundle
from launch_bundle.schemas import ApprovalDraft, CampaignPlanJSON, LaunchBundle, PrereqChecklist

__all__ = ["CampaignPlanJSON", "PrereqChecklist", "ApprovalDraft", "LaunchBundle",
           "assemble_launch_bundle", "approve_bundle"]
