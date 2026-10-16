"""Canonical Reel execution firewall.

Nothing in this module selects a credential, calls a provider, renders media, or writes a database.
It defines the capability, pricing, approval, state, verdict, delivery, and persistence boundaries
that a later creative/execution gate must satisfy.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical_contract import (
    ReelCreativeContract,
    ReelCreativeContractCompiler,
    RenderableReelAuthority,
)


class CanonicalReelExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProviderSurface(str, Enum):
    VERTEX_AI = "VERTEX_AI"
    GEMINI_API = "GEMINI_API"
    AIML_API = "AIML_API"
    FAKE_TEST = "FAKE_TEST"


class ProviderAuthorizationStatus(str, Enum):
    AUTHORIZED_PROFILE = "AUTHORIZED_PROFILE"
    EXPERIMENTAL_NOT_YET_AUTHORIZED = "EXPERIMENTAL_NOT_YET_AUTHORIZED"


class VideoProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str
    model_id: str
    surface: ProviderSurface
    authorization_status: ProviderAuthorizationStatus
    text_to_video: bool
    image_to_video: bool
    first_last_frame: bool
    reference_images: bool
    video_extension: bool
    conversational_editing: bool
    multimodal_video: bool = False
    native_audio: bool
    supported_aspect_ratios: tuple[str, ...]
    supported_durations: tuple[int, ...]
    supported_resolutions: tuple[str, ...]
    local_seed_supported: bool
    identity_control_mode: str
    cost_known: bool
    capability_source: str
    capability_source_version: str

    @property
    def identity(self) -> tuple[str, str, ProviderSurface]:
        return self.provider_name, self.model_id, self.surface


# Configured-integration truth, not the union of fields exposed by a generic SDK or a newly
# expanded product page.  Gate 2D.4A deliberately keeps the reviewed Vertex integration on the
# conservative text/image/first-last subset.  The Google documentation now redirects into Agent
# Platform and advertises additional reference/extension features; those remain disabled here
# until the exact configured request surface is exercised in a separately authorized live gate.
VERTEX_VEO_31 = VideoProviderCapabilities(
    provider_name="google",
    model_id="veo-3.1-generate-001",
    surface=ProviderSurface.VERTEX_AI,
    authorization_status=ProviderAuthorizationStatus.AUTHORIZED_PROFILE,
    text_to_video=True,
    image_to_video=True,
    first_last_frame=True,
    reference_images=False,
    video_extension=False,
    conversational_editing=False,
    multimodal_video=False,
    native_audio=False,  # the configured Adzy integration neither requests nor delivers it
    supported_aspect_ratios=("16:9", "9:16"),
    supported_durations=(4, 6, 8),
    supported_resolutions=("720p", "1080p"),
    local_seed_supported=True,
    identity_control_mode="FIRST_OR_FIRST_LAST_FRAME_ONLY",
    cost_known=False,  # Gemini API prices do not prove the Vertex commercial surface price
    capability_source=(
        "https://cloud.google.com/vertex-ai/generative-ai/docs/models/"
        "veo/3-1-generate-001"),
    capability_source_version=(
        "gate-2d4a-conservative-vertex-profile-checked-2026-08-24; "
        "google-genai-2.8.0"),
)


VERTEX_VEO_31_FAST = VERTEX_VEO_31.model_copy(update={
    "model_id": "veo-3.1-fast-generate-001",
    "capability_source_version": (
        "gate-2d4a-conservative-vertex-fast-profile-checked-2026-08-24; "
        "google-genai-2.8.0"),
})


GEMINI_OMNI_FLASH_EXPERIMENTAL = VideoProviderCapabilities(
    provider_name="google",
    model_id="gemini-omni-flash-preview",
    surface=ProviderSurface.GEMINI_API,
    authorization_status=ProviderAuthorizationStatus.EXPERIMENTAL_NOT_YET_AUTHORIZED,
    text_to_video=True,
    image_to_video=True,
    first_last_frame=False,
    reference_images=False,
    video_extension=False,
    conversational_editing=True,
    multimodal_video=True,
    native_audio=False,
    supported_aspect_ratios=(),
    supported_durations=tuple(range(3, 11)),
    supported_resolutions=("720p",),
    local_seed_supported=False,
    identity_control_mode="MULTI_INPUT_MODEL_MANAGED_NOT_YET_VERIFIED",
    cost_known=False,
    capability_source="https://ai.google.dev/gemini-api/docs/models/gemini-omni-flash",
    capability_source_version="preview-checked-2026-08-23",
)


CANONICAL_PROVIDER_REGISTRY: Mapping[
    tuple[str, str, ProviderSurface], VideoProviderCapabilities
] = {
    VERTEX_VEO_31.identity: VERTEX_VEO_31,
    VERTEX_VEO_31_FAST.identity: VERTEX_VEO_31_FAST,
}


class ProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str
    model_id: str
    surface: ProviderSurface

    @property
    def identity(self) -> tuple[str, str, ProviderSurface]:
        return self.provider_name, self.model_id, self.surface


class RequiredVideoCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text_to_video: bool = False
    image_to_video: bool = True
    first_last_frame: bool = False
    reference_images: bool = False
    video_extension: bool = False
    conversational_editing: bool = False
    native_audio: bool = False
    aspect_ratio: str = "9:16"
    clip_duration_s: int = 4
    resolution: str = "1080p"
    local_seed_required: bool = True


class CanonicalProviderConstraints(BaseModel):
    """Constraints whose removal would change the authorized provider request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity_constraint: str = ""
    required_starting_frame_hash: str = ""
    required_ending_frame_hash: str = ""
    product_anchor: str = ""
    brand_constraints: tuple[str, ...] = ()
    safety_constraints: tuple[str, ...] = ()
    negative_constraints: tuple[str, ...] = ()
    approval_id: str


def validate_constraint_preserving_retry(*, original: CanonicalProviderConstraints,
                                         retry: CanonicalProviderConstraints) -> None:
    """A retry may change transport timing, never an authorized semantic constraint."""
    if retry != original:
        raise CanonicalReelExecutionError(
            "CAPABILITY_DROPPING_RETRY_FORBIDDEN",
            "A canonical provider retry attempted to change or remove required constraints.")


