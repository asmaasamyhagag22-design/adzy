"""U5 data contracts (v2.2 §5). Additive, strict. NO composite confidence % (§10 forbidden) — the
report is concrete gaps + risks + per-axis grounding, never a magic rolled-up number."""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class Gap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str                                 # what is missing/unconfirmed (e.g. "competitors")
    why_it_matters: str                        # the consequence of the gap for the campaign
    fills_via: str                             # the concrete action/unit that closes it
    severity: Literal["info", "action"]        # info = provisional/disclosed; action = should be closed


class MissingInfoReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gaps: List[Gap] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    grounded_axes: List[str] = Field(default_factory=list)     # persona axes asserted AND evidenced
    ungrounded_axes: List[str] = Field(default_factory=list)   # persona axes asserted WITHOUT evidence
    missing_elements: List[str] = Field(default_factory=list)  # brand facts absent from the site
