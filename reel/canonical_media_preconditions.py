"""Offline media-execution precondition certification for canonical Reels.

This module is deliberately provider-free and database-free.  It records exact product-surface
identity, reviewed evidence, installed-SDK serialization, pricing identity, finite arithmetic,
safe-zone provenance, and readiness decisions.  It never creates a provider client or performs
network I/O.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical_creative import CanonicalReelExecutionPlan
from .canonical_production import CompiledVideoRequest, RetryBudget


VERIFIED_DATE = "2026-08-24"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def stable_hash(value: Any) -> str:
    raw = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MediaPreconditionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProductSurface(str, Enum):
    VERTEX_AI_GENERATIVE_AI = "VERTEX_AI_GENERATIVE_AI"
    GEMINI_ENTERPRISE_AGENT_PLATFORM = "GEMINI_ENTERPRISE_AGENT_PLATFORM"
    GEMINI_DEVELOPER_API = "GEMINI_DEVELOPER_API"
    CLOUD_TEXT_TO_SPEECH = "CLOUD_TEXT_TO_SPEECH"
    OFFLINE_SYNTHETIC_TEST = "OFFLINE_SYNTHETIC_TEST"


class ProviderSurfaceIdentity(BaseModel):
    """Full commercial and technical identity; a model name alone is never an identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    product_surface: ProductSurface
    API_surface: str
    model_id: str
    region: str
    billing_mode: str
    SDK_family: str
    SDK_version: str
    endpoint_family: str
    capability_version: str
    pricing_version: str

    @property
    def identity(self) -> tuple[str, ...]:
        return (
            self.provider, self.product_surface.value, self.API_surface, self.model_id,
            self.region, self.billing_mode, self.SDK_family, self.SDK_version,
            self.endpoint_family, self.capability_version, self.pricing_version,
        )

    @property
    def surface_model_identity(self) -> tuple[str, str, str, str, str]:
        return (self.provider, self.product_surface.value, self.API_surface,
                self.model_id, self.endpoint_family)


VERTEX_VEO_31_IDENTITY = ProviderSurfaceIdentity(
    provider="google", product_surface=ProductSurface.VERTEX_AI_GENERATIVE_AI,
    API_surface="aiplatform.googleapis.com/v1", model_id="veo-3.1-generate-001",
    region="us-central1", billing_mode="RUNTIME_SKU_NOT_PROVEN",
    SDK_family="google-genai", SDK_version="2.8.0",
    endpoint_family="publishers/google/models:predictLongRunning",
    capability_version="vertex-configured-core-2026-08-24",
    pricing_version="COST_NOT_PROVABLE",
)

VERTEX_VEO_31_FAST_IDENTITY = VERTEX_VEO_31_IDENTITY.model_copy(update={
    "model_id": "veo-3.1-fast-generate-001",
    "capability_version": "vertex-configured-fast-core-2026-08-24",
})

AGENT_PLATFORM_VEO_31_IDENTITY = VERTEX_VEO_31_IDENTITY.model_copy(update={
    "product_surface": ProductSurface.GEMINI_ENTERPRISE_AGENT_PLATFORM,
    "API_surface": "Gemini Enterprise Agent Platform API",
    "billing_mode": "FIXED_QUOTA_OR_PROVISIONED_THROUGHPUT",
    "endpoint_family": "Agent Platform model API",
    "capability_version": "agent-platform-model-page-2026-08-24",
    "pricing_version": "agent-platform-pricing-observed-unit-count-2026-08-24",
})

AGENT_PLATFORM_VEO_31_FAST_IDENTITY = AGENT_PLATFORM_VEO_31_IDENTITY.model_copy(update={
    "model_id": "veo-3.1-fast-generate-001",
})

AGENT_PLATFORM_FLASH_IMAGE_IDENTITY = ProviderSurfaceIdentity(
    provider="google", product_surface=ProductSurface.GEMINI_ENTERPRISE_AGENT_PLATFORM,
    API_surface="Gemini Enterprise Agent Platform API",
    model_id="gemini-3.1-flash-image", region="global", billing_mode="STANDARD",
    SDK_family="google-genai", SDK_version="2.8.0",
    endpoint_family="Agent Platform generate content",
    capability_version="agent-platform-flash-image-GA-2026-08-24",
    pricing_version="agent-platform-flash-image-standard-2026-08-24",
)

AGENT_PLATFORM_PRO_IMAGE_IDENTITY = AGENT_PLATFORM_FLASH_IMAGE_IDENTITY.model_copy(update={
    "model_id": "gemini-3-pro-image",
    "capability_version": "agent-platform-pro-image-GA-2026-08-24",
    "pricing_version": "COST_NOT_FULLY_PROVABLE",
})

CLOUD_TTS_FLASH_IDENTITY = ProviderSurfaceIdentity(
    provider="google", product_surface=ProductSurface.CLOUD_TEXT_TO_SPEECH,
    API_surface="texttospeech.googleapis.com", model_id="gemini-3.1-flash-tts-preview",
    region="global", billing_mode="STANDARD_PAYGO", SDK_family="google-cloud-texttospeech",
    SDK_version="NOT_INSPECTED_NOT_EXECUTION_DEFAULT", endpoint_family="text:synthesize",
    capability_version="cloud-tts-preview-2026-08-24",
    pricing_version="cloud-tts-gemini-3.1-flash-preview-2026-08-24",
)

VERTEX_GEMINI_25_FLASH_QA_IDENTITY = ProviderSurfaceIdentity(
    provider="google", product_surface=ProductSurface.VERTEX_AI_GENERATIVE_AI,
    API_surface="aiplatform.googleapis.com/v1", model_id="gemini-2.5-flash",
    region="us-central1", billing_mode="STANDARD",
    SDK_family="google-genai", SDK_version="2.8.0",
    endpoint_family="publishers/google/models:generateContent",
    capability_version="vertex-multimodal-structured-QA-2026-08-24",
    pricing_version="vertex-gemini-2.5-flash-standard-media-QA-2026-08-24",
)


class CapabilityStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    PREVIEW = "PREVIEW"
    UNKNOWN = "UNKNOWN"


class CapabilityProofKind(str, Enum):
    MODEL_SURFACE_CAPABILITY_PROVEN = "MODEL_SURFACE_CAPABILITY_PROVEN"
    SDK_FIELD_AVAILABLE = "SDK_FIELD_AVAILABLE"
    DOCUMENTATION_DIVERGENCE = "DOCUMENTATION_DIVERGENCE"


class CapabilityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_surface_identity: ProviderSurfaceIdentity
    capability_name: str
    status: CapabilityStatus
    proof_kind: CapabilityProofKind
    official_source_url: str
    verified_date: str
    source_product_name: str
    notes: str
    evidence_hash: str

    @model_validator(mode="after")
    def evidence_is_hashed_and_scoped(self) -> "CapabilityEvidence":
        material = self.model_dump(mode="json", exclude={"evidence_hash"})
        if self.evidence_hash != stable_hash(material):
            raise ValueError("capability evidence hash mismatch")
        if not self.official_source_url.startswith("https://"):
            raise ValueError("capability evidence requires an HTTPS official source")
        return self


def capability_evidence(
    identity: ProviderSurfaceIdentity, capability_name: str, status: CapabilityStatus,
    proof_kind: CapabilityProofKind, source: str, source_product_name: str, notes: str,
) -> CapabilityEvidence:
    values = {
        "provider_surface_identity": identity, "capability_name": capability_name,
        "status": status, "proof_kind": proof_kind, "official_source_url": source,
        "verified_date": VERIFIED_DATE, "source_product_name": source_product_name,
        "notes": notes,
    }
    return CapabilityEvidence(evidence_hash=stable_hash(values), **values)


VERTEX_FIRST_LAST_SOURCE = (
    "https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/"
    "generate-videos-from-first-and-last-frames")
VERTEX_MODEL_SOURCE = (
    "https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate")
