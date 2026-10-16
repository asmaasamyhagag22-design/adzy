"""Offline creative-intelligence core for canonical Reel plans.

The module consumes only the renderable projection of an approved ReelCreativeContract.  It
contains no provider client, credential lookup, network transport, prompt builder, renderer, or
persistence adapter.  Provider metadata is data used for eligibility decisions, never execution.
"""
from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .canonical_contract import (
    ClaimAuthorizationState,
    ClaimIntent,
    ClaimSurface,
    ClaimType,
    ClaimVerificationStatus,
    ReelCreativeContract,
    RenderableReelAuthority,
    authorize_claim,
)
from .canonical_execution import (
    CANONICAL_PROVIDER_REGISTRY,
    DEFECT_REPAIR_OWNER,
    GEMINI_OMNI_FLASH_EXPERIMENTAL,
    DefectType,
    PaidProviderCategory,
    ProviderAuthorizationStatus,
    RepairOwner,
    VideoProviderCapabilities,
)


class CreativeCoreError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _plain(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    text = re.sub(r"[^\w\u0600-\u06ff]+", " ", _plain(value).casefold()).strip()
    synonyms = {
        "quick": "speed", "quickly": "speed", "fast": "speed", "rapid": "speed",
        "ordering": "order", "orders": "order", "purchase": "order",
        "application": "app", "mobile": "app", "deal": "value", "deals": "value",
        "creator": "person", "actor": "person", "talent": "person",
    }
    return " ".join(synonyms.get(token, token) for token in text.split())


def _stable_hash(value: Any) -> str:
    def canonical(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return canonical(item.model_dump(mode="json"))
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, Mapping):
            return {str(key): canonical(child) for key, child in item.items()}
        if isinstance(item, (list, tuple, set, frozenset)):
            return [canonical(child) for child in item]
        return item

    raw = json.dumps(canonical(value), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _tokens(value: Any) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "to", "with", "for", "of", "in", "on",
            "من", "في", "على", "مع", "إلى", "و"}
    return {token for token in _norm(value).split() if len(token) >= 2 and token not in stop}


def _semantic_overlap(left: Any, right: Any) -> bool:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return False
    return len(a & b) / max(1, min(len(a), len(b))) >= 0.6


class Platform(str, Enum):
    INSTAGRAM_REELS = "INSTAGRAM_REELS"
    TIKTOK = "TIKTOK"
    YOUTUBE_SHORTS = "YOUTUBE_SHORTS"


class GeometryState(str, Enum):
    CONFIGURED = "CONFIGURED"
    UNCONFIGURED = "UNCONFIGURED"


class SafeZoneGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)
    left: float = Field(ge=0, le=1)


class SafeZonePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    version: str
    geometry_state: GeometryState
    geometry: Optional[SafeZoneGeometry] = None
    conceptual_reservations: tuple[str, ...] = ()
    source_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def geometry_matches_state(self) -> "SafeZonePolicy":
        if self.geometry_state == GeometryState.UNCONFIGURED and self.geometry is not None:
            raise ValueError("unconfigured safe-zone policy cannot carry invented geometry")
        if self.geometry_state == GeometryState.CONFIGURED and self.geometry is None:
            raise ValueError("configured safe-zone policy requires versioned geometry")
        return self


class TimingGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deadline_seconds: Optional[float] = Field(default=None, gt=0)
    guidance_only: bool = True
    basis: str


class DurationGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_seconds: Optional[float] = Field(default=None, gt=0)
    maximum_seconds: Optional[float] = Field(default=None, gt=0)
    preferred_minimum_seconds: Optional[float] = Field(default=None, gt=0)
    preferred_maximum_seconds: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_range(self) -> "DurationGuidance":
        if (self.minimum_seconds is not None and self.maximum_seconds is not None
                and self.minimum_seconds > self.maximum_seconds):
            raise ValueError("duration minimum exceeds maximum")
        if (self.preferred_minimum_seconds is not None
                and self.preferred_maximum_seconds is not None
                and self.preferred_minimum_seconds > self.preferred_maximum_seconds):
            raise ValueError("preferred duration minimum exceeds maximum")
        return self


class PlatformProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    platform: Platform
    aspect_ratio: str
    recommended_orientation: str
    sound_expectation: str
    social_native_expectation: str
    hook_window: TimingGuidance
    proposition_window: TimingGuidance
    brand_presence_guidance: str
    caption_guidance: str
    safe_zone_policy_id: str
    CTA_expectation: str
    duration_guidance: DurationGuidance
    source_version: str
    source_notes: tuple[str, ...]


UNCONFIGURED_SAFE_ZONES: Mapping[Platform, SafeZonePolicy] = {
    platform: SafeZonePolicy(
        policy_id=f"safe-zone-{platform.value.casefold()}-unconfigured-v1",
        version="1",
        geometry_state=GeometryState.UNCONFIGURED,
        conceptual_reservations=("reserve key messages, captions, logo, and CTA inside safe area",),
        source_notes=("No versioned official coordinates are configured locally.",),
    ) for platform in Platform
}


PLATFORM_PROFILES: Mapping[Platform, PlatformProfile] = {
    Platform.TIKTOK: PlatformProfile(
        profile_id="tiktok-creative-guidance-v1", platform=Platform.TIKTOK,
        aspect_ratio="9:16", recommended_orientation="VERTICAL",
        sound_expectation="SOUND_EXPECTED",
        social_native_expectation="SOCIAL_NATIVE_NOT_OVER_POLISHED",
        hook_window=TimingGuidance(deadline_seconds=6, basis="current official guidance"),
        proposition_window=TimingGuidance(deadline_seconds=3, basis="current official guidance"),
        brand_presence_guidance="Make the brand/product legible without forcing a logo every frame.",
        caption_guidance="Reserve platform-safe caption space; geometry must be configured.",
        safe_zone_policy_id=UNCONFIGURED_SAFE_ZONES[Platform.TIKTOK].policy_id,
        CTA_expectation="A clear close/CTA is a supported starting structure.",
        duration_guidance=DurationGuidance(),
        source_version="gate-2d2-official-guidance-check-2026-08-23",
        source_notes=("Vertical 9:16; at least 720p; sound; safe-zone aware.",
                      "Hook, body/selling point, and close are guidance, not a fixed story template."),
    ),
    Platform.INSTAGRAM_REELS: PlatformProfile(
        profile_id="instagram-reels-creative-guidance-v1", platform=Platform.INSTAGRAM_REELS,
        aspect_ratio="9:16", recommended_orientation="VERTICAL",
        sound_expectation="AUDIO_EXPECTED",
        social_native_expectation="MOBILE_NATIVE",
        hook_window=TimingGuidance(basis="no exact local deadline configured"),
        proposition_window=TimingGuidance(basis="no exact local deadline configured"),
        brand_presence_guidance="Keep brand and product legible in mobile-native creative.",
        caption_guidance="Keep key messages in the safe zone; geometry must be configured.",
        safe_zone_policy_id=UNCONFIGURED_SAFE_ZONES[Platform.INSTAGRAM_REELS].policy_id,
        CTA_expectation="Provide an intentional CTA/close when the contract contains a CTA.",
        duration_guidance=DurationGuidance(),
        source_version="gate-2d2-official-guidance-check-2026-08-23",
        source_notes=("Vertical 9:16, audio, safe-zone-aware key messages, mobile-native creative.",),
    ),
    Platform.YOUTUBE_SHORTS: PlatformProfile(
        profile_id="youtube-shorts-creative-guidance-v1", platform=Platform.YOUTUBE_SHORTS,
        aspect_ratio="9:16", recommended_orientation="VERTICAL",
        sound_expectation="SOUND_ON",
        social_native_expectation="SOCIAL_FIRST",
        hook_window=TimingGuidance(basis="no exact local deadline configured"),
        proposition_window=TimingGuidance(basis="no exact local deadline configured"),
        brand_presence_guidance="Use legible product/brand moments suited to vertical viewing.",
        caption_guidance="Reserve caption space without assuming platform geometry.",
        safe_zone_policy_id=UNCONFIGURED_SAFE_ZONES[Platform.YOUTUBE_SHORTS].policy_id,
        CTA_expectation="Use a concise action-oriented close where appropriate.",
        duration_guidance=DurationGuidance(
            preferred_minimum_seconds=10, preferred_maximum_seconds=30),
        source_version="gate-2d2-official-guidance-check-2026-08-23",
        source_notes=("Vertical, sound-on, social-first; under 60 seconds preferred for this plan.",
                      "10–30 seconds is guidance for action-oriented work, not a result prediction."),
    ),
}


def platform_profile(platform: Platform) -> PlatformProfile:
    return PLATFORM_PROFILES[platform]


