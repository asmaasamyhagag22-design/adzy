"""U6 data contracts (v2.2 §5). Additive, strict. The campaign plan mirrors the Meta hierarchy so it
imports into Ads Manager with no translation; status is PAUSED by construction (PD-7 — never live)."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from advisor.schemas import MissingInfoReport
from policy_linter.schemas import PolicyLintReport


class GeoBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    center_address: Optional[str] = None
    radius_km: Optional[float] = None
    cities: List[str] = Field(default_factory=list)
    regions: List[str] = Field(default_factory=list)


class AudienceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: List[str] = Field(default_factory=list)     # grounded persona axes as short strings
    refinement: Optional[str] = None                     # this objective's TOFU-widen / BOFU-narrow note


class KpiBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    target_value: Optional[float] = None
    unit: str = ""


class AdBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_text: str = ""
    headline: str = ""
    cta: str = ""
    destination: str
    asset_refs: List[str] = Field(default_factory=list)


class AdSetPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    funnel_stage: str
    destination: str
    placement: str = "advantage_plus"
    daily_budget: Optional[float] = None
    budget_allocation_pct: float
    geo: GeoBlock
    audience: AudienceBlock
    kpi: KpiBlock
    ads: List[AdBlock] = Field(default_factory=list)


class CampaignSpec(BaseModel):
    """One Meta campaign = one objective; its ad set(s) inherit it. A funnel media plan yields
    several campaigns."""
    model_config = ConfigDict(extra="forbid")
    name: str
    objective: str                                       # the exact Meta API objective string
    status: Literal["PAUSED"] = "PAUSED"                 # NEVER live (PD-7)
    ad_sets: List[AdSetPlan] = Field(default_factory=list)


class CampaignPlanJSON(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    market_definition_ref: str
    campaigns: List[CampaignSpec] = Field(default_factory=list)


class Prereq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: str
    required: bool = True
    note: str = ""


class PrereqChecklist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: List[Prereq] = Field(default_factory=list)


class ApprovalDraft(BaseModel):
    """DRAFT until a human sets it (PD-7). `approval_token` is sha256-bound to the campaign plan, so
    an approval never silently carries over to a mutated plan."""
    model_config = ConfigDict(extra="forbid")
    status: Literal["DRAFT", "APPROVED"] = "DRAFT"
    approved_by: Optional[str] = None
    approval_token: str
    created_at: str


class LaunchBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    status: Literal["DRAFT", "APPROVED"] = "DRAFT"
    campaign_plan: CampaignPlanJSON
    compliance: Optional[dict] = None                    # grounding.compliance.build_compliance_sheet output
    policy_lint: PolicyLintReport
    advisor: MissingInfoReport
    prerequisites: PrereqChecklist
    approval: ApprovalDraft
    ads_export_path: Optional[str] = None                # Ads-Manager import CSV (xlsx when openpyxl present)
    # 2.1 (LB-MPV2-GAP): the strategist layers the War Room shows — test cells, reality
    # check, counter-strategy — now travel WITH the export instead of silently dropping.
    media_plan_v2: Optional[dict] = None
    assets_dir: Optional[str] = None
