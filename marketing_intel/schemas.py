"""Marketing-intel schemas — strict (PD-5). Each surfaced item is an `EvidencedField[str]` whose
evidence is a VERBATIM quote + the post permalink, so a value without real evidence is invalid."""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field

from business_profile.schemas import EvidencedField

ItemKind = Literal["offer", "usp", "pain_point", "message_angle"]


class MarketingIntelligence(BaseModel):
    """The step-1 output for Media Plan v2: three evidence-bound signal lists + a reject count.

    `offers` — active promotions/offers the brand is CURRENTLY running (from its own posts).
    `usps`   — unique selling points the brand claims (its own words).
    `pain_points` — customer pain/complaints (from comments / the audience's own words).
    Every list holds `EvidencedField[str]` items; each item's `.evidence[0]` is a real permalink +
    the verbatim quote it was grounded on. `rejected` counts model items dropped for a quote that
    was NOT a verbatim substring of the snapshot (the moat working)."""
    model_config = ConfigDict(extra="forbid")

    brand_ref: str = ""
    offers: List[EvidencedField[str]] = Field(default_factory=list)
    usps: List[EvidencedField[str]] = Field(default_factory=list)
    pain_points: List[EvidencedField[str]] = Field(default_factory=list)
    # ش3 additive: زاوية الرسالة — the competitor's persuasion play (fear-of-pain / price /
    # prestige / speed / social-proof / urgency), verbatim-quote-bound like everything else.
    message_angles: List[EvidencedField[str]] = Field(default_factory=list)
    rejected: int = 0

    def is_empty(self) -> bool:
        return not (self.offers or self.usps or self.pain_points)
