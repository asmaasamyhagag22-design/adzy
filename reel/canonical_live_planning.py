"""Gate 2D.3 live, text-only canonical Reel planning boundaries.

This module may call one structured *text* provider through the narrow protocol below.  It has no
image, audio, video, search, grounding, approval, or persistence client.  A real provider call is
impossible until exact-surface pricing produces a finite envelope under the owner cap.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .canonical_contract import ReelCreativeContract, RenderableReelAuthority
from .canonical_creative import (
    AudioCategory,
    AudioLayerPlan,
    ConceptDistinctnessResult,
    ConceptDistinctnessState,
    ConceptTournament,
    ConceptTournamentResult,
    CreativeConcept,
    CreativeDefect,
    FitnessState,
    GeometryState,
    OverlayPlan,
    Platform,
    PlatformProfile,
    ReferenceType,
    ReelBlueprint,
    SeedPlan,
    ShotContract,
    ShotExecutionDecision,
    ShotExecutionState,
    TournamentDecision,
    VoiceoverScript,
    assess_concept_distinctness,
    compile_voiceover_script,
    evaluate_concept,
)
from .canonical_execution import (
    DefectType,
    ProviderBillingMode,
    ProviderSurface,
    RepairOwner,
    TextProviderPricingSnapshot,
    resolve_text_provider_pricing,
)


class LivePlanningError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _plain(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return " ".join(
        "".join(char if (char.isalnum() or "\u0600" <= char <= "\u06ff") else " "
                for char in _plain(value).casefold()).split())


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def stable_hash(value: Any) -> str:
    raw = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Provider-independent, render-safe creative context
# ---------------------------------------------------------------------------


class ProvenanceClass(str, Enum):
    RENDER_SAFE = "RENDER_SAFE"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    NOT_RENDERABLE = "NOT_RENDERABLE"


_RENDER_SAFE_KEYS = frozenset({
    "brand_name", "brand_colors", "visual_identity_cues", "offering_names",
    "app_mobile_ordering_capability", "product_category", "allowed_product_focus",
    "language", "locale", "visual_constraints", "known_place_descriptions",
    "packaging_descriptions",
})
_REFERENCE_ONLY_KEYS = frozenset({
    "logo_reference_asset_ids", "product_reference_asset_ids",
    "place_reference_asset_ids", "packaging_reference_asset_ids",
    "owned_visual_reference_asset_ids",
})
_INTERNAL_KEYS = frozenset({
    "competitor_metrics", "cta_counts", "peer_averages", "review_volume",
    "research_sufficiency", "strategic_rationale", "swot", "audience_prevalence",
    "ratings_as_proof", "reviews_as_proof", "internal_evidence",
})


class CreativeContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    value: str | tuple[str, ...]
    provenance_class: ProvenanceClass
    source_id: str
    description: str = ""

    @model_validator(mode="after")
    def key_matches_provenance(self) -> "CreativeContextItem":
        key = _norm(self.key).replace(" ", "_")
        if not self.source_id:
            raise ValueError("creative context requires a real provenance source id")
        if self.provenance_class == ProvenanceClass.RENDER_SAFE and key not in _RENDER_SAFE_KEYS:
            raise ValueError("field is not allowlisted for render-safe planning context")
        if self.provenance_class == ProvenanceClass.REFERENCE_ONLY \
                and key not in _REFERENCE_ONLY_KEYS:
            raise ValueError("reference-only context may contain only asset ids/descriptions")
        if key in _INTERNAL_KEYS and self.provenance_class != ProvenanceClass.NOT_RENDERABLE:
            raise ValueError("internal research context cannot be renderable")
        return self


class CreativeContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pack_id: str
    strategy_version_id: str
    reel_contract_hash: str
    items: tuple[CreativeContextItem, ...]
    pack_hash: str

    @model_validator(mode="after")
    def deterministic_and_unique(self) -> "CreativeContextPack":
        keys = [item.key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("creative context keys must be unique")
        material = self.model_dump(mode="json", exclude={"pack_id", "pack_hash"})
        if self.pack_hash != stable_hash(material):
            raise ValueError("creative context hash mismatch")
        return self

    def planner_projection(self) -> Mapping[str, Any]:
        """Return the only context payload a live concept proposer may receive."""
        render_safe = {
            item.key: item.value for item in self.items
            if item.provenance_class == ProvenanceClass.RENDER_SAFE
        }
        references = tuple({
            "key": item.key,
            "asset_ids": item.value,
            "description": item.description,
            "source_id": item.source_id,
        } for item in self.items if item.provenance_class == ProvenanceClass.REFERENCE_ONLY)
        return {
            "pack_id": self.pack_id,
            "pack_hash": self.pack_hash,
            "render_safe": render_safe,
            "reference_only": references,
        }


def create_creative_context_pack(*, strategy_version_id: str, reel_contract_hash: str,
                                 items: Sequence[CreativeContextItem]) -> CreativeContextPack:
    material = {
        "strategy_version_id": strategy_version_id,
        "reel_contract_hash": reel_contract_hash,
        "items": [_jsonable(item) for item in items],
    }
    digest = stable_hash(material)
    return CreativeContextPack(
        pack_id="ccp_" + digest[:24], pack_hash=digest,
        strategy_version_id=strategy_version_id, reel_contract_hash=reel_contract_hash,
        items=tuple(items))


class SafeAssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str
    source: str
    asset_type: ReferenceType
    description: str
    source_hash: str
    consumer_visible: bool
    provider_compatible: bool
    QA_only: bool


# ---------------------------------------------------------------------------
# Exact-surface text pricing, finite preflight, structured calls and traces
# ---------------------------------------------------------------------------


class TextModelStatus(str, Enum):
    CONFIGURED_SUPPORTED = "CONFIGURED_SUPPORTED"
    CONFIGURED_PRICE_UNVERIFIED = "CONFIGURED_PRICE_UNVERIFIED"
    DEPRECATED = "DEPRECATED"
    SHUT_DOWN = "SHUT_DOWN"


class TextModelConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    surface: ProviderSurface
    model_id: str
    billing_mode: ProviderBillingMode = ProviderBillingMode.STANDARD
    status: TextModelStatus
    supports_structured_output: bool
    suitable_for_creative_reasoning: bool
    requires_search_grounding: bool = False
    configured_project: str = ""
    configured_location: str = ""
    pricing: Optional[TextProviderPricingSnapshot] = None

    @model_validator(mode="after")
    def price_matches_model_surface(self) -> "TextModelConfiguration":
        if self.pricing and (
            self.pricing.provider_name != self.provider
            or self.pricing.surface != self.surface
            or self.pricing.model_id != self.model_id
            or self.pricing.billing_mode != self.billing_mode
        ):
            raise ValueError("pricing belongs to a different provider/model/surface/billing mode")
        return self


def canonical_google_vertex_planning_configuration(
    *, project: str, location: str, model_id: str = "gemini-2.5-flash",
    billing_mode: ProviderBillingMode = ProviderBillingMode.STANDARD,
) -> TextModelConfiguration:
    pricing = resolve_text_provider_pricing(
        provider_name="google", model_id=model_id, surface=ProviderSurface.VERTEX_AI,
        billing_mode=billing_mode)
    return TextModelConfiguration(
        provider="google", surface=ProviderSurface.VERTEX_AI, model_id=model_id,
        billing_mode=billing_mode,
        status=(TextModelStatus.CONFIGURED_SUPPORTED if pricing is not None
                else TextModelStatus.CONFIGURED_PRICE_UNVERIFIED),
        supports_structured_output=True, suitable_for_creative_reasoning=True,
        requires_search_grounding=False,
        configured_project=project, configured_location=location, pricing=pricing)


def choose_lowest_cost_configured_model(
    configurations: Sequence[TextModelConfiguration],
) -> Optional[TextModelConfiguration]:
    eligible = [item for item in configurations if (
        item.status == TextModelStatus.CONFIGURED_SUPPORTED
        and item.supports_structured_output
        and item.suitable_for_creative_reasoning
        and not item.requires_search_grounding
        and item.pricing is not None
    )]
    if not eligible:
        return None
    return min(eligible, key=lambda item: (
        item.pricing.input_text_image_video_usd_per_million
        + item.pricing.output_text_response_reasoning_usd_per_million,
        item.model_id,
    ))


class PlanningPurpose(str, Enum):
    CONCEPT_PROPOSER = "CONCEPT_PROPOSER"
    BLIND_CREATIVE_CRITIC = "BLIND_CREATIVE_CRITIC"
    TARGETED_CONCEPT_REPAIR = "TARGETED_CONCEPT_REPAIR"


class PlanningCallBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: PlanningPurpose
    maximum_input_tokens: int = Field(gt=0)
    maximum_output_tokens: int = Field(gt=0)
    maximum_thinking_tokens: int = Field(ge=0)
    input_bound_basis: str = "legacy-unrecorded"
    output_bound_basis: str = "legacy-unrecorded"
    thinking_bound_basis: str = "legacy-unrecorded"


class PlanningCostState(str, Enum):
    FINITE_AUTHORIZED = "FINITE_AUTHORIZED"
    COST_NOT_PROVABLE = "COST_NOT_PROVABLE"
    PLANNING_COST_CAP_EXCEEDED = "PLANNING_COST_CAP_EXCEEDED"
    COST_CAP_EXCEEDED = "PLANNING_COST_CAP_EXCEEDED"
    MODEL_NOT_ELIGIBLE = "MODEL_NOT_ELIGIBLE"


class PlanningCostEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    surface: ProviderSurface
    model_id: str
    billing_mode: ProviderBillingMode = ProviderBillingMode.STANDARD
    state: PlanningCostState
    pricing_source: str = ""
    pricing_version: str = ""
    input_usd_per_million_tokens: Optional[Decimal] = None
    output_usd_per_million_tokens: Optional[Decimal] = None
    cached_input_usd_per_million_tokens: Optional[Decimal] = None
    call_budgets: tuple[PlanningCallBudget, ...]
    maximum_live_calls: int = 3
    maximum_schema_retries: int = 1
    maximum_authorized_spend_usd: Decimal = Decimal("0.15")
    worst_case_cost_usd: Optional[Decimal] = None

    @model_validator(mode="after")
    def bounded_and_consistent(self) -> "PlanningCostEnvelope":
        if self.maximum_live_calls != 3 or self.maximum_schema_retries != 1:
            raise ValueError("Gate 2D.3 permits three live calls and one schema retry")
        if len(self.call_budgets) != 3 or len({item.purpose for item in self.call_budgets}) != 3:
            raise ValueError("the finite envelope needs proposer, critic, and repair budgets")
        if not self.maximum_authorized_spend_usd.is_finite() \
                or self.maximum_authorized_spend_usd != Decimal("0.15"):
            raise ValueError("planning spend cap must be exactly USD 0.15")
        if self.state == PlanningCostState.FINITE_AUTHORIZED:
            values = (self.input_usd_per_million_tokens,
                      self.output_usd_per_million_tokens,
                      self.cached_input_usd_per_million_tokens,
                      self.worst_case_cost_usd)
            if any(value is None or not value.is_finite() for value in values):
                raise ValueError("authorized planning requires finite prices and worst case")
            if not self.pricing_source or not self.pricing_version:
                raise ValueError("authorized planning requires versioned price provenance")
            if self.worst_case_cost_usd > self.maximum_authorized_spend_usd:
                raise ValueError("authorized planning cannot exceed its owner cap")
        elif self.worst_case_cost_usd is not None and not self.worst_case_cost_usd.is_finite():
            raise ValueError("a reported worst case must be finite")
        return self


DEFAULT_LIVE_CALL_BUDGETS = (
    PlanningCallBudget(purpose=PlanningPurpose.CONCEPT_PROPOSER,
                       maximum_input_tokens=16000, maximum_output_tokens=7000,
                       maximum_thinking_tokens=4000,
                       input_bound_basis=(
                           "UTF-8 byte upper-bound enforced on system + payload + response schema"),
                       output_bound_basis=(
                           "SDK max_output_tokens for one structured batch of at most four concepts"),
                       thinking_bound_basis="SDK thinking_budget for creative proposal reasoning"),
    PlanningCallBudget(purpose=PlanningPurpose.BLIND_CREATIVE_CRITIC,
                       maximum_input_tokens=18000, maximum_output_tokens=5000,
                       maximum_thinking_tokens=3000,
                       input_bound_basis=(
                           "UTF-8 byte upper-bound for system + anonymized payload + response schema"),
                       output_bound_basis=(
                           "SDK max_output_tokens for four qualitative structured critiques"),
                       thinking_bound_basis="SDK thinking_budget for qualitative comparison"),
    PlanningCallBudget(purpose=PlanningPurpose.TARGETED_CONCEPT_REPAIR,
                       maximum_input_tokens=16000, maximum_output_tokens=7000,
                       maximum_thinking_tokens=4000,
                       input_bound_basis=(
                           "UTF-8 byte upper-bound for system + repair payload + response schema"),
                       output_bound_basis=(
                           "SDK max_output_tokens for selective replacement concepts only"),
                       thinking_bound_basis="SDK thinking_budget for targeted repair reasoning"),
)


def build_planning_cost_envelope(
    configuration: TextModelConfiguration,
    *, call_budgets: Sequence[PlanningCallBudget] = DEFAULT_LIVE_CALL_BUDGETS,
    cap_usd: Decimal = Decimal("0.15"),
) -> PlanningCostEnvelope:
    common = dict(
        provider=configuration.provider, surface=configuration.surface,
        model_id=configuration.model_id, billing_mode=configuration.billing_mode,
        call_budgets=tuple(call_budgets),
        maximum_authorized_spend_usd=cap_usd,
    )
    eligible = (
        configuration.status in {
            TextModelStatus.CONFIGURED_SUPPORTED,
            TextModelStatus.CONFIGURED_PRICE_UNVERIFIED,
        }
        and configuration.supports_structured_output
        and configuration.suitable_for_creative_reasoning
        and not configuration.requires_search_grounding
    )
    if not eligible:
        return PlanningCostEnvelope(state=PlanningCostState.MODEL_NOT_ELIGIBLE, **common)
    price = configuration.pricing
    if price is None:
        return PlanningCostEnvelope(state=PlanningCostState.COST_NOT_PROVABLE, **common)
    per_call_costs = tuple((
        Decimal(item.maximum_input_tokens)
        * price.input_text_image_video_usd_per_million
        + Decimal(item.maximum_output_tokens + item.maximum_thinking_tokens)
        * price.output_text_response_reasoning_usd_per_million
    ) / Decimal(1_000_000) for item in call_budgets)
    # A schema retry consumes a normal call slot.  Three times the largest declared call is a
    # conservative upper bound across proposer+retry+critic or proposer+critic+repair.
    worst = max(per_call_costs) * Decimal(3)
    state = (PlanningCostState.FINITE_AUTHORIZED if worst <= cap_usd
             else PlanningCostState.PLANNING_COST_CAP_EXCEEDED)
    return PlanningCostEnvelope(
        state=state, pricing_source=price.pricing_source_url,
        pricing_version=price.pricing_version_id,
        input_usd_per_million_tokens=price.input_text_image_video_usd_per_million,
        output_usd_per_million_tokens=price.output_text_response_reasoning_usd_per_million,
        cached_input_usd_per_million_tokens=price.cached_input_usd_per_million,
        worst_case_cost_usd=worst, **common)


class LiveConceptBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    concepts: tuple[CreativeConcept, ...] = Field(min_length=3, max_length=4)
    concise_rationale: str

    @model_validator(mode="after")
    def unique_concepts(self) -> "LiveConceptBatch":
        if len({item.concept_id for item in self.concepts}) != len(self.concepts):
            raise ValueError("live concepts require unique ids")
        return self


class CriticState(str, Enum):
    STRONG = "STRONG"
    ACCEPTABLE = "ACCEPTABLE"
    WEAK = "WEAK"
    BLOCKING = "BLOCKING"


class CriticDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hook_fitness: CriticState
    target_relevance: CriticState
    platform_nativeness: CriticState
    value_demonstrability: CriticState
    product_prominence: CriticState
    narrative_coherence: CriticState
    visual_specificity: CriticState
    cliché_risk: CriticState
    production_feasibility: CriticState
    repairability: CriticState


class ConceptCritique(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    anonymized_label: str
    dimensions: CriticDimensions
    strongest_creative_reason: str
    biggest_weakness: str
    specific_repair_suggestion: str


class BlindCriticBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    critiques: tuple[ConceptCritique, ...] = Field(min_length=2, max_length=4)
    concise_rationale: str

    @model_validator(mode="after")
    def labels_are_unique(self) -> "BlindCriticBatch":
        labels = [item.anonymized_label for item in self.critiques]
        if len(labels) != len(set(labels)):
            raise ValueError("critic labels must be unique")
        return self


class TargetedConceptRepairBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repaired_concepts: tuple[CreativeConcept, ...] = Field(min_length=1, max_length=4)
    repaired_original_concept_ids: tuple[str, ...]
    concise_rationale: str

    @model_validator(mode="after")
    def replacement_is_selective(self) -> "TargetedConceptRepairBatch":
        if len(self.repaired_concepts) != len(self.repaired_original_concept_ids):
            raise ValueError("targeted repair must replace only named original concepts")
        return self


class StructuredPlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: PlanningPurpose
    system_instruction: str
    payload: Mapping[str, Any]
    input_artifact_hashes: tuple[str, ...]
    maximum_output_tokens: int = Field(gt=0)
    maximum_thinking_tokens: int = Field(ge=0)
    retry_index: int = Field(default=0, ge=0, le=1)


class StructuredProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payload: Any
    provider_timestamp: datetime
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)
    thinking_tokens: int = Field(ge=0)
    model_id: str
    concise_rationale: str = ""
    provider_usage_metadata: Mapping[str, int | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def cached_tokens_are_input_subset(self) -> "StructuredProviderResponse":
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input tokens cannot exceed prompt input tokens")
        return self


R = TypeVar("R", bound=BaseModel)


class StructuredTextPlanningProvider(Protocol):
    def generate_structured(
        self, request: StructuredPlanningRequest, response_model: type[R],
    ) -> StructuredProviderResponse: ...


class GoogleVertexStructuredTextProvider:
    """One-attempt Google Vertex adapter: JSON schema, bounded output/thinking, no tools.

    Pricing is intentionally absent here. The call controller must pass preflight using an
    independently versioned exact-surface price record before this adapter can be reached.
    """

    def __init__(self, configuration: TextModelConfiguration,
                 envelope: PlanningCostEnvelope, *, client: Any = None) -> None:
        if configuration.provider.casefold() != "google" \
                or configuration.surface != ProviderSurface.VERTEX_AI:
            raise LivePlanningError("TEXT_PROVIDER_SURFACE_MISMATCH",
                                    "Google Vertex adapter requires the Vertex surface")
        if configuration.billing_mode != ProviderBillingMode.STANDARD:
            raise LivePlanningError("TEXT_PROVIDER_BILLING_MODE_MISMATCH",
                                    "this adapter is configured for Vertex Standard requests")
        if not configuration.configured_project or not configuration.configured_location:
            raise LivePlanningError("TEXT_PROVIDER_CONFIGURATION_MISSING",
                                    "Vertex project and location are required")
        if envelope.state != PlanningCostState.FINITE_AUTHORIZED:
            raise LivePlanningError("COST_NOT_PROVABLE",
                                    "Vertex adapter cannot exist before finite cost preflight")
        if (envelope.provider, envelope.surface, envelope.model_id,
                envelope.billing_mode) != (
            configuration.provider, configuration.surface, configuration.model_id,
            configuration.billing_mode,
        ):
            raise LivePlanningError("TEXT_PROVIDER_ENVELOPE_MISMATCH",
                                    "cost envelope belongs to another model surface")
        self.configuration = configuration
        self.envelope = envelope
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(
                vertexai=True, project=self.configuration.configured_project,
                location=self.configuration.configured_location)
        return self._client

    def generate_structured(
        self, request: StructuredPlanningRequest, response_model: type[R],
    ) -> StructuredProviderResponse:
        from google.genai import types

        budget = next(item for item in self.envelope.call_budgets
                      if item.purpose == request.purpose)
        if request.maximum_output_tokens > budget.maximum_output_tokens \
                or request.maximum_thinking_tokens > budget.maximum_thinking_tokens:
            raise LivePlanningError("LIVE_CALL_BUDGET_EXCEEDED",
                                    "Vertex request exceeds its finite cost envelope")

        response = self._get_client().models.generate_content(
            model=self.configuration.model_id,
            contents=json.dumps(_jsonable(request.payload), ensure_ascii=False,
                                sort_keys=True, separators=(",", ":")),
            config=types.GenerateContentConfig(
                system_instruction=request.system_instruction,
                temperature=0.8,
                response_mime_type="application/json",
                response_schema=response_model,
                max_output_tokens=request.maximum_output_tokens,
                thinking_config=types.ThinkingConfig(
                    include_thoughts=False,
                    thinking_budget=request.maximum_thinking_tokens),
            ),
        )
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, BaseModel):
            payload: Any = parsed.model_dump(mode="python")
        elif parsed is not None:
            payload = parsed
        else:
            try:
                payload = json.loads(getattr(response, "text", "") or "")
            except json.JSONDecodeError:
                payload = getattr(response, "text", "") or ""
        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        cached_input_tokens = int(getattr(usage, "cached_content_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        thinking_tokens = int(getattr(usage, "thoughts_token_count", 0) or 0)
        finish_reason = ""
        candidates = getattr(response, "candidates", None) or ()
        if candidates:
            raw_reason = getattr(candidates[0], "finish_reason", None)
            finish_reason = _plain(getattr(raw_reason, "name", raw_reason))
        rationale = _plain(payload.get("concise_rationale")) \
            if isinstance(payload, Mapping) else ""
        return StructuredProviderResponse(
            payload=payload, provider_timestamp=datetime.now(timezone.utc),
            input_tokens=input_tokens, cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens, thinking_tokens=thinking_tokens,
            model_id=_plain(getattr(response, "model_version", ""))
            or self.configuration.model_id,
            concise_rationale=rationale,
            provider_usage_metadata={
                "prompt_token_count": input_tokens,
                "cached_content_token_count": cached_input_tokens,
                "candidates_token_count": output_tokens,
                "thoughts_token_count": thinking_tokens,
                "finish_reason": finish_reason,
            })


class LiveModelCallTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    surface: ProviderSurface
    model_id: str
    timestamp: datetime
    request_purpose: PlanningPurpose
    input_artifact_hashes: tuple[str, ...]
    input_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    thinking_tokens: int
    billable_output_tokens: int
    total_billable_tokens: int
    structured_output_valid: bool
    retry_index: int
    cost_usd: Decimal
    pricing_version_id: str
    provider_usage_metadata: Mapping[str, int | str]
    concise_rationale: str = ""

    @model_validator(mode="after")
    def billed_categories_reconcile(self) -> "LiveModelCallTrace":
        if self.cached_input_tokens + self.uncached_input_tokens != self.input_tokens:
            raise ValueError("cached and uncached input must reconcile to prompt input")
        if self.billable_output_tokens != self.output_tokens + self.thinking_tokens:
            raise ValueError("reasoning tokens must be billed with response output")
        if self.total_billable_tokens != self.input_tokens + self.billable_output_tokens:
            raise ValueError("total billable token units do not reconcile")
        if not self.pricing_version_id:
            raise ValueError("actual accounting requires the pricing snapshot version")
        return self


class LiveStructuredCallController:
    """Counts every network attempt; schema retries consume the global three-call budget."""

    def __init__(self, *, configuration: TextModelConfiguration,
                 envelope: PlanningCostEnvelope,
                 provider: StructuredTextPlanningProvider) -> None:
        self.configuration = configuration
        self.envelope = envelope
        self.provider = provider
        self.traces: list[LiveModelCallTrace] = []
        self._schema_retries = 0

    @property
    def provider_calls(self) -> int:
        return len(self.traces)

    @property
    def actual_cost_usd(self) -> Decimal:
        return sum((item.cost_usd for item in self.traces), Decimal("0"))

    def _budget(self, purpose: PlanningPurpose) -> PlanningCallBudget:
        return next(item for item in self.envelope.call_budgets if item.purpose == purpose)

    def _guard(
        self, request: StructuredPlanningRequest, response_model: type[BaseModel],
    ) -> PlanningCallBudget:
        if self.envelope.state != PlanningCostState.FINITE_AUTHORIZED:
            code = ("COST_NOT_PROVABLE" if self.envelope.state ==
                    PlanningCostState.COST_NOT_PROVABLE else self.envelope.state.value)
            raise LivePlanningError(code, "live text planning is blocked before provider call")
        if self.provider_calls >= self.envelope.maximum_live_calls:
            raise LivePlanningError("LIVE_CALL_LIMIT_EXCEEDED", "three-call limit reached")
        if request.retry_index > self.envelope.maximum_schema_retries:
            raise LivePlanningError("LIVE_SCHEMA_RETRY_LIMIT_EXCEEDED", "only one retry is allowed")
        budget = self._budget(request.purpose)
        if request.maximum_output_tokens > budget.maximum_output_tokens \
                or request.maximum_thinking_tokens > budget.maximum_thinking_tokens:
            raise LivePlanningError("LIVE_CALL_BUDGET_EXCEEDED", "request exceeds finite envelope")
        # Token count cannot exceed the serialized UTF-8 byte count, so this binds every billed
        # input component without relying on a provider call or a mutable tokenizer package.
        bounded_input = {
            "system_instruction": request.system_instruction,
            "payload": request.payload,
            "response_schema": response_model.model_json_schema(),
        }
        serialized_bytes = len(json.dumps(
            _jsonable(bounded_input), ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8"))
        if serialized_bytes > budget.maximum_input_tokens:
            raise LivePlanningError("LIVE_INPUT_BUDGET_EXCEEDED", "bounded input is too large")
        return budget

    def call(self, request: StructuredPlanningRequest, response_model: type[R],
             *, allow_schema_retry: bool = False) -> R:
        self._guard(request, response_model)
        response = self.provider.generate_structured(request, response_model)
        if response.model_id != self.configuration.model_id:
            raise LivePlanningError("LIVE_MODEL_ID_MISMATCH", "provider returned another model")
        in_rate = self.envelope.input_usd_per_million_tokens or Decimal("0")
        out_rate = self.envelope.output_usd_per_million_tokens or Decimal("0")
        cached_rate = self.envelope.cached_input_usd_per_million_tokens or Decimal("0")
        uncached_input_tokens = response.input_tokens - response.cached_input_tokens
        billable_output_tokens = response.output_tokens + response.thinking_tokens
        cost = (
            Decimal(uncached_input_tokens) * in_rate
            + Decimal(response.cached_input_tokens) * cached_rate
            + Decimal(billable_output_tokens) * out_rate
        ) / Decimal(1_000_000)
        valid = True
        parsed: Optional[R] = None
        validation_message = ""
        try:
            parsed = response_model.model_validate(response.payload)
        except ValidationError as error:
            valid = False
            validation_message = "; ".join(
                f"{'.'.join(str(v) for v in item['loc'])}: {item['type']}"
                for item in error.errors(include_url=False)[:8])
        self.traces.append(LiveModelCallTrace(
            provider=self.configuration.provider, surface=self.configuration.surface,
            model_id=response.model_id, timestamp=response.provider_timestamp,
            request_purpose=request.purpose,
            input_artifact_hashes=request.input_artifact_hashes,
            input_tokens=response.input_tokens,
            cached_input_tokens=response.cached_input_tokens,
            uncached_input_tokens=uncached_input_tokens,
            output_tokens=response.output_tokens, thinking_tokens=response.thinking_tokens,
            billable_output_tokens=billable_output_tokens,
            total_billable_tokens=response.input_tokens + billable_output_tokens,
            structured_output_valid=valid, retry_index=request.retry_index,
            cost_usd=cost, pricing_version_id=self.envelope.pricing_version,
            provider_usage_metadata=response.provider_usage_metadata,
            concise_rationale=response.concise_rationale))
        if self.actual_cost_usd > self.envelope.maximum_authorized_spend_usd:
            raise LivePlanningError("PLANNING_COST_CAP_EXCEEDED", "actual planning cap exceeded")
        if valid:
            assert parsed is not None
            return parsed
        if not allow_schema_retry or self._schema_retries >= 1 \
                or self.provider_calls >= self.envelope.maximum_live_calls:
            raise LivePlanningError("LIVE_PROPOSER_SCHEMA_INVALID", validation_message)
        self._schema_retries += 1
        retry = request.model_copy(update={
            "retry_index": 1,
            "payload": {**dict(request.payload), "targeted_schema_repair": validation_message},
        })
        return self.call(retry, response_model, allow_schema_retry=False)


def build_proposer_request(*, contract: ReelCreativeContract, context: CreativeContextPack,
                           profile: PlatformProfile,
                           budget: PlanningCallBudget) -> StructuredPlanningRequest:
    if profile.platform != Platform.INSTAGRAM_REELS:
        raise LivePlanningError("PLATFORM_PROFILE_MISMATCH", "first live test requires Instagram")
    authority: RenderableReelAuthority = contract.planner_authority()
    payload = {
        "reel_contract": authority.model_dump(mode="json"),
        "creative_context": context.planner_projection(),
        "platform_profile": profile.model_dump(mode="json"),
        "creative_freedom": [item.value for item in authority.creative_free_fields],
        "requirements": {
            "concept_count": "3-4",
            "structured_output": "LiveConceptBatch",
            "video_not_poster": True,
            "consumer_semantics_must_be_claim_intents": True,
            "no_proof_when_proof_points_empty": True,
            "no_search_or_grounding": True,
        },
    }
    return StructuredPlanningRequest(
        purpose=PlanningPurpose.CONCEPT_PROPOSER,
        system_instruction=(
            "Create materially distinct, platform-native short-video concepts. Return only the "
            "supplied structured schema. Do not reveal hidden reasoning."),
        payload=payload,
        input_artifact_hashes=(contract.contract_hash, context.pack_hash,
                               stable_hash(profile)),
        maximum_output_tokens=budget.maximum_output_tokens,
        maximum_thinking_tokens=budget.maximum_thinking_tokens)


def build_blind_critic_request(*, contract: ReelCreativeContract,
                               profile: PlatformProfile,
                               concepts: Sequence[CreativeConcept],
                               budget: PlanningCallBudget) -> StructuredPlanningRequest:
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    anonymized = ({
        "label": labels[index],
        "concept": concept.model_dump(
            mode="json", exclude={"concept_id", "concept_fingerprint"}),
    } for index, concept in enumerate(concepts))
    payload = {
        "reel_contract": contract.planner_authority().model_dump(mode="json"),
        "platform_profile": profile.model_dump(mode="json"),
        "anonymized_concepts": tuple(anonymized),
        "critic_authority": "creative quality only; cannot authorize claims or alter strategy",
        "numeric_scores_forbidden": True,
    }
    return StructuredPlanningRequest(
        purpose=PlanningPurpose.BLIND_CREATIVE_CRITIC,
        system_instruction=(
            "Return qualitative creative criticism in the supplied schema. Never authorize a "
            "claim, change strategy, select a winner, or provide hidden reasoning."),
        payload=payload,
        input_artifact_hashes=(contract.contract_hash, stable_hash(profile),
                               stable_hash([item.concept_fingerprint for item in concepts])),
        maximum_output_tokens=budget.maximum_output_tokens,
        maximum_thinking_tokens=budget.maximum_thinking_tokens)


def selective_repair_concept_ids(
    concepts: Sequence[CreativeConcept],
    distinctness: ConceptDistinctnessResult,
    defects: Sequence[CreativeDefect] = (),
) -> tuple[str, ...]:
    known = {item.concept_id for item in concepts}
    targeted: list[str] = []
    for left, right in distinctness.duplicate_pairs:
        if right in known and right not in targeted:
            targeted.append(right)
    for defect in defects:
        if defect.concept_id in known and defect.concept_id not in targeted:
            targeted.append(defect.concept_id)
    return tuple(targeted)


# ---------------------------------------------------------------------------
# Qualitative critic + deterministic selection (no weights)
# ---------------------------------------------------------------------------


class LiveTournamentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: TournamentDecision
    selected_concept_id: str = ""
    canonical_tournament: ConceptTournamentResult
    ordered_critic_dimensions: tuple[str, ...]
    explanation: tuple[str, ...]


_CRITIC_ORDER = (
    "hook_fitness", "target_relevance", "platform_nativeness",
    "value_demonstrability", "product_prominence", "narrative_coherence",
    "visual_specificity", "cliché_risk", "production_feasibility", "repairability",
)
_CRITIC_BAND = {
    CriticState.BLOCKING: 0, CriticState.WEAK: 1,
    CriticState.ACCEPTABLE: 2, CriticState.STRONG: 3,
}


def run_live_tournament(*, contract: ReelCreativeContract, profile: PlatformProfile,
                        concepts: Sequence[CreativeConcept],
                        critic: BlindCriticBatch) -> LiveTournamentResult:
    canonical = ConceptTournament().run(contract, profile, concepts)
    if canonical.decision == TournamentDecision.NO_VIABLE_CONCEPT:
        return LiveTournamentResult(
            decision=canonical.decision, canonical_tournament=canonical,
            ordered_critic_dimensions=_CRITIC_ORDER,
            explanation=("Canonical hard gates rejected the candidate set.",))
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    label_by_id = {item.concept_id: labels[index] for index, item in enumerate(concepts)}
    critique = {item.anonymized_label: item for item in critic.critiques}
    viable = [item.concept_id for item in canonical.fitness if not item.fails_hard_gate()]
    if set(label_by_id[item] for item in viable) - set(critique):
        raise LivePlanningError("BLIND_CRITIC_COVERAGE_INCOMPLETE",
                                "critic omitted a canonically viable concept")
    survivors = [item for item in viable if not any(
        getattr(critique[label_by_id[item]].dimensions, dimension) == CriticState.BLOCKING
        for dimension in _CRITIC_ORDER)]
    explanation = ["Canonical strategy and claim hard gates retained authority."]
    if not survivors:
        return LiveTournamentResult(
            decision=TournamentDecision.NO_VIABLE_CONCEPT,
            canonical_tournament=canonical, ordered_critic_dimensions=_CRITIC_ORDER,
            explanation=tuple((*explanation, "Every survivor had a blocking creative defect.")))
    for dimension in _CRITIC_ORDER:
        if len(survivors) <= 1:
            break
        best = max(_CRITIC_BAND[getattr(
            critique[label_by_id[item]].dimensions, dimension)] for item in survivors)
        narrowed = [item for item in survivors if _CRITIC_BAND[getattr(
            critique[label_by_id[item]].dimensions, dimension)] == best]
        if len(narrowed) < len(survivors):
            explanation.append(
                f"Qualitative ordered comparison retained {', '.join(narrowed)} on {dimension}.")
        survivors = narrowed
    if len(survivors) != 1:
        return LiveTournamentResult(
            decision=TournamentDecision.TIE_REQUIRES_REVIEW,
            canonical_tournament=canonical, ordered_critic_dimensions=_CRITIC_ORDER,
            explanation=tuple((*explanation, "No winner after ordered criteria; no weights used.")))
    return LiveTournamentResult(
        decision=TournamentDecision.SELECTED, selected_concept_id=survivors[0],
        canonical_tournament=canonical, ordered_critic_dimensions=_CRITIC_ORDER,
        explanation=tuple((*explanation, f"Selected {survivors[0]} deterministically.")))


# ---------------------------------------------------------------------------
# Creative red team, typed shot semantics, references, audio, and preview
# ---------------------------------------------------------------------------


class BrandSwapResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_id: str
    brand_specific: bool
    defect: Optional[CreativeDefect] = None
    evidence: tuple[str, ...] = ()


def brand_swap_test(concept: CreativeConcept,
                    context: CreativeContextPack) -> BrandSwapResult:
    projection = context.planner_projection()["render_safe"]
    brand_elements: list[str] = []
    for key in ("brand_name", "offering_names", "visual_identity_cues",
                "known_place_descriptions", "packaging_descriptions"):
        raw = projection.get(key, ())
        values = (raw,) if isinstance(raw, str) else tuple(raw)
        brand_elements.extend(_plain(item) for item in values if len(_norm(item)) >= 3)
    treatment = " ".join(_plain(getattr(concept, key)) for key in (
        "creative_thesis", "hook_mechanism", "narrative_device", "value_demonstration",
        "product_role", "brand_entry", "visual_grammar", "reveal_pattern"))
    matched = tuple(item for item in brand_elements if _norm(item) in _norm(treatment))
    if matched:
        return BrandSwapResult(concept_id=concept.concept_id, brand_specific=True,
                               evidence=matched)
    return BrandSwapResult(
        concept_id=concept.concept_id, brand_specific=False,
        defect=CreativeDefect(
            defect_type=DefectType.CONCEPT_TOO_GENERIC, repair_owner=RepairOwner.CONCEPT,
            concept_id=concept.concept_id,
            detail="Treatment survives a restaurant-brand swap without meaningful loss."))


def generic_asset_sequence_defect(concept: CreativeConcept) -> Optional[CreativeDefect]:
    combined = _norm(" ".join((concept.creative_thesis, concept.narrative_device,
                               concept.reveal_pattern)))
    generic_phrases = ("show burger show app show cta", "show product show app show cta",
                       "product app cta", "burger app cta")
    progression_terms = ("reverse", "tension", "match cut", "transformation", "journey",
                         "reveal", "choice", "cause", "payoff")
    if any(phrase in combined for phrase in generic_phrases) \
            and not any(term in combined for term in progression_terms):
        return CreativeDefect(
            defect_type=DefectType.GENERIC_ASSET_SEQUENCE,
            repair_owner=RepairOwner.CONCEPT, concept_id=concept.concept_id,
            detail="Concept is an asset list rather than a progressing short-video treatment.")
    return None


class EarlyAttentionWindow(str, Enum):
    FIRST_VISUAL = "FIRST_VISUAL"
    ONE_TO_THREE_SECONDS = "ONE_TO_THREE_SECONDS"
    THREE_TO_SIX_SECONDS = "THREE_TO_SIX_SECONDS"


class ObservationState(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    NOT_ASSESSED = "NOT_ASSESSED"


class EarlyAttentionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window: EarlyAttentionWindow
    hook_clarity: ObservationState
    value_visibility: ObservationState
    product_visibility: ObservationState
    brand_visibility: ObservationState
    observation: str


def review_early_attention(blueprint: ReelBlueprint) -> tuple[EarlyAttentionObservation, ...]:
    windows = ((EarlyAttentionWindow.FIRST_VISUAL, 0.25),
               (EarlyAttentionWindow.ONE_TO_THREE_SECONDS, 3.0),
               (EarlyAttentionWindow.THREE_TO_SIX_SECONDS, 6.0))
    results = []
    for window, deadline in windows:
        beats = [item for item in blueprint.beat_sequence if item.start_seconds < deadline]
        results.append(EarlyAttentionObservation(
            window=window,
            hook_clarity=(ObservationState.PRESENT if any(
                item.role.value == "HOOK" for item in beats) else ObservationState.ABSENT),
            value_visibility=(ObservationState.PRESENT if any(
                item.communicates_proposition for item in beats) else ObservationState.ABSENT),
            product_visibility=(ObservationState.PRESENT if any(
                item.product_visible for item in beats) else ObservationState.ABSENT),
            brand_visibility=(ObservationState.PRESENT if any(
                item.brand_visible for item in beats) else ObservationState.ABSENT),
            observation="Plan observation only; no retention or performance prediction."))
    return tuple(results)


class PhoneAppSceneMode(str, Enum):
    REAL_UI_REQUIRED = "REAL_UI_REQUIRED"
    ABSTRACT_PHONE_CONTEXT_SUFFICIENT = "ABSTRACT_PHONE_CONTEXT_SUFFICIENT"
    DETERMINISTIC_SCREEN_COMPOSITE_REQUIRED = "DETERMINISTIC_SCREEN_COMPOSITE_REQUIRED"


class PhysicalActionState(str, Enum):
    PLAUSIBLE = "PLAUSIBLE"
    NEEDS_REPAIR = "NEEDS_REPAIR"
    BLOCKING = "BLOCKING"


class LiveShotContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot: ShotContract
    beginning_visual_state: str
    ending_visual_state: str
    product_role: str
    physical_action_state: PhysicalActionState
    action_bindings: tuple[str, ...]
    phone_app_scene_mode: Optional[PhoneAppSceneMode] = None
    generative_ui_typography_allowed: bool = False
    main_QA_risk: str

    @model_validator(mode="after")
    def specific_and_physically_bound(self) -> "LiveShotContract":
        fields = (self.beginning_visual_state, self.ending_visual_state,
                  self.product_role, self.main_QA_risk)
        if any(len(_norm(item)) < 5 for item in fields):
            raise ValueError("live shot states, product role, and QA risk must be specific")
        action = _norm(self.shot.action_spec)
        vague = {"show user enjoying burger", "show user enjoying product", "show product"}
        if action in vague:
            raise ValueError("SHOT_ACTION_UNDERSPECIFIED")
        needs_phone_mode = any(term in action or term in _norm(self.shot.subject_spec)
                               for term in ("phone", "app", "screen", "interface"))
        if needs_phone_mode and self.phone_app_scene_mode is None:
            raise ValueError("phone/app shots require an explicit UI mode")
        if self.phone_app_scene_mode in {
            PhoneAppSceneMode.REAL_UI_REQUIRED,
            PhoneAppSceneMode.DETERMINISTIC_SCREEN_COMPOSITE_REQUIRED,
        } and self.generative_ui_typography_allowed:
            raise ValueError("exact app UI/text cannot be delegated to generative video")
        if self.physical_action_state != PhysicalActionState.PLAUSIBLE \
                and not self.action_bindings:
            raise ValueError("action risk needs explicit actor/object bindings")
        return self


class ReferenceFeasibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    assets: tuple[SafeAssetRecord, ...]
    missing_types: tuple[ReferenceType, ...]
    execution_fitness_reduced: bool
    defects: tuple[CreativeDefect, ...]


def audit_live_references(shot: ShotContract,
                          assets: Sequence[SafeAssetRecord]) -> ReferenceFeasibility:
    available = {item.asset_type for item in assets}
    missing = tuple(item for item in shot.reference_requirements if item not in available)
    defects = tuple(CreativeDefect(
        defect_type=DefectType.MISSING_REQUIRED_REFERENCE,
        repair_owner=RepairOwner.SEED, shot_id=shot.shot_id,
        detail=f"Required real reference is absent: {item.value}") for item in missing)
    return ReferenceFeasibility(
        shot_id=shot.shot_id, assets=tuple(assets), missing_types=missing,
        execution_fitness_reduced=bool(missing), defects=defects)


class CapabilityRepairState(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    PLAN_REPAIR = "PLAN_REPAIR"
    NO_AUTHORIZED_PROVIDER_FOR_CONCEPT = "NO_AUTHORIZED_PROVIDER_FOR_CONCEPT"


class CapabilityAwareRepairDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_id: str
    state: CapabilityRepairState
    affected_shot_ids: tuple[str, ...]
    same_creative_idea_preserved: bool
    explanation: str


def decide_capability_aware_repair(
    concept: CreativeConcept, decisions: Sequence[ShotExecutionDecision], *,
    supported_reexpression_preserves_idea: bool,
) -> CapabilityAwareRepairDecision:
    affected = tuple(item.shot_id for item in decisions if item.state in {
        ShotExecutionState.UNSUPPORTED_REQUIRED_CAPABILITY,
        ShotExecutionState.NEEDS_PLAN_REPAIR,
        ShotExecutionState.NO_AUTHORIZED_PROVIDER,
    })
    if not affected:
        state = CapabilityRepairState.ELIGIBLE
    elif supported_reexpression_preserves_idea:
        state = CapabilityRepairState.PLAN_REPAIR
    else:
        state = CapabilityRepairState.NO_AUTHORIZED_PROVIDER_FOR_CONCEPT
    return CapabilityAwareRepairDecision(
        concept_id=concept.concept_id, state=state, affected_shot_ids=affected,
        same_creative_idea_preserved=supported_reexpression_preserves_idea,
        explanation=("Same treatment can be re-expressed through an authorized mode."
                     if state == CapabilityRepairState.PLAN_REPAIR else
                     "Authorized capability is sufficient." if state == CapabilityRepairState.ELIGIBLE
                     else "Required capability cannot be removed without creative collapse."))


class VoiceoverChoice(str, Enum):
    NO_VOICEOVER = "NO_VOICEOVER"
    MINIMAL_MARKETING_VOICEOVER = "MINIMAL_MARKETING_VOICEOVER"
    FULL_MARKETING_VOICEOVER = "FULL_MARKETING_VOICEOVER"


class LiveAudioPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    choice: VoiceoverChoice
    environment_audio: AudioLayerPlan
    SFX: AudioLayerPlan
    music: AudioLayerPlan
    voiceover_script: Optional[VoiceoverScript]
    exact_CTA: str
    language: str
    locale: str
    owner_language_choice_required: bool
    defects: tuple[CreativeDefect, ...]
    provider_selected: bool = False

    @model_validator(mode="after")
    def voiceover_is_optional_and_exact(self) -> "LiveAudioPlan":
        if self.choice == VoiceoverChoice.NO_VOICEOVER and self.voiceover_script is not None:
            raise ValueError("no-voiceover choice cannot carry a script")
        if self.choice != VoiceoverChoice.NO_VOICEOVER:
            if self.voiceover_script is None:
                raise ValueError("marketing voiceover requires an exact typed script")
            if self.voiceover_script.CTA_line != self.exact_CTA:
                raise ValueError("voiceover CTA drift")
        if self.provider_selected:
            raise ValueError("Gate 2D.3 cannot select or call an audio provider")
        return self


def compile_live_audio_plan(contract: ReelCreativeContract, *, choice: VoiceoverChoice,
                            music_direction: str,
                            environment_direction: str = "natural environment bed",
                            sfx_direction: str = "editorial accents planned only") -> LiveAudioPlan:
    uncertain = not _plain(contract.locale) or _norm(contract.locale) in {"und", "unknown"}
    defects = (() if not uncertain else (CreativeDefect(
        defect_type=DefectType.LANGUAGE_VARIETY_NEEDS_OWNER_CHOICE,
        repair_owner=RepairOwner.AUDIO,
        detail="Canonical locale is insufficient for a spoken language variety choice."),))
    script = None if choice == VoiceoverChoice.NO_VOICEOVER \
        else compile_voiceover_script(contract, time_budget=(4.0 if choice ==
                                      VoiceoverChoice.MINIMAL_MARKETING_VOICEOVER else 8.0))
    return LiveAudioPlan(
        choice=choice,
        environment_audio=AudioLayerPlan(
            category=AudioCategory.ENVIRONMENT_AUDIO, planned=True,
            direction=environment_direction),
        SFX=AudioLayerPlan(category=AudioCategory.SFX, planned=True,
                           direction=sfx_direction),
        music=AudioLayerPlan(category=AudioCategory.MUSIC, planned=True,
                             direction=music_direction),
        voiceover_script=script, exact_CTA=contract.exact_cta,
        language=contract.language or "und", locale=contract.locale,
        owner_language_choice_required=uncertain, defects=defects)


def render_storyboard_preview(
    *, blueprint: ReelBlueprint, live_shots: Sequence[LiveShotContract],
    overlay_plan: OverlayPlan, mode_decisions: Sequence[ShotExecutionDecision],
    reference_audits: Sequence[ReferenceFeasibility], audio_plan: LiveAudioPlan,
) -> str:
    beats = {item.beat_id: item for item in blueprint.beat_sequence}
    decisions = {item.shot_id: item for item in mode_decisions}
    references = {item.shot_id: item for item in reference_audits}
    lines = ["# Provider-free storyboard preview", ""]
    for item in live_shots:
        beat = beats[item.shot.beat_id]
        decision = decisions.get(item.shot.shot_id)
        refs = references.get(item.shot.shot_id)
        overlays = [overlay.text for overlay in overlay_plan.items
                    if overlay.temporal_range[0] < beat.end_seconds
                    and overlay.temporal_range[1] > beat.start_seconds]
        claim_states = tuple(intent.verification_status.value for intent in (
            *item.shot.claim_intents, *item.shot.visual_implication_intents)) or ("NO_CLAIM",)
        lines.extend((
            f"## {item.shot.shot_id} · {beat.start_seconds:.1f}–{beat.end_seconds:.1f}s",
            f"- TIME: {beat.start_seconds:.1f}–{beat.end_seconds:.1f}s",
            f"- PURPOSE: {item.shot.creative_purpose}",
            f"- WHAT WE SEE: {item.beginning_visual_state} → {item.ending_visual_state}",
            f"- WHAT WE HEAR: {item.shot.audio_intent}; VO={audio_plan.choice.value}",
            f"- OVERLAY: {', '.join(overlays) if overlays else 'NONE'}",
            f"- STRATEGIC ROLE: {item.shot.strategic_role}",
            f"- CLAIM STATUS: {', '.join(claim_states)}",
            f"- GENERATION MODE: {decision.execution_mode.value if decision else 'UNRESOLVED'}",
            f"- REFERENCE REQUIREMENTS: {', '.join(v.value for v in item.shot.reference_requirements) or 'NONE'}"
            f"; missing={','.join(v.value for v in refs.missing_types) if refs else 'NOT_AUDITED'}",
            f"- MAIN QA RISK: {item.main_QA_risk}",
            "",
        ))
    return "\n".join(lines).rstrip() + "\n"


class NextGateCostState(str, Enum):
    FINITE = "FINITE"
    COST_NOT_PROVABLE = "COST_NOT_PROVABLE"


class NextGateCostItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str
    provider: str
    surface: ProviderSurface
    model_id: str
    base_units: Decimal
    worst_case_units: Decimal
    unit: str
    unit_price_usd: Optional[Decimal]
    pricing_source: str = ""
    pricing_version: str = ""

    @model_validator(mode="after")
    def units_and_price_are_honest(self) -> "NextGateCostItem":
        if any(not item.is_finite() or item < 0
               for item in (self.base_units, self.worst_case_units)):
            raise ValueError("next-gate units must be finite and non-negative")
        if self.worst_case_units < self.base_units:
            raise ValueError("worst case cannot be below base plan")
        if self.unit_price_usd is not None and (
            not self.unit_price_usd.is_finite() or self.unit_price_usd < 0
            or not self.pricing_source or not self.pricing_version
        ):
            raise ValueError("priced next-gate items need finite versioned provenance")
        return self


class NextGateCostEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: NextGateCostState
    items: tuple[NextGateCostItem, ...]
    base_plan_cost_usd: Optional[Decimal]
    worst_case_authorized_cost_usd: Optional[Decimal]
    provider_calls: int = 0

    @model_validator(mode="after")
    def totals_match_provability(self) -> "NextGateCostEnvelope":
        unpriced = any(item.unit_price_usd is None for item in self.items)
        if unpriced and self.state != NextGateCostState.COST_NOT_PROVABLE:
            raise ValueError("an unpriced exact surface must remain COST_NOT_PROVABLE")
        if self.state == NextGateCostState.COST_NOT_PROVABLE and (
            self.base_plan_cost_usd is not None
            or self.worst_case_authorized_cost_usd is not None
        ):
            raise ValueError("unknown total cannot be represented as a finite dollar value")
        if self.provider_calls != 0:
            raise ValueError("next-gate estimate cannot make provider calls")
        return self


def build_next_gate_cost_envelope(
    items: Sequence[NextGateCostItem],
) -> NextGateCostEnvelope:
    if any(item.unit_price_usd is None for item in items):
        return NextGateCostEnvelope(
            state=NextGateCostState.COST_NOT_PROVABLE, items=tuple(items),
            base_plan_cost_usd=None, worst_case_authorized_cost_usd=None)
    base = sum((item.base_units * item.unit_price_usd for item in items), Decimal("0"))
    worst = sum((item.worst_case_units * item.unit_price_usd for item in items), Decimal("0"))
    return NextGateCostEnvelope(
        state=NextGateCostState.FINITE, items=tuple(items),
        base_plan_cost_usd=base, worst_case_authorized_cost_usd=worst)


class LivePlanState(str, Enum):
    PLAN_READY_FOR_HUMAN_REVIEW = "PLAN_READY_FOR_HUMAN_REVIEW"
    NEEDS_CREATIVE_REPAIR = "NEEDS_CREATIVE_REPAIR"
    LIVE_REEL_PLAN_BLOCKED = "LIVE_REEL_PLAN_BLOCKED"


class CanonicalInputInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_strategy_version_id: str
    real_strategy_loaded: bool
    fixture_used: bool = False
    source: str
    verified_locked_values: Mapping[str, Any] = Field(default_factory=dict)
    blocker_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def no_fixture_substitution(self) -> "CanonicalInputInspection":
        if self.fixture_used:
            raise ValueError("Gate 2D.3 cannot substitute a fixture for the live canonical input")
        return self


class LivePlanAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gate: str = "2D.3"
    state: LivePlanState
    canonical_input: CanonicalInputInspection
    text_model_configuration: TextModelConfiguration
    planning_cost_envelope: PlanningCostEnvelope
    provider_calls: int = 0
    actual_cost_usd: Decimal = Decimal("0")
    media_provider_calls: int = 0
    search_or_grounding_calls: int = 0
    approval_consumed: bool = False
    DB_schema_modified: bool = False
    traces: tuple[LiveModelCallTrace, ...] = ()
    blocker_codes: tuple[str, ...] = ()
    missing_review_artifacts: tuple[str, ...] = ()
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def terminal_truth(self) -> "LivePlanAudit":
        if self.provider_calls != len(self.traces):
            raise ValueError("trace count must equal provider call count")
        if any((self.media_provider_calls, self.search_or_grounding_calls)):
            raise ValueError("Gate 2D.3 is text-only and ungrounded")
        if self.approval_consumed or self.DB_schema_modified:
            raise ValueError("review planning cannot authorize execution or mutate schema")
        if self.state == LivePlanState.LIVE_REEL_PLAN_BLOCKED and not self.blocker_codes:
            raise ValueError("a blocked live plan needs typed blocker codes")
        return self


REVIEW_ARTIFACT_FILENAMES = (
    "reel_contract.json", "creative_context.json", "concepts.json", "critic.json",
    "tournament.json", "blueprint.json", "coverage_graph.json", "shot_contracts.json",
    "references.json", "audio_plan.json", "overlay_plan.json", "execution_plan.json",
    "storyboard_preview.md", "cost_envelope.json", "live_plan_audit.json",
)


__all__ = [
    "BlindCriticBatch", "BrandSwapResult", "CanonicalInputInspection",
    "CapabilityAwareRepairDecision", "CapabilityRepairState", "ConceptCritique",
    "CreativeContextItem", "CreativeContextPack", "CriticDimensions", "CriticState",
    "DEFAULT_LIVE_CALL_BUDGETS", "EarlyAttentionObservation", "EarlyAttentionWindow",
    "GoogleVertexStructuredTextProvider",
    "LiveAudioPlan", "LiveConceptBatch", "LiveModelCallTrace", "LivePlanAudit",
    "LivePlanState", "LivePlanningError", "LiveShotContract", "LiveStructuredCallController",
    "LiveTournamentResult", "NextGateCostEnvelope", "NextGateCostItem", "NextGateCostState",
    "ObservationState", "PhoneAppSceneMode", "PhysicalActionState", "PlanningCallBudget",
    "PlanningCostEnvelope", "PlanningCostState", "PlanningPurpose", "ProvenanceClass",
    "REVIEW_ARTIFACT_FILENAMES", "ReferenceFeasibility", "SafeAssetRecord",
    "StructuredPlanningRequest", "StructuredProviderResponse", "StructuredTextPlanningProvider",
    "TargetedConceptRepairBatch", "TextModelConfiguration", "TextModelStatus",
    "VoiceoverChoice", "audit_live_references", "brand_swap_test",
    "build_blind_critic_request", "build_next_gate_cost_envelope",
    "build_planning_cost_envelope", "build_proposer_request",
    "canonical_google_vertex_planning_configuration", "choose_lowest_cost_configured_model",
    "compile_live_audio_plan",
    "create_creative_context_pack", "decide_capability_aware_repair",
    "generic_asset_sequence_defect", "render_storyboard_preview", "review_early_attention",
    "run_live_tournament", "selective_repair_concept_ids", "stable_hash",
]