class CreativeBoundaries(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_hash: str
    strategy_version_id: str
    immutable_target: str
    immutable_objective: str
    immutable_frame_of_reference: str
    immutable_consumer_value: str
    immutable_primary_message: str
    exact_cta: str
    allowed_claim_ids: tuple[str, ...]
    proof_claim_ids: tuple[str, ...]
    prohibited_claims: tuple[str, ...]
    prohibited_visual_implications: tuple[str, ...]
    creative_freedoms: tuple[str, ...]


def creative_boundaries(contract: ReelCreativeContract) -> CreativeBoundaries:
    authority = contract.planner_authority()
    return CreativeBoundaries(
        contract_hash=authority.contract_hash,
        strategy_version_id=authority.strategy_version_id,
        immutable_target=authority.target,
        immutable_objective=authority.objective_description,
        immutable_frame_of_reference=authority.frame_of_reference,
        immutable_consumer_value=authority.consumer_value,
        immutable_primary_message=authority.primary_message,
        exact_cta=authority.exact_cta,
        allowed_claim_ids=authority.allowed_claim_ids,
        proof_claim_ids=tuple(item.claim_id for item in authority.proof_points),
        prohibited_claims=authority.prohibited_claims,
        prohibited_visual_implications=authority.prohibited_visual_implications,
        creative_freedoms=tuple(item.value for item in authority.creative_free_fields),
    )


class ActorMode(str, Enum):
    NO_ACTOR = "NO_ACTOR"
    HANDS_ONLY = "HANDS_ONLY"
    SINGLE_ACTOR = "SINGLE_ACTOR"
    MULTIPLE_ACTORS = "MULTIPLE_ACTORS"


class ProductionComplexity(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CONTINUOUS_GENERATION = "CONTINUOUS_GENERATION"


class RepairabilityIntent(str, Enum):
    MODULAR_REPAIRABLE = "MODULAR_REPAIRABLE"
    CONTINUOUS_DEPENDENCY = "CONTINUOUS_DEPENDENCY"


class ProductPresencePolicy(str, Enum):
    OPENING = "OPENING"
    EARLY = "EARLY"
    DISTRIBUTED = "DISTRIBUTED"
    PAYOFF = "PAYOFF"
    CONTINUOUS_WHERE_PLAUSIBLE = "CONTINUOUS_WHERE_PLAUSIBLE"


class BeatRole(str, Enum):
    HOOK = "HOOK"
    CONTEXT = "CONTEXT"
    TENSION = "TENSION"
    PRODUCT_REVEAL = "PRODUCT_REVEAL"
    VALUE_DEMONSTRATION = "VALUE_DEMONSTRATION"
    PROOF = "PROOF"
    PAYOFF = "PAYOFF"
    REACTION = "REACTION"
    BRAND_MOMENT = "BRAND_MOMENT"
    CTA = "CTA"


class CreativeConcept(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_id: str
    reel_contract_hash: str
    platform: Platform
    creative_thesis: str
    hook_mechanism: str
    narrative_device: str
    audience_tension: str
    value_demonstration: str
    product_role: str
    brand_entry: str
    emotional_lever: str
    visual_grammar: str
    reveal_pattern: str
    actor_mode: ActorMode
    pacing_intent: str
    audio_direction: str
    CTA_transition: str
    claim_intents: tuple[ClaimIntent, ...]
    visual_implication_intents: tuple[ClaimIntent, ...]
    expected_duration_range: tuple[float, float]
    production_complexity: ProductionComplexity
    required_capabilities: tuple[str, ...]
    creative_risks: tuple[str, ...]
    why_it_may_fail: tuple[str, ...]
    declared_target: str
    preserved_consumer_value: str
    declared_CTA: str
    planned_beat_roles: tuple[BeatRole, ...]
    product_presence_policy: ProductPresencePolicy
    repairability_intent: RepairabilityIntent = RepairabilityIntent.MODULAR_REPAIRABLE
    concept_fingerprint: str

    @model_validator(mode="after")
    def structurally_valid(self) -> "CreativeConcept":
        if not 2 <= len(self.planned_beat_roles) <= 12:
            raise ValueError("a creative concept needs a bounded flexible beat proposal")
        if self.expected_duration_range[0] <= 0 \
                or self.expected_duration_range[0] > self.expected_duration_range[1]:
            raise ValueError("invalid expected duration range")
        if any(intent.surface != ClaimSurface.VISUAL_IMPLICATION
               for intent in self.visual_implication_intents):
            raise ValueError("visual implication intents must use the visual implication surface")
        expected = _concept_fingerprint_material(self)
        if self.concept_fingerprint != _stable_hash(expected):
            raise ValueError("concept fingerprint does not match its creative treatment")
        return self


def _concept_fingerprint_material(concept: Any) -> Mapping[str, Any]:
    def value(name: str) -> Any:
        if isinstance(concept, Mapping):
            return concept.get(name)
        return getattr(concept, name)

    def json_value(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, tuple):
            return [json_value(v) for v in item]
        return item

    fields = (
        "reel_contract_hash", "platform", "creative_thesis", "hook_mechanism",
        "narrative_device", "audience_tension", "value_demonstration", "product_role",
        "brand_entry", "emotional_lever", "visual_grammar", "reveal_pattern", "actor_mode",
        "pacing_intent", "audio_direction", "CTA_transition", "claim_intents",
        "visual_implication_intents", "expected_duration_range", "production_complexity",
        "required_capabilities", "creative_risks", "why_it_may_fail", "declared_target",
        "preserved_consumer_value", "declared_CTA", "planned_beat_roles",
        "product_presence_policy", "repairability_intent",
    )
    return {field: json_value(value(field)) for field in fields}


def create_creative_concept(**values: Any) -> CreativeConcept:
    material = _concept_fingerprint_material(values)
    fingerprint = _stable_hash(material)
    return CreativeConcept(
        concept_id=values.get("concept_id") or "rccp_" + fingerprint[:24],
        concept_fingerprint=fingerprint,
        **{key: value for key, value in values.items() if key != "concept_id"},
    )


class ConceptProposer(Protocol):
    def propose(self, contract: RenderableReelAuthority,
                platform: PlatformProfile) -> Sequence[CreativeConcept]: ...


class FixtureConceptProposer:
    """Explicit offline seam.  It returns prebuilt fixtures and can never invoke a provider."""

    def __init__(self, concepts: Sequence[CreativeConcept]) -> None:
        self._concepts = tuple(concepts)
        self.calls = 0

    def propose(self, contract: RenderableReelAuthority,
                platform: PlatformProfile) -> Sequence[CreativeConcept]:
        self.calls += 1
        if any(item.reel_contract_hash != contract.contract_hash for item in self._concepts):
            raise CreativeCoreError("STRATEGY_DRIFT", "fixture belongs to another contract")
        if any(item.platform != platform.platform for item in self._concepts):
            raise CreativeCoreError("PLATFORM_STYLE_MISMATCH", "fixture uses another platform")
        return self._concepts


class FitnessState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REPAIR = "NEEDS_REPAIR"
    NOT_ASSESSED = "NOT_ASSESSED"


class FitnessAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: FitnessState
    reason: str


class CreativeFitnessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_id: str
    strategy_alignment: FitnessAssessment
    claim_safety: FitnessAssessment
    target_relevance: FitnessAssessment
    hook_fitness: FitnessAssessment
    platform_nativeness: FitnessAssessment
    product_prominence: FitnessAssessment
    value_demonstrability: FitnessAssessment
    narrative_coherence: FitnessAssessment
    visual_feasibility: FitnessAssessment
    production_repairability: FitnessAssessment
    cost_feasibility: FitnessAssessment
    creative_distinctiveness: FitnessAssessment
    defects: tuple["CreativeDefect", ...] = ()

    def fails_hard_gate(self) -> bool:
        return any(getattr(self, field).state == FitnessState.FAIL for field in (
            "strategy_alignment", "claim_safety", "platform_nativeness", "visual_feasibility"))


class CreativeDefect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    defect_type: DefectType
    repair_owner: RepairOwner
    detail: str
    concept_id: str = ""
    beat_id: str = ""
    coverage_id: str = ""
    shot_id: str = ""

    @model_validator(mode="after")
    def uses_canonical_smallest_owner(self) -> "CreativeDefect":
        if DEFECT_REPAIR_OWNER[self.defect_type] != self.repair_owner:
            raise ValueError("creative defect is assigned to the wrong canonical repair owner")
        return self


class ConceptDistinctnessState(str, Enum):
    DISTINCT = "DISTINCT"
    NOT_DISTINCT = "NOT_DISTINCT"


class ConceptDistinctnessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ConceptDistinctnessState
    duplicate_pairs: tuple[tuple[str, str], ...] = ()
    defects: tuple[CreativeDefect, ...] = ()


def _same_treatment(left: CreativeConcept, right: CreativeConcept) -> bool:
    categorical = (
        left.actor_mode == right.actor_mode,
        left.product_presence_policy == right.product_presence_policy,
    )
    fields = (
        "hook_mechanism", "narrative_device", "product_role", "visual_grammar",
        "reveal_pattern", "emotional_lever",
    )
    matches = sum(_semantic_overlap(getattr(left, field), getattr(right, field))
                  for field in fields)
    return (all(categorical) and matches >= 3) or matches >= 5


def assess_concept_distinctness(concepts: Sequence[CreativeConcept]
                                ) -> ConceptDistinctnessResult:
    pairs = tuple(
        (left.concept_id, right.concept_id)
        for index, left in enumerate(concepts)
        for right in concepts[index + 1:]
        if _same_treatment(left, right)
    )
    if not pairs:
        return ConceptDistinctnessResult(state=ConceptDistinctnessState.DISTINCT)
    defect = CreativeDefect(
        defect_type=DefectType.CREATIVE_CONCEPTS_NOT_DISTINCT,
        repair_owner=RepairOwner.CONCEPT,
        detail="Candidate wording changed without a substantively different creative treatment.",
    )
    return ConceptDistinctnessResult(
        state=ConceptDistinctnessState.NOT_DISTINCT,
        duplicate_pairs=pairs, defects=(defect,))


def _assessment(state: FitnessState, reason: str) -> FitnessAssessment:
    return FitnessAssessment(state=state, reason=reason)


def _capability_possible(required: Sequence[str],
                         registry: Sequence[VideoProviderCapabilities]) -> bool:
    aliases = {
        "TEXT_TO_VIDEO": "text_to_video", "IMAGE_TO_VIDEO": "image_to_video",
        "FIRST_LAST_FRAME": "first_last_frame", "REFERENCE_IMAGES": "reference_images",
        "VIDEO_EXTENSION": "video_extension", "CONVERSATIONAL_EDIT": "conversational_editing",
        "MULTIMODAL_VIDEO": "multimodal_video", "NATIVE_AUDIO": "native_audio",
        "LOCAL_SEED": "local_seed_supported",
    }
    if any(item not in aliases for item in required):
        return False
    for provider in registry:
        if all(bool(getattr(provider, aliases[item], False)) for item in required):
            return True
    return not required


def evaluate_concept(
    concept: CreativeConcept,
    contract: ReelCreativeContract,
    profile: PlatformProfile,
    *,
    provider_registry: Sequence[VideoProviderCapabilities] = tuple(
        (*CANONICAL_PROVIDER_REGISTRY.values(), GEMINI_OMNI_FLASH_EXPERIMENTAL)),
) -> CreativeFitnessResult:
    defects: list[CreativeDefect] = []
    strategy_errors: list[str] = []
    if concept.reel_contract_hash != contract.contract_hash:
        strategy_errors.append("concept is bound to another contract")
    if _norm(concept.declared_target) != _norm(contract.target):
        strategy_errors.append("target changed")
    if _norm(concept.preserved_consumer_value) != _norm(contract.consumer_value):
        strategy_errors.append("consumer value changed")
    if concept.declared_CTA != contract.exact_cta:
        strategy_errors.append("CTA changed")
    if strategy_errors:
        defect_type = (DefectType.CTA_MISMATCH if strategy_errors == ["CTA changed"]
                       else DefectType.STRATEGY_DRIFT)
        defects.append(CreativeDefect(
            defect_type=defect_type, repair_owner=RepairOwner.CONTRACT,
            concept_id=concept.concept_id, detail="; ".join(strategy_errors)))

    claim_errors: list[str] = []
    for intent in (*concept.claim_intents, *concept.visual_implication_intents):
        authorization = authorize_claim(intent, contract)
        if authorization.state in {
                ClaimAuthorizationState.BLOCKED, ClaimAuthorizationState.REQUIRES_REPAIR}:
            claim_errors.append(authorization.code)
            defect_lookup = {
                "INTERNAL_EVIDENCE_LEAK": DefectType.INTERNAL_EVIDENCE_LEAK,
                "CTA_MISMATCH": DefectType.CTA_MISMATCH,
                "PROHIBITED_VISUAL_IMPLICATION":
                    DefectType.VISUAL_METAPHOR_CONFLICTS_WITH_TRUTH,
            }
            defect_type = defect_lookup.get(authorization.code, DefectType.UNSUPPORTED_CLAIM)
            owner = RepairOwner.CONCEPT if defect_type == \
                DefectType.VISUAL_METAPHOR_CONFLICTS_WITH_TRUTH else RepairOwner.CONTRACT
            defects.append(CreativeDefect(
                defect_type=defect_type, repair_owner=owner,
                concept_id=concept.concept_id, detail=authorization.detail))

    duration_valid = True
    if (profile.duration_guidance.maximum_seconds is not None
            and concept.expected_duration_range[0] >
            profile.duration_guidance.maximum_seconds):
        duration_valid = False
    if (profile.duration_guidance.minimum_seconds is not None
            and concept.expected_duration_range[1] <
            profile.duration_guidance.minimum_seconds):
        duration_valid = False
    platform_valid = concept.platform == profile.platform and duration_valid
    feasible = _capability_possible(concept.required_capabilities, provider_registry)
    if not feasible:
        defects.append(CreativeDefect(
            defect_type=DefectType.UNEXECUTABLE_SHOT_PLAN, repair_owner=RepairOwner.SHOT,
            concept_id=concept.concept_id,
            detail="No registered capability profile can satisfy the concept requirements."))

    value_bound = any(
        set(intent.supporting_allowed_claim_ids) & set(contract.allowed_claim_ids)
        for intent in concept.claim_intents)
    if not value_bound or not _plain(concept.value_demonstration):
        defects.append(CreativeDefect(
            defect_type=DefectType.VALUE_NOT_DEMONSTRATED, repair_owner=RepairOwner.ARC,
            concept_id=concept.concept_id,
            detail="The canonical consumer value is not visibly demonstrated."))

    return CreativeFitnessResult(
        concept_id=concept.concept_id,
        strategy_alignment=_assessment(
            FitnessState.FAIL if strategy_errors else FitnessState.PASS,
            "; ".join(strategy_errors) or "Locked strategic fields are preserved."),
        claim_safety=_assessment(
            FitnessState.FAIL if claim_errors else FitnessState.PASS,
            ", ".join(claim_errors) or "All claim and visual intents passed the claim firewall."),
        target_relevance=_assessment(
            FitnessState.PASS if _plain(concept.audience_tension) else FitnessState.NEEDS_REPAIR,
            "Audience tension is explicit." if concept.audience_tension else "Audience tension missing."),
        hook_fitness=_assessment(
            FitnessState.PASS if _plain(concept.hook_mechanism) else FitnessState.NEEDS_REPAIR,
            "Hook mechanism is explicit." if concept.hook_mechanism else "Hook is undefined."),
        platform_nativeness=_assessment(
            FitnessState.PASS if platform_valid and concept.visual_grammar else FitnessState.FAIL,
            "Treatment fits the selected profile." if platform_valid else
            "Platform or duration is incompatible with the selected profile."),
        product_prominence=_assessment(
            FitnessState.PASS if concept.product_role else FitnessState.NEEDS_REPAIR,
            "Product has an explicit narrative role." if concept.product_role else
            "Product role is missing."),
        value_demonstrability=_assessment(
            FitnessState.PASS if value_bound and concept.value_demonstration else FitnessState.FAIL,
            "Value is bound to an allowed claim and a visual demonstration." if value_bound else
            "Value demonstration is not bound to canonical claim authority."),
        narrative_coherence=_assessment(
            FitnessState.PASS if concept.narrative_device and concept.planned_beat_roles
            else FitnessState.NEEDS_REPAIR,
            "Narrative device resolves into flexible beats."),
        visual_feasibility=_assessment(
            FitnessState.PASS if feasible else FitnessState.FAIL,
            "At least one registered capability profile could satisfy the treatment." if feasible
            else "Required capabilities are impossible in the registry."),
        production_repairability=_assessment(
            FitnessState.PASS if concept.repairability_intent ==
            RepairabilityIntent.MODULAR_REPAIRABLE else FitnessState.NEEDS_REPAIR,
            "Treatment is modular." if concept.repairability_intent ==
            RepairabilityIntent.MODULAR_REPAIRABLE else "Treatment has a continuous dependency."),
        cost_feasibility=_assessment(
            FitnessState.NOT_ASSESSED,
            "Gate 2D.2 declares paid categories but does not infer provider pricing."),
        creative_distinctiveness=_assessment(
            FitnessState.NOT_ASSESSED, "Assessed across the tournament candidate set."),
        defects=tuple(defects),
    )


class TournamentDecision(str, Enum):
    SELECTED = "SELECTED"
    TIE_REQUIRES_REVIEW = "TIE_REQUIRES_REVIEW"
    NO_VIABLE_CONCEPT = "NO_VIABLE_CONCEPT"


class PreferenceBand(str, Enum):
    WEAK = "WEAK"
    ADEQUATE = "ADEQUATE"
    STRONG = "STRONG"


class CreativePreference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hook_clarity: PreferenceBand
    target_relevance: PreferenceBand
    value_demonstration: PreferenceBand
    product_presence: PreferenceBand
    narrative_economy: PreferenceBand
    platform_fit: PreferenceBand
    repairability: PreferenceBand


class ConceptTournamentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: TournamentDecision
    selected_concept_id: str = ""
    explanation: tuple[str, ...]
    fitness: tuple[CreativeFitnessResult, ...]
    preferences: Mapping[str, CreativePreference]
    discarded_concept_ids: tuple[str, ...]
    defects: tuple[CreativeDefect, ...] = ()


def _preference(concept: CreativeConcept, contract: ReelCreativeContract,
                profile: PlatformProfile) -> CreativePreference:
    immediate = ("immediate", "first frame", "cold open", "product-first", "pov")
    hook = PreferenceBand.STRONG if any(word in _norm(concept.hook_mechanism)
                                        for word in immediate) else PreferenceBand.ADEQUATE
    target = (PreferenceBand.STRONG if _semantic_overlap(
        concept.audience_tension, contract.target) else PreferenceBand.ADEQUATE)
    value = (PreferenceBand.STRONG if _semantic_overlap(
        concept.value_demonstration, contract.consumer_value) else PreferenceBand.ADEQUATE)
    product = (PreferenceBand.STRONG if concept.product_presence_policy in {
        ProductPresencePolicy.OPENING, ProductPresencePolicy.CONTINUOUS_WHERE_PLAUSIBLE}
        else PreferenceBand.ADEQUATE)
    economy = (PreferenceBand.STRONG if concept.production_complexity == ProductionComplexity.LOW
               else PreferenceBand.ADEQUATE if concept.production_complexity ==
               ProductionComplexity.MODERATE else PreferenceBand.WEAK)
    platform_fit = PreferenceBand.STRONG if (
        profile.social_native_expectation.split("_")[0].casefold() in
        _norm(concept.visual_grammar) or "social" in _norm(concept.visual_grammar)
        or "pov" in _norm(concept.visual_grammar)) else PreferenceBand.ADEQUATE
    repair = (PreferenceBand.STRONG if concept.repairability_intent ==
              RepairabilityIntent.MODULAR_REPAIRABLE else PreferenceBand.WEAK)
    return CreativePreference(
        hook_clarity=hook, target_relevance=target, value_demonstration=value,
        product_presence=product, narrative_economy=economy, platform_fit=platform_fit,
        repairability=repair)


class ConceptTournament:
    """Hard-gate then lexicographic preference elimination; no weighted or numeric score."""

    _PREFERENCE_ORDER = (
        "hook_clarity", "target_relevance", "value_demonstration", "product_presence",
        "narrative_economy", "platform_fit", "repairability",
    )
    _BAND_ORDER = {PreferenceBand.WEAK: 0, PreferenceBand.ADEQUATE: 1,
                   PreferenceBand.STRONG: 2}

    def run(self, contract: ReelCreativeContract, profile: PlatformProfile,
            concepts: Sequence[CreativeConcept]) -> ConceptTournamentResult:
        if not 2 <= len(concepts) <= 5:
            raise CreativeCoreError(
                "CONCEPT_COUNT_OUT_OF_RANGE", "A tournament accepts two to five concepts.")
        distinctness = assess_concept_distinctness(concepts)
        fitness = [evaluate_concept(item, contract, profile) for item in concepts]
        if distinctness.state == ConceptDistinctnessState.NOT_DISTINCT:
            fitness = [item.model_copy(update={
                "creative_distinctiveness": _assessment(
                    FitnessState.FAIL, "Treatment duplicates another candidate.")})
                for item in fitness]
            return ConceptTournamentResult(
                decision=TournamentDecision.NO_VIABLE_CONCEPT,
                explanation=("Candidate set failed substantive treatment distinctness.",),
                fitness=tuple(fitness), preferences={},
                discarded_concept_ids=tuple(item.concept_id for item in concepts),
                defects=distinctness.defects)

        fitness = [item.model_copy(update={
            "creative_distinctiveness": _assessment(
                FitnessState.PASS, "Substantive treatment differs from the other candidates.")})
            for item in fitness]
        by_id = {item.concept_id: item for item in concepts}
        viable = [item.concept_id for item in fitness if not item.fails_hard_gate()]
        hard_discarded = [item.concept_id for item in fitness if item.fails_hard_gate()]
        if not viable:
            defect = CreativeDefect(
                defect_type=DefectType.NO_VIABLE_CONCEPT, repair_owner=RepairOwner.CONCEPT,
                detail="Every candidate failed strategy, claims, platform, or feasibility.")
            return ConceptTournamentResult(
                decision=TournamentDecision.NO_VIABLE_CONCEPT,
                explanation=("No candidate survived Stage A hard gates.",),
                fitness=tuple(fitness), preferences={},
                discarded_concept_ids=tuple(hard_discarded), defects=(defect,))

        preferences = {item_id: _preference(by_id[item_id], contract, profile)
                       for item_id in viable}
        survivors = list(viable)
        explanation = [f"Stage A retained {len(viable)} of {len(concepts)} candidates."]
        for criterion in self._PREFERENCE_ORDER:
            if len(survivors) == 1:
                break
            best = max(self._BAND_ORDER[getattr(preferences[item_id], criterion)]
                       for item_id in survivors)
            narrowed = [item_id for item_id in survivors
                        if self._BAND_ORDER[getattr(preferences[item_id], criterion)] == best]
            if len(narrowed) < len(survivors):
                explanation.append(
                    f"Stage B retained {', '.join(narrowed)} on {criterion}; no weights were used.")
            survivors = narrowed
        discarded = tuple(item.concept_id for item in concepts
                          if item.concept_id not in survivors)
        if len(survivors) != 1:
            return ConceptTournamentResult(
                decision=TournamentDecision.TIE_REQUIRES_REVIEW,
                explanation=tuple((*explanation, "Candidates remain tied after ordered criteria.")),
                fitness=tuple(fitness), preferences=preferences,
                discarded_concept_ids=discarded)
        return ConceptTournamentResult(
            decision=TournamentDecision.SELECTED, selected_concept_id=survivors[0],
            explanation=tuple((*explanation, f"Selected {survivors[0]} by ordered evidence.")),
            fitness=tuple(fitness), preferences=preferences,
            discarded_concept_ids=discarded)


class BrandPresencePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    guidance: str
    logo_frequency: str = "AS_NEEDED_NOT_EVERY_FRAME"


class CTAPlanPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exact_text: str
    delivery: str = "DETERMINISTIC_END_CARD"
    spoken_if_voiceover: bool = True


class NarrativeBeat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    beat_id: str
    role: BeatRole
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    narrative_purpose: str
    visual_treatment: str
    communicates_proposition: bool = False
    product_visible: bool = False
    brand_visible: bool = False
    claim_intents: tuple[ClaimIntent, ...] = ()

    @model_validator(mode="after")
    def time_order(self) -> "NarrativeBeat":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("beat must have positive duration")
        return self


class ReelBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    blueprint_id: str
    contract_hash: str
    selected_concept_id: str
    platform_profile_id: str
    target_duration_seconds: float = Field(gt=0)
    narrative_arc: str
    beat_sequence: tuple[NarrativeBeat, ...]
    hook_deadline: Optional[float] = Field(default=None, gt=0)
    proposition_deadline: Optional[float] = Field(default=None, gt=0)
    product_presence_policy: ProductPresencePolicy
    brand_presence_policy: BrandPresencePolicy
    CTA_policy: CTAPlanPolicy
    overlay_policy: str
    audio_strategy: str
    creative_risks: tuple[str, ...]
    mandatory_constraints: tuple[str, ...]
    blueprint_hash: str

    @model_validator(mode="after")
    def hash_and_time_are_valid(self) -> "ReelBlueprint":
        if not self.beat_sequence:
            raise ValueError("blueprint needs at least one beat")
        if self.beat_sequence[0].start_seconds != 0:
            raise ValueError("first beat must start at zero")
        if any(right.start_seconds < left.end_seconds
               for left, right in zip(self.beat_sequence, self.beat_sequence[1:])):
            raise ValueError("blueprint beats overlap")
        if self.beat_sequence[-1].end_seconds > self.target_duration_seconds + 0.001:
            raise ValueError("beat sequence exceeds target duration")
        material = self.model_dump(mode="json", exclude={"blueprint_id", "blueprint_hash"})
        if self.blueprint_hash != _stable_hash(material):
            raise ValueError("blueprint hash mismatch")
        return self


_BEAT_PURPOSE: Mapping[BeatRole, str] = {
    BeatRole.HOOK: "earn attention through the selected hook mechanism",
    BeatRole.CONTEXT: "establish the audience situation",
    BeatRole.TENSION: "make the audience tension legible without claiming prevalence",
    BeatRole.PRODUCT_REVEAL: "introduce the product as part of the selected treatment",
    BeatRole.VALUE_DEMONSTRATION: "show the canonical consumer value in use",
    BeatRole.PROOF: "show separately approved consumer-facing proof",
    BeatRole.PAYOFF: "resolve the treatment with product consequence, not superiority",
    BeatRole.REACTION: "show an expressive reaction without implying unapproved outcomes",
    BeatRole.BRAND_MOMENT: "make brand identity legible",
    BeatRole.CTA: "deliver the exact canonical CTA",
}


def _beat_claims(role: BeatRole, concept: CreativeConcept,
                 contract: ReelCreativeContract) -> tuple[ClaimIntent, ...]:
    if role == BeatRole.CTA:
        return (ClaimIntent(
            surface=ClaimSurface.END_CARD, text=contract.exact_cta,
            claim_type=ClaimType.CTA, factual=False, expressive=False,
            exact_wording_required=True,
            verification_status=ClaimVerificationStatus.NOT_VERIFIED),)
    if role == BeatRole.PROOF:
        if not contract.proof_points:
            return ()
        proof = contract.proof_points[0]
        return (ClaimIntent(
            surface=ClaimSurface.ON_SCREEN_COPY, text=proof.statement,
            supporting_allowed_claim_ids=(proof.claim_id,), claim_type=ClaimType.PROOF,
            factual=True, expressive=False, exact_wording_required=False,
            verification_status=ClaimVerificationStatus.NOT_VERIFIED),)
    if role in {BeatRole.PRODUCT_REVEAL, BeatRole.VALUE_DEMONSTRATION, BeatRole.PAYOFF}:
        return tuple(concept.claim_intents[:1])
    return ()


def compile_blueprint(concept: CreativeConcept, contract: ReelCreativeContract,
                      profile: PlatformProfile) -> ReelBlueprint:
    if concept.reel_contract_hash != contract.contract_hash:
        raise CreativeCoreError("STRATEGY_DRIFT", "concept is not bound to this contract")
    if BeatRole.PROOF in concept.planned_beat_roles and not contract.proof_points:
        raise CreativeCoreError(
            "NO_APPROVED_CONSUMER_PROOF", "proof-less contract cannot compile a proof beat")
    target = concept.expected_duration_range[1]
    if profile.duration_guidance.maximum_seconds is not None:
        target = min(target, profile.duration_guidance.maximum_seconds)
    target = max(target, concept.expected_duration_range[0])
    if profile.duration_guidance.minimum_seconds is not None:
        target = max(target, profile.duration_guidance.minimum_seconds)
    roles = concept.planned_beat_roles
    hook_duration = min(2.0, target / len(roles)) if roles[0] == BeatRole.HOOK else 0
    cta_duration = min(2.0, target / len(roles)) if roles[-1] == BeatRole.CTA else 0
    remaining = target - hook_duration - cta_duration
    flexible_count = len(roles) - int(bool(hook_duration)) - int(bool(cta_duration))
    flexible_duration = remaining / max(1, flexible_count)
    beats: list[NarrativeBeat] = []
    cursor = 0.0
    for index, role in enumerate(roles):
        if index == 0 and hook_duration:
            duration = hook_duration
        elif index == len(roles) - 1 and cta_duration:
            duration = cta_duration
        else:
            duration = flexible_duration
        end = target if index == len(roles) - 1 else round(cursor + duration, 3)
        product_visible = role in {
            BeatRole.PRODUCT_REVEAL, BeatRole.VALUE_DEMONSTRATION,
            BeatRole.PAYOFF, BeatRole.BRAND_MOMENT, BeatRole.CTA}
        if concept.product_presence_policy == ProductPresencePolicy.CONTINUOUS_WHERE_PLAUSIBLE:
            product_visible = True
        elif concept.product_presence_policy == ProductPresencePolicy.OPENING and index == 0:
            product_visible = True
        beat = NarrativeBeat(
            beat_id=f"beat_{index + 1:02d}_{role.value.casefold()}", role=role,
            start_seconds=round(cursor, 3), end_seconds=round(end, 3),
            narrative_purpose=_BEAT_PURPOSE[role],
            visual_treatment=f"{concept.visual_grammar} / {role.value.casefold()}",
            communicates_proposition=role in {
                BeatRole.PRODUCT_REVEAL, BeatRole.VALUE_DEMONSTRATION},
            product_visible=product_visible,
            brand_visible=role in {BeatRole.BRAND_MOMENT, BeatRole.CTA},
            claim_intents=_beat_claims(role, concept, contract),
        )
        beats.append(beat)
        cursor = end
    material = {
        "contract_hash": contract.contract_hash,
        "selected_concept_id": concept.concept_id,
        "platform_profile_id": profile.profile_id,
        "target_duration_seconds": target,
        "narrative_arc": concept.narrative_device,
        "beat_sequence": [item.model_dump(mode="json") for item in beats],
        "hook_deadline": profile.hook_window.deadline_seconds,
        "proposition_deadline": profile.proposition_window.deadline_seconds,
        "product_presence_policy": concept.product_presence_policy.value,
        "brand_presence_policy": BrandPresencePolicy(
            guidance=profile.brand_presence_guidance).model_dump(mode="json"),
        "CTA_policy": CTAPlanPolicy(exact_text=contract.exact_cta).model_dump(mode="json"),
        "overlay_policy": "EXACT_MARKETING_TEXT_BY_DETERMINISTIC_COMPOSITOR",
        "audio_strategy": concept.audio_direction,
        "creative_risks": concept.creative_risks,
        "mandatory_constraints": (
            f"preserve target: {contract.target}",
            f"preserve consumer value: {contract.consumer_value}",
            f"use exact CTA: {contract.exact_cta}",
            "never expose internal strategic evidence",
        ),
    }
    digest = _stable_hash(material)
    return ReelBlueprint(
        blueprint_id="rbp_" + digest[:24], blueprint_hash=digest, **material)


def validate_blueprint(blueprint: ReelBlueprint, contract: ReelCreativeContract,
                       profile: PlatformProfile) -> tuple[CreativeDefect, ...]:
    defects: list[CreativeDefect] = []
    hooks = [item for item in blueprint.beat_sequence if item.role == BeatRole.HOOK]
    if (blueprint.hook_deadline is not None
            and (not hooks or hooks[0].start_seconds > blueprint.hook_deadline)):
        defects.append(CreativeDefect(
            defect_type=DefectType.WEAK_HOOK, repair_owner=RepairOwner.CONCEPT,
            detail="Hook begins after the platform guidance window."))
    proposition = next((item for item in blueprint.beat_sequence
                        if item.communicates_proposition), None)
    if (blueprint.proposition_deadline is not None
            and (proposition is None
                 or proposition.start_seconds > blueprint.proposition_deadline)):
        defects.append(CreativeDefect(
            defect_type=DefectType.PROPOSITION_TOO_LATE, repair_owner=RepairOwner.ARC,
            beat_id=proposition.beat_id if proposition else "",
            detail="Canonical proposition arrives after the platform guidance deadline."))
    product = next((item for item in blueprint.beat_sequence if item.product_visible), None)
    if blueprint.product_presence_policy in {
            ProductPresencePolicy.OPENING, ProductPresencePolicy.EARLY,
            ProductPresencePolicy.CONTINUOUS_WHERE_PLAUSIBLE}:
        deadline = blueprint.hook_deadline or max(3.0, blueprint.target_duration_seconds * 0.25)
        if product is None or product.start_seconds > deadline:
            defects.append(CreativeDefect(
                defect_type=DefectType.PRODUCT_APPEARS_TOO_LATE, repair_owner=RepairOwner.ARC,
                beat_id=product.beat_id if product else "",
                detail="Product first appears after its declared presence policy allows."))
    if BeatRole.PROOF in {item.role for item in blueprint.beat_sequence} \
            and not contract.proof_points:
        defects.append(CreativeDefect(
            defect_type=DefectType.UNSUPPORTED_CLAIM, repair_owner=RepairOwner.CONTRACT,
            detail="Blueprint fabricates a proof beat for a proof-less contract."))
    treatments = {_norm(item.visual_treatment) for item in blueprint.beat_sequence}
    if len(blueprint.beat_sequence) > 2 and len(treatments) == 1:
        defects.append(CreativeDefect(
            defect_type=DefectType.SHOT_REDUNDANCY, repair_owner=RepairOwner.ARC,
            detail="Every beat repeats the same visual treatment."))
    if (blueprint.product_presence_policy == ProductPresencePolicy.CONTINUOUS_WHERE_PLAUSIBLE
            and any(not item.product_visible for item in blueprint.beat_sequence)):
        defects.append(CreativeDefect(
            defect_type=DefectType.VALUE_NOT_DEMONSTRATED, repair_owner=RepairOwner.ARC,
            detail="Product disappears despite a continuous-where-plausible policy."))
    if blueprint.CTA_policy.exact_text != contract.exact_cta:
        defects.append(CreativeDefect(
            defect_type=DefectType.CTA_MISMATCH, repair_owner=RepairOwner.CONTRACT,
            detail="Blueprint rewrites the canonical CTA."))
    return tuple(defects)


class CoverageRequirement(str, Enum):
    MANDATORY = "MANDATORY"
    OPTIONAL = "OPTIONAL"


class CoverageRole(str, Enum):
    PRIMARY = "PRIMARY"
    BACKUP = "BACKUP"


class RepairPriority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class CoverageNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    coverage_id: str
    beat_id: str
    purpose: str
    mandatory_or_optional: CoverageRequirement
    primary_or_backup: CoverageRole
    expected_duration: float = Field(gt=0)
    claim_intents: tuple[ClaimIntent, ...]
    required_subjects: tuple[str, ...]
    product_requirement: str
    continuity_group: str
    repair_priority: RepairPriority


class CoverageEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    from_coverage_id: str
    to_coverage_id: str
    relationship: str


class CoverageGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_id: str
    blueprint_hash: str
    nodes: tuple[CoverageNode, ...]
    edges: tuple[CoverageEdge, ...]
    graph_hash: str

    @model_validator(mode="after")
    def is_a_valid_graph(self) -> "CoverageGraph":
        ids = {item.coverage_id for item in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("coverage ids must be unique")
        if any(edge.from_coverage_id not in ids or edge.to_coverage_id not in ids
               for edge in self.edges):
            raise ValueError("coverage edge references an unknown node")
        material = self.model_dump(mode="json", exclude={"graph_id", "graph_hash"})
        if self.graph_hash != _stable_hash(material):
            raise ValueError("coverage graph hash mismatch")
        return self


def compile_coverage_graph(blueprint: ReelBlueprint,
                           concept: CreativeConcept) -> CoverageGraph:
    nodes: list[CoverageNode] = []
    edges: list[CoverageEdge] = []
    previous_primary = ""
    for beat in blueprint.beat_sequence:
        primary_id = f"coverage_{beat.beat_id}_primary"
        nodes.append(CoverageNode(
            coverage_id=primary_id, beat_id=beat.beat_id,
            purpose=f"primary coverage: {beat.narrative_purpose}",
            mandatory_or_optional=CoverageRequirement.MANDATORY,
            primary_or_backup=CoverageRole.PRIMARY,
            expected_duration=beat.end_seconds - beat.start_seconds,
            claim_intents=beat.claim_intents,
            required_subjects=(("actor",) if concept.actor_mode != ActorMode.NO_ACTOR else ()),
            product_requirement="VISIBLE_AND_VERIFIABLE" if beat.product_visible else "OPTIONAL",
            continuity_group="main_product_world",
            repair_priority=(RepairPriority.CRITICAL if beat.role in {
                BeatRole.HOOK, BeatRole.VALUE_DEMONSTRATION, BeatRole.CTA}
                else RepairPriority.NORMAL),
        ))
        if previous_primary:
            edges.append(CoverageEdge(
                from_coverage_id=previous_primary, to_coverage_id=primary_id,
                relationship="NARRATIVE_SEQUENCE"))
        previous_primary = primary_id
        if beat.role in {BeatRole.HOOK, BeatRole.VALUE_DEMONSTRATION}:
            backup_id = f"coverage_{beat.beat_id}_backup"
            nodes.append(CoverageNode(
                coverage_id=backup_id, beat_id=beat.beat_id,
                purpose=f"isolated backup coverage: {beat.narrative_purpose}",
                mandatory_or_optional=CoverageRequirement.OPTIONAL,
                primary_or_backup=CoverageRole.BACKUP,
                expected_duration=beat.end_seconds - beat.start_seconds,
                claim_intents=beat.claim_intents,
                required_subjects=(("actor",) if concept.actor_mode != ActorMode.NO_ACTOR else ()),
                product_requirement="VISIBLE_AND_VERIFIABLE" if beat.product_visible else "OPTIONAL",
                continuity_group="main_product_world",
                repair_priority=RepairPriority.HIGH,
            ))
            edges.append(CoverageEdge(
                from_coverage_id=primary_id, to_coverage_id=backup_id,
                relationship="BACKUP_ALTERNATIVE"))
    material = {
        "blueprint_hash": blueprint.blueprint_hash,
        "nodes": [item.model_dump(mode="json") for item in nodes],
        "edges": [item.model_dump(mode="json") for item in edges],
    }
    digest = _stable_hash(material)
    return CoverageGraph(
        graph_id="cvg_" + digest[:24], graph_hash=digest, **material)


class GeneratedTextCategory(str, Enum):
    MARKETING_COPY = "MARKETING_COPY"
    CTA = "CTA"
    CAPTIONS = "CAPTIONS"
    DISCLAIMER = "DISCLAIMER"
    PROTECTED_BRAND_TEXT = "PROTECTED_BRAND_TEXT"
    INCIDENTAL_ENVIRONMENT_TEXT = "INCIDENTAL_ENVIRONMENT_TEXT"


class GeneratedTextDisposition(str, Enum):
    NEVER_RENDER_IN_GENERATIVE_VIDEO = "NEVER_RENDER_IN_GENERATIVE_VIDEO"
    DETERMINISTIC_COMPOSITOR_ONLY = "DETERMINISTIC_COMPOSITOR_ONLY"
    MAY_EXIST_IF_IDENTITY_FIDELITY_VERIFIED = "MAY_EXIST_IF_IDENTITY_FIDELITY_VERIFIED"
    AVOID_REJECT_IF_MALFORMED_OR_CLAIM_BEARING = \
        "AVOID_REJECT_IF_MALFORMED_OR_CLAIM_BEARING"


class GeneratedTextRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: GeneratedTextCategory
    disposition: GeneratedTextDisposition


class GeneratedTextPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    rules: tuple[GeneratedTextRule, ...]

    def rule_for(self, category: GeneratedTextCategory) -> GeneratedTextRule:
        return next(item for item in self.rules if item.category == category)

    @model_validator(mode="after")
    def one_safe_rule_per_category(self) -> "GeneratedTextPolicy":
        if {item.category for item in self.rules} != set(GeneratedTextCategory):
            raise ValueError("generated text policy must cover every category once")
        if len(self.rules) != len(GeneratedTextCategory):
            raise ValueError("generated text policy contains duplicates")
        required = {
            GeneratedTextCategory.MARKETING_COPY:
                GeneratedTextDisposition.NEVER_RENDER_IN_GENERATIVE_VIDEO,
            GeneratedTextCategory.CTA:
                GeneratedTextDisposition.DETERMINISTIC_COMPOSITOR_ONLY,
            GeneratedTextCategory.CAPTIONS:
                GeneratedTextDisposition.DETERMINISTIC_COMPOSITOR_ONLY,
            GeneratedTextCategory.DISCLAIMER:
                GeneratedTextDisposition.DETERMINISTIC_COMPOSITOR_ONLY,
            GeneratedTextCategory.PROTECTED_BRAND_TEXT:
                GeneratedTextDisposition.MAY_EXIST_IF_IDENTITY_FIDELITY_VERIFIED,
            GeneratedTextCategory.INCIDENTAL_ENVIRONMENT_TEXT:
                GeneratedTextDisposition.AVOID_REJECT_IF_MALFORMED_OR_CLAIM_BEARING,
        }
        if any(item.disposition != required[item.category] for item in self.rules):
            raise ValueError("generated text policy delegates protected marketing typography")
        return self


CANONICAL_GENERATED_TEXT_POLICY = GeneratedTextPolicy(
    policy_id="canonical-generated-text-v2",
    rules=tuple(GeneratedTextRule(category=category, disposition=disposition)
                for category, disposition in {
                    GeneratedTextCategory.MARKETING_COPY:
                        GeneratedTextDisposition.NEVER_RENDER_IN_GENERATIVE_VIDEO,
                    GeneratedTextCategory.CTA:
                        GeneratedTextDisposition.DETERMINISTIC_COMPOSITOR_ONLY,
                    GeneratedTextCategory.CAPTIONS:
                        GeneratedTextDisposition.DETERMINISTIC_COMPOSITOR_ONLY,
                    GeneratedTextCategory.DISCLAIMER:
                        GeneratedTextDisposition.DETERMINISTIC_COMPOSITOR_ONLY,
                    GeneratedTextCategory.PROTECTED_BRAND_TEXT:
                        GeneratedTextDisposition.MAY_EXIST_IF_IDENTITY_FIDELITY_VERIFIED,
                    GeneratedTextCategory.INCIDENTAL_ENVIRONMENT_TEXT:
                        GeneratedTextDisposition.AVOID_REJECT_IF_MALFORMED_OR_CLAIM_BEARING,
                }.items()),
)


class ReferenceType(str, Enum):
    PRODUCT_REFERENCE = "PRODUCT_REFERENCE"
    BRAND_REFERENCE = "BRAND_REFERENCE"
    PLACE_REFERENCE = "PLACE_REFERENCE"
    CHARACTER_REFERENCE = "CHARACTER_REFERENCE"
    STYLE_REFERENCE = "STYLE_REFERENCE"


class ReferenceUsageMode(str, Enum):
    SEED_CONDITIONING = "SEED_CONDITIONING"
    PROVIDER_REFERENCE = "PROVIDER_REFERENCE"
    QA_REFERENCE = "QA_REFERENCE"
    PROTECTED_ASSET = "PROTECTED_ASSET"


class ReferenceAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_id: str
    reference_type: ReferenceType
    usage_modes: tuple[ReferenceUsageMode, ...]
    source_hash: str
    source_note: str


class ReferencePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    references: tuple[ReferenceAsset, ...]
    provider_attachment_claimed: bool = False

    @model_validator(mode="after")
    def attachment_claim_is_true(self) -> "ReferencePlan":
        declared = any(ReferenceUsageMode.PROVIDER_REFERENCE in item.usage_modes
                       for item in self.references)
        if declared != self.provider_attachment_claimed:
            raise ValueError("provider attachment flag must match reference usage modes")
        return self


def validate_reference_plan(plan: ReferencePlan,
                            capabilities: VideoProviderCapabilities) -> None:
    if plan.provider_attachment_claimed and not capabilities.reference_images:
        raise CreativeCoreError(
            "UNSUPPORTED_REQUIRED_CAPABILITY",
            "selected provider surface cannot receive provider reference images")


class SeedShotPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    seed_required: bool
    conditioning_references: tuple[str, ...]
    QA_references: tuple[str, ...]
    expected_composition: str
    protected_product_features: tuple[str, ...]
    prohibited_text: tuple[str, ...]
    expected_actor_identity: str
    expected_place_identity: str
    success_criteria: tuple[str, ...]


class SeedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    shots: tuple[SeedShotPlan, ...]
    provider_calls: int = 0


class ImageProviderAuthorization(str, Enum):
    SUPPORTED_METADATA_ONLY = "SUPPORTED_METADATA_ONLY"
    DEPRECATED_NOT_CANONICAL = "DEPRECATED_NOT_CANONICAL"


class ImageProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str
    model_id: str
    authorization: ImageProviderAuthorization
    canonical_default: bool
    capabilities_not_inferred: tuple[str, ...]
    surface_name: str = "UNSPECIFIED"
    lifecycle_status: str = "UNVERIFIED"
    preferred_role: str = "METADATA_ONLY"
    supported_aspect_ratios: tuple[str, ...] = ()
    supported_output_resolutions: tuple[str, ...] = ()
    pricing_state: str = "NOT_ASSESSED"
    source_version: str
    capability_source: str = ""


IMAGE_PROVIDER_CAPABILITIES: tuple[ImageProviderCapabilities, ...] = (
    ImageProviderCapabilities(
        provider_name="google", model_id="gemini-3.1-flash-image",
        authorization=ImageProviderAuthorization.SUPPORTED_METADATA_ONLY,
        canonical_default=True,
        capabilities_not_inferred=("pricing", "provider activation", "4K GA status"),
        surface_name="AGENT_PLATFORM", lifecycle_status="GA",
        preferred_role="STANDARD_SEED_GENERATION_AND_EDITING",
        supported_aspect_ratios=("9:16", "16:9", "1:1"),
        supported_output_resolutions=("1K", "2K"),
        source_version="gate-2d4a-reviewed-2026-08-24",
        capability_source=(
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/"
            "models/capabilities/image-generation")),
    ImageProviderCapabilities(
        provider_name="google", model_id="gemini-3-pro-image",
        authorization=ImageProviderAuthorization.SUPPORTED_METADATA_ONLY,
        canonical_default=False,
        capabilities_not_inferred=("pricing", "provider activation", "4K GA status"),
        surface_name="AGENT_PLATFORM", lifecycle_status="GA",
        preferred_role="HIGH_COMPLEXITY_HERO_ESCALATION",
        supported_aspect_ratios=("9:16", "16:9", "1:1"),
        supported_output_resolutions=("1K", "2K"),
        source_version="gate-2d4a-reviewed-2026-08-24",
        capability_source=(
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/"
            "models/gemini/3-pro-image")),
    ImageProviderCapabilities(
        provider_name="google", model_id="gemini-3-pro-image-preview",
        authorization=ImageProviderAuthorization.DEPRECATED_NOT_CANONICAL,
        canonical_default=False,
        capabilities_not_inferred=("availability", "pricing", "capability parity"),
        lifecycle_status="DEPRECATED", preferred_role="NEVER_CANONICAL",
        source_version="deprecated-preview-record"),
    ImageProviderCapabilities(
        provider_name="google", model_id="gemini-3.1-flash-image-preview",
        authorization=ImageProviderAuthorization.DEPRECATED_NOT_CANONICAL,
        canonical_default=False,
        capabilities_not_inferred=("availability", "pricing", "capability parity"),
        lifecycle_status="DEPRECATED", preferred_role="NEVER_CANONICAL",
        source_version="deprecated-preview-record"),
    ImageProviderCapabilities(
        provider_name="google", model_id="imagen",
        authorization=ImageProviderAuthorization.DEPRECATED_NOT_CANONICAL,
        canonical_default=False,
        capabilities_not_inferred=("specific retired endpoint", "availability", "pricing"),
        lifecycle_status="RETIRED_FAMILY_NOT_CANONICAL",
        preferred_role="NEVER_CANONICAL",
        source_version="gate-2d4a-imagen-family-exclusion-2026-08-24"),
)


class AudioCategory(str, Enum):
    ENVIRONMENT_AUDIO = "ENVIRONMENT_AUDIO"
    SFX = "SFX"
    MUSIC = "MUSIC"
    MARKETING_VOICEOVER = "MARKETING_VOICEOVER"
    NONCRITICAL_DIALOGUE = "NONCRITICAL_DIALOGUE"


class VoiceoverLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    line_id: str
    text: str
    time_budget_seconds: float = Field(gt=0)
    claim_intents: tuple[ClaimIntent, ...]
    exact_wording: bool


class VoiceoverScript(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script_id: str
    language: str
    locale: str
    ordered_lines: tuple[VoiceoverLine, ...]
    time_budget: float = Field(gt=0)
    pronunciation_notes: tuple[str, ...]
    CTA_line: str
    script_hash: str

    @model_validator(mode="after")
    def hash_and_budget(self) -> "VoiceoverScript":
        if sum(item.time_budget_seconds for item in self.ordered_lines) > self.time_budget:
            raise ValueError("voiceover lines exceed script time budget")
        material = self.model_dump(mode="json", exclude={"script_id", "script_hash"})
        if self.script_hash != _stable_hash(material):
            raise ValueError("voiceover script hash mismatch")
        return self


class AudioLayerPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: AudioCategory
    planned: bool
    direction: str
    exact_approved_artifact_required: bool = False
    later_transcript_verification_required: bool = False


class AudioPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    environment_audio: AudioLayerPlan
    SFX: AudioLayerPlan
    music: AudioLayerPlan
    marketing_voiceover: AudioLayerPlan
    noncritical_dialogue: AudioLayerPlan
    voiceover_script: Optional[VoiceoverScript] = None
    provider_selected: bool = False
    plan_hash: str


def compile_voiceover_script(contract: ReelCreativeContract,
                             *, time_budget: float = 8.0) -> VoiceoverScript:
    message = ClaimIntent(
        surface=ClaimSurface.VOICE_OVER, text=contract.consumer_value,
        supporting_allowed_claim_ids=contract.allowed_claim_ids[:1],
        claim_type=ClaimType.MESSAGE, factual=True, expressive=False,
        exact_wording_required=True,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED)
    cta = ClaimIntent(
        surface=ClaimSurface.VOICE_OVER, text=contract.exact_cta,
        claim_type=ClaimType.CTA, factual=False, expressive=False,
        exact_wording_required=True,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED)
    for intent in (message, cta):
        result = authorize_claim(intent, contract)
        if result.state != ClaimAuthorizationState.AUTHORIZED:
            raise CreativeCoreError(result.code, result.detail)
    lines = (
        VoiceoverLine(line_id="vo_01_value", text=contract.consumer_value,
                      time_budget_seconds=time_budget - 2,
                      claim_intents=(message,), exact_wording=True),
        VoiceoverLine(line_id="vo_02_cta", text=contract.exact_cta,
                      time_budget_seconds=2, claim_intents=(cta,), exact_wording=True),
    )
    material = {
        "language": contract.language or "und", "locale": contract.locale,
        "ordered_lines": [item.model_dump(mode="json") for item in lines],
        "time_budget": time_budget, "pronunciation_notes": (),
        "CTA_line": contract.exact_cta,
    }
    digest = _stable_hash(material)
    return VoiceoverScript(
        script_id="vos_" + digest[:24], script_hash=digest, **material)


def compile_audio_plan(contract: ReelCreativeContract,
                       concept: CreativeConcept) -> AudioPlan:
    script = compile_voiceover_script(contract)
    material = {
        "environment_audio": AudioLayerPlan(
            category=AudioCategory.ENVIRONMENT_AUDIO, planned=True,
            direction="natural environment bed").model_dump(mode="json"),
        "SFX": AudioLayerPlan(
            category=AudioCategory.SFX, planned=True,
            direction="editorial accents; no provider selected").model_dump(mode="json"),
        "music": AudioLayerPlan(
            category=AudioCategory.MUSIC, planned=True,
            direction=concept.audio_direction).model_dump(mode="json"),
        "marketing_voiceover": AudioLayerPlan(
            category=AudioCategory.MARKETING_VOICEOVER, planned=True,
            direction="exact approved script only", exact_approved_artifact_required=True,
            later_transcript_verification_required=True).model_dump(mode="json"),
        "noncritical_dialogue": AudioLayerPlan(
            category=AudioCategory.NONCRITICAL_DIALOGUE, planned=False,
            direction="none planned").model_dump(mode="json"),
        "voiceover_script": script.model_dump(mode="json"),
        "provider_selected": False,
    }
    digest = _stable_hash(material)
    return AudioPlan(plan_id="aud_" + digest[:24], plan_hash=digest, **material)


class OverlaySemanticRole(str, Enum):
    CAPTION = "CAPTION"
    HEADLINE = "HEADLINE"
    CTA = "CTA"
    LOGO_LOCKUP = "LOGO_LOCKUP"
    DISCLAIMER = "DISCLAIMER"
    END_CARD = "END_CARD"


class OverlayItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    overlay_id: str
    text: str
    semantic_role: OverlaySemanticRole
    claim_authorization: str
    platform_safe_zone_requirement: bool
    temporal_range: tuple[float, float]
    placement_intent: str
    exact_geometry: Optional[SafeZoneGeometry] = None


class OverlayPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    safe_zone_policy_id: str
    geometry_state: GeometryState
    items: tuple[OverlayItem, ...]
    deterministic_compositor_required: bool = True
    plan_hash: str


def compile_overlay_plan(contract: ReelCreativeContract, blueprint: ReelBlueprint,
                         safe_zone: SafeZonePolicy) -> OverlayPlan:
    cta_intent = ClaimIntent(
        surface=ClaimSurface.END_CARD, text=contract.exact_cta,
        claim_type=ClaimType.CTA, factual=False, expressive=False,
        exact_wording_required=True,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED)
    authorization = authorize_claim(cta_intent, contract)
    if authorization.state != ClaimAuthorizationState.AUTHORIZED:
        raise CreativeCoreError(authorization.code, authorization.detail)
    start = max(0.0, blueprint.target_duration_seconds - 2)
    items = (OverlayItem(
        overlay_id="overlay_exact_cta", text=contract.exact_cta,
        semantic_role=OverlaySemanticRole.CTA,
        claim_authorization=authorization.code,
        platform_safe_zone_requirement=True,
        temporal_range=(start, blueprint.target_duration_seconds),
        placement_intent="end-card primary action area; exact coordinates unresolved",
        exact_geometry=safe_zone.geometry),)
    material = {
        "safe_zone_policy_id": safe_zone.policy_id,
        "geometry_state": safe_zone.geometry_state.value,
        "items": [item.model_dump(mode="json") for item in items],
        "deterministic_compositor_required": True,
    }
    digest = _stable_hash(material)
    return OverlayPlan(plan_id="ovp_" + digest[:24], plan_hash=digest, **material)


class ProviderRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requires_text_to_video: bool = False
    requires_image_to_video: bool = False
    requires_first_last_frame: bool = False
    requires_reference_images: bool = False
    requires_video_editing: bool = False
    requires_multimodal_video: bool = False
    requires_video_extension: bool = False
    requires_identity_consistency: bool = False
    requires_native_audio: bool = False
    allowed_durations: tuple[int, ...] = ()
    aspect_ratio: str = "9:16"
    resolution_floor: str = "720p"
    local_seed_input_required: bool = False


class ShotRepairability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    isolated_repair_boundaries: tuple[str, ...]
    previous_shot_dependency: str
    next_shot_dependency: str
    can_regenerate_alone: bool
    invalidates_audio: bool
    invalidates_captions: bool
    invalidates_continuity: bool


class ShotContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    coverage_id: str
    beat_id: str
    strategic_role: str
    creative_purpose: str
    duration_target: float = Field(gt=0)
    subject_spec: str
    product_spec: str
    location_spec: str
    actor_spec: str
    action_spec: str
    camera_spec: str
    composition_spec: str
    motion_spec: str
    lighting_style: str
    continuity_group: str
    protected_identity_requirements: tuple[str, ...]
    protected_brand_requirements: tuple[str, ...]
    claim_intents: tuple[ClaimIntent, ...]
    visual_implication_intents: tuple[ClaimIntent, ...]
    generated_text_policy: GeneratedTextPolicy
    protected_text_policy: str
    audio_intent: str
    overlay_reservation: str
    reference_requirements: tuple[ReferenceType, ...]
    seed_requirements: str
    provider_requirements: ProviderRequirements
    success_criteria: tuple[str, ...]
    failure_conditions: tuple[str, ...]
    repair_owner: RepairOwner
    repairability: ShotRepairability
    shot_hash: str

    @model_validator(mode="after")
    def executable_hash(self) -> "ShotContract":
        material = self.model_dump(mode="json", exclude={"shot_hash"})
        if self.shot_hash != _stable_hash(material):
            raise ValueError("shot hash mismatch")
        if (self.repairability.can_regenerate_alone
                and self.repairability.invalidates_continuity
                and not self.repairability.isolated_repair_boundaries):
            raise ValueError("modular repair claim lacks an isolated boundary")
        return self


def _shot_contract(node: CoverageNode, beat: NarrativeBeat, concept: CreativeConcept,
                   profile: PlatformProfile) -> ShotContract:
    seed_required = node.product_requirement == "VISIBLE_AND_VERIFIABLE"
    requirements = ProviderRequirements(
        requires_image_to_video=seed_required,
        requires_text_to_video=not seed_required,
        requires_first_last_frame=(
            seed_required and beat.role in {BeatRole.VALUE_DEMONSTRATION, BeatRole.PAYOFF}),
        requires_identity_consistency=seed_required or concept.actor_mode != ActorMode.NO_ACTOR,
        allowed_durations=(4, 6, 8), aspect_ratio=profile.aspect_ratio,
        resolution_floor="720p", local_seed_input_required=seed_required,
    )
    values: dict[str, Any] = {
        "shot_id": "shot_" + _stable_hash({"coverage": node.coverage_id})[:20],
        "coverage_id": node.coverage_id,
        "beat_id": node.beat_id,
        "strategic_role": beat.role.value,
        "creative_purpose": node.purpose,
        "duration_target": node.expected_duration,
        "subject_spec": ", ".join(node.required_subjects) or "product/environment only",
        "product_spec": node.product_requirement,
        "location_spec": "truthful generic environment; no place identity unless referenced",
        "actor_spec": concept.actor_mode.value,
        "action_spec": beat.narrative_purpose,
        "camera_spec": concept.visual_grammar,
        "composition_spec": f"vertical {profile.aspect_ratio}; reserve overlay-safe composition",
        "motion_spec": concept.pacing_intent,
        "lighting_style": "consistent with the selected visual grammar",
        "continuity_group": node.continuity_group,
        "protected_identity_requirements": (("same actor identity across continuity group",)
                                             if concept.actor_mode != ActorMode.NO_ACTOR else ()),
        "protected_brand_requirements": ("product and packaging fidelity", "brand mark fidelity"),
        "claim_intents": node.claim_intents,
        "visual_implication_intents": concept.visual_implication_intents,
        "generated_text_policy": CANONICAL_GENERATED_TEXT_POLICY,
        "protected_text_policy": (
            "protected brand text may exist only if identity and fidelity can be verified"),
        "audio_intent": concept.audio_direction,
        "overlay_reservation": "conceptual safe area; exact geometry comes from SafeZonePolicy",
        "reference_requirements": ((ReferenceType.PRODUCT_REFERENCE,
                                    ReferenceType.BRAND_REFERENCE) if seed_required else ()),
        "seed_requirements": "LOCAL_SEED_REQUIRED" if seed_required else "NO_SEED_REQUIRED",
        "provider_requirements": requirements,
        "success_criteria": (
            node.purpose, "claim intents remain within canonical authority",
            "product/brand fidelity is later verifiable"),
        "failure_conditions": (
            "unapproved factual or visual implication", "malformed or claim-bearing generated text",
            "product or identity drift"),
        "repair_owner": RepairOwner.SHOT,
        "repairability": ShotRepairability(
            isolated_repair_boundaries=(node.coverage_id,),
            previous_shot_dependency="continuity QA only",
            next_shot_dependency="continuity QA only",
            can_regenerate_alone=(concept.repairability_intent ==
                                  RepairabilityIntent.MODULAR_REPAIRABLE),
            invalidates_audio=False, invalidates_captions=False,
            invalidates_continuity=(concept.actor_mode != ActorMode.NO_ACTOR or seed_required),
        ),
    }
    values["shot_hash"] = _stable_hash(values)
    return ShotContract(**values)


def compile_shot_contracts(graph: CoverageGraph, blueprint: ReelBlueprint,
                           concept: CreativeConcept,
                           profile: PlatformProfile) -> tuple[ShotContract, ...]:
    beats = {item.beat_id: item for item in blueprint.beat_sequence}
    return tuple(_shot_contract(node, beats[node.beat_id], concept, profile)
                 for node in graph.nodes)


def compile_reference_and_seed_plans(
    shots: Sequence[ShotContract],
    *,
    product_reference: Optional[ReferenceAsset] = None,
    brand_reference: Optional[ReferenceAsset] = None,
    capabilities: VideoProviderCapabilities,
) -> tuple[tuple[ReferencePlan, ...], SeedPlan]:
    reference_plans: list[ReferencePlan] = []
    seed_shots: list[SeedShotPlan] = []
    supplied = [item for item in (product_reference, brand_reference) if item is not None]
    for shot in shots:
        references: list[ReferenceAsset] = []
        for asset in supplied:
            if asset.reference_type not in shot.reference_requirements:
                continue
            modes = list(asset.usage_modes)
            if ReferenceUsageMode.PROVIDER_REFERENCE in modes and not capabilities.reference_images:
                modes.remove(ReferenceUsageMode.PROVIDER_REFERENCE)
                if ReferenceUsageMode.SEED_CONDITIONING not in modes:
                    modes.append(ReferenceUsageMode.SEED_CONDITIONING)
                if ReferenceUsageMode.QA_REFERENCE not in modes:
                    modes.append(ReferenceUsageMode.QA_REFERENCE)
            references.append(asset.model_copy(update={"usage_modes": tuple(modes)}))
        provider_claimed = any(ReferenceUsageMode.PROVIDER_REFERENCE in item.usage_modes
                               for item in references)
        plan = ReferencePlan(
            shot_id=shot.shot_id, references=tuple(references),
            provider_attachment_claimed=provider_claimed)
        validate_reference_plan(plan, capabilities)
        reference_plans.append(plan)
        conditioning = tuple(item.reference_id for item in references
                             if ReferenceUsageMode.SEED_CONDITIONING in item.usage_modes)
        qa = tuple(item.reference_id for item in references
                   if ReferenceUsageMode.QA_REFERENCE in item.usage_modes)
        seed_required = shot.provider_requirements.local_seed_input_required
        seed_shots.append(SeedShotPlan(
            shot_id=shot.shot_id, seed_required=seed_required,
            conditioning_references=conditioning, QA_references=qa,
            expected_composition=shot.composition_spec,
            protected_product_features=shot.protected_brand_requirements,
            prohibited_text=("marketing copy", "CTA", "captions", "disclaimers"),
            expected_actor_identity=(shot.actor_spec if shot.actor_spec not in {
                ActorMode.NO_ACTOR.value, ActorMode.HANDS_ONLY.value} else ""),
            expected_place_identity="",
            success_criteria=shot.success_criteria,
        ))
    return tuple(reference_plans), SeedPlan(
        plan_id="seed_" + _stable_hash([item.model_dump(mode="json")
                                        for item in seed_shots])[:24],
        shots=tuple(seed_shots), provider_calls=0)


class ContinuityDimension(str, Enum):
    ACTOR = "ACTOR"
    PRODUCT = "PRODUCT"
    PACKAGING = "PACKAGING"
    PLACE = "PLACE"
    WARDROBE = "WARDROBE"
    LIGHTING = "LIGHTING"
    TIME_OF_DAY = "TIME_OF_DAY"
    SCREEN_DEVICE = "SCREEN_DEVICE"
    VISUAL_STYLE = "VISUAL_STYLE"


class ContinuityGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str
    shot_ids: tuple[str, ...]
    required_dimensions: tuple[ContinuityDimension, ...]
    textual_hints_are_not_guarantees: bool = True


class ContinuityPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    groups: tuple[ContinuityGroup, ...]
    later_QA_required: bool = True
    plan_hash: str


def compile_continuity_plan(shots: Sequence[ShotContract],
                            concept: CreativeConcept) -> ContinuityPlan:
    grouped: dict[str, list[str]] = {}
    for shot in shots:
        grouped.setdefault(shot.continuity_group, []).append(shot.shot_id)
    dimensions = [ContinuityDimension.PRODUCT, ContinuityDimension.PACKAGING,
                  ContinuityDimension.LIGHTING, ContinuityDimension.VISUAL_STYLE]
    if concept.actor_mode != ActorMode.NO_ACTOR:
        dimensions.extend((ContinuityDimension.ACTOR, ContinuityDimension.WARDROBE))
    groups = tuple(ContinuityGroup(
        group_id=group_id, shot_ids=tuple(shot_ids),
        required_dimensions=tuple(dimensions))
        for group_id, shot_ids in sorted(grouped.items()))
    material = {"groups": [item.model_dump(mode="json") for item in groups],
                "later_QA_required": True}
    digest = _stable_hash(material)
    return ContinuityPlan(plan_id="ctp_" + digest[:24], plan_hash=digest, **material)


class ShotExecutionMode(str, Enum):
    TEXT_TO_VIDEO = "TEXT_TO_VIDEO"
    IMAGE_TO_VIDEO = "IMAGE_TO_VIDEO"
    FIRST_LAST_FRAME = "FIRST_LAST_FRAME"
    MULTIMODAL_VIDEO = "MULTIMODAL_VIDEO"
    CONVERSATIONAL_EDIT = "CONVERSATIONAL_EDIT"
    VIDEO_EXTENSION = "VIDEO_EXTENSION"


class ShotExecutionState(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    NO_AUTHORIZED_PROVIDER = "NO_AUTHORIZED_PROVIDER"
    UNSUPPORTED_REQUIRED_CAPABILITY = "UNSUPPORTED_REQUIRED_CAPABILITY"
    NEEDS_PLAN_REPAIR = "NEEDS_PLAN_REPAIR"


class ShotExecutionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    state: ShotExecutionState
    execution_mode: ShotExecutionMode
    eligible_provider_identities: tuple[str, ...] = ()
    would_be_capable_if_authorized: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    explanation: str
    provider_calls: int = 0


def _provider_identity(capabilities: VideoProviderCapabilities) -> str:
    return (f"{capabilities.provider_name}:{capabilities.model_id}:"
            f"{capabilities.surface.value}")


def _execution_mode(requirements: ProviderRequirements) -> ShotExecutionMode:
    if requirements.requires_video_extension:
        return ShotExecutionMode.VIDEO_EXTENSION
    if requirements.requires_video_editing:
        return ShotExecutionMode.CONVERSATIONAL_EDIT
    if requirements.requires_multimodal_video:
        return ShotExecutionMode.MULTIMODAL_VIDEO
    if requirements.requires_first_last_frame:
        return ShotExecutionMode.FIRST_LAST_FRAME
    if requirements.requires_image_to_video:
        return ShotExecutionMode.IMAGE_TO_VIDEO
    return ShotExecutionMode.TEXT_TO_VIDEO


def _provider_support(capabilities: VideoProviderCapabilities,
                      requirements: ProviderRequirements) -> tuple[str, ...]:
    pairs = (
        ("text_to_video", requirements.requires_text_to_video),
        ("image_to_video", requirements.requires_image_to_video),
        ("first_last_frame", requirements.requires_first_last_frame),
        ("reference_images", requirements.requires_reference_images),
        ("conversational_editing", requirements.requires_video_editing),
        ("multimodal_video", requirements.requires_multimodal_video),
        ("video_extension", requirements.requires_video_extension),
        ("native_audio", requirements.requires_native_audio),
    )
    missing = [name for name, needed in pairs
               if needed and not bool(getattr(capabilities, name))]
    if requirements.local_seed_input_required and not capabilities.local_seed_supported:
        missing.append("local_seed_supported")
    if (requirements.requires_identity_consistency
            and not _plain(capabilities.identity_control_mode)):
        missing.append("identity_consistency")
    if requirements.aspect_ratio not in capabilities.supported_aspect_ratios:
        missing.append("aspect_ratio=" + requirements.aspect_ratio)
    if requirements.allowed_durations and not (
            set(requirements.allowed_durations) & set(capabilities.supported_durations)):
        missing.append("allowed_duration")
    resolution_rank = {"720p": 720, "1080p": 1080, "4k": 2160}
    floor = resolution_rank.get(requirements.resolution_floor.casefold(), 0)
    if not any(resolution_rank.get(item.casefold(), 0) >= floor
               for item in capabilities.supported_resolutions):
        missing.append("resolution_floor=" + requirements.resolution_floor)
    return tuple(missing)


class ShotModeRouter:
    def __init__(self, registry: Sequence[VideoProviderCapabilities]) -> None:
        self._registry = tuple(registry)

    def route(self, shot: ShotContract) -> ShotExecutionDecision:
        requirements = shot.provider_requirements
        mode = _execution_mode(requirements)
        if (requirements.requires_reference_images
                and ReferenceType.PRODUCT_REFERENCE not in shot.reference_requirements
                and ReferenceType.BRAND_REFERENCE not in shot.reference_requirements):
            return ShotExecutionDecision(
                shot_id=shot.shot_id, state=ShotExecutionState.NEEDS_PLAN_REPAIR,
                execution_mode=mode, missing_capabilities=("reference plan",),
                explanation="Shot requests provider references without a reference contract.")
        supported = [(item, _provider_support(item, requirements)) for item in self._registry]
        authorized = tuple(sorted(
            _provider_identity(item) for item, missing in supported
            if not missing and item.authorization_status ==
            ProviderAuthorizationStatus.AUTHORIZED_PROFILE))
        experimental = tuple(sorted(
            _provider_identity(item) for item, missing in supported
            if not missing and item.authorization_status !=
            ProviderAuthorizationStatus.AUTHORIZED_PROFILE))
        if authorized:
            return ShotExecutionDecision(
                shot_id=shot.shot_id, state=ShotExecutionState.ELIGIBLE,
                execution_mode=mode, eligible_provider_identities=authorized,
                explanation="Authorized capability metadata satisfies the shot; nothing executed.")
        if experimental:
            return ShotExecutionDecision(
                shot_id=shot.shot_id, state=ShotExecutionState.NO_AUTHORIZED_PROVIDER,
                execution_mode=mode, would_be_capable_if_authorized=experimental,
                explanation="Only an experimental, unauthorized profile would be capable.")
        missing = sorted({value for _item, values in supported for value in values})
        state = (ShotExecutionState.NO_AUTHORIZED_PROVIDER if not self._registry else
                 ShotExecutionState.UNSUPPORTED_REQUIRED_CAPABILITY)
        return ShotExecutionDecision(
            shot_id=shot.shot_id, state=state, execution_mode=mode,
            missing_capabilities=tuple(missing),
            explanation=("No provider profiles are registered." if not self._registry else
                         "No registered model-surface capability profile can satisfy the shot."))


class QAApplicability(str, Enum):
    MANDATORY = "MANDATORY"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class QARequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: str
    applicability: QAApplicability
    reason: str


class FinalQARequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirements: tuple[QARequirement, ...]

    @model_validator(mode="after")
    def complete_without_duplicates(self) -> "FinalQARequirements":
        expected = {
            "strategy_alignment", "claim_safety", "creative_fitness", "product_fidelity",
            "brand_fidelity", "human_fidelity", "physical_plausibility", "motion_quality",
            "temporal_consistency", "cross_scene_continuity", "audio_script_integrity",
            "caption_integrity", "platform_compliance", "container_integrity",
        }
        actual = [item.dimension for item in self.requirements]
        if set(actual) != expected or len(actual) != len(expected):
            raise ValueError("final QA requirements must classify every terminal dimension once")
        return self

    def for_dimension(self, dimension: str) -> QARequirement:
        return next(item for item in self.requirements if item.dimension == dimension)


def compile_QA_requirements(concept: CreativeConcept,
                            audio_plan: AudioPlan) -> FinalQARequirements:
    mandatory = (
        "strategy_alignment", "claim_safety", "creative_fitness", "product_fidelity",
        "brand_fidelity", "motion_quality", "physical_plausibility", "temporal_consistency",
        "cross_scene_continuity", "caption_integrity", "platform_compliance",
        "container_integrity",
    )
    requirements = [QARequirement(
        dimension=item, applicability=QAApplicability.MANDATORY,
        reason="This plan exposes the dimension in the final artifact.") for item in mandatory]
    requirements.append(QARequirement(
        dimension="audio_script_integrity",
        applicability=(QAApplicability.MANDATORY if
                       audio_plan.marketing_voiceover.planned else QAApplicability.NOT_APPLICABLE),
        reason="Marketing voiceover is planned." if audio_plan.marketing_voiceover.planned
        else "No marketing voiceover is planned."))
    requirements.append(QARequirement(
        dimension="human_fidelity",
        applicability=(QAApplicability.NOT_APPLICABLE if concept.actor_mode == ActorMode.NO_ACTOR
                       else QAApplicability.MANDATORY),
        reason="No human appears." if concept.actor_mode == ActorMode.NO_ACTOR
        else "A human or human body part appears."))
    return FinalQARequirements(requirements=tuple(requirements))


def validate_QA_requirements(concept: CreativeConcept,
                             requirements: FinalQARequirements
                             ) -> tuple[CreativeDefect, ...]:
    human = requirements.for_dimension("human_fidelity")
    if (concept.actor_mode != ActorMode.NO_ACTOR
            and human.applicability == QAApplicability.NOT_APPLICABLE):
        return (CreativeDefect(
            defect_type=DefectType.TERMINAL_VERIFICATION_INCOMPLETE,
            repair_owner=RepairOwner.COMPOSITOR,
            concept_id=concept.concept_id,
            detail="Human fidelity cannot be NOT_APPLICABLE when a human appears."),)
    return ()


def validate_shot_repairability(shot: ShotContract, concept: CreativeConcept
                                ) -> tuple[CreativeDefect, ...]:
    if (concept.repairability_intent == RepairabilityIntent.MODULAR_REPAIRABLE
            and not shot.repairability.can_regenerate_alone):
        return (CreativeDefect(
            defect_type=DefectType.UNEXECUTABLE_SHOT_PLAN,
            repair_owner=RepairOwner.SHOT,
            concept_id=concept.concept_id, shot_id=shot.shot_id,
            detail="Shot cannot be regenerated independently despite a modular plan."),)
    return ()


class RepairTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    defect_type: DefectType
    repair_owner: RepairOwner
    smallest_scope: str
    never_regenerate_strategy: bool = True


REPAIR_POLICY: tuple[RepairTemplate, ...] = (
    RepairTemplate(defect_type=DefectType.WEAK_HOOK, repair_owner=RepairOwner.CONCEPT,
                   smallest_scope="concept hook or narrative arc"),
    RepairTemplate(defect_type=DefectType.CREATIVE_CONCEPTS_NOT_DISTINCT,
                   repair_owner=RepairOwner.CONCEPT, smallest_scope="candidate treatments"),
    RepairTemplate(defect_type=DefectType.NO_VIABLE_CONCEPT,
                   repair_owner=RepairOwner.CONCEPT, smallest_scope="concept tournament"),
    RepairTemplate(defect_type=DefectType.VALUE_NOT_DEMONSTRATED,
                   repair_owner=RepairOwner.ARC, smallest_scope="value beat"),
    RepairTemplate(defect_type=DefectType.PRODUCT_APPEARS_TOO_LATE,
                   repair_owner=RepairOwner.ARC, smallest_scope="product entry beat"),
    RepairTemplate(defect_type=DefectType.PROPOSITION_TOO_LATE,
                   repair_owner=RepairOwner.ARC, smallest_scope="proposition beat timing"),
    RepairTemplate(defect_type=DefectType.VISUAL_METAPHOR_CONFLICTS_WITH_TRUTH,
                   repair_owner=RepairOwner.CONCEPT, smallest_scope="visual metaphor"),
    RepairTemplate(defect_type=DefectType.UNEXECUTABLE_SHOT_PLAN,
                   repair_owner=RepairOwner.SHOT, smallest_scope="one shot contract"),
    RepairTemplate(defect_type=DefectType.PRODUCT_IDENTITY_DRIFT,
                   repair_owner=RepairOwner.SEED, smallest_scope="seed or affected video shot"),
    RepairTemplate(defect_type=DefectType.AUDIO_SCRIPT_MISMATCH,
                   repair_owner=RepairOwner.AUDIO, smallest_scope="audio artifact"),
    RepairTemplate(defect_type=DefectType.SAFE_ZONE_VIOLATION,
                   repair_owner=RepairOwner.PLATFORM_LAYOUT,
                   smallest_scope="compositor platform layout"),
    RepairTemplate(defect_type=DefectType.CTA_MISMATCH,
                   repair_owner=RepairOwner.CONTRACT,
                   smallest_scope="contract if authority is wrong; overlay if delivery is wrong"),
    RepairTemplate(defect_type=DefectType.SHOT_REDUNDANCY,
                   repair_owner=RepairOwner.ARC, smallest_scope="repeated coverage nodes"),
)


class TargetedRepairPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    defect_type: DefectType
    repair_owner: RepairOwner
    target_ids: tuple[str, ...]
    smallest_scope: str
    invalidates_strategy: bool = False


def targeted_repair_plan(defect: CreativeDefect) -> TargetedRepairPlan:
    template = next((item for item in REPAIR_POLICY
                     if item.defect_type == defect.defect_type), None)
    owner = template.repair_owner if template else defect.repair_owner
    scope = template.smallest_scope if template else "smallest owning artifact"
    ids = tuple(value for value in (
        defect.shot_id, defect.coverage_id, defect.beat_id, defect.concept_id) if value)
    return TargetedRepairPlan(
        defect_type=defect.defect_type, repair_owner=owner,
        target_ids=ids, smallest_scope=scope, invalidates_strategy=False)


class PlanReadiness(str, Enum):
    PLAN_READY = "PLAN_READY"
    PLAN_NEEDS_REPAIR = "PLAN_NEEDS_REPAIR"
    NO_VIABLE_CONCEPT = "NO_VIABLE_CONCEPT"
    NO_AUTHORIZED_PROVIDER = "NO_AUTHORIZED_PROVIDER"
    SAFE_ZONE_CONFIGURATION_REQUIRED = "SAFE_ZONE_CONFIGURATION_REQUIRED"


class CanonicalReelExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    contract_hash: str
    platform_profile: PlatformProfile
    selected_concept: CreativeConcept
    blueprint: ReelBlueprint
    coverage_graph: CoverageGraph
    shot_contracts: tuple[ShotContract, ...]
    reference_plans: tuple[ReferencePlan, ...]
    seed_plan: SeedPlan
    continuity_plan: ContinuityPlan
    audio_plan: AudioPlan
    overlay_plan: OverlayPlan
    provider_requirements: tuple[ProviderRequirements, ...]
    provider_eligibility_decisions: tuple[ShotExecutionDecision, ...]
    QA_requirements: FinalQARequirements
    repair_policy: tuple[RepairTemplate, ...]
    planned_paid_categories: tuple[PaidProviderCategory, ...]
    cost_state: str
    creative_defects: tuple[CreativeDefect, ...]
    creative_valid: bool
    executable_now: bool
    plan_readiness: PlanReadiness
    provider_calls: int = 0
    plan_hash: str

    @model_validator(mode="after")
    def deterministic_plan(self) -> "CanonicalReelExecutionPlan":
        material = self.model_dump(mode="json", exclude={"plan_id", "plan_hash"})
        if self.plan_hash != _stable_hash(material):
            raise ValueError("execution plan hash mismatch")
        if self.provider_calls != 0:
            raise ValueError("Gate 2D.2 execution plans cannot contain provider calls")
        return self


class CreativePlanCompilation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tournament: ConceptTournamentResult
    plan: Optional[CanonicalReelExecutionPlan] = None
    readiness: PlanReadiness
    provider_calls: int = 0
    billable: bool = False


def compile_canonical_creative_plan(
    contract: ReelCreativeContract,
    profile: PlatformProfile,
    concepts: Sequence[CreativeConcept],
    *,
    safe_zone: Optional[SafeZonePolicy] = None,
    video_provider_registry: Sequence[VideoProviderCapabilities] = tuple(
        (*CANONICAL_PROVIDER_REGISTRY.values(), GEMINI_OMNI_FLASH_EXPERIMENTAL)),
    product_reference: Optional[ReferenceAsset] = None,
    brand_reference: Optional[ReferenceAsset] = None,
    finite_cost_envelope_known: bool = False,
) -> CreativePlanCompilation:
    tournament = ConceptTournament().run(contract, profile, concepts)
    if tournament.decision == TournamentDecision.NO_VIABLE_CONCEPT:
        return CreativePlanCompilation(
            tournament=tournament, readiness=PlanReadiness.NO_VIABLE_CONCEPT)
    if tournament.decision == TournamentDecision.TIE_REQUIRES_REVIEW:
        return CreativePlanCompilation(
            tournament=tournament, readiness=PlanReadiness.PLAN_NEEDS_REPAIR)
    concept = next(item for item in concepts
                   if item.concept_id == tournament.selected_concept_id)
    blueprint = compile_blueprint(concept, contract, profile)
    selected_fitness = next(item for item in tournament.fitness
                            if item.concept_id == concept.concept_id)
    defects = list(selected_fitness.defects)
    defects.extend(validate_blueprint(blueprint, contract, profile))
    graph = compile_coverage_graph(blueprint, concept)
    shots = compile_shot_contracts(graph, blueprint, concept, profile)
    authorized = next((item for item in video_provider_registry
                       if item.authorization_status ==
                       ProviderAuthorizationStatus.AUTHORIZED_PROFILE), None)
    reference_capabilities = authorized or (video_provider_registry[0]
                                             if video_provider_registry else None)
    if reference_capabilities is None:
        reference_plans = tuple(ReferencePlan(
            shot_id=shot.shot_id, references=(), provider_attachment_claimed=False)
            for shot in shots)
        seed_plan = SeedPlan(
            plan_id="seed_empty", shots=tuple(SeedShotPlan(
                shot_id=shot.shot_id,
                seed_required=shot.provider_requirements.local_seed_input_required,
                conditioning_references=(), QA_references=(),
                expected_composition=shot.composition_spec,
                protected_product_features=shot.protected_brand_requirements,
                prohibited_text=("marketing copy", "CTA", "captions", "disclaimers"),
                expected_actor_identity="", expected_place_identity="",
                success_criteria=shot.success_criteria) for shot in shots))
    else:
        reference_plans, seed_plan = compile_reference_and_seed_plans(
            shots, product_reference=product_reference, brand_reference=brand_reference,
            capabilities=reference_capabilities)
    refs_by_shot = {item.shot_id: item for item in reference_plans}
    for seed in seed_plan.shots:
        if seed.seed_required and not seed.conditioning_references:
            defects.append(CreativeDefect(
                defect_type=DefectType.UNEXECUTABLE_SHOT_PLAN, repair_owner=RepairOwner.SHOT,
                shot_id=seed.shot_id,
                detail="A required local seed has no conditioning reference."))
    router = ShotModeRouter(video_provider_registry)
    decisions = tuple(router.route(shot) for shot in shots)
    for decision in decisions:
        if decision.state in {ShotExecutionState.UNSUPPORTED_REQUIRED_CAPABILITY,
                              ShotExecutionState.NEEDS_PLAN_REPAIR}:
            defects.append(CreativeDefect(
                defect_type=DefectType.UNEXECUTABLE_SHOT_PLAN, repair_owner=RepairOwner.SHOT,
                shot_id=decision.shot_id, detail=decision.explanation))
    audio = compile_audio_plan(contract, concept)
    zone = safe_zone or UNCONFIGURED_SAFE_ZONES[profile.platform]
    if zone.policy_id != profile.safe_zone_policy_id:
        raise CreativeCoreError(
            "SAFE_ZONE_POLICY_MISMATCH", "profile and safe-zone policy versions disagree")
    overlay = compile_overlay_plan(contract, blueprint, zone)
    continuity = compile_continuity_plan(shots, concept)
    qa = compile_QA_requirements(concept, audio)

    creative_valid = not any(item.defect_type in {
        DefectType.STRATEGY_DRIFT, DefectType.UNSUPPORTED_CLAIM,
        DefectType.INTERNAL_EVIDENCE_LEAK, DefectType.CTA_MISMATCH,
        DefectType.VISUAL_METAPHOR_CONFLICTS_WITH_TRUTH,
        DefectType.VALUE_NOT_DEMONSTRATED, DefectType.PROPOSITION_TOO_LATE,
        DefectType.PRODUCT_APPEARS_TOO_LATE, DefectType.SHOT_REDUNDANCY,
    } for item in defects)
    if not creative_valid or any(item.state in {
            ShotExecutionState.UNSUPPORTED_REQUIRED_CAPABILITY,
            ShotExecutionState.NEEDS_PLAN_REPAIR} for item in decisions):
        readiness = PlanReadiness.PLAN_NEEDS_REPAIR
    elif any(item.state == ShotExecutionState.NO_AUTHORIZED_PROVIDER for item in decisions):
        readiness = PlanReadiness.NO_AUTHORIZED_PROVIDER
    elif (zone.geometry_state == GeometryState.UNCONFIGURED
          and any(item.platform_safe_zone_requirement for item in overlay.items)):
        readiness = PlanReadiness.SAFE_ZONE_CONFIGURATION_REQUIRED
    elif not finite_cost_envelope_known:
        readiness = PlanReadiness.PLAN_NEEDS_REPAIR
    else:
        readiness = PlanReadiness.PLAN_READY
    # Even PLAN_READY means ready to cross the human-approval boundary.  No approval is consumed,
    # so nothing in Gate 2D.2 is executable now.
    executable = False
    paid = [PaidProviderCategory.VIDEO]
    if any(item.seed_required for item in seed_plan.shots):
        paid.append(PaidProviderCategory.SEED_IMAGE)
    if audio.marketing_voiceover.planned:
        paid.append(PaidProviderCategory.TTS)
    if audio.SFX.planned:
        paid.append(PaidProviderCategory.SFX)
    values: dict[str, Any] = {
        "contract_hash": contract.contract_hash,
        "platform_profile": profile,
        "selected_concept": concept,
        "blueprint": blueprint,
        "coverage_graph": graph,
        "shot_contracts": shots,
        "reference_plans": reference_plans,
        "seed_plan": seed_plan,
        "continuity_plan": continuity,
        "audio_plan": audio,
        "overlay_plan": overlay,
        "provider_requirements": tuple(item.provider_requirements for item in shots),
        "provider_eligibility_decisions": decisions,
        "QA_requirements": qa,
        "repair_policy": REPAIR_POLICY,
        "planned_paid_categories": tuple(paid),
        "cost_state": ("FINITE_ENVELOPE_SUPPLIED_EXTERNALLY" if finite_cost_envelope_known
                       else "NOT_ASSESSED_NO_PRICING_IN_GATE_2D2"),
        "creative_defects": tuple(defects),
        "creative_valid": creative_valid,
        "executable_now": executable,
        "plan_readiness": readiness,
        "provider_calls": 0,
    }
    digest = _stable_hash({key: (value.model_dump(mode="json")
                                if isinstance(value, BaseModel)
                                else [item.model_dump(mode="json") if isinstance(item, BaseModel)
                                      else item.value if isinstance(item, Enum) else item
                                      for item in value] if isinstance(value, tuple)
                                else value.value if isinstance(value, Enum) else value)
                           for key, value in values.items()})
    plan = CanonicalReelExecutionPlan(
        plan_id="crp_" + digest[:24], plan_hash=digest, **values)
    return CreativePlanCompilation(tournament=tournament, plan=plan, readiness=readiness)


def _canonical_message_intent(contract: ReelCreativeContract) -> ClaimIntent:
    return ClaimIntent(
        surface=ClaimSurface.ON_SCREEN_COPY, text=contract.consumer_value,
        supporting_allowed_claim_ids=contract.allowed_claim_ids[:1],
        claim_type=ClaimType.MESSAGE, factual=True, expressive=False,
        exact_wording_required=False,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED)


def _safe_visual_intent(contract: ReelCreativeContract, semantic: str) -> ClaimIntent:
    return ClaimIntent(
        surface=ClaimSurface.VISUAL_IMPLICATION, semantic_intent=semantic,
        supporting_allowed_claim_ids=contract.allowed_claim_ids[:1],
        claim_type=ClaimType.VISUAL, factual=True, expressive=False,
        exact_wording_required=False,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED)


def buffalo_fixture_concepts(contract: ReelCreativeContract,
                             platform: Platform = Platform.TIKTOK
                             ) -> tuple[CreativeConcept, ...]:
    """Four deterministic treatment families; these are benchmarks, not performance claims."""
    shared: dict[str, Any] = {
        "reel_contract_hash": contract.contract_hash,
        "platform": platform,
        "audience_tension": contract.target,
        "value_demonstration": contract.consumer_value,
        "claim_intents": (_canonical_message_intent(contract),),
        "expected_duration_range": (15.0, 15.0),
        "required_capabilities": ("IMAGE_TO_VIDEO", "LOCAL_SEED"),
        "creative_risks": ("app screen or packaging identity may drift",),
        "why_it_may_fail": ("value may be described without becoming visually legible",),
        "declared_target": contract.target,
        "preserved_consumer_value": contract.consumer_value,
        "declared_CTA": contract.exact_cta,
        "repairability_intent": RepairabilityIntent.MODULAR_REPAIRABLE,
    }
    return (
        create_creative_concept(
            **shared, concept_id="buffalo_pov_convenience",
            creative_thesis="Make convenience tangible through a first-person task flow.",
            hook_mechanism="POV cold open on a thumb beginning an app order",
            narrative_device="compressed first-person ordering journey",
            product_role="phone interface guides the journey and food resolves it",
            brand_entry="brand enters through verified product and end card",
            emotional_lever="relief from decision friction",
            visual_grammar="POV social-native jump cuts",
            reveal_pattern="hand-to-phone then product payoff",
            actor_mode=ActorMode.HANDS_ONLY,
            pacing_intent="quick modular cuts with readable actions",
            audio_direction="natural taps, light music, exact marketing voiceover",
            CTA_transition="phone action resolves into deterministic end card",
            visual_implication_intents=(_safe_visual_intent(
                contract, "A user's hand selects an app option"),),
            production_complexity=ProductionComplexity.MODERATE,
            planned_beat_roles=(BeatRole.HOOK, BeatRole.CONTEXT,
                                BeatRole.VALUE_DEMONSTRATION, BeatRole.PAYOFF, BeatRole.CTA),
            product_presence_policy=ProductPresencePolicy.EARLY),
        create_creative_concept(
            **shared, concept_id="buffalo_app_deal_reveal",
            creative_thesis="Turn value discovery into a controlled visual reveal.",
            hook_mechanism="concealed choice opens into an app and deal reveal",
            narrative_device="progressive information reveal",
            product_role="app is the discovery surface; product is the reveal payoff",
            brand_entry="brand appears at the verified app/product reveal",
            emotional_lever="curiosity",
            visual_grammar="graphic social reveals and match cuts",
            reveal_pattern="mystery card to app choice to product",
            actor_mode=ActorMode.NO_ACTOR,
            pacing_intent="escalating reveals with modular inserts",
            audio_direction="reveal accents, music, exact marketing voiceover",
            CTA_transition="final reveal becomes deterministic CTA card",
            visual_implication_intents=(_safe_visual_intent(
                contract, "An app choice is followed by a product reveal"),),
            production_complexity=ProductionComplexity.MODERATE,
            planned_beat_roles=(BeatRole.HOOK, BeatRole.PRODUCT_REVEAL,
                                BeatRole.VALUE_DEMONSTRATION, BeatRole.BRAND_MOMENT, BeatRole.CTA),
            product_presence_policy=ProductPresencePolicy.DISTRIBUTED),
        create_creative_concept(
            **shared, concept_id="buffalo_product_first_demo",
            creative_thesis="Demonstrate app value by tracing a product back to the choice.",
            hook_mechanism="immediate product-first action in the first frame",
            narrative_device="reverse reveal from product payoff to ordering steps",
            product_role="verified product opens, app demonstrates choice, product closes",
            brand_entry="verified product carries brand entry from opening",
            emotional_lever="instant appetite and control",
            visual_grammar="social-native macro inserts and functional match cuts",
            reveal_pattern="product hero to app selection to product return",
            actor_mode=ActorMode.HANDS_ONLY,
            pacing_intent="economical modular demonstration",
            audio_direction="product sounds, light rhythm, exact marketing voiceover",
            CTA_transition="completed demonstration cuts to deterministic CTA card",
            visual_implication_intents=(_safe_visual_intent(
                contract, "A product and app interface appear in the same sequence"),),
            production_complexity=ProductionComplexity.LOW,
            planned_beat_roles=(BeatRole.HOOK, BeatRole.PRODUCT_REVEAL,
                                BeatRole.VALUE_DEMONSTRATION, BeatRole.PAYOFF, BeatRole.CTA),
            product_presence_policy=ProductPresencePolicy.OPENING),
        create_creative_concept(
            **shared, concept_id="buffalo_creator_discovery",
            creative_thesis="Let a creator-style discovery make the app value relatable.",
            hook_mechanism="creator-style discovery reaction",
            narrative_device="personal discovery diary",
            product_role="app and product are demonstrated by one person",
            brand_entry="brand follows the creator's verified product discovery",
            emotional_lever="recognition",
            visual_grammar="handheld creator social diary",
            reveal_pattern="face reaction to phone demonstration to product",
            actor_mode=ActorMode.SINGLE_ACTOR,
            pacing_intent="conversational modular clips",
            audio_direction="environment audio, music, exact marketing voiceover",
            CTA_transition="creator gesture leads to deterministic CTA card",
            visual_implication_intents=(_safe_visual_intent(
                contract, "One person looks at the app and product"),),
            production_complexity=ProductionComplexity.HIGH,
            planned_beat_roles=(BeatRole.HOOK, BeatRole.REACTION,
                                BeatRole.VALUE_DEMONSTRATION, BeatRole.BRAND_MOMENT, BeatRole.CTA),
            product_presence_policy=ProductPresencePolicy.DISTRIBUTED),
    )


def buffalo_fixture_references() -> tuple[ReferenceAsset, ReferenceAsset]:
    product = ReferenceAsset(
        reference_id="buffalo-approved-product-fixture",
        reference_type=ReferenceType.PRODUCT_REFERENCE,
        usage_modes=(ReferenceUsageMode.SEED_CONDITIONING, ReferenceUsageMode.QA_REFERENCE),
        source_hash=_stable_hash("offline-approved-buffalo-product-fixture"),
        source_note="offline benchmark fixture; not claimed as a provider reference")
    brand = ReferenceAsset(
        reference_id="buffalo-approved-brand-fixture",
        reference_type=ReferenceType.BRAND_REFERENCE,
        usage_modes=(ReferenceUsageMode.QA_REFERENCE, ReferenceUsageMode.PROTECTED_ASSET),
        source_hash=_stable_hash("offline-approved-buffalo-brand-fixture"),
        source_note="offline benchmark fixture; deterministic QA/protected asset only")
    return product, brand


__all__ = [
    "ActorMode", "AudioCategory", "AudioLayerPlan", "AudioPlan", "BeatRole",
    "BrandPresencePolicy", "CANONICAL_GENERATED_TEXT_POLICY",
    "CanonicalReelExecutionPlan", "ConceptDistinctnessResult", "ConceptDistinctnessState",
    "ConceptProposer", "ConceptTournament", "ConceptTournamentResult", "ContinuityDimension",
    "ContinuityGroup", "ContinuityPlan", "CoverageEdge", "CoverageGraph", "CoverageNode",
    "CoverageRequirement", "CoverageRole", "CreativeBoundaries", "CreativeConcept",
    "CreativeCoreError", "CreativeDefect", "CreativeFitnessResult", "CreativePlanCompilation",
    "DurationGuidance", "FinalQARequirements", "FitnessAssessment", "FitnessState",
    "FixtureConceptProposer", "GeneratedTextCategory", "GeneratedTextDisposition",
    "GeneratedTextPolicy", "GeneratedTextRule", "GeometryState", "IMAGE_PROVIDER_CAPABILITIES",
    "ImageProviderAuthorization", "ImageProviderCapabilities", "NarrativeBeat", "OverlayItem",
    "OverlayPlan", "OverlaySemanticRole", "PLATFORM_PROFILES", "PlanReadiness", "Platform",
    "PlatformProfile", "PreferenceBand", "ProductPresencePolicy", "ProviderRequirements",
    "QAApplicability", "QARequirement", "REPAIR_POLICY", "ReferenceAsset", "ReferencePlan",
    "ReferenceType", "ReferenceUsageMode", "RepairPriority", "RepairTemplate",
    "RepairabilityIntent", "ReelBlueprint", "SafeZoneGeometry", "SafeZonePolicy", "SeedPlan",
    "SeedShotPlan", "ShotContract", "ShotExecutionDecision", "ShotExecutionMode",
    "ShotExecutionState", "ShotModeRouter", "ShotRepairability", "TargetedRepairPlan",
    "TimingGuidance", "TournamentDecision", "UNCONFIGURED_SAFE_ZONES", "VoiceoverLine",
    "VoiceoverScript", "assess_concept_distinctness", "buffalo_fixture_concepts",
    "buffalo_fixture_references", "compile_QA_requirements", "compile_audio_plan",
    "compile_blueprint", "compile_canonical_creative_plan", "compile_continuity_plan",
    "compile_coverage_graph", "compile_overlay_plan", "compile_reference_and_seed_plans",
    "compile_shot_contracts", "compile_voiceover_script", "create_creative_concept",
    "creative_boundaries", "evaluate_concept", "platform_profile", "targeted_repair_plan",
    "validate_blueprint", "validate_reference_plan",
    "validate_QA_requirements", "validate_shot_repairability",
]
