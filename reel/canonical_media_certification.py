"""Gate 2D.4C: narrow, offline media-certification repair.

The records in this module are reviewed snapshots.  Importing or evaluating them performs no
network request, provider generation, credential read, or database operation.  In particular,
the public Google Cloud SKU table observation is *not* promoted into a Cloud Billing Catalog API
pricingExpression: the former displays ``count`` while canonical Reel arithmetic needs an exact
per-second billing unit.
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical_media_preconditions import (
    MediaBillingUnit,
    MediaOutputModality,
    MediaPreconditionError,
    ProductSurface,
    ProviderSurfaceIdentity,
    VERTEX_GEMINI_25_FLASH_QA_IDENTITY,
    VERTEX_VEO_31_FAST_IDENTITY,
    VERTEX_VEO_31_IDENTITY,
    VideoAudioMode,
    stable_hash,
)


REVIEWED_DATE = "2026-08-24"
VERTEX_PRICING_REQUESTED_URL = "https://cloud.google.com/vertex-ai/generative-ai/pricing"
GENERATIVE_AI_PRICING_REDIRECT_URL = (
    "https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing")
VERTEX_VEO_31_ANNOUNCEMENT_URL = (
    "https://cloud.google.com/blog/products/ai-machine-learning/"
    "veo-3-1-lite-and-a-new-veo-upscaling-capability-on-vertex-ai")
VERTEX_RELEASE_NOTES_URL = "https://docs.cloud.google.com/vertex-ai/docs/release-notes"
GOOGLE_CLOUD_SKUS_URL = "https://cloud.google.com/skus"
GOOGLE_GEN_AI_SKU_GROUP_URL = "https://cloud.google.com/skus/sku-groups/gen-ai"
VERTEX_BILLING_SERVICE_ID = "C7E2-9256-1C43"


class PricingCertificationStatus(str, Enum):
    CATALOG_REQUIRED = "CATALOG_REQUIRED"
    CERTIFIED = "CERTIFIED"
    UNRESOLVED = "UNRESOLVED"


class SurfacePricingProvenance(BaseModel):
    """The redirected page stays tied to its official Vertex referrer without aliasing surfaces."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_official_url: str
    final_redirect_url: str
    referring_official_product: str
    referring_source_url: str
    redirect_verified_date: str
    billing_catalog_service: str
    sku_ids: tuple[str, ...]
    pricing_status: PricingCertificationStatus
    evidence_hash: str

    @model_validator(mode="after")
    def exact_vertex_chain(self) -> "SurfacePricingProvenance":
        if self.requested_official_url != VERTEX_PRICING_REQUESTED_URL:
            raise ValueError("pricing provenance must begin at the official Vertex pricing URL")
        if self.final_redirect_url != GENERATIVE_AI_PRICING_REDIRECT_URL:
            raise ValueError("pricing provenance must preserve the observed redirect destination")
        if self.referring_official_product != "Vertex AI" \
                or self.referring_source_url != VERTEX_VEO_31_ANNOUNCEMENT_URL:
            raise ValueError("an arbitrary page or product cannot create a Vertex pricing alias")
        if self.billing_catalog_service != VERTEX_BILLING_SERVICE_ID:
            raise ValueError("billing catalog service identity changed")
        material = self.model_dump(mode="json", exclude={"evidence_hash"})
        if self.evidence_hash != stable_hash(material):
            raise ValueError("pricing provenance hash mismatch")
        return self


def _surface_pricing_provenance() -> SurfacePricingProvenance:
    values = {
        "requested_official_url": VERTEX_PRICING_REQUESTED_URL,
        "final_redirect_url": GENERATIVE_AI_PRICING_REDIRECT_URL,
        "referring_official_product": "Vertex AI",
        "referring_source_url": VERTEX_VEO_31_ANNOUNCEMENT_URL,
        "redirect_verified_date": REVIEWED_DATE,
        "billing_catalog_service": VERTEX_BILLING_SERVICE_ID,
        "sku_ids": (
            "4D50-275D-FC3F", "F64B-2A78-7BAE", "7DE3-56E5-CE6C",
            "A9F8-76D1-003D", "D679-F281-7C1A", "A0A5-329E-CDBE",
            "EBB6-730E-171A", "A88E-25E5-8E63", "083C-91E7-7B9A",
            "3653-1168-5D76",
        ),
        "pricing_status": PricingCertificationStatus.CATALOG_REQUIRED,
    }
    return SurfacePricingProvenance(**values, evidence_hash=stable_hash(values))


VERTEX_SURFACE_PRICING_PROVENANCE = _surface_pricing_provenance()


class BillingEvidenceAuthority(str, Enum):
    CLOUD_BILLING_CATALOG_API_PRICING_EXPRESSION = (
        "CLOUD_BILLING_CATALOG_API_PRICING_EXPRESSION")
    PUBLIC_GOOGLE_CLOUD_SKU_TABLE = "PUBLIC_GOOGLE_CLOUD_SKU_TABLE"


class BillingModelMappingStatus(str, Enum):
    CERTIFIED = "CERTIFIED"
    CORROBORATED_BUT_NOT_EXACT = "CORROBORATED_BUT_NOT_EXACT"
    UNRESOLVED = "UNRESOLVED"