def select_canonical_provider(
    selection: ProviderSelection,
    requirements: RequiredVideoCapabilities,
    *,
    registry: Mapping[tuple[str, str, ProviderSurface], VideoProviderCapabilities] =
    CANONICAL_PROVIDER_REGISTRY,
) -> VideoProviderCapabilities:
    """Select exactly one named provider profile; there is deliberately no fallback search."""
    capabilities = registry.get(selection.identity)
    if capabilities is None:
        raise CanonicalReelExecutionError(
            "PROVIDER_NOT_EXPLICITLY_AVAILABLE",
            "The exact provider, model, and surface are not registered for canonical Reel.")
    if capabilities.authorization_status != ProviderAuthorizationStatus.AUTHORIZED_PROFILE:
        raise CanonicalReelExecutionError(
            "PROVIDER_NOT_AUTHORIZED",
            "The selected provider profile is experimental or not yet authorized.")
    boolean_fields = (
        "text_to_video", "image_to_video", "first_last_frame", "reference_images",
        "video_extension", "conversational_editing", "native_audio",
    )
    unsupported = [field for field in boolean_fields
                   if getattr(requirements, field) and not getattr(capabilities, field)]
    if requirements.local_seed_required and not capabilities.local_seed_supported:
        unsupported.append("local_seed_supported")
    if requirements.aspect_ratio not in capabilities.supported_aspect_ratios:
        unsupported.append("aspect_ratio=" + requirements.aspect_ratio)
    if requirements.clip_duration_s not in capabilities.supported_durations:
        unsupported.append("duration=" + str(requirements.clip_duration_s))
    if requirements.resolution not in capabilities.supported_resolutions:
        unsupported.append("resolution=" + requirements.resolution)
    if unsupported:
        raise CanonicalReelExecutionError(
            "UNSUPPORTED_REQUIRED_CAPABILITY",
            "Selected provider lacks required capabilities: " + ", ".join(unsupported))
    return capabilities


