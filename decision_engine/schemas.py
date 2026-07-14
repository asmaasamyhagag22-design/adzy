"""U3 data contracts (v2.2 §5). Additive, strict; reuses U1's KPITarget so the policy target speaks
the same metric vocabulary as the media plan (no fork — PD-2)."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from media_plan.schemas import KPITarget


class PerformanceSnapshot(BaseModel):
    """One day (or period) of measured performance for one ad. ONE schema for the simulator AND CSV
    import, so a demo series and a real client export are judged by identical code."""
    model_config = ConfigDict(extra="forbid")

    campaign_ref: str
    ad_ref: str
    date: date
    spend: float = Field(ge=0.0)
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    ctr: float = Field(ge=0.0)            # click-through rate, fraction (0.02 = 2%)
    frequency: float = Field(ge=0.0)      # avg impressions per person
    conversions: int = Field(ge=0)
    cpl: Optional[float] = Field(default=None, ge=0.0)   # cost per result; None until a conversion lands


class DecisionKind(str, Enum):
    KEEP = "keep"
    WATCH = "watch"
    REDUCE_BUDGET = "reduce_budget"
    SCALE = "scale"
    ROTATE_CREATIVE = "rotate_creative"
    KILL_AND_REPLACE = "kill_and_replace"
    NO_DECISION = "no_decision"           # not enough signal yet (guardrail) — the honest default


class CampaignPolicy(BaseModel):
    """The thresholds the ladder judges against. Defaults encode standard Meta media-buying guardrails
    (documented as configurable estimates, not scraped truth)."""
    model_config = ConfigDict(extra="forbid")

    kpi: KPITarget
    test_budget: float = Field(gt=0.0)
    min_spend_multiple: float = 3.0       # need >= 3x the target cost spent before a cost read is trusted
    min_hours: int = 72                   # a minimum runtime before ANY decision
    min_conversions: int = 5              # a floor below which there is simply nothing to read
    learning_phase_conversions: int = 50  # T-4: no KILL/SCALE before Meta's ~50-conversion learning exit
    fatigue_frequency: float = 3.5        # avg impressions/person above which fatigue is plausible
    fatigue_ctr_drop_pct: float = 25.0    # CTR fall (recent vs baseline) that, WITH high frequency, = fatigue
    scale_step_max_pct: float = 20.0      # never scale budget by more than this in one step


class Decision(BaseModel):
    """The engine's verdict — the kind, the named rules that fired, and human-readable reasons. No
    budget is moved and nothing is published (PD-7); this is advice a human acts on."""
    model_config = ConfigDict(extra="forbid")

    kind: DecisionKind
    triggered_rules: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