class BillingCatalogSkuEvidence(BaseModel):
    """Exact observed billing identity; absent API fields remain absent, never inferred."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service_id: str
    sku_id: str
    description: str
    authority: BillingEvidenceAuthority
    display_unit: str
    usage_unit: Optional[str] = None
    base_unit: Optional[str] = None
    base_unit_conversion_factor: Optional[Decimal] = None
    unit_price: Decimal
    currency: str
    effective_time: Optional[str] = None
    regions: tuple[str, ...]
    source_url: str
    observed_date: str
    canonical_billing_unit: Optional[MediaBillingUnit] = None
    mapping_status: BillingModelMappingStatus

    @field_validator("unit_price", "base_unit_conversion_factor")
    @classmethod
    def finite_nonnegative(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("billing catalog numbers must be finite and non-negative")
        return value

    @model_validator(mode="after")
    def second_requires_catalog_semantics(self) -> "BillingCatalogSkuEvidence":
        if self.service_id != VERTEX_BILLING_SERVICE_ID:
            raise ValueError("SKU evidence is not from the Vertex AI billing service")
        if self.currency != "USD" or not self.source_url.startswith("https://cloud.google.com/"):
            raise ValueError("SKU evidence requires the official USD Cloud source")
        if self.canonical_billing_unit == MediaBillingUnit.SECOND:
            usage = (self.usage_unit or "").strip().casefold()
            if self.authority != BillingEvidenceAuthority.CLOUD_BILLING_CATALOG_API_PRICING_EXPRESSION:
                raise ValueError("a public table display cannot certify SECOND")
            if usage not in {"s", "sec", "second", "seconds"}:
                raise ValueError("catalog usageUnit does not prove seconds")
            if self.base_unit is None or self.base_unit_conversion_factor is None:
                raise ValueError("SECOND needs catalog base-unit semantics")
            if self.mapping_status != BillingModelMappingStatus.CERTIFIED:
                raise ValueError("SECOND pricing cannot outpace model-to-SKU certification")
        return self

    @property
    def canonical_price_certified(self) -> bool:
        return (
            self.canonical_billing_unit == MediaBillingUnit.SECOND
            and self.mapping_status == BillingModelMappingStatus.CERTIFIED
        )


def _public_vertex_sku(sku_id: str, description: str, price: str) -> BillingCatalogSkuEvidence:
    return BillingCatalogSkuEvidence(
        service_id=VERTEX_BILLING_SERVICE_ID, sku_id=sku_id, description=description,
        authority=BillingEvidenceAuthority.PUBLIC_GOOGLE_CLOUD_SKU_TABLE,
        display_unit="count", unit_price=Decimal(price), currency="USD",
        regions=("global",), source_url=GOOGLE_CLOUD_SKUS_URL,
        observed_date=REVIEWED_DATE,
        mapping_status=BillingModelMappingStatus.CORROBORATED_BUT_NOT_EXACT,
    )


CURRENT_VERTEX_VEO_SKUS = (
    _public_vertex_sku("4D50-275D-FC3F", "Veo 3 Video Generation", "0.20"),
    _public_vertex_sku("F64B-2A78-7BAE", "Veo 3 Audio Video Generation", "0.40"),
    _public_vertex_sku("7DE3-56E5-CE6C", "Veo 3 Fast Video 720p Generation", "0.08"),
    _public_vertex_sku("A9F8-76D1-003D", "Veo 3 Fast 720p Audio Video Generation", "0.10"),
    _public_vertex_sku("D679-F281-7C1A", "Veo 3 Fast 1080p Video Generation", "0.10"),
    _public_vertex_sku("A0A5-329E-CDBE", "Veo 3 Fast 1080p Audio Video Generation", "0.12"),
    _public_vertex_sku("EBB6-730E-171A", "Veo 3 4k Video Generation", "0.40"),
    _public_vertex_sku("A88E-25E5-8E63", "Veo 3 4k Audio Video Generation", "0.60"),
    _public_vertex_sku("083C-91E7-7B9A", "Veo 3 Fast 4k Video Generation", "0.25"),
    _public_vertex_sku("3653-1168-5D76", "Veo 3 Fast 4k Audio Video Generation", "0.30"),
)


class BillingSkuRegistry:
    def __init__(self, evidence: Sequence[BillingCatalogSkuEvidence]) -> None:
        self._items: dict[tuple[str, str], BillingCatalogSkuEvidence] = {}
        for item in evidence:
            key = (item.service_id, item.sku_id)
            if key in self._items:
                raise ValueError("duplicate billing service/SKU identity")
            self._items[key] = item

    def resolve(self, *, service_id: str, sku_id: str) -> Optional[BillingCatalogSkuEvidence]:
        return self._items.get((service_id, sku_id))

    @property
    def evidence(self) -> tuple[BillingCatalogSkuEvidence, ...]:
        return tuple(self._items.values())


VERTEX_VEO_SKU_REGISTRY = BillingSkuRegistry(CURRENT_VERTEX_VEO_SKUS)


class BillingModelMappingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_model_id: str
    service_id: str
    candidate_sku_ids: tuple[str, ...]
    official_evidence_urls: tuple[str, ...]
    status: BillingModelMappingStatus
    reason: str
    evidence_hash: str

    @model_validator(mode="after")
    def exact_and_hashed(self) -> "BillingModelMappingEvidence":
        if self.service_id != VERTEX_BILLING_SERVICE_ID:
            raise ValueError("model mapping changed billing service")
        if not all(url.startswith("https://cloud.google.com/")
                   or url.startswith("https://docs.cloud.google.com/")
                   for url in self.official_evidence_urls):
            raise ValueError("model mapping requires official Google Cloud evidence")
        material = self.model_dump(mode="json", exclude={"evidence_hash"})
        if self.evidence_hash != stable_hash(material):
            raise ValueError("model mapping evidence hash mismatch")
        return self


def _mapping(model_id: str, sku_ids: tuple[str, ...]) -> BillingModelMappingEvidence:
    values = {
        "runtime_model_id": model_id,
        "service_id": VERTEX_BILLING_SERVICE_ID,
        "candidate_sku_ids": sku_ids,
        "official_evidence_urls": (
            VERTEX_RELEASE_NOTES_URL, VERTEX_VEO_31_ANNOUNCEMENT_URL,
            VERTEX_PRICING_REQUESTED_URL, GOOGLE_GEN_AI_SKU_GROUP_URL,
        ),
        "status": BillingModelMappingStatus.CORROBORATED_BUT_NOT_EXACT,
        "reason": (
            "Vertex release history, the Vertex-referred pricing page, and Vertex billing SKUs "
            "corroborate the Veo 3 family. The SKU description does not name the 3.1 runtime "
            "model and no pricingExpression usage unit was obtained; exact mapping is not certified."),
    }
    return BillingModelMappingEvidence(**values, evidence_hash=stable_hash(values))


VERTEX_VEO_31_MODEL_MAPPING = _mapping(
    "veo-3.1-generate-001", ("4D50-275D-FC3F", "F64B-2A78-7BAE"))
VERTEX_VEO_31_FAST_MODEL_MAPPING = _mapping(
    "veo-3.1-fast-generate-001",
    ("7DE3-56E5-CE6C", "A9F8-76D1-003D", "D679-F281-7C1A", "A0A5-329E-CDBE"))


class VertexPricingRepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: PricingCertificationStatus
    api_access_status: str
    public_service_verified: bool
    public_sku_ids_verified: tuple[str, ...]
    vertex_lite_sku_status: str
    exact_missing_evidence: tuple[str, ...]


VERTEX_PRICING_REPAIR_RESULT = VertexPricingRepairResult(
    state=PricingCertificationStatus.UNRESOLVED,
    api_access_status=(
        "NO_GCLOUD_OR_ADC; EXISTING_API_KEY_NOT_TRANSMITTED_AFTER_CREDENTIAL_SAFETY_BLOCK; "
        "PUBLIC_SKU_TABLE_REVIEWED_READ_ONLY"),
    public_service_verified=True,
    public_sku_ids_verified=tuple(item.sku_id for item in CURRENT_VERTEX_VEO_SKUS),
    vertex_lite_sku_status=(
        "NO_VERTEX_AI_LITE_SKU_IN_FILTERED_PUBLIC_RESULTS; A GEMINI_API LITE SKU EXISTS BUT IS "
        "A DIFFERENT SERVICE AND CANNOT PRICE VERTEX"),
    exact_missing_evidence=(
        "Cloud Billing Catalog API pricingExpression.usageUnit for each selected Vertex SKU",
        "Cloud Billing Catalog API baseUnit and baseUnitConversionFactor",
        "Cloud Billing Catalog API effectiveTime for the selected USD price",
        "CERTIFIED mapping from veo-3.1[-fast]-generate-001 to the exact Vertex SKU",
    ),
)


class IntegrationCapabilityState(str, Enum):
    GA_SUPPORTED = "GA_SUPPORTED"
    UNRESOLVED_FOR_CURRENT_GA_SURFACE = "UNRESOLVED_FOR_CURRENT_GA_SURFACE"
    PREVIEW_HISTORY_PRESENT_BUT_NOT_AUTHORIZED = (
        "PREVIEW_HISTORY_PRESENT_BUT_NOT_AUTHORIZED")
    MODEL_CAPABILITY_SUPPORTED_POLICY_DISABLED = (
        "MODEL_CAPABILITY_SUPPORTED_POLICY_DISABLED")
    PREVIEW_CANDIDATE = "PREVIEW_CANDIDATE"


class AdzyAudioExecutionPolicy(str, Enum):
    VIDEO_ONLY = "VIDEO_ONLY"


class VeoCapabilityReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: ProviderSurfaceIdentity
    core_capabilities: tuple[str, ...]
    reference_images: IntegrationCapabilityState
    video_extension: IntegrationCapabilityState
    model_native_audio: IntegrationCapabilityState
    adzy_audio_execution_policy: AdzyAudioExecutionPolicy
    reference_images_execution_enabled: bool = False
    video_extension_execution_enabled: bool = False
    native_marketing_speech_enabled: bool = False
    official_sources: tuple[str, ...]

    @model_validator(mode="after")
    def unresolved_and_preview_features_stay_disabled(self) -> "VeoCapabilityReconciliation":
        if self.reference_images != IntegrationCapabilityState.UNRESOLVED_FOR_CURRENT_GA_SURFACE:
            raise ValueError("current GA reference-image status was overclaimed")
        if self.video_extension != \
                IntegrationCapabilityState.PREVIEW_HISTORY_PRESENT_BUT_NOT_AUTHORIZED:
            raise ValueError("extension preview history was collapsed into GA")
        if self.reference_images_execution_enabled or self.video_extension_execution_enabled:
            raise ValueError("unresolved/preview capabilities cannot be executed")
        if self.model_native_audio == \
                IntegrationCapabilityState.MODEL_CAPABILITY_SUPPORTED_POLICY_DISABLED \
                and self.adzy_audio_execution_policy != AdzyAudioExecutionPolicy.VIDEO_ONLY:
            raise ValueError("model audio capability silently changed Adzy policy")
        return self


def _capability(identity: ProviderSurfaceIdentity) -> VeoCapabilityReconciliation:
    return VeoCapabilityReconciliation(
        identity=identity,
        core_capabilities=(
            "TEXT_TO_VIDEO", "IMAGE_TO_VIDEO", "FIRST_LAST_FRAME", "ASPECT_9_16",
            "ASPECT_16_9", "DURATION_4_6_8", "RESOLUTION_720P_1080P"),
        reference_images=IntegrationCapabilityState.UNRESOLVED_FOR_CURRENT_GA_SURFACE,
        video_extension=(
            IntegrationCapabilityState.PREVIEW_HISTORY_PRESENT_BUT_NOT_AUTHORIZED),
        model_native_audio=(
            IntegrationCapabilityState.MODEL_CAPABILITY_SUPPORTED_POLICY_DISABLED),
        adzy_audio_execution_policy=AdzyAudioExecutionPolicy.VIDEO_ONLY,
        official_sources=(
            VERTEX_RELEASE_NOTES_URL, VERTEX_VEO_31_ANNOUNCEMENT_URL,
            "https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/"
            "generate-videos-from-first-and-last-frames",
        ),
    )


VERTEX_VEO_31_CAPABILITY_RECONCILIATION = _capability(VERTEX_VEO_31_IDENTITY)
VERTEX_VEO_31_FAST_CAPABILITY_RECONCILIATION = _capability(VERTEX_VEO_31_FAST_IDENTITY)


VERTEX_VEO_31_LITE_IDENTITY = VERTEX_VEO_31_IDENTITY.model_copy(update={
    "model_id": "veo-3.1-lite-generate-001",
    "billing_mode": "RUNTIME_SKU_NOT_PROVEN_PREVIEW",
    "capability_version": "vertex-preview-candidate-2026-08-24",
    "pricing_version": "COST_NOT_PROVABLE",
})


class VeoLiteProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: ProviderSurfaceIdentity
    lifecycle_state: IntegrationCapabilityState
    routing_authorized: bool
    canonical_default: bool
    local_sdk_metadata_result: str
    official_source: str


VERTEX_VEO_31_LITE_PROFILE = VeoLiteProfile(
    identity=VERTEX_VEO_31_LITE_IDENTITY,
    lifecycle_state=IntegrationCapabilityState.PREVIEW_CANDIDATE,
    routing_authorized=False, canonical_default=False,
    local_sdk_metadata_result=(
        "google-genai 2.8.0 accepts the model id through the same generic offline "
        "GenerateVideosConfig serialization; no runtime capability or price is inferred"),
    official_source=VERTEX_RELEASE_NOTES_URL,
)


class QAPurpose(str, Enum):
    SEED_QA = "SEED_QA"
    SHOT_VIDEO_QA = "SHOT_VIDEO_QA"
    FINAL_REEL_QA = "FINAL_REEL_QA"
    VOICE_VERIFICATION = "VOICE_VERIFICATION"


class QAInputRateKind(str, Enum):
    TEXT_IMAGE_VIDEO = "TEXT_IMAGE_VIDEO"
    AUDIO = "AUDIO"


class QALifecycleState(str, Enum):
    AVAILABLE_WITH_SCHEDULED_RETIREMENT = "AVAILABLE_WITH_SCHEDULED_RETIREMENT"


class QAPricingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: ProviderSurfaceIdentity
    currency: str = "USD"
    billing_unit: MediaBillingUnit = MediaBillingUnit.PER_1M_TOKENS
    text_image_video_input_usd_per_million: Decimal
    audio_input_usd_per_million: Decimal
    text_response_reasoning_output_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal
    pricing_source: str
    pricing_verified_date: str
    pricing_version: str

    @model_validator(mode="after")
    def exact_vertex_standard(self) -> "QAPricingSnapshot":
        if self.identity != VERTEX_GEMINI_25_FLASH_QA_IDENTITY:
            raise ValueError("QA pricing belongs to a different provider surface")
        if self.currency != "USD" or self.billing_unit != MediaBillingUnit.PER_1M_TOKENS:
            raise ValueError("QA pricing unit changed")
        if self.audio_input_usd_per_million == self.text_image_video_input_usd_per_million:
            raise ValueError("audio pricing cannot reuse the generic multimodal rate")
        for value in (
            self.text_image_video_input_usd_per_million,
            self.audio_input_usd_per_million,
            self.text_response_reasoning_output_usd_per_million,
            self.cached_input_usd_per_million,
        ):
            if not value.is_finite() or value < 0:
                raise ValueError("QA token prices must be finite")
        return self


VERTEX_GEMINI_25_FLASH_QA_PRICING = QAPricingSnapshot(
    identity=VERTEX_GEMINI_25_FLASH_QA_IDENTITY,
    text_image_video_input_usd_per_million=Decimal("0.30"),
    audio_input_usd_per_million=Decimal("1.00"),
    text_response_reasoning_output_usd_per_million=Decimal("2.50"),
    cached_input_usd_per_million=Decimal("0.03"),
    pricing_source=VERTEX_PRICING_REQUESTED_URL,
    pricing_verified_date=REVIEWED_DATE,
    pricing_version="vertex-gemini-2.5-flash-standard-media-QA-2026-08-24",
)


class QAProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: ProviderSurfaceIdentity
    roles: tuple[QAPurpose, ...]
    supports_structured_output: bool
    supports_image: bool
    supports_video: bool
    supports_audio: bool
    lifecycle_state: QALifecycleState
    retirement_date: str
    replacement_required_before: str
    contracts_provider_neutral: bool
    execution_called: bool = False

    @model_validator(mode="after")
    def migration_is_required_before_retirement(self) -> "QAProviderProfile":
        if date.fromisoformat(self.replacement_required_before) >= \
                date.fromisoformat(self.retirement_date):
            raise ValueError("QA replacement must be required before retirement")
        if self.execution_called:
            raise ValueError("Gate 2D.4C cannot call a QA provider")
        return self


CANONICAL_QA_PROVIDER = QAProviderProfile(
    identity=VERTEX_GEMINI_25_FLASH_QA_IDENTITY,
    roles=tuple(QAPurpose),
    supports_structured_output=True, supports_image=True, supports_video=True,
    supports_audio=True,
    lifecycle_state=QALifecycleState.AVAILABLE_WITH_SCHEDULED_RETIREMENT,
    retirement_date="2026-10-16", replacement_required_before="2026-10-01",
    contracts_provider_neutral=True,
)


class QATokenEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: QAPurpose
    input_rate_kind: QAInputRateKind
    max_input_tokens: int = Field(gt=0, le=200_000)
    max_output_tokens: int = Field(gt=0, le=16_384)
    max_reasoning_tokens: int = Field(ge=0, le=32_768)
    maximum_calls: int = Field(gt=0, le=16)
    maximum_media_inputs: int = Field(gt=0, le=16)
    maximum_media_duration_seconds: int = Field(ge=0, le=60)
    provider_count_tokens_required_before_call: bool = True
    pricing_version: str
    bound_reason: str

    @property
    def maximum_billable_output_tokens(self) -> int:
        return self.max_output_tokens + self.max_reasoning_tokens


CANONICAL_QA_TOKEN_ENVELOPES = (
    QATokenEnvelope(
        purpose=QAPurpose.SEED_QA, input_rate_kind=QAInputRateKind.TEXT_IMAGE_VIDEO,
        max_input_tokens=8_192, max_output_tokens=1_024, max_reasoning_tokens=2_048,
        maximum_calls=8, maximum_media_inputs=1, maximum_media_duration_seconds=0,
        pricing_version=VERTEX_GEMINI_25_FLASH_QA_PRICING.pricing_version,
        bound_reason=(
            "At most eight canonical seed images; each call receives one image, the compact "
            "ShotContract constraints, and a bounded structured verdict schema.")),
    QATokenEnvelope(
        purpose=QAPurpose.SHOT_VIDEO_QA, input_rate_kind=QAInputRateKind.TEXT_IMAGE_VIDEO,
        max_input_tokens=16_384, max_output_tokens=1_536, max_reasoning_tokens=3_072,
        maximum_calls=8, maximum_media_inputs=1, maximum_media_duration_seconds=8,
        pricing_version=VERTEX_GEMINI_25_FLASH_QA_PRICING.pricing_version,
        bound_reason=(
            "At most eight shots; one <=8-second video per call plus sampled-frame/constraint "
            "metadata and a bounded temporal-defect verdict.")),
    QATokenEnvelope(
        purpose=QAPurpose.FINAL_REEL_QA, input_rate_kind=QAInputRateKind.TEXT_IMAGE_VIDEO,
        max_input_tokens=32_768, max_output_tokens=2_048, max_reasoning_tokens=4_096,
        maximum_calls=1, maximum_media_inputs=1, maximum_media_duration_seconds=60,
        pricing_version=VERTEX_GEMINI_25_FLASH_QA_PRICING.pricing_version,
        bound_reason=(
            "One composed Reel up to the canonical 60-second product limit, including overlays, "
            "audio, and one structured final-delivery verdict.")),
    QATokenEnvelope(
        purpose=QAPurpose.VOICE_VERIFICATION, input_rate_kind=QAInputRateKind.AUDIO,
        max_input_tokens=8_192, max_output_tokens=1_024, max_reasoning_tokens=2_048,
        maximum_calls=2, maximum_media_inputs=1, maximum_media_duration_seconds=60,
        pricing_version=VERTEX_GEMINI_25_FLASH_QA_PRICING.pricing_version,
        bound_reason=(
            "One <=60-second VO track and at most one verification retry; output is a compact "
            "structured transcript plus exact/semantic comparison.")),
)
QA_TOKEN_ENVELOPE_BY_PURPOSE = {item.purpose: item for item in CANONICAL_QA_TOKEN_ENVELOPES}


def require_input_within_provider_count(*, envelope: QATokenEnvelope,
                                        actual_input_tokens: int) -> None:
    """Pre-generation gate fed by the provider token-count mechanism."""
    if actual_input_tokens < 0:
        raise MediaPreconditionError("INVALID_TOKEN_COUNT", "token count cannot be negative")
    if actual_input_tokens > envelope.max_input_tokens:
        raise MediaPreconditionError(
            "QA_INPUT_TOKEN_BOUND_EXCEEDED",
            f"{envelope.purpose.value} input has {actual_input_tokens} tokens; "
            f"authorized maximum is {envelope.max_input_tokens}")


class QAPurposeCost(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: QAPurpose
    input_cost_usd: Decimal
    output_and_reasoning_cost_usd: Decimal
    worst_case_cost_usd: Decimal
    maximum_calls: int
    pricing_version: str


def calculate_QA_worst_case_cost(
    envelope: QATokenEnvelope,
    pricing: QAPricingSnapshot = VERTEX_GEMINI_25_FLASH_QA_PRICING,
) -> QAPurposeCost:
    input_rate = (
        pricing.audio_input_usd_per_million
        if envelope.input_rate_kind == QAInputRateKind.AUDIO
        else pricing.text_image_video_input_usd_per_million)
    input_cost = (Decimal(envelope.max_input_tokens) * input_rate
                  / Decimal("1000000") * envelope.maximum_calls)
    output_cost = (Decimal(envelope.maximum_billable_output_tokens)
                   * pricing.text_response_reasoning_output_usd_per_million
                   / Decimal("1000000") * envelope.maximum_calls)
    return QAPurposeCost(
        purpose=envelope.purpose, input_cost_usd=input_cost,
        output_and_reasoning_cost_usd=output_cost,
        worst_case_cost_usd=input_cost + output_cost,
        maximum_calls=envelope.maximum_calls, pricing_version=pricing.pricing_version)


CANONICAL_QA_WORST_CASE_COSTS = tuple(
    calculate_QA_worst_case_cost(item) for item in CANONICAL_QA_TOKEN_ENVELOPES)
CANONICAL_QA_TOTAL_WORST_CASE_USD = sum(
    (item.worst_case_cost_usd for item in CANONICAL_QA_WORST_CASE_COSTS), Decimal("0"))


class VoiceVerificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_role: str
    current_implementation: ProviderSurfaceIdentity
    flow: tuple[str, ...]
    maximum_voice_duration_seconds: int
    maximum_verification_retries: int
    token_envelope: QATokenEnvelope
    pricing_proven: bool
    provider_called: bool = False


CANONICAL_VOICE_VERIFICATION_PLAN = VoiceVerificationPlan(
    provider_role="VOICE_VERIFICATION",
    current_implementation=VERTEX_GEMINI_25_FLASH_QA_IDENTITY,
    flow=("AUDIO_INPUT", "STRUCTURED_TRANSCRIPTION", "EXACT_SCRIPT_COMPARISON",
          "SEMANTIC_SCRIPT_COMPARISON", "TYPED_VERDICT"),
    maximum_voice_duration_seconds=60, maximum_verification_retries=1,
    token_envelope=QA_TOKEN_ENVELOPE_BY_PURPOSE[QAPurpose.VOICE_VERIFICATION],
    pricing_proven=True, provider_called=False,
)


class PlatformAssetReviewStatus(str, Enum):
    OFFICIAL_TEMPLATE_STORED = "OFFICIAL_TEMPLATE_STORED"
    PREVIEW_ONLY = "PREVIEW_ONLY"
    VERIFIED_GEOMETRY = "VERIFIED_GEOMETRY"


class SafeZoneAssetProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str
    platform: str
    placement: str
    aspect_ratio: str
    language_direction: str
    official_page_url: str
    direct_asset_url: str = ""
    retrieved_timestamp: str = ""
    original_filename: str
    content_type: str = ""
    local_asset_path: str = ""
    sha256: str = ""
    review_status: PlatformAssetReviewStatus
    deterministic_geometry_available: bool = False
    notes: str

    @model_validator(mode="after")
    def storage_requires_complete_provenance(self) -> "SafeZoneAssetProvenance":
        if not self.official_page_url.startswith("https://"):
            raise ValueError("safe-zone asset requires an official HTTPS page")
        stored = self.review_status in {
            PlatformAssetReviewStatus.OFFICIAL_TEMPLATE_STORED,
            PlatformAssetReviewStatus.VERIFIED_GEOMETRY,
        }
        if stored:
            if not all((self.direct_asset_url, self.retrieved_timestamp,
                        self.original_filename, self.content_type, self.local_asset_path)) \
                    or len(self.sha256) != 64:
                raise ValueError("stored platform asset lacks checksum/provenance")
        elif self.local_asset_path or self.sha256 or self.retrieved_timestamp:
            raise ValueError("preview-only asset cannot claim local retrieval")
        if self.review_status == PlatformAssetReviewStatus.VERIFIED_GEOMETRY \
                and not self.deterministic_geometry_available:
            raise ValueError("verified geometry requires deterministic extraction")
        if self.review_status != PlatformAssetReviewStatus.VERIFIED_GEOMETRY \
                and self.deterministic_geometry_available:
            raise ValueError("template/preview cannot silently become geometry")
        return self

    def verify_local_copy(self, repository_root: Path) -> bool:
        if self.review_status == PlatformAssetReviewStatus.PREVIEW_ONLY:
            return False
        root = repository_root.resolve()
        candidate = (root / self.local_asset_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise MediaPreconditionError(
                "SAFE_ZONE_PATH_OUTSIDE_REPOSITORY", self.asset_id) from exc
        if not candidate.is_file():
            return False
        return hashlib.sha256(candidate.read_bytes()).hexdigest() == self.sha256


META_REELS_SAFE_ZONE_ASSET = SafeZoneAssetProvenance(
    asset_id="META_REELS_SAFE_ZONE_CHECKER_REVIEW",
    platform="INSTAGRAM_REELS", placement="REELS", aspect_ratio="9:16",
    language_direction="DIRECTION_NEUTRAL_TEMPLATE",
    official_page_url="https://www.facebook.com/business/ads/facebook-instagram-reels-ads",
    original_filename="Reels safe-zone checker (PPT/PSD/Keynote; direct file unavailable)",
    review_status=PlatformAssetReviewStatus.PREVIEW_ONLY,
    deterministic_geometry_available=False,
    notes=(
        "Official page advertises PPT/PSD/Keynote templates, but the current direct download was "
        "not safely obtainable without an authenticated/working file link. No geometry inferred."),
)

TIKTOK_SAFE_ZONE_PAGE = (
    "https://ads.us.tiktok.com/resources/help/article/"
    "tiktok-reservation-in-feed-ads-reach-frequency")
TIKTOK_LTR_SAFE_ZONE_ASSET = SafeZoneAssetProvenance(
    asset_id="TIKTOK_IN_FEED_STANDARD_LTR_REVIEW", platform="TIKTOK",
    placement="IN_FEED", aspect_ratio="9:16", language_direction="LTR",
    official_page_url=TIKTOK_SAFE_ZONE_PAGE,
    direct_asset_url=(
        "https://lf19-cdn-tos.tiktokcdn-us.com/obj/i18nblog-tx/tt4b_cms/en-US/"
        "5jqet0ab9qci-G00kdgWsqLxYFXtKguBTi.zip"),
    original_filename="In-Feed - Standard Version LTR.zip",
    content_type="application/zip", review_status=PlatformAssetReviewStatus.PREVIEW_ONLY,
    deterministic_geometry_available=False,
    notes=(
        "Official page exposes a distinct LTR file, but its current CDN key returned not found at "
        "retrieval time. Nothing was stored and no geometry inferred."),
)
TIKTOK_RTL_SAFE_ZONE_ASSET = SafeZoneAssetProvenance(
    asset_id="TIKTOK_IN_FEED_ARABIC_RTL_REVIEW", platform="TIKTOK",
    placement="IN_FEED", aspect_ratio="9:16", language_direction="RTL_ARABIC",
    official_page_url=TIKTOK_SAFE_ZONE_PAGE,
    direct_asset_url=(
        "https://lf19-cdn-tos.tiktokcdn-us.com/obj/i18nblog-tx/tt4b_cms/en-US/"
        "5jqet0ab9qci-Tf64DrkREs8sfsTnIB4g8.zip"),
    original_filename="In-Feed - Arabic Version RTL.zip",
    content_type="application/zip", review_status=PlatformAssetReviewStatus.PREVIEW_ONLY,
    deterministic_geometry_available=False,
    notes=(
        "Official page exposes a separate Arabic RTL file; it was not mirrored from LTR. Its "
        "current CDN key returned not found, so nothing was stored and no geometry inferred."),
)


class CertificationReadinessState(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"


class MediaCertificationReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str
    execution_readiness: CertificationReadinessState
    delivery_readiness: CertificationReadinessState
    provider_request_compatible: bool
    used_capabilities_certified: bool
    video_pricing_certified: bool
    seed_pricing_available_or_deferred: bool
    QA_pricing_finite: bool
    retry_budget_finite: bool
    safe_zone_verified: bool
    compositor_geometry_verified: bool
    final_overlays_present: bool
    final_QA_passed: bool
    live_plan_present: bool
    blockers: tuple[str, ...]
    provider_generation_calls: int = 0
    database_mutations: int = 0
    spend_usd: Decimal = Decimal("0")

    @model_validator(mode="after")
    def readiness_and_side_effects_are_truthful(self) -> "MediaCertificationReadiness":
        execution = all((
            self.provider_request_compatible, self.used_capabilities_certified,
            self.video_pricing_certified, self.seed_pricing_available_or_deferred,
            self.QA_pricing_finite, self.retry_budget_finite,
        ))
        delivery = execution and all((
            self.safe_zone_verified, self.compositor_geometry_verified,
            self.final_overlays_present, self.final_QA_passed, self.live_plan_present,
        ))
        if (self.execution_readiness == CertificationReadinessState.READY) != execution:
            raise ValueError("execution readiness does not match non-live preconditions")
        if (self.delivery_readiness == CertificationReadinessState.READY) != delivery:
            raise ValueError("delivery readiness does not match delivery preconditions")
        if self.provider_generation_calls or self.database_mutations or self.spend_usd != 0:
            raise ValueError("Gate 2D.4C must remain zero-call, zero-mutation, and zero-spend")
        return self


def evaluate_media_certification_readiness(
    *, platform: str, provider_request_compatible: bool,
    used_capabilities_certified: bool, video_pricing_certified: bool,
    seed_pricing_available_or_deferred: bool, QA_pricing_finite: bool,
    retry_budget_finite: bool, safe_zone_verified: bool,
    compositor_geometry_verified: bool, final_overlays_present: bool,
    final_QA_passed: bool, live_plan_present: bool,
) -> MediaCertificationReadiness:
    conditions = {
        "PROVIDER_REQUEST_NOT_COMPATIBLE": provider_request_compatible,
        "USED_CAPABILITY_NOT_CERTIFIED": used_capabilities_certified,
        "VERTEX_VIDEO_PRICING_NOT_CERTIFIED": video_pricing_certified,
        "SEED_PRICING_NOT_AVAILABLE_OR_DEFERRED": seed_pricing_available_or_deferred,
        "QA_PRICING_NOT_FINITE": QA_pricing_finite,
        "RETRY_BUDGET_NOT_FINITE": retry_budget_finite,
        "SAFE_ZONE_NOT_VERIFIED": safe_zone_verified,
        "COMPOSITOR_GEOMETRY_NOT_VERIFIED": compositor_geometry_verified,
        "FINAL_OVERLAYS_NOT_PRESENT": final_overlays_present,
        "FINAL_QA_NOT_PASSED": final_QA_passed,
        "LIVE_PLAN_NOT_PRESENT": live_plan_present,
    }
    execution = all(tuple(conditions.values())[:6])
    delivery = execution and all(tuple(conditions.values())[6:])
    return MediaCertificationReadiness(
        platform=platform,
        execution_readiness=(CertificationReadinessState.READY if execution
                             else CertificationReadinessState.NOT_READY),
        delivery_readiness=(CertificationReadinessState.READY if delivery
                            else CertificationReadinessState.NOT_READY),
        provider_request_compatible=provider_request_compatible,
        used_capabilities_certified=used_capabilities_certified,
        video_pricing_certified=video_pricing_certified,
        seed_pricing_available_or_deferred=seed_pricing_available_or_deferred,
        QA_pricing_finite=QA_pricing_finite, retry_budget_finite=retry_budget_finite,
        safe_zone_verified=safe_zone_verified,
        compositor_geometry_verified=compositor_geometry_verified,
        final_overlays_present=final_overlays_present,
        final_QA_passed=final_QA_passed, live_plan_present=live_plan_present,
        blockers=tuple(name for name, satisfied in conditions.items() if not satisfied),
    )


CURRENT_INSTAGRAM_READINESS = evaluate_media_certification_readiness(
    platform="INSTAGRAM_REELS", provider_request_compatible=True,
    used_capabilities_certified=True, video_pricing_certified=False,
    seed_pricing_available_or_deferred=True, QA_pricing_finite=True,
    retry_budget_finite=True, safe_zone_verified=False,
    compositor_geometry_verified=False, final_overlays_present=False,
    final_QA_passed=False, live_plan_present=False,
)


GATE_2D4C_SIDE_EFFECT_AUDIT: Mapping[str, Any] = {
    "authority_label": "OFFLINE_CERTIFICATION_REPAIR_NOT_LIVE_AUTHORITY",
    "documentation_reads": True,
    "public_billing_table_reads": 1,
    "billing_API_successful_reads": 0,
    "provider_generation_calls": 0,
    "Gemini_QA_calls": 0,
    "Veo_calls": 0,
    "image_calls": 0,
    "TTS_calls": 0,
    "database_reads": 0,
    "database_mutations": 0,
    "execution_approval_consumed": False,
    "spend_usd": "0",
}


def gate_2d4c_certification_bundle() -> Mapping[str, Any]:
    """Serializable evidence summary used by artifacts and regression tests."""
    return {
        "authority_label": "OFFLINE_CERTIFICATION_REPAIR_NOT_LIVE_AUTHORITY",
        "surface_pricing_provenance": VERTEX_SURFACE_PRICING_PROVENANCE.model_dump(mode="json"),
        "vertex_skus": [item.model_dump(mode="json") for item in CURRENT_VERTEX_VEO_SKUS],
        "model_mappings": [
            VERTEX_VEO_31_MODEL_MAPPING.model_dump(mode="json"),
            VERTEX_VEO_31_FAST_MODEL_MAPPING.model_dump(mode="json"),
        ],
        "pricing_repair_result": VERTEX_PRICING_REPAIR_RESULT.model_dump(mode="json"),
        "capabilities": [
            VERTEX_VEO_31_CAPABILITY_RECONCILIATION.model_dump(mode="json"),
            VERTEX_VEO_31_FAST_CAPABILITY_RECONCILIATION.model_dump(mode="json"),
        ],
        "lite": VERTEX_VEO_31_LITE_PROFILE.model_dump(mode="json"),
        "QA_provider": CANONICAL_QA_PROVIDER.model_dump(mode="json"),
        "QA_pricing": VERTEX_GEMINI_25_FLASH_QA_PRICING.model_dump(mode="json"),
        "QA_envelopes": [item.model_dump(mode="json")
                         for item in CANONICAL_QA_TOKEN_ENVELOPES],
        "QA_worst_case_costs": [item.model_dump(mode="json")
                                for item in CANONICAL_QA_WORST_CASE_COSTS],
        "QA_total_worst_case_usd": str(CANONICAL_QA_TOTAL_WORST_CASE_USD),
        "voice_verification": CANONICAL_VOICE_VERIFICATION_PLAN.model_dump(mode="json"),
        "safe_zone_assets": [
            META_REELS_SAFE_ZONE_ASSET.model_dump(mode="json"),
            TIKTOK_LTR_SAFE_ZONE_ASSET.model_dump(mode="json"),
            TIKTOK_RTL_SAFE_ZONE_ASSET.model_dump(mode="json"),
        ],
        "instagram_readiness": CURRENT_INSTAGRAM_READINESS.model_dump(mode="json"),
        "side_effect_audit": dict(GATE_2D4C_SIDE_EFFECT_AUDIT),
    }


__all__ = [
    "AdzyAudioExecutionPolicy", "BillingCatalogSkuEvidence", "BillingEvidenceAuthority",
    "BillingModelMappingEvidence", "BillingModelMappingStatus", "BillingSkuRegistry",
    "CANONICAL_QA_PROVIDER", "CANONICAL_QA_TOKEN_ENVELOPES",
    "CANONICAL_QA_TOTAL_WORST_CASE_USD", "CANONICAL_QA_WORST_CASE_COSTS",
    "CANONICAL_VOICE_VERIFICATION_PLAN", "CURRENT_INSTAGRAM_READINESS",
    "CURRENT_VERTEX_VEO_SKUS", "CertificationReadinessState",
    "GATE_2D4C_SIDE_EFFECT_AUDIT", "IntegrationCapabilityState",
    "META_REELS_SAFE_ZONE_ASSET", "MediaCertificationReadiness",
    "PlatformAssetReviewStatus", "PricingCertificationStatus", "QAPricingSnapshot",
    "QAProviderProfile", "QAPurpose", "QAPurposeCost", "QATokenEnvelope",
    "SafeZoneAssetProvenance", "SurfacePricingProvenance", "TIKTOK_LTR_SAFE_ZONE_ASSET",
    "TIKTOK_RTL_SAFE_ZONE_ASSET", "VERTEX_GEMINI_25_FLASH_QA_PRICING",
    "VERTEX_PRICING_REPAIR_RESULT", "VERTEX_SURFACE_PRICING_PROVENANCE",
    "VERTEX_VEO_31_CAPABILITY_RECONCILIATION", "VERTEX_VEO_31_FAST_CAPABILITY_RECONCILIATION",
    "VERTEX_VEO_31_LITE_PROFILE", "VERTEX_VEO_31_MODEL_MAPPING",
    "VERTEX_VEO_31_FAST_MODEL_MAPPING", "VERTEX_VEO_SKU_REGISTRY",
    "calculate_QA_worst_case_cost", "evaluate_media_certification_readiness",
    "gate_2d4c_certification_bundle", "require_input_within_provider_count",
]