class CanonicalReelExecutionPreferences(BaseModel):
    """Execution choices only. Pydantic rejects every undeclared strategic override."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str = "instagram_reels"
    desired_duration_min_s: int = Field(default=15, ge=4, le=60)
    desired_duration_max_s: int = Field(default=15, ge=4, le=60)
    creative_style_preference: str = ""
    actor_preference: str = ""
    music_preference: str = ""
    quality_cost_mode: str = "standard"
    provider: ProviderSelection
    required_capabilities: RequiredVideoCapabilities = Field(
        default_factory=RequiredVideoCapabilities)

    @model_validator(mode="after")
    def valid_range(self) -> "CanonicalReelExecutionPreferences":
        if self.desired_duration_min_s > self.desired_duration_max_s:
            raise ValueError("desired duration minimum cannot exceed maximum")
        return self


class ReelExecutionState(str, Enum):
    CONTRACT_READY = "CONTRACT_READY"
    PLAN_READY = "PLAN_READY"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AUTHORIZED = "AUTHORIZED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_TRANSITIONS: Mapping[ReelExecutionState, frozenset[ReelExecutionState]] = {
    ReelExecutionState.CONTRACT_READY: frozenset({ReelExecutionState.PLAN_READY,
                                                  ReelExecutionState.BLOCKED,
                                                  ReelExecutionState.CANCELLED}),
    ReelExecutionState.PLAN_READY: frozenset({ReelExecutionState.AWAITING_APPROVAL,
                                              ReelExecutionState.BLOCKED,
                                              ReelExecutionState.CANCELLED}),
    ReelExecutionState.AWAITING_APPROVAL: frozenset({ReelExecutionState.AUTHORIZED,
                                                     ReelExecutionState.BLOCKED,
                                                     ReelExecutionState.CANCELLED}),
    ReelExecutionState.AUTHORIZED: frozenset({ReelExecutionState.EXECUTING,
                                              ReelExecutionState.CANCELLED}),
    ReelExecutionState.EXECUTING: frozenset({ReelExecutionState.VERIFYING,
                                             ReelExecutionState.FAILED,
                                             ReelExecutionState.CANCELLED}),
    ReelExecutionState.VERIFYING: frozenset({ReelExecutionState.PASSED,
                                             ReelExecutionState.BLOCKED,
                                             ReelExecutionState.FAILED}),
    ReelExecutionState.PASSED: frozenset(),
    ReelExecutionState.BLOCKED: frozenset(),
    ReelExecutionState.FAILED: frozenset(),
    ReelExecutionState.CANCELLED: frozenset(),
}


class ReelExecutionStateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ReelExecutionState
    history: tuple[ReelExecutionState, ...] = ()

    def transition(self, target: ReelExecutionState) -> "ReelExecutionStateRecord":
        if target not in _TRANSITIONS[self.state]:
            raise CanonicalReelExecutionError(
                "INVALID_EXECUTION_STATE_TRANSITION",
                f"Canonical Reel cannot transition from {self.state} to {target}.")
        return ReelExecutionStateRecord(state=target, history=(*self.history, self.state))


class CanonicalReelPreparation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: bool = True
    billable: bool = False
    provider_calls: int = 0
    state: ReelExecutionState = ReelExecutionState.CONTRACT_READY
    reel_contract: ReelCreativeContract
    planner_authority: RenderableReelAuthority
    execution_preferences: CanonicalReelExecutionPreferences
    selected_provider_capabilities: VideoProviderCapabilities


def prepare_canonical_reel(
    context: Mapping[str, Any], preferences: CanonicalReelExecutionPreferences, *,
    registry: Mapping[tuple[str, str, ProviderSurface], VideoProviderCapabilities] =
    CANONICAL_PROVIDER_REGISTRY,
) -> CanonicalReelPreparation:
    contract = ReelCreativeContractCompiler().compile(context)
    capabilities = select_canonical_provider(
        preferences.provider, preferences.required_capabilities, registry=registry)
    return CanonicalReelPreparation(
        reel_contract=contract,
        planner_authority=contract.planner_authority(),
        execution_preferences=preferences,
        selected_provider_capabilities=capabilities,
    )


class PaidProviderCategory(str, Enum):
    DIRECTOR = "DIRECTOR"
    SEED_IMAGE = "SEED_IMAGE"
    TTS = "TTS"
    SFX = "SFX"
    VIDEO = "VIDEO"
    REVIEW = "REVIEW"


class PricingUnit(str, Enum):
    SECOND = "SECOND"
    CALL = "CALL"
    CHARACTER = "CHARACTER"
    TOKEN = "TOKEN"
    PER_1M_TOKENS = "PER_1M_TOKENS"


class ProviderBillingMode(str, Enum):
    STANDARD = "STANDARD"
    PRIORITY = "PRIORITY"
    FLEX_BATCH = "FLEX_BATCH"


class PricingVerificationStatus(str, Enum):
    VERIFIED_OFFICIAL_SOURCE = "VERIFIED_OFFICIAL_SOURCE"
    UNVERIFIED = "UNVERIFIED"


class ProviderPricing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str
    surface: ProviderSurface
    model_id: str
    resolution: str = ""
    pricing_version: str
    effective_date: str
    currency: str = "USD"
    unit: PricingUnit
    unit_price_usd: Decimal
    source: str

    @field_validator("unit_price_usd")
    @classmethod
    def finite_nonnegative_price(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("provider price must be finite and non-negative")
        return value

    @property
    def provider_identity(self) -> tuple[str, str, ProviderSurface]:
        return self.provider_name, self.model_id, self.surface


class TextProviderPricingSnapshot(BaseModel):
    """Reviewed token-price snapshot keyed by the exact commercial request surface.

    This extends Gate 2D.1's versioned provider-pricing boundary for text categories whose input,
    cached-input, and output/reasoning rates differ. Runtime page scraping is deliberately absent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str
    surface: ProviderSurface
    model_id: str
    billing_mode: ProviderBillingMode
    currency: str = "USD"
    unit: PricingUnit = PricingUnit.PER_1M_TOKENS
    input_text_image_video_usd_per_million: Decimal
    output_text_response_reasoning_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal
    pricing_source_url: str
    pricing_verified_date: str
    pricing_status: PricingVerificationStatus
    pricing_version_id: str

    @field_validator(
        "input_text_image_video_usd_per_million",
        "output_text_response_reasoning_usd_per_million",
        "cached_input_usd_per_million",
    )
    @classmethod
    def finite_nonnegative_token_rate(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("token price must be finite and non-negative")
        return value

    @model_validator(mode="after")
    def official_versioned_snapshot(self) -> "TextProviderPricingSnapshot":
        if self.currency != "USD" or self.unit != PricingUnit.PER_1M_TOKENS:
            raise ValueError("canonical text pricing must use USD per 1M tokens")
        if self.pricing_status != PricingVerificationStatus.VERIFIED_OFFICIAL_SOURCE:
            raise ValueError("canonical text pricing must be verified from an official source")
        if not self.pricing_source_url.startswith("https://cloud.google.com/"):
            raise ValueError("canonical Vertex pricing requires an official Google Cloud URL")
        if not self.pricing_verified_date or not self.pricing_version_id:
            raise ValueError("canonical text pricing requires a verified date and version id")
        return self

    @property
    def identity(self) -> tuple[str, str, ProviderSurface, ProviderBillingMode]:
        return self.provider_name, self.model_id, self.surface, self.billing_mode


VERTEX_GEMINI_25_FLASH_STANDARD_PRICING_2026_08_23 = TextProviderPricingSnapshot(
    provider_name="google",
    surface=ProviderSurface.VERTEX_AI,
    model_id="gemini-2.5-flash",
    billing_mode=ProviderBillingMode.STANDARD,
    currency="USD",
    unit=PricingUnit.PER_1M_TOKENS,
    input_text_image_video_usd_per_million=Decimal("0.30"),
    output_text_response_reasoning_usd_per_million=Decimal("2.50"),
    cached_input_usd_per_million=Decimal("0.03"),
    pricing_source_url="https://cloud.google.com/vertex-ai/generative-ai/pricing",
    pricing_verified_date="2026-08-23",
    pricing_status=PricingVerificationStatus.VERIFIED_OFFICIAL_SOURCE,
    pricing_version_id="vertex-gemini-2.5-flash-standard-2026-08-23",
)


TEXT_PROVIDER_PRICING_REGISTRY: Mapping[
    tuple[str, str, ProviderSurface, ProviderBillingMode], TextProviderPricingSnapshot
] = {
    VERTEX_GEMINI_25_FLASH_STANDARD_PRICING_2026_08_23.identity:
        VERTEX_GEMINI_25_FLASH_STANDARD_PRICING_2026_08_23,
}


def resolve_text_provider_pricing(
    *, provider_name: str, model_id: str, surface: ProviderSurface,
    billing_mode: ProviderBillingMode,
    registry: Mapping[
        tuple[str, str, ProviderSurface, ProviderBillingMode], TextProviderPricingSnapshot
    ] = TEXT_PROVIDER_PRICING_REGISTRY,
) -> Optional[TextProviderPricingSnapshot]:
    """Resolve one exact reviewed snapshot; never cross model, surface, or billing mode."""
    return registry.get((provider_name, model_id, surface, billing_mode))


# Reference metadata only.  It is explicitly identified as GEMINI_API pricing and is not reused
# as Vertex pricing.  Canonical execution still requires an envelope built for its exact surface.
GEMINI_API_VEO_PRICING_2026_08_23 = (
    ProviderPricing(provider_name="google", surface=ProviderSurface.GEMINI_API,
                    model_id="veo-3.1-standard", resolution="720p_or_1080p",
                    pricing_version="gemini-api-2026-08-23", effective_date="2026-08-23",
                    unit=PricingUnit.SECOND, unit_price_usd=Decimal("0.40"),
                    source="https://ai.google.dev/gemini-api/docs/pricing"),
    ProviderPricing(provider_name="google", surface=ProviderSurface.GEMINI_API,
                    model_id="veo-3.1-fast", resolution="720p",
                    pricing_version="gemini-api-2026-08-23", effective_date="2026-08-23",
                    unit=PricingUnit.SECOND, unit_price_usd=Decimal("0.10"),
                    source="https://ai.google.dev/gemini-api/docs/pricing"),
    ProviderPricing(provider_name="google", surface=ProviderSurface.GEMINI_API,
                    model_id="veo-3.1-fast", resolution="1080p",
                    pricing_version="gemini-api-2026-08-23", effective_date="2026-08-23",
                    unit=PricingUnit.SECOND, unit_price_usd=Decimal("0.12"),
                    source="https://ai.google.dev/gemini-api/docs/pricing"),
    ProviderPricing(provider_name="google", surface=ProviderSurface.GEMINI_API,
                    model_id="veo-3.1-lite", resolution="720p",
                    pricing_version="gemini-api-2026-08-23", effective_date="2026-08-23",
                    unit=PricingUnit.SECOND, unit_price_usd=Decimal("0.05"),
                    source="https://ai.google.dev/gemini-api/docs/pricing"),
    ProviderPricing(provider_name="google", surface=ProviderSurface.GEMINI_API,
                    model_id="veo-3.1-lite", resolution="1080p",
                    pricing_version="gemini-api-2026-08-23", effective_date="2026-08-23",
                    unit=PricingUnit.SECOND, unit_price_usd=Decimal("0.08"),
                    source="https://ai.google.dev/gemini-api/docs/pricing"),
)


class PlannedProviderCost(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: PaidProviderCategory
    pricing: ProviderPricing
    maximum_units: Decimal
    maximum_cost_usd: Decimal

    @model_validator(mode="after")
    def exact_and_finite(self) -> "PlannedProviderCost":
        if not self.maximum_units.is_finite() or self.maximum_units < 0:
            raise ValueError("planned provider units must be finite and non-negative")
        expected = self.pricing.unit_price_usd * self.maximum_units
        if not self.maximum_cost_usd.is_finite() or self.maximum_cost_usd != expected:
            raise ValueError("maximum cost must equal the versioned unit price times maximum units")
        return self


class ReelCostEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope_id: str
    pricing_version: str
    planned_categories: tuple[PaidProviderCategory, ...]
    items: tuple[PlannedProviderCost, ...]
    maximum_cost_usd: Decimal
    cost_provable: bool = True

    @model_validator(mode="after")
    def complete_and_finite(self) -> "ReelCostEnvelope":
        if not self.maximum_cost_usd.is_finite() or self.maximum_cost_usd < 0:
            raise ValueError("canonical maximum cost must be finite and non-negative")
        if not self.cost_provable:
            raise ValueError("COST_NOT_PROVABLE")
        actual = tuple(dict.fromkeys(v.category for v in self.items))
        expected = tuple(dict.fromkeys(self.planned_categories))
        if set(actual) != set(expected):
            raise ValueError("every planned paid provider category must have pricing")
        if any(not item.pricing.source or not item.pricing.pricing_version for item in self.items):
            raise ValueError("COST_NOT_PROVABLE")
        if sum((v.maximum_cost_usd for v in self.items), Decimal("0")) \
                != self.maximum_cost_usd:
            raise ValueError("cost envelope total does not equal its planned items")
        return self


def build_cost_envelope(*, pricing_version: str,
                        planned_categories: Sequence[PaidProviderCategory],
                        items: Sequence[PlannedProviderCost]) -> ReelCostEnvelope:
    material = {
        "pricing_version": pricing_version,
        "planned_categories": [v.value for v in planned_categories],
        "items": [v.model_dump(mode="json") for v in items],
    }
    digest = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return ReelCostEnvelope(
        envelope_id="rce_" + digest[:32], pricing_version=pricing_version,
        planned_categories=tuple(planned_categories), items=tuple(items),
        maximum_cost_usd=sum((v.maximum_cost_usd for v in items), Decimal("0")))


class GeneratedMode(str, Enum):
    FULLY_GENERATED = "FULLY_GENERATED"
    DEGRADED_PREVIEW = "DEGRADED_PREVIEW"
    RAW_PHOTO_LEGACY = "RAW_PHOTO_LEGACY"


class ReelExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    strategy_version_id: str
    reel_contract_hash: str
    creative_plan_hash: str
    provider: ProviderSelection
    output_mode: GeneratedMode
    cost_envelope: ReelCostEnvelope
    execution_plan_hash: str


def build_execution_plan(*, contract: ReelCreativeContract, creative_plan_hash: str,
                         provider: ProviderSelection, output_mode: GeneratedMode,
                         cost_envelope: ReelCostEnvelope) -> ReelExecutionPlan:
    if not creative_plan_hash:
        raise CanonicalReelExecutionError("PLAN_HASH_MISSING",
                                          "An immutable creative plan hash is required.")
    video_prices = [item.pricing.provider_identity for item in cost_envelope.items
                    if item.category == PaidProviderCategory.VIDEO]
    if not video_prices or any(identity != provider.identity for identity in video_prices):
        raise CanonicalReelExecutionError(
            "PRICING_IDENTITY_MISMATCH",
            "Video pricing must belong to the exact selected provider, model, and surface.")
    material = {
        "strategy_version_id": contract.strategy_version_id,
        "reel_contract_hash": contract.contract_hash,
        "creative_plan_hash": creative_plan_hash,
        "provider": provider.model_dump(mode="json"),
        "output_mode": output_mode.value,
        "cost_envelope_id": cost_envelope.envelope_id,
        "maximum_cost_usd": str(cost_envelope.maximum_cost_usd),
    }
    digest = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return ReelExecutionPlan(
        plan_id="rep_" + digest[:32], execution_plan_hash=digest,
        strategy_version_id=contract.strategy_version_id,
        reel_contract_hash=contract.contract_hash,
        creative_plan_hash=creative_plan_hash, provider=provider,
        output_mode=output_mode, cost_envelope=cost_envelope)


def require_provable_provider_cost(*, capabilities: VideoProviderCapabilities,
                                   pricing: Optional[ProviderPricing]) -> None:
    """Unknown or cross-surface pricing makes the provider ineligible for paid execution."""
    if not capabilities.cost_known or pricing is None:
        raise CanonicalReelExecutionError(
            "COST_NOT_PROVABLE",
            "The exact provider surface has no proven versioned price for this execution.")
    if (pricing.provider_name, pricing.model_id, pricing.surface) != capabilities.identity:
        raise CanonicalReelExecutionError(
            "PRICING_IDENTITY_MISMATCH",
            "Pricing belongs to a different provider, model, or commercial surface.")


class ReelExecutionApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str
    strategy_version_id: str
    reel_contract_hash: str
    execution_plan_hash: str
    provider: ProviderSelection
    maximum_approved_cost_usd: Decimal
    issued_at: datetime
    approver_user_id: str
    expires_at: datetime
    one_shot: bool = True
    authorization_digest: str

    @field_validator("maximum_approved_cost_usd")
    @classmethod
    def finite_cap(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("approval cost cap must be finite and non-negative")
        return value


def _approval_material(values: Mapping[str, Any]) -> bytes:
    def canonical(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return canonical(value.model_dump(mode="python"))
        if isinstance(value, Mapping):
            return {str(k): canonical(v) for k, v in value.items()
                    if str(k) != "authorization_digest"}
        if isinstance(value, (list, tuple)):
            return [canonical(v) for v in value]
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, Enum):
            return value.value
        return value

    return json.dumps(canonical(values), sort_keys=True, separators=(",", ":")).encode()


def issue_execution_approval(*, plan: ReelExecutionPlan, approver_user_id: str,
                             expires_at: datetime, signing_key: bytes,
                             maximum_approved_cost_usd: Optional[Decimal] = None,
                             issued_at: Optional[datetime] = None,
                             approval_id: Optional[str] = None) -> ReelExecutionApproval:
    if not signing_key:
        raise CanonicalReelExecutionError("APPROVAL_SIGNING_KEY_MISSING",
                                          "Approval cannot be issued without a signing key.")
    now = issued_at or datetime.now(timezone.utc)
    if now.tzinfo is None or expires_at.tzinfo is None or expires_at <= now:
        raise CanonicalReelExecutionError("APPROVAL_EXPIRY_INVALID",
                                          "Approval expiry must be an aware future timestamp.")
    cap = maximum_approved_cost_usd
    cap = plan.cost_envelope.maximum_cost_usd if cap is None else cap
    if not cap.is_finite() or cap < plan.cost_envelope.maximum_cost_usd:
        raise CanonicalReelExecutionError(
            "APPROVAL_COST_CAP_TOO_LOW",
            "Approval cost cap must cover the finite execution cost envelope.")
    values: dict[str, Any] = {
        "approval_id": approval_id or "rea_" + uuid.uuid4().hex,
        "strategy_version_id": plan.strategy_version_id,
        "reel_contract_hash": plan.reel_contract_hash,
        "execution_plan_hash": plan.execution_plan_hash,
        "provider": plan.provider,
        "maximum_approved_cost_usd": cap,
        "issued_at": now,
        "approver_user_id": approver_user_id,
        "expires_at": expires_at,
        "one_shot": True,
    }
    values["authorization_digest"] = hmac.new(
        signing_key, _approval_material(values), hashlib.sha256).hexdigest()
    return ReelExecutionApproval(**values)


def validate_execution_approval(*, approval: ReelExecutionApproval,
                                contract: ReelCreativeContract,
                                plan: ReelExecutionPlan,
                                signing_key: bytes,
                                now: Optional[datetime] = None) -> None:
    expected = hmac.new(signing_key, _approval_material(approval.model_dump(mode="python")),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, approval.authorization_digest):
        raise CanonicalReelExecutionError("APPROVAL_SIGNATURE_INVALID",
                                          "Execution approval signature is invalid.")
    checks = {
        "APPROVAL_STRATEGY_MISMATCH": approval.strategy_version_id == contract.strategy_version_id,
        "APPROVAL_CONTRACT_MISMATCH": approval.reel_contract_hash == contract.contract_hash,
        "APPROVAL_PLAN_MISMATCH": approval.execution_plan_hash == plan.execution_plan_hash,
        "APPROVAL_PROVIDER_MISMATCH": approval.provider == plan.provider,
        "APPROVAL_COST_CAP_EXCEEDED": (
            approval.maximum_approved_cost_usd >= plan.cost_envelope.maximum_cost_usd),
    }
    for code, passed in checks.items():
        if not passed:
            raise CanonicalReelExecutionError(code, "Execution approval does not bind this run.")
    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None or when >= approval.expires_at:
        raise CanonicalReelExecutionError("APPROVAL_EXPIRED", "Execution approval has expired.")


class ApprovalUseRegistry:
    """Process-local one-shot guard; durable consumption belongs to the persistence seam."""

    def __init__(self) -> None:
        self._used: set[str] = set()
        self._lock = threading.Lock()

    def consume(self, approval_id: str) -> None:
        with self._lock:
            if approval_id in self._used:
                raise CanonicalReelExecutionError(
                    "APPROVAL_ALREADY_USED", "One-shot execution approval was already consumed.")
            self._used.add(approval_id)


class AuthorizedExecutionContext(BaseModel):
    """A one-shot approval consumed into one execution session shared by paid boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval: ReelExecutionApproval
    strategy_version_id: str
    reel_contract_hash: str
    execution_plan_hash: str
    provider: ProviderSelection
    maximum_approved_cost_usd: Decimal


def begin_authorized_execution(*, approval: Optional[ReelExecutionApproval],
                               contract: ReelCreativeContract, plan: ReelExecutionPlan,
                               signing_key: bytes, use_registry: ApprovalUseRegistry,
                               now: Optional[datetime] = None) -> AuthorizedExecutionContext:
    if approval is None:
        raise CanonicalReelExecutionError(
            "EXECUTION_APPROVAL_REQUIRED", "Paid execution cannot begin without approval.")
    validate_execution_approval(
        approval=approval, contract=contract, plan=plan, signing_key=signing_key, now=now)
    use_registry.consume(approval.approval_id)
    return AuthorizedExecutionContext(
        approval=approval, strategy_version_id=contract.strategy_version_id,
        reel_contract_hash=contract.contract_hash,
        execution_plan_hash=plan.execution_plan_hash, provider=plan.provider,
        maximum_approved_cost_usd=approval.maximum_approved_cost_usd)


class AuthorizedCostTracker:
    """Reserve each boundary's finite worst case before invoking that provider."""

    def __init__(self) -> None:
        self._total = Decimal("0")
        self._by_category: dict[PaidProviderCategory, Decimal] = {}
        self._lock = threading.Lock()

    @property
    def reserved_total_usd(self) -> Decimal:
        return self._total

    def reserve(self, *, category: PaidProviderCategory, provider: ProviderSelection,
                maximum_call_cost_usd: Decimal,
                plan: ReelExecutionPlan,
                authorization: AuthorizedExecutionContext) -> None:
        if not maximum_call_cost_usd.is_finite() or maximum_call_cost_usd < 0:
            raise CanonicalReelExecutionError(
                "COST_NOT_PROVABLE", "A paid boundary has no finite maximum call cost.")
        matching = [item for item in plan.cost_envelope.items
                    if item.category == category
                    and item.pricing.provider_identity == provider.identity]
        if category not in plan.cost_envelope.planned_categories:
            raise CanonicalReelExecutionError(
                "UNPLANNED_PROVIDER_CATEGORY",
                "The approval does not include this paid provider category.")
        if not matching:
            raise CanonicalReelExecutionError(
                "UNPLANNED_PROVIDER_IDENTITY",
                "The approval does not include this provider, model, and surface for the category.")
        if category == PaidProviderCategory.VIDEO and provider != plan.provider:
            raise CanonicalReelExecutionError(
                "PROVIDER_SWITCH_FORBIDDEN",
                "Video execution cannot switch away from the exact provider decision.")
        category_limit = sum((item.maximum_cost_usd for item in matching), Decimal("0"))
        with self._lock:
            next_category = self._by_category.get(category, Decimal("0")) + maximum_call_cost_usd
            next_total = self._total + maximum_call_cost_usd
            if next_category > category_limit or next_total > authorization.maximum_approved_cost_usd:
                raise CanonicalReelExecutionError(
                    "APPROVAL_COST_CAP_EXCEEDED",
                    "The paid boundary would exceed its approved finite cost envelope.")
            self._by_category[category] = next_category
            self._total = next_total


T = TypeVar("T")


def execute_authorized_paid_boundary(
    provider_call: Callable[[], T], *, authorization: Optional[AuthorizedExecutionContext],
    contract: ReelCreativeContract, plan: ReelExecutionPlan, signing_key: bytes,
    cost_tracker: AuthorizedCostTracker, category: PaidProviderCategory,
    provider: ProviderSelection,
    maximum_call_cost_usd: Decimal, now: Optional[datetime] = None,
) -> T:
    """The sole canonical paid-call seam. Missing or invalid authority stops before the call."""
    if authorization is None:
        raise CanonicalReelExecutionError(
            "EXECUTION_APPROVAL_REQUIRED", "A paid provider cannot run without approval.")
    validate_execution_approval(
        approval=authorization.approval, contract=contract, plan=plan,
        signing_key=signing_key, now=now)
    bindings = (
        authorization.strategy_version_id == contract.strategy_version_id,
        authorization.reel_contract_hash == contract.contract_hash,
        authorization.execution_plan_hash == plan.execution_plan_hash,
        authorization.provider == plan.provider,
    )
    if not all(bindings):
        raise CanonicalReelExecutionError(
            "AUTHORIZED_EXECUTION_MISMATCH",
            "The paid boundary does not belong to this authorized execution.")
    cost_tracker.reserve(
        category=category, provider=provider, maximum_call_cost_usd=maximum_call_cost_usd,
        plan=plan, authorization=authorization)
    return provider_call()


class VerdictDimension(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_ASSESSED = "NOT_ASSESSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TerminalVerdict(str, Enum):
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"


class DefectType(str, Enum):
    STRATEGY_DRIFT = "STRATEGY_DRIFT"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    INTERNAL_EVIDENCE_LEAK = "INTERNAL_EVIDENCE_LEAK"
    CTA_MISMATCH = "CTA_MISMATCH"
    WEAK_HOOK = "WEAK_HOOK"
    PRODUCT_IDENTITY_DRIFT = "PRODUCT_IDENTITY_DRIFT"
    PRODUCT_MUTATION = "PRODUCT_MUTATION"
    BRAND_IDENTITY_DRIFT = "BRAND_IDENTITY_DRIFT"
    SUBJECT_IDENTITY_DRIFT = "SUBJECT_IDENTITY_DRIFT"
    ACTION_BINDING_ERROR = "ACTION_BINDING_ERROR"
    PHYSICS_ANOMALY = "PHYSICS_ANOMALY"
    HUMAN_ANATOMY_ERROR = "HUMAN_ANATOMY_ERROR"
    TEMPORAL_FLICKER = "TEMPORAL_FLICKER"
    SHOT_REDUNDANCY = "SHOT_REDUNDANCY"
    CONTINUITY_BREAK = "CONTINUITY_BREAK"
    AUDIO_SCRIPT_MISMATCH = "AUDIO_SCRIPT_MISMATCH"
    CAPTION_SEMANTIC_DRIFT = "CAPTION_SEMANTIC_DRIFT"
    SAFE_ZONE_VIOLATION = "SAFE_ZONE_VIOLATION"
    PLATFORM_STYLE_MISMATCH = "PLATFORM_STYLE_MISMATCH"
    TERMINAL_VERIFICATION_INCOMPLETE = "TERMINAL_VERIFICATION_INCOMPLETE"
    CREATIVE_CONCEPTS_NOT_DISTINCT = "CREATIVE_CONCEPTS_NOT_DISTINCT"
    NO_VIABLE_CONCEPT = "NO_VIABLE_CONCEPT"
    VALUE_NOT_DEMONSTRATED = "VALUE_NOT_DEMONSTRATED"
    PRODUCT_APPEARS_TOO_LATE = "PRODUCT_APPEARS_TOO_LATE"
    PROPOSITION_TOO_LATE = "PROPOSITION_TOO_LATE"
    VISUAL_METAPHOR_CONFLICTS_WITH_TRUTH = "VISUAL_METAPHOR_CONFLICTS_WITH_TRUTH"
    UNEXECUTABLE_SHOT_PLAN = "UNEXECUTABLE_SHOT_PLAN"
    GENERIC_ASSET_SEQUENCE = "GENERIC_ASSET_SEQUENCE"
    CONCEPT_TOO_GENERIC = "CONCEPT_TOO_GENERIC"
    SHOT_ACTION_UNDERSPECIFIED = "SHOT_ACTION_UNDERSPECIFIED"
    ACTION_BINDING_RISK = "ACTION_BINDING_RISK"
    PHYSICS_RISK = "PHYSICS_RISK"
    MISSING_REQUIRED_REFERENCE = "MISSING_REQUIRED_REFERENCE"
    NO_AUTHORIZED_PROVIDER_FOR_CONCEPT = "NO_AUTHORIZED_PROVIDER_FOR_CONCEPT"
    LANGUAGE_VARIETY_NEEDS_OWNER_CHOICE = "LANGUAGE_VARIETY_NEEDS_OWNER_CHOICE"
    LIVE_PROPOSER_SCHEMA_INVALID = "LIVE_PROPOSER_SCHEMA_INVALID"
    GENERATED_TEXT_CONTAMINATION = "GENERATED_TEXT_CONTAMINATION"


class RepairOwner(str, Enum):
    CONTRACT = "CONTRACT"
    CONCEPT = "CONCEPT"
    ARC = "ARC"
    PLAN = "PLAN"
    SHOT = "SHOT"
    SEED = "SEED"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    COMPOSITOR = "COMPOSITOR"
    PLATFORM_LAYOUT = "PLATFORM_LAYOUT"


DEFECT_REPAIR_OWNER: Mapping[DefectType, RepairOwner] = {
    DefectType.STRATEGY_DRIFT: RepairOwner.CONTRACT,
    DefectType.UNSUPPORTED_CLAIM: RepairOwner.CONTRACT,
    DefectType.INTERNAL_EVIDENCE_LEAK: RepairOwner.CONTRACT,
    DefectType.CTA_MISMATCH: RepairOwner.CONTRACT,
    DefectType.WEAK_HOOK: RepairOwner.CONCEPT,
    DefectType.PRODUCT_IDENTITY_DRIFT: RepairOwner.SEED,
    DefectType.PRODUCT_MUTATION: RepairOwner.VIDEO,
    DefectType.BRAND_IDENTITY_DRIFT: RepairOwner.SEED,
    DefectType.SUBJECT_IDENTITY_DRIFT: RepairOwner.VIDEO,
    DefectType.ACTION_BINDING_ERROR: RepairOwner.SHOT,
    DefectType.PHYSICS_ANOMALY: RepairOwner.VIDEO,
    DefectType.HUMAN_ANATOMY_ERROR: RepairOwner.VIDEO,
    DefectType.TEMPORAL_FLICKER: RepairOwner.VIDEO,
    DefectType.SHOT_REDUNDANCY: RepairOwner.ARC,
    DefectType.CONTINUITY_BREAK: RepairOwner.ARC,
    DefectType.AUDIO_SCRIPT_MISMATCH: RepairOwner.AUDIO,
    DefectType.CAPTION_SEMANTIC_DRIFT: RepairOwner.COMPOSITOR,
    DefectType.SAFE_ZONE_VIOLATION: RepairOwner.PLATFORM_LAYOUT,
    DefectType.PLATFORM_STYLE_MISMATCH: RepairOwner.PLATFORM_LAYOUT,
    DefectType.TERMINAL_VERIFICATION_INCOMPLETE: RepairOwner.COMPOSITOR,
    DefectType.CREATIVE_CONCEPTS_NOT_DISTINCT: RepairOwner.CONCEPT,
    DefectType.NO_VIABLE_CONCEPT: RepairOwner.CONCEPT,
    DefectType.VALUE_NOT_DEMONSTRATED: RepairOwner.ARC,
    DefectType.PRODUCT_APPEARS_TOO_LATE: RepairOwner.ARC,
    DefectType.PROPOSITION_TOO_LATE: RepairOwner.ARC,
    DefectType.VISUAL_METAPHOR_CONFLICTS_WITH_TRUTH: RepairOwner.CONCEPT,
    DefectType.UNEXECUTABLE_SHOT_PLAN: RepairOwner.SHOT,
    DefectType.GENERIC_ASSET_SEQUENCE: RepairOwner.CONCEPT,
    DefectType.CONCEPT_TOO_GENERIC: RepairOwner.CONCEPT,
    DefectType.SHOT_ACTION_UNDERSPECIFIED: RepairOwner.SHOT,
    DefectType.ACTION_BINDING_RISK: RepairOwner.SHOT,
    DefectType.PHYSICS_RISK: RepairOwner.SHOT,
    DefectType.MISSING_REQUIRED_REFERENCE: RepairOwner.SEED,
    DefectType.NO_AUTHORIZED_PROVIDER_FOR_CONCEPT: RepairOwner.CONCEPT,
    DefectType.LANGUAGE_VARIETY_NEEDS_OWNER_CHOICE: RepairOwner.AUDIO,
    DefectType.LIVE_PROPOSER_SCHEMA_INVALID: RepairOwner.CONCEPT,
    DefectType.GENERATED_TEXT_CONTAMINATION: RepairOwner.VIDEO,
}


class ReelDefect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    defect_type: DefectType
    repair_owner: RepairOwner
    detail: str

    @model_validator(mode="after")
    def owner_is_canonical(self) -> "ReelDefect":
        if DEFECT_REPAIR_OWNER[self.defect_type] != self.repair_owner:
            raise ValueError("defect is assigned to the wrong repair owner")
        return self


class FinalReelVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_alignment: VerdictDimension = VerdictDimension.NOT_ASSESSED
    claim_safety: VerdictDimension = VerdictDimension.NOT_ASSESSED
    creative_fitness: VerdictDimension = VerdictDimension.NOT_ASSESSED
    product_fidelity: VerdictDimension = VerdictDimension.NOT_ASSESSED
    brand_fidelity: VerdictDimension = VerdictDimension.NOT_ASSESSED
    human_fidelity: VerdictDimension = VerdictDimension.NOT_ASSESSED
    physical_plausibility: VerdictDimension = VerdictDimension.NOT_ASSESSED
    motion_quality: VerdictDimension = VerdictDimension.NOT_ASSESSED
    temporal_consistency: VerdictDimension = VerdictDimension.NOT_ASSESSED
    cross_scene_continuity: VerdictDimension = VerdictDimension.NOT_ASSESSED
    audio_script_integrity: VerdictDimension = VerdictDimension.NOT_ASSESSED
    caption_integrity: VerdictDimension = VerdictDimension.NOT_ASSESSED
    platform_compliance: VerdictDimension = VerdictDimension.NOT_ASSESSED
    container_integrity: VerdictDimension = VerdictDimension.NOT_ASSESSED
    defects: tuple[ReelDefect, ...] = ()

    def terminal_verdict(self) -> TerminalVerdict:
        dimensions = {field: getattr(self, field) for field in type(self).model_fields
                      if field != "defects"}
        mandatory = {
            "strategy_alignment", "claim_safety", "creative_fitness", "product_fidelity",
            "brand_fidelity", "physical_plausibility", "motion_quality",
            "temporal_consistency", "cross_scene_continuity", "platform_compliance",
            "container_integrity",
        }
        if any(v in {VerdictDimension.FAIL, VerdictDimension.NOT_ASSESSED}
               for v in dimensions.values()):
            return TerminalVerdict.BLOCKED
        if any(dimensions[field] != VerdictDimension.PASS for field in mandatory):
            return TerminalVerdict.BLOCKED
        return TerminalVerdict.PASSED


class CanonicalArtifactCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: GeneratedMode
    video_base64: str = ""
    reel_path: str = ""
    mime_type: str = "video/mp4"


class CanonicalDeliveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal_verdict: TerminalVerdict
    delivered: bool
    video_base64: Optional[str] = None
    reel_path: Optional[str] = None
    mime_type: Optional[str] = None
    failure_code: str = ""
    defects: tuple[ReelDefect, ...] = ()


def terminal_delivery(candidate: CanonicalArtifactCandidate,
                      verdict: FinalReelVerdict) -> CanonicalDeliveryResponse:
    """Never expose successful bytes/path unless every mandatory dimension passed."""
    if candidate.mode != GeneratedMode.FULLY_GENERATED:
        return CanonicalDeliveryResponse(
            terminal_verdict=TerminalVerdict.BLOCKED, delivered=False,
            failure_code=("DEGRADED_PREVIEW_NOT_CANONICAL" if
                          candidate.mode == GeneratedMode.DEGRADED_PREVIEW
                          else "RAW_PHOTO_FALLBACK_NOT_CANONICAL"),
            defects=verdict.defects)
    terminal = verdict.terminal_verdict()
    if terminal != TerminalVerdict.PASSED:
        return CanonicalDeliveryResponse(
            terminal_verdict=TerminalVerdict.BLOCKED, delivered=False,
            failure_code="TERMINAL_VERDICT_BLOCKED", defects=verdict.defects)
    if not candidate.video_base64 and not candidate.reel_path:
        return CanonicalDeliveryResponse(
            terminal_verdict=TerminalVerdict.BLOCKED, delivered=False,
            failure_code="CANONICAL_ARTIFACT_MISSING", defects=verdict.defects)
    return CanonicalDeliveryResponse(
        terminal_verdict=TerminalVerdict.PASSED, delivered=True,
        video_base64=candidate.video_base64 or None,
        reel_path=candidate.reel_path or None, mime_type=candidate.mime_type,
        defects=verdict.defects)


class CanonicalReelPersistence(Protocol):
    """Persistence seam only; no schema or adapter is implemented in Gate 2D.1."""

    def begin_reel_run(self, *, strategy_version_id: str, profile_snapshot_id: str,
                       contract: Mapping[str, Any]) -> Mapping[str, str]: ...
    def record_plan(self, *, run_id: str, plan: Mapping[str, Any]) -> None: ...
    def record_approval(self, *, run_id: str, approval: Mapping[str, Any]) -> None: ...
    def record_attempt(self, *, run_id: str, attempt: Mapping[str, Any]) -> str: ...
    def record_scene_artifact(self, *, attempt_id: str, scene_id: str,
                              artifact: Mapping[str, Any]) -> None: ...
    def record_qa(self, *, attempt_id: str, verdict: Mapping[str, Any]) -> None: ...
    def record_cost(self, *, attempt_id: str, cost: Mapping[str, Any]) -> None: ...
    def record_final_asset(self, *, attempt_id: str, asset: Mapping[str, Any]) -> str: ...
    def link_gallery(self, *, asset_id: str, user_id: str) -> str: ...


__all__ = [
    "ApprovalUseRegistry", "AuthorizedCostTracker", "AuthorizedExecutionContext",
    "CANONICAL_PROVIDER_REGISTRY", "CanonicalArtifactCandidate",
    "CanonicalDeliveryResponse", "CanonicalReelExecutionError",
    "CanonicalProviderConstraints",
    "CanonicalReelExecutionPreferences", "CanonicalReelPersistence",
    "CanonicalReelPreparation", "DEFECT_REPAIR_OWNER", "DefectType", "FinalReelVerdict",
    "GEMINI_API_VEO_PRICING_2026_08_23", "GEMINI_OMNI_FLASH_EXPERIMENTAL",
    "GeneratedMode", "PaidProviderCategory", "PlannedProviderCost", "PricingUnit",
    "PricingVerificationStatus", "ProviderAuthorizationStatus", "ProviderBillingMode",
    "ProviderPricing", "ProviderSelection", "ProviderSurface",
    "ReelCostEnvelope", "ReelDefect", "ReelExecutionApproval", "ReelExecutionPlan",
    "ReelExecutionState", "ReelExecutionStateRecord", "RepairOwner",
    "RequiredVideoCapabilities", "TEXT_PROVIDER_PRICING_REGISTRY", "TerminalVerdict",
    "TextProviderPricingSnapshot", "VERTEX_GEMINI_25_FLASH_STANDARD_PRICING_2026_08_23",
    "VERTEX_VEO_31", "VERTEX_VEO_31_FAST",
    "VerdictDimension", "VideoProviderCapabilities", "begin_authorized_execution",
    "build_cost_envelope",
    "build_execution_plan", "execute_authorized_paid_boundary", "issue_execution_approval",
    "prepare_canonical_reel", "resolve_text_provider_pricing", "select_canonical_provider",
    "terminal_delivery",
    "require_provable_provider_cost", "validate_constraint_preserving_retry",
    "validate_execution_approval",
]
