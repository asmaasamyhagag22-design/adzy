"""Per-group LLM response schemas.

OpenAI's strict structured outputs require:
- No generics
- No default values (all fields explicit)
- No Optional ambiguity at the top level (we use Union[T, None] explicitly)

Each group has its own concrete response class. The merger (step 6)
converts these into the EvidencedField shapes in the BusinessProfile.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ..schemas import Confidence, LLMEvidenceRef


# ---------------------------------------------------------------------
# Group 1: Identity
# ---------------------------------------------------------------------

class IdentityResponse(BaseModel):
    tagline_value: Optional[str] = None
    tagline_evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    tagline_confidence: Confidence = Confidence.NONE
    tagline_reasoning: Optional[str] = None

    description_value: Optional[str] = None
    description_evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    description_confidence: Confidence = Confidence.NONE
    description_reasoning: Optional[str] = None

    category_value: Optional[str] = None  # validated against BusinessCategory enum after parse
    category_evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    category_confidence: Confidence = Confidence.NONE
    category_reasoning: Optional[str] = None

    # The two axes `category` conflated (process.md §8.11). Optional so responses produced before
    # the split still parse.
    #   market_category — WHAT is sold, free text in the brand's words ("handmade jewellery")
    #   business_model  — HOW it sells, validated against BusinessModel after parse
    market_category_value: Optional[str] = None
    market_category_evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    market_category_confidence: Confidence = Confidence.NONE
    business_model_value: Optional[str] = None
    business_model_evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    business_model_confidence: Confidence = Confidence.NONE


# ---------------------------------------------------------------------
# Group 2: Offerings + pricing posture
# ---------------------------------------------------------------------

class OfferingItem(BaseModel):
    name: str
    short_description: Optional[str] = None
    price_text: Optional[str] = None
    evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM


class OfferingsResponse(BaseModel):
    offerings: list[OfferingItem] = Field(default_factory=list)

    pricing_posture_value: Optional[str] = None  # validated against PricingPosture
    pricing_posture_evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    pricing_posture_confidence: Confidence = Confidence.NONE
    pricing_posture_reasoning: Optional[str] = None


# ---------------------------------------------------------------------
# Group 3: Positioning (audience + value props + tone)
# ---------------------------------------------------------------------

class StringListItem(BaseModel):
    value: str
    evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM


class PositioningResponse(BaseModel):
    audience_type_value: Optional[str] = None  # validated against AudienceType
    audience_type_evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    audience_type_confidence: Confidence = Confidence.NONE
    audience_type_reasoning: Optional[str] = None

    audience_signals: list[StringListItem] = Field(default_factory=list)
    value_propositions: list[StringListItem] = Field(default_factory=list)
    # Catch-all: a UNIQUE, concrete competitive edge / operational detail that doesn't
    # fit audience/value_props/tone (e.g. "24/7 same-day delivery", "only ISO-certified
    # lab in the city"). Same grounding rules — each must cite a block_id + verbatim quote.
    other_unique_insights: list[StringListItem] = Field(default_factory=list)

    tone_of_voice_value: Optional[str] = None  # validated against ToneOfVoice
    tone_of_voice_evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    tone_of_voice_confidence: Confidence = Confidence.NONE
    tone_of_voice_reasoning: Optional[str] = None


# ---------------------------------------------------------------------
# Group 4: Trust + service areas
# ---------------------------------------------------------------------

class TrustResponse(BaseModel):
    trust_signals: list[StringListItem] = Field(default_factory=list)
    service_areas: list[StringListItem] = Field(default_factory=list)