AGENT_PLATFORM_MODEL_SOURCE = (
    "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/"
    "veo/3-1-generate")
VERTEX_VEO_FAMILY_ANNOUNCEMENT_SOURCE = (
    "https://cloud.google.com/blog/products/ai-machine-learning/"
    "veo-3-1-lite-and-a-new-veo-upscaling-capability-on-vertex-ai")


def canonical_capability_evidence() -> tuple[CapabilityEvidence, ...]:
    records: list[CapabilityEvidence] = []
    core_notes = (
        "The configured Vertex REST endpoint is shown in the official example. The former Vertex "
        "URL currently redirects to Agent Platform, so only the explicitly reviewed core subset is "
        "eligible in Adzy.")
    for identity in (VERTEX_VEO_31_IDENTITY, VERTEX_VEO_31_FAST_IDENTITY):
        for name in ("TEXT_TO_VIDEO", "IMAGE_TO_VIDEO", "FIRST_LAST_FRAME",
                     "ASPECT_9_16", "ASPECT_16_9", "DURATION_4_6_8",
                     "RESOLUTION_720P_1080P"):
            records.append(capability_evidence(
                identity, name, CapabilityStatus.SUPPORTED,
                CapabilityProofKind.MODEL_SURFACE_CAPABILITY_PROVEN,
                VERTEX_FIRST_LAST_SOURCE, "Google Vertex/Agent Platform REST documentation",
                core_notes))
        for name in ("REFERENCE_IMAGE_TO_VIDEO", "VIDEO_EXTENSION"):
            records.append(capability_evidence(
                identity, name, CapabilityStatus.UNKNOWN,
                CapabilityProofKind.DOCUMENTATION_DIVERGENCE,
                VERTEX_MODEL_SOURCE, "Google documentation redirect",
                "The requested Vertex URL now redirects to an Agent Platform page that advertises "
                "broader support. That does not certify the configured Adzy Vertex identity; the "
                "compiler remains fail-closed."))
        records.append(capability_evidence(
            identity, "MODEL_NATIVE_AUDIO_CAPABILITY", CapabilityStatus.SUPPORTED,
            CapabilityProofKind.MODEL_SURFACE_CAPABILITY_PROVEN,
            VERTEX_VEO_FAMILY_ANNOUNCEMENT_SOURCE,
            "Google Cloud Vertex AI Veo 3.1 family announcement",
            "The model family supports native audio. Adzy's independent execution policy keeps "
            "marketing speech disabled and uses VIDEO_ONLY unless a later plan authorizes audio."))
    for identity in (AGENT_PLATFORM_VEO_31_IDENTITY, AGENT_PLATFORM_VEO_31_FAST_IDENTITY):
        for name in ("REFERENCE_IMAGE_TO_VIDEO", "VIDEO_EXTENSION"):
            records.append(capability_evidence(
                identity, name, CapabilityStatus.SUPPORTED,
                CapabilityProofKind.MODEL_SURFACE_CAPABILITY_PROVEN,
                AGENT_PLATFORM_MODEL_SOURCE, "Gemini Enterprise Agent Platform",
                "This evidence is intentionally scoped to Agent Platform and cannot upgrade Vertex."))
    return tuple(records)


class ConfiguredCapabilityMatrixRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: ProviderSurfaceIdentity
    authorization: str
    text_to_video: bool
    image_to_video: bool
    first_last_frame: bool
    reference_images: bool
    video_extension: bool
    native_audio: bool
    durations: tuple[int, ...]
    resolutions: tuple[str, ...]
    aspect_ratios: tuple[str, ...]
    notes: str


CONFIGURED_VERTEX_CAPABILITY_MATRIX = (
    ConfiguredCapabilityMatrixRow(
        identity=VERTEX_VEO_31_IDENTITY, authorization="CONFIGURED_CONSERVATIVE_PROFILE",
        text_to_video=True, image_to_video=True, first_last_frame=True,
        reference_images=False, video_extension=False, native_audio=False,
        durations=(4, 6, 8), resolutions=("720p", "1080p"),
        aspect_ratios=("9:16", "16:9"),
        notes="Reference/extension/audio remain disabled until exact configured-surface proof."),
    ConfiguredCapabilityMatrixRow(
        identity=VERTEX_VEO_31_FAST_IDENTITY,
        authorization="CONFIGURED_CONSERVATIVE_PROFILE",
        text_to_video=True, image_to_video=True, first_last_frame=True,
        reference_images=False, video_extension=False, native_audio=False,
        durations=(4, 6, 8), resolutions=("720p", "1080p"),
        aspect_ratios=("9:16", "16:9"),
        notes="Fast is capability-gated independently; no Lite profile was added."),
)


class VideoAudioMode(str, Enum):
    VIDEO_ONLY = "VIDEO_ONLY"
    VIDEO_PLUS_AUDIO = "VIDEO_PLUS_AUDIO"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MediaOutputModality(str, Enum):
    VIDEO = "VIDEO"
    VIDEO_AUDIO = "VIDEO_AUDIO"
    IMAGE_INPUT = "IMAGE_INPUT"
    IMAGE_OUTPUT = "IMAGE_OUTPUT"
    TEXT_INPUT = "TEXT_INPUT"
    AUDIO_INPUT = "AUDIO_INPUT"
    TEXT_OUTPUT = "TEXT_OUTPUT"
    AUDIO_OUTPUT = "AUDIO_OUTPUT"
    QA_CALL = "QA_CALL"


class MediaBillingUnit(str, Enum):
    SECOND = "SECOND"
    CALL = "CALL"
    TOKEN = "TOKEN"
    PER_1M_TOKENS = "PER_1M_TOKENS"
    UNKNOWN_COUNT = "UNKNOWN_COUNT"


class MediaPricingStatus(str, Enum):
    VERIFIED = "VERIFIED"
    SYNTHETIC_ARITHMETIC_ONLY = "SYNTHETIC_ARITHMETIC_ONLY"
    UNPROVEN_SURFACE_IDENTITY = "UNPROVEN_SURFACE_IDENTITY"
    UNPROVEN_BILLING_UNIT = "UNPROVEN_BILLING_UNIT"
    UNREVIEWED = "UNREVIEWED"
    EXPIRED = "EXPIRED"


class MediaPriceKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    product_surface: ProductSurface
    API_surface: str
    model_id: str
    output_modality: MediaOutputModality
    audio_mode: VideoAudioMode
    resolution: str
    billing_mode: str
    region: str

    @property
    def identity(self) -> tuple[str, ...]:
        return tuple(str(value.value if isinstance(value, Enum) else value)
                     for value in self.model_dump(mode="python").values())


def price_key(
    identity: ProviderSurfaceIdentity, output_modality: MediaOutputModality,
    audio_mode: VideoAudioMode, resolution: str,
) -> MediaPriceKey:
    return MediaPriceKey(
        provider=identity.provider, product_surface=identity.product_surface,
        API_surface=identity.API_surface, model_id=identity.model_id,
        output_modality=output_modality, audio_mode=audio_mode, resolution=resolution,
        billing_mode=identity.billing_mode, region=identity.region)


class MediaPricingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: MediaPriceKey
    price_unit: MediaBillingUnit
    unit_price: Decimal
    currency: str = "USD"
    units_per_artifact: Optional[Decimal] = None
    official_source: str
    verified_date: str
    pricing_version: str
    status: MediaPricingStatus
    review_expires: Optional[str] = None
    notes: str = ""

    @field_validator("unit_price", "units_per_artifact")
    @classmethod
    def finite_nonnegative(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("media pricing numbers must be finite and non-negative")
        return value

    @model_validator(mode="after")
    def reviewed_record_is_complete(self) -> "MediaPricingRecord":
        source_is_valid = (self.official_source.startswith("https://")
                           or (self.status == MediaPricingStatus.SYNTHETIC_ARITHMETIC_ONLY
                               and self.official_source.startswith("synthetic://")))
        if self.currency != "USD" or not source_is_valid:
            raise ValueError("media pricing needs an official HTTPS source or synthetic label")
        if self.status == MediaPricingStatus.VERIFIED:
            if self.price_unit == MediaBillingUnit.UNKNOWN_COUNT:
                raise ValueError("unknown count cannot be a verified billing unit")
            if not self.pricing_version or not self.verified_date:
                raise ValueError("verified pricing needs a version and review date")
        return self

    def cost(self, units: Decimal) -> Decimal:
        if self.status not in {MediaPricingStatus.VERIFIED,
                               MediaPricingStatus.SYNTHETIC_ARITHMETIC_ONLY}:
            raise MediaPreconditionError("COST_NOT_PROVABLE", "pricing record is not verified")
        if not units.is_finite() or units < 0:
            raise MediaPreconditionError("INVALID_MEDIA_UNITS", "media units must be finite")
        if self.price_unit == MediaBillingUnit.PER_1M_TOKENS:
            return units * self.unit_price / Decimal("1000000")
        return units * self.unit_price


class MediaPricingRegistry:
    def __init__(self, records: Sequence[MediaPricingRecord], *,
                 allow_synthetic: bool = False) -> None:
        self._allow_synthetic = allow_synthetic
        self._records: dict[tuple[str, ...], MediaPricingRecord] = {}
        for record in records:
            if record.key.identity in self._records:
                raise ValueError("duplicate exact media pricing identity")
            self._records[record.key.identity] = record

    @property
    def records(self) -> tuple[MediaPricingRecord, ...]:
        return tuple(self._records.values())

    def resolve(self, key: MediaPriceKey, *, require_verified: bool = True,
                block_expired: bool = True) -> Optional[MediaPricingRecord]:
        record = self._records.get(key.identity)
        if record is None:
            return None
        if block_expired and record.review_expires \
                and date.fromisoformat(record.review_expires) < date.fromisoformat(VERIFIED_DATE):
            return None
        if require_verified:
            allowed = {MediaPricingStatus.VERIFIED}
            if self._allow_synthetic:
                allowed.add(MediaPricingStatus.SYNTHETIC_ARITHMETIC_ONLY)
            if record.status not in allowed:
                return None
        return record


AGENT_PLATFORM_PRICING_SOURCE = (
    "https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing")
CLOUD_TTS_PRICING_SOURCE = "https://cloud.google.com/text-to-speech/pricing"
VERTEX_GENERATIVE_AI_PRICING_SOURCE = (
    "https://cloud.google.com/vertex-ai/generative-ai/pricing")


def _pricing_record(
    key: MediaPriceKey, unit: MediaBillingUnit, rate: str, version: str,
    *, source: str = AGENT_PLATFORM_PRICING_SOURCE,
    status: MediaPricingStatus = MediaPricingStatus.VERIFIED,
    units_per_artifact: Optional[str] = None, notes: str = "",
) -> MediaPricingRecord:
    return MediaPricingRecord(
        key=key, price_unit=unit, unit_price=Decimal(rate), official_source=source,
        verified_date=VERIFIED_DATE, pricing_version=version, status=status,
        units_per_artifact=(Decimal(units_per_artifact)
                            if units_per_artifact is not None else None), notes=notes)


def reviewed_media_pricing_records() -> tuple[MediaPricingRecord, ...]:
    records: list[MediaPricingRecord] = []
    # The page currently renders Veo prices per "count".  Without a SKU/runtime proof that count
    # means one generated second, these observations are kept but are not resolvable prices.
    observed = (
        (AGENT_PLATFORM_VEO_31_IDENTITY, VideoAudioMode.VIDEO_PLUS_AUDIO, "720p", "0.40"),
        (AGENT_PLATFORM_VEO_31_IDENTITY, VideoAudioMode.VIDEO_PLUS_AUDIO, "1080p", "0.40"),
        (AGENT_PLATFORM_VEO_31_IDENTITY, VideoAudioMode.VIDEO_ONLY, "720p", "0.20"),
        (AGENT_PLATFORM_VEO_31_IDENTITY, VideoAudioMode.VIDEO_ONLY, "1080p", "0.20"),
        (AGENT_PLATFORM_VEO_31_FAST_IDENTITY, VideoAudioMode.VIDEO_PLUS_AUDIO, "720p", "0.10"),
        (AGENT_PLATFORM_VEO_31_FAST_IDENTITY, VideoAudioMode.VIDEO_PLUS_AUDIO, "1080p", "0.12"),
        (AGENT_PLATFORM_VEO_31_FAST_IDENTITY, VideoAudioMode.VIDEO_ONLY, "720p", "0.08"),
        (AGENT_PLATFORM_VEO_31_FAST_IDENTITY, VideoAudioMode.VIDEO_ONLY, "1080p", "0.10"),
    )
    for identity, audio_mode, resolution, rate in observed:
        modality = (MediaOutputModality.VIDEO_AUDIO
                    if audio_mode == VideoAudioMode.VIDEO_PLUS_AUDIO
                    else MediaOutputModality.VIDEO)
        records.append(_pricing_record(
            price_key(identity, modality, audio_mode, resolution),
            MediaBillingUnit.UNKNOWN_COUNT, rate,
            "agent-platform-veo-observed-count-2026-08-24",
            status=MediaPricingStatus.UNPROVEN_BILLING_UNIT,
            notes="Observed official table value; not registered as Vertex or per-second pricing."))
    for resolution, tokens in (("512", "747"), ("1K", "1120"),
                               ("2K", "1680"), ("4K", "2520")):
        records.append(_pricing_record(
            price_key(AGENT_PLATFORM_FLASH_IMAGE_IDENTITY,
                      MediaOutputModality.IMAGE_OUTPUT,
                      VideoAudioMode.NOT_APPLICABLE, resolution),
            MediaBillingUnit.PER_1M_TOKENS, "60.00",
            "agent-platform-gemini-3.1-flash-image-standard-2026-08-24",
            units_per_artifact=tokens))
    records.append(_pricing_record(
        price_key(AGENT_PLATFORM_FLASH_IMAGE_IDENTITY,
                  MediaOutputModality.IMAGE_INPUT,
                  VideoAudioMode.NOT_APPLICABLE, "ANY"),
        MediaBillingUnit.PER_1M_TOKENS, "0.50",
        "agent-platform-gemini-3.1-flash-image-standard-2026-08-24"))
    records.append(_pricing_record(
        price_key(AGENT_PLATFORM_FLASH_IMAGE_IDENTITY,
                  MediaOutputModality.TEXT_INPUT,
                  VideoAudioMode.NOT_APPLICABLE, "ANY"),
        MediaBillingUnit.PER_1M_TOKENS, "0.50",
        "agent-platform-gemini-3.1-flash-image-standard-2026-08-24"))
    # The current standard table does not expose a complete non-cached Pro input/output tuple.
    records.append(_pricing_record(
        price_key(AGENT_PLATFORM_PRO_IMAGE_IDENTITY,
                  MediaOutputModality.IMAGE_OUTPUT,
                  VideoAudioMode.NOT_APPLICABLE, "1K"),
        MediaBillingUnit.PER_1M_TOKENS, "120.00",
        "agent-platform-gemini-3-pro-image-footnote-2026-08-24",
        status=MediaPricingStatus.UNPROVEN_SURFACE_IDENTITY,
        units_per_artifact="1120",
        notes="Output footnote observed; complete hero request pricing is not certified."))
    records.extend((
        _pricing_record(
            price_key(CLOUD_TTS_FLASH_IDENTITY, MediaOutputModality.TEXT_INPUT,
                      VideoAudioMode.NOT_APPLICABLE, "N/A"),
            MediaBillingUnit.PER_1M_TOKENS, "1.00",
            "cloud-tts-gemini-3.1-flash-preview-2026-08-24",
            source=CLOUD_TTS_PRICING_SOURCE),
        _pricing_record(
            price_key(CLOUD_TTS_FLASH_IDENTITY, MediaOutputModality.AUDIO_OUTPUT,
                      VideoAudioMode.NOT_APPLICABLE, "N/A"),
            MediaBillingUnit.PER_1M_TOKENS, "20.00",
            "cloud-tts-gemini-3.1-flash-preview-2026-08-24",
            source=CLOUD_TTS_PRICING_SOURCE,
            notes="Cloud TTS documents 25 audio tokens per generated second."),
    ))
    # Exact Vertex STANDARD QA rates. Audio is deliberately separate: the current official row
    # prices audio input at $1/M tokens, not the $0.30/M text/image/video rate.
    records.extend((
        _pricing_record(
            price_key(VERTEX_GEMINI_25_FLASH_QA_IDENTITY,
                      MediaOutputModality.IMAGE_INPUT,
                      VideoAudioMode.NOT_APPLICABLE, "TEXT_IMAGE_VIDEO"),
            MediaBillingUnit.PER_1M_TOKENS, "0.30",
            "vertex-gemini-2.5-flash-standard-media-QA-2026-08-24",
            source=VERTEX_GENERATIVE_AI_PRICING_SOURCE,
            notes="Vertex STANDARD text/image/video input rate."),
        _pricing_record(
            price_key(VERTEX_GEMINI_25_FLASH_QA_IDENTITY,
                      MediaOutputModality.AUDIO_INPUT,
                      VideoAudioMode.NOT_APPLICABLE, "AUDIO"),
            MediaBillingUnit.PER_1M_TOKENS, "1.00",
            "vertex-gemini-2.5-flash-standard-media-QA-2026-08-24",
            source=VERTEX_GENERATIVE_AI_PRICING_SOURCE,
            notes="Exact current Vertex STANDARD audio-input row; not a generic-rate alias."),
        _pricing_record(
            price_key(VERTEX_GEMINI_25_FLASH_QA_IDENTITY,
                      MediaOutputModality.TEXT_OUTPUT,
                      VideoAudioMode.NOT_APPLICABLE, "RESPONSE_REASONING"),
            MediaBillingUnit.PER_1M_TOKENS, "2.50",
            "vertex-gemini-2.5-flash-standard-media-QA-2026-08-24",
            source=VERTEX_GENERATIVE_AI_PRICING_SOURCE,
            notes="Text response and reasoning are both billable output tokens."),
    ))
    return tuple(records)


DEFAULT_MEDIA_PRICING_REGISTRY = MediaPricingRegistry(reviewed_media_pricing_records())


class SDKCompatibilityState(str, Enum):
    SERIALIZATION_PROVEN_OFFLINE = "SERIALIZATION_PROVEN_OFFLINE"
    INCOMPATIBLE = "INCOMPATIBLE"


class SDKRequestCompatibilityCertification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SDKCompatibilityState
    provider_surface_identity: ProviderSurfaceIdentity
    installed_google_genai_version: str
    request_classes: tuple[str, ...]
    SDK_fields_available: Mapping[str, bool]
    SDK_fields_not_model_capabilities: tuple[str, ...]
    compiled_request_hash: str
    serialized_request: Mapping[str, Any]
    serialization_hash: str
    provider_calls: int = 0
    network_calls: int = 0

    @model_validator(mode="after")
    def offline_and_hashed(self) -> "SDKRequestCompatibilityCertification":
        if self.provider_calls or self.network_calls:
            raise ValueError("SDK compatibility certification must remain offline")
        if self.serialization_hash != stable_hash(self.serialized_request):
            raise ValueError("SDK request serialization hash mismatch")
        if self.state == SDKCompatibilityState.SERIALIZATION_PROVEN_OFFLINE \
                and not all(self.SDK_fields_available.values()):
            raise ValueError("a required local SDK field is missing")
        return self


def certify_google_genai_video_request_offline(
    request: CompiledVideoRequest, *, identity: ProviderSurfaceIdentity,
    audio_mode: VideoAudioMode = VideoAudioMode.VIDEO_ONLY,
) -> SDKRequestCompatibilityCertification:
    """Build and serialize the installed SDK objects without constructing a client."""
    expected_request_hash = stable_hash(
        request.model_dump(mode="json", exclude={"request_hash"}))
    if request.request_hash != expected_request_hash:
        raise MediaPreconditionError(
            "COMPILED_REQUEST_HASH_INVALID",
            "SDK certification refuses a request changed after canonical compilation")
    if request.model_id != identity.model_id:
        raise MediaPreconditionError(
            "SURFACE_IDENTITY_MISMATCH", "compiled request model and SDK identity differ")
    if identity.product_surface != ProductSurface.VERTEX_AI_GENERATIVE_AI:
        raise MediaPreconditionError(
            "WRONG_SDK_CERTIFICATION_SURFACE", "this adapter certifies the configured Vertex path")
    from google.genai import types  # local schema import only; creates no client and no network

    installed_version = importlib.metadata.version("google-genai")
    if installed_version != identity.SDK_version:
        raise MediaPreconditionError(
            "SDK_VERSION_DRIFT", f"expected {identity.SDK_version}; installed {installed_version}")
    config_fields = types.GenerateVideosConfig.model_fields
    field_names = (
        "duration_seconds", "resolution", "aspect_ratio", "person_generation",
        "negative_prompt", "output_gcs_uri", "last_frame", "generate_audio",
    )
    availability = {name: name in config_fields for name in field_names}
    if not all(availability.values()):
        missing = tuple(name for name, available in availability.items() if not available)
        raise MediaPreconditionError(
            "SDK_REQUEST_FIELD_MISSING", "missing installed SDK fields: " + ", ".join(missing))

    def redacted_image(asset_id: str) -> Any:
        return types.Image(
            gcs_uri=f"gs://redacted-offline-fixture/{stable_hash(asset_id)[:20]}.png",
            mime_type="image/png")

    first_frame = redacted_image(request.first_frame_asset_id) \
        if request.first_frame_asset_id else None
    last_frame = redacted_image(request.last_frame_asset_id) \
        if request.last_frame_asset_id else None
    negative_prompt = "; ".join(request.negative_constraints)
    person_generation = (
        "disallow" if any("disallow" in item.casefold()
                          for item in request.safety_person_controls) else "allow_adult")
    config = types.GenerateVideosConfig(
        number_of_videos=1,
        output_gcs_uri="gs://redacted-offline-output/canonical-reel/",
        duration_seconds=request.duration_seconds,
        aspect_ratio=request.aspect_ratio,
        resolution=request.resolution,
        person_generation=person_generation,
        negative_prompt=negative_prompt,
        generate_audio=(audio_mode == VideoAudioMode.VIDEO_PLUS_AUDIO),
        last_frame=last_frame,
    )
    # The first frame is a top-level method argument in google-genai 2.8.0; the last frame is in
    # GenerateVideosConfig.  No Models instance or Client is constructed.
    serialized = {
        "method": "Models.generate_videos",
        "model": request.model_id,
        "prompt": {"redacted": True, "sha256": request.prompt.prompt_hash},
        "image": (first_frame.model_dump(mode="json", by_alias=True, exclude_none=True)
                  if first_frame is not None else None),
        "config": config.model_dump(mode="json", by_alias=True, exclude_none=True),
        "compiled_request_hash": request.request_hash,
        "surface_identity_hash": stable_hash(identity),
    }
    return SDKRequestCompatibilityCertification(
        state=SDKCompatibilityState.SERIALIZATION_PROVEN_OFFLINE,
        provider_surface_identity=identity,
        installed_google_genai_version=installed_version,
        request_classes=("Models.generate_videos", "GenerateVideosConfig", "Image"),
        SDK_fields_available=availability,
        SDK_fields_not_model_capabilities=("last_frame", "reference_images", "mask"),
        compiled_request_hash=request.request_hash, serialized_request=serialized,
        serialization_hash=stable_hash(serialized))


class MediaCostCategory(str, Enum):
    SEED_GENERATION = "SEED_GENERATION"
    SEED_QA = "SEED_QA"
    VIDEO_GENERATION = "VIDEO_GENERATION"
    VIDEO_QA = "VIDEO_QA"
    VOICE_GENERATION = "VOICE_GENERATION"
    VOICE_VERIFICATION = "VOICE_VERIFICATION"
    FINAL_REEL_QA = "FINAL_REEL_QA"


class PricedUnitComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    price_key: MediaPriceKey
    units_per_attempt: Decimal = Field(ge=0)
    reason: str

    @field_validator("units_per_attempt")
    @classmethod
    def finite_units(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("priced component units must be finite")
        return value


class ShotMediaProviderDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    execution_mode: str
    video_surface_identity: ProviderSurfaceIdentity
    video_price_key: MediaPriceKey
    duration_seconds: int = Field(gt=0)
    resolution: str
    audio_mode: VideoAudioMode
    seed_required: bool
    seed_surface_identity: Optional[ProviderSurfaceIdentity] = None
    seed_price_key: Optional[MediaPriceKey] = None
    seed_resolution: str = ""
    seed_units_per_attempt: Optional[Decimal] = None
    seed_input_components: tuple[PricedUnitComponent, ...] = ()
    authorized_video_attempts: int = Field(default=1, ge=1, le=10)
    authorized_seed_attempts: int = Field(default=1, ge=1, le=10)
    routing_factors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def surface_and_audio_are_explicit(self) -> "ShotMediaProviderDecision":
        if self.video_price_key.model_id != self.video_surface_identity.model_id \
                or self.video_price_key.product_surface != \
                self.video_surface_identity.product_surface:
            raise ValueError("video price key and execution surface differ")
        if self.video_price_key.resolution != self.resolution:
            raise ValueError("video decision silently changes pricing resolution")
        expected_modality = (MediaOutputModality.VIDEO_AUDIO
                             if self.audio_mode == VideoAudioMode.VIDEO_PLUS_AUDIO
                             else MediaOutputModality.VIDEO)
        if self.video_price_key.output_modality != expected_modality \
                or self.video_price_key.audio_mode != self.audio_mode:
            raise ValueError("video audio decision and pricing identity differ")
        if self.seed_required:
            if self.seed_surface_identity is None or self.seed_price_key is None:
                raise ValueError("required seed needs an exact provider and price identity")
            if self.seed_price_key.model_id != self.seed_surface_identity.model_id \
                    or self.seed_price_key.product_surface != \
                    self.seed_surface_identity.product_surface:
                raise ValueError("seed price key and execution surface differ")
            modalities = {item.price_key.output_modality for item in self.seed_input_components}
            if MediaOutputModality.TEXT_INPUT not in modalities:
                raise ValueError("seed generation must price prompt text input")
        elif self.seed_price_key is not None or self.seed_surface_identity is not None:
            raise ValueError("seed-free shot cannot carry hidden seed spend")
        elif self.seed_input_components:
            raise ValueError("seed-free shot cannot carry seed input spend")
        return self


class AuxiliaryMediaWork(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: MediaCostCategory
    price_key: MediaPriceKey
    base_units: Decimal = Field(ge=0)
    worst_case_units: Decimal = Field(ge=0)
    reason: str

    @model_validator(mode="after")
    def finite_and_ordered(self) -> "AuxiliaryMediaWork":
        if not self.base_units.is_finite() or not self.worst_case_units.is_finite():
            raise ValueError("auxiliary media units must be finite")
        if self.worst_case_units < self.base_units:
            raise ValueError("worst-case units cannot be less than base units")
        return self


class ProviderDecisionSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_label: str = "OFFLINE_SYNTHETIC_NOT_LIVE_AUTHORITY"
    shot_decisions: tuple[ShotMediaProviderDecision, ...]
    auxiliary_work: tuple[AuxiliaryMediaWork, ...]
    mandatory_categories: tuple[MediaCostCategory, ...] = tuple(MediaCostCategory)
    planning_cost: Decimal = Decimal("0")
    planning_pricing_version: str = "ALREADY_PLANNED_OFFLINE_ZERO_INCREMENTAL_COST"
    provider_calls: int = 0

    @model_validator(mode="after")
    def offline_and_finite(self) -> "ProviderDecisionSet":
        non_live = ("OFFLINE" in self.authority_label
                    or self.authority_label == "SYNTHETIC_COST_EXAMPLE_NOT_LIVE_PLAN")
        if not non_live or self.provider_calls:
            raise ValueError("Gate 2D.4B decisions cannot claim live authority or provider calls")
        if not self.planning_cost.is_finite() or self.planning_cost < 0:
            raise ValueError("planning cost must be finite")
        ids = tuple(item.shot_id for item in self.shot_decisions)
        if len(set(ids)) != len(ids):
            raise ValueError("each shot needs exactly one provider decision")
        return self


class MediaCostState(str, Enum):
    COST_PROVABLE = "COST_PROVABLE"
    COST_NOT_PROVABLE = "COST_NOT_PROVABLE"


class MediaCostEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_label: str
    state: MediaCostState
    planning_cost: Decimal
    seed_base_cost: Optional[Decimal]
    seed_worst_case_cost: Optional[Decimal]
    seed_qa_cost: Optional[Decimal]
    seed_qa_worst_case_cost: Optional[Decimal]
    video_base_cost: Optional[Decimal]
    video_worst_case_cost: Optional[Decimal]
    video_qa_cost: Optional[Decimal]
    video_qa_worst_case_cost: Optional[Decimal]
    voice_cost: Optional[Decimal]
    voice_worst_case_cost: Optional[Decimal]
    voice_verification_cost: Optional[Decimal]
    voice_verification_worst_case_cost: Optional[Decimal]
    final_qa_cost: Optional[Decimal]
    final_qa_worst_case_cost: Optional[Decimal]
    total_base_cost: Optional[Decimal]
    total_worst_case_cost: Optional[Decimal]
    category_base_costs: Mapping[str, Optional[Decimal]]
    category_worst_case_costs: Mapping[str, Optional[Decimal]]
    unpriced_categories: tuple[MediaCostCategory, ...]
    pricing_versions: tuple[str, ...]
    envelope_hash: str

    @model_validator(mode="after")
    def totals_follow_provability(self) -> "MediaCostEnvelope":
        material = self.model_dump(mode="json", exclude={"envelope_hash"})
        if self.envelope_hash != stable_hash(material):
            raise ValueError("media cost envelope hash mismatch")
        if self.state == MediaCostState.COST_PROVABLE:
            if self.unpriced_categories or self.total_base_cost is None \
                    or self.total_worst_case_cost is None:
                raise ValueError("provable cost requires complete finite totals")
        elif self.total_base_cost is not None or self.total_worst_case_cost is not None:
            raise ValueError("COST_NOT_PROVABLE must not publish partial totals as totals")
        return self


def _sum_optional(values: Sequence[Optional[Decimal]]) -> Optional[Decimal]:
    if any(value is None for value in values):
        return None
    return sum((value for value in values if value is not None), Decimal("0"))


class CanonicalMediaCostPlanner:
    """Compute hard base and retry maxima from exact shot decisions, never scene averages."""

    def __init__(self, registry: MediaPricingRegistry = DEFAULT_MEDIA_PRICING_REGISTRY) -> None:
        self._registry = registry

    def plan(
        self, plan: CanonicalReelExecutionPlan, decisions: ProviderDecisionSet,
        retry_budget: RetryBudget,
    ) -> MediaCostEnvelope:
        plan_shot_ids = tuple(shot.shot_id for shot in plan.shot_contracts)
        decision_ids = tuple(item.shot_id for item in decisions.shot_decisions)
        if set(plan_shot_ids) != set(decision_ids) or len(plan_shot_ids) != len(decision_ids):
            raise MediaPreconditionError(
                "PROVIDER_DECISIONS_INCOMPLETE", "provider decision set must match every plan shot")
        seed_requirements = {item.shot_id: item.seed_required for item in plan.seed_plan.shots}
        base: dict[MediaCostCategory, Optional[Decimal]] = {
            item: Decimal("0") for item in MediaCostCategory}
        worst: dict[MediaCostCategory, Optional[Decimal]] = {
            item: Decimal("0") for item in MediaCostCategory}
        versions: list[str] = [decisions.planning_pricing_version]
        seen_modalities: dict[MediaCostCategory, set[MediaOutputModality]] = {
            item: set() for item in MediaCostCategory}

        def add(category: MediaCostCategory, key: MediaPriceKey,
                base_units: Decimal, worst_units: Decimal) -> None:
            seen_modalities[category].add(key.output_modality)
            record = self._registry.resolve(key)
            if record is None:
                base[category] = None
                worst[category] = None
                return
            if base[category] is not None:
                base[category] += record.cost(base_units)
            if worst[category] is not None:
                worst[category] += record.cost(worst_units)
            versions.append(record.pricing_version)

        for decision in decisions.shot_decisions:
            if decision.authorized_video_attempts > max(1, retry_budget.video_attempts_per_shot) \
                    or decision.authorized_seed_attempts > \
                    max(1, retry_budget.seed_attempts_per_shot):
                raise MediaPreconditionError(
                    "SHOT_RETRY_EXCEEDS_BUDGET",
                    "per-shot attempts exceed the finite global retry budget")
            if decision.seed_required != seed_requirements.get(decision.shot_id, False):
                raise MediaPreconditionError(
                    "SEED_DECISION_MISMATCH", "seed cost decision differs from canonical seed plan")
            video_record = self._registry.resolve(decision.video_price_key)
            if video_record is None:
                base[MediaCostCategory.VIDEO_GENERATION] = None
                worst[MediaCostCategory.VIDEO_GENERATION] = None
            else:
                video_units = Decimal(decision.duration_seconds)
                if video_record.price_unit != MediaBillingUnit.SECOND:
                    base[MediaCostCategory.VIDEO_GENERATION] = None
                    worst[MediaCostCategory.VIDEO_GENERATION] = None
                else:
                    add(MediaCostCategory.VIDEO_GENERATION, decision.video_price_key,
                        video_units, video_units * decision.authorized_video_attempts)
            if decision.seed_required and decision.seed_price_key is not None:
                seed_record = self._registry.resolve(decision.seed_price_key)
                units = decision.seed_units_per_attempt
                if units is None and seed_record is not None:
                    units = seed_record.units_per_artifact
                if units is None:
                    base[MediaCostCategory.SEED_GENERATION] = None
                    worst[MediaCostCategory.SEED_GENERATION] = None
                else:
                    add(MediaCostCategory.SEED_GENERATION, decision.seed_price_key,
                        units, units * decision.authorized_seed_attempts)
                for component in decision.seed_input_components:
                    add(MediaCostCategory.SEED_GENERATION, component.price_key,
                        component.units_per_attempt,
                        component.units_per_attempt * decision.authorized_seed_attempts)
                seed_plan = next(item for item in plan.seed_plan.shots
                                 if item.shot_id == decision.shot_id)
                input_modalities = {item.price_key.output_modality
                                    for item in decision.seed_input_components}
                if seed_plan.conditioning_references \
                        and MediaOutputModality.IMAGE_INPUT not in input_modalities:
                    base[MediaCostCategory.SEED_GENERATION] = None
                    worst[MediaCostCategory.SEED_GENERATION] = None
        for work in decisions.auxiliary_work:
            add(work.category, work.price_key, work.base_units, work.worst_case_units)

        required_components = {
            MediaCostCategory.SEED_GENERATION: {
                MediaOutputModality.TEXT_INPUT, MediaOutputModality.IMAGE_OUTPUT},
            MediaCostCategory.SEED_QA: {
                MediaOutputModality.TEXT_INPUT, MediaOutputModality.TEXT_OUTPUT},
            MediaCostCategory.VIDEO_QA: {
                MediaOutputModality.TEXT_INPUT, MediaOutputModality.TEXT_OUTPUT},
            MediaCostCategory.VOICE_GENERATION: {
                MediaOutputModality.TEXT_INPUT, MediaOutputModality.AUDIO_OUTPUT},
            MediaCostCategory.VOICE_VERIFICATION: {
                MediaOutputModality.TEXT_INPUT, MediaOutputModality.TEXT_OUTPUT},
            MediaCostCategory.FINAL_REEL_QA: {
                MediaOutputModality.TEXT_INPUT, MediaOutputModality.TEXT_OUTPUT},
        }
        for category, required in required_components.items():
            if category in decisions.mandatory_categories \
                    and not required.issubset(seen_modalities[category]):
                base[category] = None
                worst[category] = None

        mandatory = set(decisions.mandatory_categories)
        unpriced = tuple(category for category in MediaCostCategory
                         if category in mandatory
                         and (base[category] is None or worst[category] is None))
        state = (MediaCostState.COST_NOT_PROVABLE if unpriced
                 else MediaCostState.COST_PROVABLE)
        base_values = [base[category] for category in decisions.mandatory_categories]
        worst_values = [worst[category] for category in decisions.mandatory_categories]
        total_base = (_sum_optional(base_values) + decisions.planning_cost
                      if state == MediaCostState.COST_PROVABLE else None)
        total_worst = (_sum_optional(worst_values) + decisions.planning_cost
                       if state == MediaCostState.COST_PROVABLE else None)

        def value(category: MediaCostCategory, *, maximum: bool = False) -> Optional[Decimal]:
            return (worst if maximum else base)[category]

        values = {
            "authority_label": decisions.authority_label, "state": state,
            "planning_cost": decisions.planning_cost,
            "seed_base_cost": value(MediaCostCategory.SEED_GENERATION),
            "seed_worst_case_cost": value(MediaCostCategory.SEED_GENERATION, maximum=True),
            "seed_qa_cost": value(MediaCostCategory.SEED_QA),
            "seed_qa_worst_case_cost": value(MediaCostCategory.SEED_QA, maximum=True),
            "video_base_cost": value(MediaCostCategory.VIDEO_GENERATION),
            "video_worst_case_cost": value(MediaCostCategory.VIDEO_GENERATION, maximum=True),
            "video_qa_cost": value(MediaCostCategory.VIDEO_QA),
            "video_qa_worst_case_cost": value(MediaCostCategory.VIDEO_QA, maximum=True),
            "voice_cost": value(MediaCostCategory.VOICE_GENERATION),
            "voice_worst_case_cost": value(MediaCostCategory.VOICE_GENERATION, maximum=True),
            "voice_verification_cost": value(MediaCostCategory.VOICE_VERIFICATION),
            "voice_verification_worst_case_cost": value(
                MediaCostCategory.VOICE_VERIFICATION, maximum=True),
            "final_qa_cost": value(MediaCostCategory.FINAL_REEL_QA),
            "final_qa_worst_case_cost": value(MediaCostCategory.FINAL_REEL_QA, maximum=True),
            "total_base_cost": total_base, "total_worst_case_cost": total_worst,
            "category_base_costs": {key.value: val for key, val in base.items()},
            "category_worst_case_costs": {key.value: val for key, val in worst.items()},
            "unpriced_categories": unpriced,
            "pricing_versions": tuple(dict.fromkeys(versions)),
        }
        return MediaCostEnvelope(envelope_hash=stable_hash(values), **values)


class EconomicRoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    considered_factors: tuple[str, ...] = (
        "required_fidelity", "supported_capability", "shot_criticality",
        "identity_risk", "motion_complexity", "production_role")
    tier_rules: Mapping[str, tuple[str, ...]] = {}
    policy_frozen: bool = False
    requires_live_evidence_before_freezing: bool = True


CONFIGURABLE_ECONOMIC_ROUTING_POLICY = EconomicRoutingPolicy(
    policy_id="media-routing-seam-v1-unfrozen",
    tier_rules={
        "ITERATION_OR_LOW_RISK": ("cheaper capable model may be considered",),
        "NORMAL_PRODUCTION": ("Fast may be considered where fidelity is proven sufficient",),
        "HERO_OR_ESCALATION": ("Standard may be considered with explicit escalation",),
    })


class MediaBudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_base_cost: Optional[Decimal] = None
    max_worst_case_cost: Optional[Decimal] = None
    maximum_video_attempts: int = Field(ge=1, le=10)
    maximum_seed_attempts: int = Field(ge=1, le=10)
    model_escalation_allowed: bool = False
    audio_escalation_allowed: bool = False
    approval_required_above: Optional[Decimal] = None
    currency: str = "USD"

    @field_validator("max_base_cost", "max_worst_case_cost", "approval_required_above")
    @classmethod
    def finite_budget(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("media budget values must be finite")
        return value


UNSET_OWNER_MEDIA_BUDGET_POLICY = MediaBudgetPolicy(
    maximum_video_attempts=1, maximum_seed_attempts=1,
    model_escalation_allowed=False, audio_escalation_allowed=False)


class LanguageDirection(str, Enum):
    LTR = "LTR"
    RTL = "RTL"


class SafeZoneReviewStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PENDING_REVIEW = "PENDING_REVIEW"
    UNCONFIGURED = "UNCONFIGURED"


class CoordinateBasis(str, Enum):
    NORMALIZED_RECT = "NORMALIZED_RECT"
    MASK_PIXEL_CANVAS = "MASK_PIXEL_CANVAS"
    NONE = "NONE"


class NormalizedSafeRect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left: Decimal = Field(ge=0, le=1)
    top: Decimal = Field(ge=0, le=1)
    right: Decimal = Field(ge=0, le=1)
    bottom: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def positive_area(self) -> "NormalizedSafeRect":
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("safe-zone rectangle must have positive area")
        return self


class SafeZoneAssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    platform: str
    placement: str
    aspect_ratio: str
    language_direction: LanguageDirection
    source: str
    source_version_date: str
    local_asset_path: str = ""
    local_asset_hash: str = ""
    review_status: SafeZoneReviewStatus
    coordinate_basis: CoordinateBasis
    intended_overlay_categories: tuple[str, ...]
    normalized_safe_rect: Optional[NormalizedSafeRect] = None

    @model_validator(mode="after")
    def verified_means_locally_reviewable(self) -> "SafeZoneAssetRecord":
        if not self.source.startswith("https://"):
            raise ValueError("safe-zone provenance requires an official HTTPS source")
        if self.review_status == SafeZoneReviewStatus.VERIFIED:
            if not self.local_asset_path or len(self.local_asset_hash) != 64:
                raise ValueError("verified safe-zone asset requires local path and SHA-256")
            if self.coordinate_basis == CoordinateBasis.NORMALIZED_RECT \
                    and self.normalized_safe_rect is None:
                raise ValueError("verified normalized geometry requires exact coordinates")
        elif self.local_asset_path or self.local_asset_hash \
                or self.normalized_safe_rect is not None:
            raise ValueError("unconfigured safe zone cannot smuggle guessed geometry")
        return self


class SafeZoneAssetRegistry:
    def __init__(self, records: Sequence[SafeZoneAssetRecord]) -> None:
        self._records: dict[tuple[str, str, str, LanguageDirection], SafeZoneAssetRecord] = {}
        for record in records:
            key = (record.platform, record.placement, record.aspect_ratio,
                   record.language_direction)
            if key in self._records:
                raise ValueError("duplicate platform safe-zone identity")
            self._records[key] = record

    @property
    def records(self) -> tuple[SafeZoneAssetRecord, ...]:
        return tuple(self._records.values())

    def resolve(self, *, platform: str, placement: str, aspect_ratio: str,
                language_direction: LanguageDirection) -> Optional[SafeZoneAssetRecord]:
        return self._records.get((platform, placement, aspect_ratio, language_direction))

    def verify_local_assets(self, repository_root: Path) -> tuple[str, ...]:
        defects: list[str] = []
        root = repository_root.resolve()
        for record in self.records:
            if record.review_status != SafeZoneReviewStatus.VERIFIED:
                continue
            candidate = (root / record.local_asset_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                defects.append(record.policy_id + ":PATH_OUTSIDE_REPOSITORY")
                continue
            if not candidate.is_file():
                defects.append(record.policy_id + ":LOCAL_ASSET_MISSING")
                continue
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if digest != record.local_asset_hash:
                defects.append(record.policy_id + ":LOCAL_ASSET_HASH_MISMATCH")
        return tuple(defects)


META_SAFE_ZONE_SOURCE = "https://www.facebook.com/business/ads/facebook-instagram-reels-ads"
TIKTOK_SAFE_ZONE_SOURCE = (
    "https://ads.tiktok.com/help/article/tiktok-reservation-in-feed-ads-reach-frequency")

UNCONFIGURED_SAFE_ZONE_REGISTRY = SafeZoneAssetRegistry((
    SafeZoneAssetRecord(
        policy_id="INSTAGRAM_REELS_9_16_UNCONFIGURED", platform="INSTAGRAM_REELS",
        placement="REELS", aspect_ratio="9:16", language_direction=LanguageDirection.LTR,
        source=META_SAFE_ZONE_SOURCE, source_version_date=VERIFIED_DATE,
        review_status=SafeZoneReviewStatus.UNCONFIGURED, coordinate_basis=CoordinateBasis.NONE,
        intended_overlay_categories=("CAPTIONS", "CTA", "LOGO", "DISCLAIMER")),
    SafeZoneAssetRecord(
        policy_id="INSTAGRAM_REELS_9_16_RTL_UNCONFIGURED", platform="INSTAGRAM_REELS",
        placement="REELS", aspect_ratio="9:16", language_direction=LanguageDirection.RTL,
        source=META_SAFE_ZONE_SOURCE, source_version_date=VERIFIED_DATE,
        review_status=SafeZoneReviewStatus.UNCONFIGURED, coordinate_basis=CoordinateBasis.NONE,
        intended_overlay_categories=("CAPTIONS", "CTA", "LOGO", "DISCLAIMER")),
    SafeZoneAssetRecord(
        policy_id="TIKTOK_REELS_LTR", platform="TIKTOK_REELS", placement="IN_FEED",
        aspect_ratio="9:16", language_direction=LanguageDirection.LTR,
        source=TIKTOK_SAFE_ZONE_SOURCE, source_version_date=VERIFIED_DATE,
        review_status=SafeZoneReviewStatus.UNCONFIGURED, coordinate_basis=CoordinateBasis.NONE,
        intended_overlay_categories=("CAPTIONS", "CTA", "LOGO", "DISCLAIMER")),
    SafeZoneAssetRecord(
        policy_id="TIKTOK_REELS_ARABIC_RTL", platform="TIKTOK_REELS", placement="IN_FEED",
        aspect_ratio="9:16", language_direction=LanguageDirection.RTL,
        source=TIKTOK_SAFE_ZONE_SOURCE, source_version_date=VERIFIED_DATE,
        review_status=SafeZoneReviewStatus.UNCONFIGURED, coordinate_basis=CoordinateBasis.NONE,
        intended_overlay_categories=("CAPTIONS", "CTA", "LOGO", "DISCLAIMER")),
))


class OverlayBoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    overlay_id: str
    category: str
    left_px: int = Field(ge=0)
    top_px: int = Field(ge=0)
    right_px: int = Field(gt=0)
    bottom_px: int = Field(gt=0)

    @model_validator(mode="after")
    def positive_box(self) -> "OverlayBoundingBox":
        if self.left_px >= self.right_px or self.top_px >= self.bottom_px:
            raise ValueError("overlay bounding box must have positive area")
        return self


class GeometryValidationState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class GeometryValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: GeometryValidationState
    policy_id: str
    canvas_width: int
    canvas_height: int
    failing_overlay_ids: tuple[str, ...] = ()
    reason: str


class PlatformGeometryValidator:
    def validate(
        self, *, canvas_width: int, canvas_height: int,
        overlays: Sequence[OverlayBoundingBox], policy: SafeZoneAssetRecord,
        locale_direction: LanguageDirection,
    ) -> GeometryValidationResult:
        if canvas_width <= 0 or canvas_height <= 0:
            raise ValueError("canvas dimensions must be positive")
        if policy.review_status != SafeZoneReviewStatus.VERIFIED \
                or policy.coordinate_basis != CoordinateBasis.NORMALIZED_RECT \
                or policy.normalized_safe_rect is None:
            return GeometryValidationResult(
                state=GeometryValidationState.NOT_CONFIGURED, policy_id=policy.policy_id,
                canvas_width=canvas_width, canvas_height=canvas_height,
                reason="No reviewed normalized geometry is configured; this is not a pass.")
        if policy.language_direction != locale_direction:
            return GeometryValidationResult(
                state=GeometryValidationState.FAIL, policy_id=policy.policy_id,
                canvas_width=canvas_width, canvas_height=canvas_height,
                failing_overlay_ids=tuple(item.overlay_id for item in overlays),
                reason="Locale direction does not match the reviewed safe-zone asset.")
        safe = policy.normalized_safe_rect
        left = Decimal(canvas_width) * safe.left
        right = Decimal(canvas_width) * safe.right
        top = Decimal(canvas_height) * safe.top
        bottom = Decimal(canvas_height) * safe.bottom
        failing = tuple(item.overlay_id for item in overlays
                        if Decimal(item.left_px) < left or Decimal(item.right_px) > right
                        or Decimal(item.top_px) < top or Decimal(item.bottom_px) > bottom)
        return GeometryValidationResult(
            state=(GeometryValidationState.FAIL if failing else GeometryValidationState.PASS),
            policy_id=policy.policy_id, canvas_width=canvas_width,
            canvas_height=canvas_height, failing_overlay_ids=failing,
            reason=("One or more overlays leave the reviewed safe zone." if failing
                    else "Every overlay is inside the reviewed normalized geometry."))


class ReadinessState(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"


class MediaReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_label: str = "OFFLINE_PRECONDITION_CERTIFICATION_NOT_LIVE_AUTHORITY"
    execution_readiness: ReadinessState
    delivery_readiness: ReadinessState
    capability_proven: bool
    request_serialization_proven: bool
    required_pricing_proven: bool
    finite_cost_computable: bool
    retry_budget_finite: bool
    safe_zone_configured: bool
    compositor_geometry_proven: bool
    final_platform_requirements_proven: bool
    blockers: tuple[str, ...]
    provider_calls: int = 0
    database_reads: int = 0
    database_mutations: int = 0
    spend_usd: Decimal = Decimal("0")

    @model_validator(mode="after")
    def states_match_evidence(self) -> "MediaReadinessReport":
        execution = all((self.capability_proven, self.request_serialization_proven,
                         self.required_pricing_proven, self.finite_cost_computable,
                         self.retry_budget_finite))
        delivery = execution and all((self.safe_zone_configured,
                                      self.compositor_geometry_proven,
                                      self.final_platform_requirements_proven))
        if (self.execution_readiness == ReadinessState.READY) != execution:
            raise ValueError("execution readiness overclaims its evidence")
        if (self.delivery_readiness == ReadinessState.READY) != delivery:
            raise ValueError("delivery readiness overclaims its evidence")
        if any((self.provider_calls, self.database_reads, self.database_mutations)) \
                or self.spend_usd != 0:
            raise ValueError("Gate 2D.4B must remain provider-free, DB-free, and zero spend")
        return self


def evaluate_media_readiness(
    *, capability_proven: bool, request_serialization_proven: bool,
    cost_envelope: MediaCostEnvelope, retry_budget: RetryBudget,
    safe_zone: SafeZoneAssetRecord,
    geometry_result: GeometryValidationResult,
    final_platform_requirements_proven: bool,
) -> MediaReadinessReport:
    retry_finite = retry_budget.total_retry_cost_ceiling_usd.is_finite()
    pricing = cost_envelope.state == MediaCostState.COST_PROVABLE
    safe_zone_ready = safe_zone.review_status == SafeZoneReviewStatus.VERIFIED
    geometry_ready = geometry_result.state == GeometryValidationState.PASS
    conditions = {
        "CAPABILITY_NOT_PROVEN": capability_proven,
        "SDK_SERIALIZATION_NOT_PROVEN": request_serialization_proven,
        "REQUIRED_PRICING_NOT_PROVEN": pricing,
        "FINITE_COST_NOT_COMPUTABLE": pricing,
        "RETRY_BUDGET_NOT_FINITE": retry_finite,
        "SAFE_ZONE_UNCONFIGURED": safe_zone_ready,
        "COMPOSITOR_GEOMETRY_NOT_PROVEN": geometry_ready,
        "FINAL_PLATFORM_REQUIREMENTS_NOT_PROVEN": final_platform_requirements_proven,
    }
    execution = all(tuple(conditions.values())[:5])
    delivery = execution and all(tuple(conditions.values())[5:])
    return MediaReadinessReport(
        execution_readiness=(ReadinessState.READY if execution else ReadinessState.NOT_READY),
        delivery_readiness=(ReadinessState.READY if delivery else ReadinessState.NOT_READY),
        capability_proven=capability_proven,
        request_serialization_proven=request_serialization_proven,
        required_pricing_proven=pricing, finite_cost_computable=pricing,
        retry_budget_finite=retry_finite, safe_zone_configured=safe_zone_ready,
        compositor_geometry_proven=geometry_ready,
        final_platform_requirements_proven=final_platform_requirements_proven,
        blockers=tuple(name for name, satisfied in conditions.items() if not satisfied))


__all__ = [
    "AGENT_PLATFORM_FLASH_IMAGE_IDENTITY", "AGENT_PLATFORM_PRO_IMAGE_IDENTITY",
    "AGENT_PLATFORM_VEO_31_FAST_IDENTITY", "AGENT_PLATFORM_VEO_31_IDENTITY",
    "AuxiliaryMediaWork", "CLOUD_TTS_FLASH_IDENTITY", "CONFIGURED_VERTEX_CAPABILITY_MATRIX",
    "CONFIGURABLE_ECONOMIC_ROUTING_POLICY", "CanonicalMediaCostPlanner",
    "CapabilityEvidence", "CapabilityProofKind", "CapabilityStatus", "CoordinateBasis",
    "DEFAULT_MEDIA_PRICING_REGISTRY", "EconomicRoutingPolicy", "GeometryValidationResult",
    "GeometryValidationState", "LanguageDirection", "MediaBillingUnit", "MediaBudgetPolicy",
    "MediaCostCategory", "MediaCostEnvelope", "MediaCostState", "MediaOutputModality",
    "MediaPreconditionError", "MediaPriceKey", "MediaPricingRecord", "MediaPricingRegistry",
    "MediaPricingStatus", "MediaReadinessReport", "NormalizedSafeRect", "OverlayBoundingBox",
    "PlatformGeometryValidator", "ProductSurface", "ProviderDecisionSet",
    "ProviderSurfaceIdentity", "ReadinessState", "SDKCompatibilityState",
    "SDKRequestCompatibilityCertification", "SafeZoneAssetRecord", "SafeZoneAssetRegistry",
    "SafeZoneReviewStatus", "ShotMediaProviderDecision", "PricedUnitComponent",
    "UNCONFIGURED_SAFE_ZONE_REGISTRY",
    "UNSET_OWNER_MEDIA_BUDGET_POLICY", "VERTEX_GEMINI_25_FLASH_QA_IDENTITY",
    "VERTEX_VEO_31_FAST_IDENTITY",
    "VERTEX_VEO_31_IDENTITY", "VideoAudioMode", "canonical_capability_evidence",
    "certify_google_genai_video_request_offline", "evaluate_media_readiness", "price_key",
    "reviewed_media_pricing_records", "stable_hash",
]
