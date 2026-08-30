"""Media Plan v2 schemas — strict (PD-5)."""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class KillSwitch(BaseModel):
    """The cost-per-lead ceiling that ENDS an ad set (after the learning phase). Derived from a real
    scraped price when available; a disclosed default otherwise — never a silent invented number."""
    model_config = ConfigDict(extra="forbid")

    max_cpl: float
    currency: str = ""
    basis: str = "default_kpi"        # "price_margin" (grounded) | "default_kpi" (disclosed fallback)
    disclosure: str = ""              # non-empty ONLY when the default fallback was used


class RealityCheck(BaseModel):
    """§5 — the operator's guardrails. Capacity caps SPEND (never buy more leads than you can serve);
    the kill-switch caps COST per lead; scale/fatigue are the hardcoded industry rules the decision
    engine enforces. None of these is an LLM opinion."""
    model_config = ConfigDict(extra="forbid")

    max_daily_leads: int
    capacity_source: str              # "human" (a real number) | "config_default" (a vertical prior)
    daily_spend_ceiling: float        # max_daily_leads × kill_switch.max_cpl
    kill_switch: KillSwitch
    scale_rule: str                   # from decision_engine CampaignPolicy — an industry rule, not a guess
    fatigue_rule: str
    notes: List[str] = Field(default_factory=list)


class TestCell(BaseModel):
    """§3 — ONE budget cell testing ONE hypothesis. The plan runs 3 in parallel so a winner emerges
    from evidence, not opinion. Each is seeded by a GROUNDED input (persona / a real offer / a SWOT edge)."""
    model_config = ConfigDict(extra="forbid")

    name: str
    axis: str                         # "audience" | "offer" | "competitor"
    hypothesis: str
    budget_share: float               # fraction of the test budget (the 3 sum to 1.0)
    targeting_note: str
    grounded_in: str                  # the evidence this cell is built on
    # §4 Creative Brief for THIS ad set — funnel stage + angle + CTA, passed to the creative engine.
    funnel_stage: str = ""            # tofu | mofu | bofu
    angle: str = ""
    cta: str = ""
    # ش4: "أب القرار" — the competitor evidence this cell counters ("" when not competitor-informed).
    counter_basis: str = ""


class MediaPlanV2(BaseModel):
    """The Senior-Media-Buyer plan: the strategic narrative, the 3 test cells, the reality-check
    guardrails, and the evidence-bound marketing-intel inputs — a closed universe (§6)."""
    model_config = ConfigDict(extra="forbid")

    brand_ref: str = ""
    strategic_narrative: str = ""
    test_cells: List[TestCell] = Field(default_factory=list)
    reality_check: RealityCheck
    offers: List[str] = Field(default_factory=list)
    usps: List[str] = Field(default_factory=list)
    pain_points: List[str] = Field(default_factory=list)
    guardrails: List[str] = Field(default_factory=list)
    open_inputs: List[str] = Field(default_factory=list)   # §7 — numbers the system needs, never guesses
    # ش4: the competitor-informed rationale lines (longevity evidence, NEVER an invented rival CPL).
    competitor_rationale: List[str] = Field(default_factory=list)
