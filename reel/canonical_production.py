"""Offline canonical Reel production harness.

This module translates an already-approved :class:`CanonicalReelExecutionPlan` into inspectable
provider-neutral and provider-specific requests.  It has no provider client, credential lookup,
database import, network primitive, renderer, or persistence implementation.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical_creative import (
    IMAGE_PROVIDER_CAPABILITIES,
    AudioPlan,
    CanonicalReelExecutionPlan,
    ContinuityPlan,
    FinalQARequirements,
    GeneratedTextPolicy,
    ImageProviderAuthorization,
    ImageProviderCapabilities,
    OverlaySemanticRole,
    Platform,
    ProviderRequirements,
    ReferencePlan,
    ReferenceUsageMode,
    SeedShotPlan,
    ShotContract,
    ShotExecutionMode,
    VoiceoverScript,
)
from .canonical_execution import (
    ProviderSurface,
    RepairOwner,
    VERTEX_VEO_31,
    VERTEX_VEO_31_FAST,
    VideoProviderCapabilities,
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def _stable_hash(value: Any) -> str:
    raw = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ProductionHarnessError(RuntimeError):
    def __init__(self, code: str, message: str, *, repair_owner: Optional[RepairOwner] = None) -> None:
        super().__init__(message)
        self.code = code
        self.repair_owner = repair_owner


class RequirementState(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PhoneUIExecutionMode(str, Enum):
    NONE = "NONE"
    ABSTRACT_PHONE_CONTEXT = "ABSTRACT_PHONE_CONTEXT"
    DETERMINISTIC_SCREEN_COMPOSITE = "DETERMINISTIC_SCREEN_COMPOSITE"
    PROTECTED_UI_REFERENCE = "PROTECTED_UI_REFERENCE"


class ManifestInputType(str, Enum):
    SEED_IMAGE = "SEED_IMAGE"
    FIRST_FRAME = "FIRST_FRAME"
    LAST_FRAME = "LAST_FRAME"
    PROVIDER_REFERENCE = "PROVIDER_REFERENCE"
    PROTECTED_UI_REFERENCE = "PROTECTED_UI_REFERENCE"


class ManifestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_type: ManifestInputType
    required: bool
    asset_id: str = ""


class ReferenceUsageBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_id: str
    usage_modes: tuple[ReferenceUsageMode, ...]


class ShotInputBindings(BaseModel):
    """Material input bindings; empty IDs remain visible and are never auto-filled."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seed_image_asset_id: str = ""
    first_frame_asset_id: str = ""
    last_frame_asset_id: str = ""
    provider_reference_asset_ids: tuple[str, ...] = ()
    protected_UI_reference_asset_id: str = ""
    requested_duration_seconds: Optional[int] = Field(default=None, gt=0)
    requested_resolution: str = "720p"
    phone_UI_mode: PhoneUIExecutionMode = PhoneUIExecutionMode.NONE
    consumer_language: str = "und"
    provider_instruction_language: str = "en"
    safety_person_controls: tuple[str, ...] = (
        "standard advertising safety review required",
        "no unapproved real-person identity",
    )

    @model_validator(mode="after")
    def exact_UI_never_becomes_a_prompt(self) -> "ShotInputBindings":
        if (self.phone_UI_mode == PhoneUIExecutionMode.PROTECTED_UI_REFERENCE
                and not self.protected_UI_reference_asset_id):
            raise ValueError("PROTECTED_UI_REFERENCE requires an exact protected UI asset")
        if self.provider_instruction_language.casefold() != "en":
            raise ValueError("current Veo profile requires English provider instructions")
        return self


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


class ShotExecutionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    shot_hash: str
    contract_hash: str
    plan_hash: str
    seed_shot_hash: str
    reference_plan_hash: str
    continuity_plan_hash: str
    audio_plan_hash: str
    execution_mode: ShotExecutionMode
    required_inputs: tuple[ManifestInput, ...]
    seed_requirement: RequirementState
    first_frame_requirement: RequirementState
    last_frame_requirement: RequirementState
    reference_usage: tuple[ReferenceUsageBinding, ...]
    protected_product_features: tuple[str, ...]
    protected_brand_features: tuple[str, ...]
    generated_text_policy: GeneratedTextPolicy
    generated_text_policy_hash: str
    action_spec: str
    camera_spec: str
    motion_spec: str
    composition_spec: str
    lighting_style: str
    beginning_state: str
    ending_state: str
    continuity_requirements: tuple[str, ...]
    negative_constraints: tuple[str, ...]
    provider_requirements: ProviderRequirements
    provider_capability_identity: str
    provider_capability_version: str
    QA_requirements: tuple[str, ...]
    allowed_repairs: tuple[RepairOwner, ...]
    expected_duration_seconds: float = Field(gt=0)
    requested_duration_seconds: Optional[int] = Field(default=None, gt=0)
    requested_resolution: str
    phone_UI_mode: PhoneUIExecutionMode
    consumer_language: str
    provider_instruction_language: str = "en"
    safety_person_controls: tuple[str, ...]
    execution_manifest_hash: str

    @model_validator(mode="after")
    def deterministic_manifest(self) -> "ShotExecutionManifest":
        material = self.model_dump(mode="json", exclude={"execution_manifest_hash"})
        if self.execution_manifest_hash != _stable_hash(material):
            raise ValueError("execution manifest hash mismatch")
        if self.generated_text_policy_hash != _stable_hash(self.generated_text_policy):
            raise ValueError("generated text policy hash mismatch")
        if self.provider_instruction_language != "en":
            raise ValueError("current Veo instruction language is English")
        return self


class ShotExecutionManifestCompiler:
    def compile(
        self, *, shot_contract: ShotContract, seed_shot: SeedShotPlan,
        reference_plan: ReferencePlan, continuity_plan: ContinuityPlan,
        audio_plan: AudioPlan, provider_requirements: ProviderRequirements,
        provider_capabilities: VideoProviderCapabilities, contract_hash: str,
        plan_hash: str, QA_requirements: FinalQARequirements,
        bindings: ShotInputBindings,
    ) -> ShotExecutionManifest:
        if not isinstance(audio_plan, AudioPlan) or not audio_plan.plan_hash:
            raise ProductionHarnessError(
                "PROVIDER_REQUEST_INVALID", "Shot manifest requires a hashed AudioPlan",
                repair_owner=RepairOwner.AUDIO)
        expected_audio_hash = _stable_hash(audio_plan.model_dump(
            mode="json", exclude={"plan_id", "plan_hash"}))
        if audio_plan.plan_hash != expected_audio_hash:
            raise ProductionHarnessError(
                "PROVIDER_REQUEST_INVALID", "AudioPlan hash does not match its material fields",
                repair_owner=RepairOwner.AUDIO)
        expected_continuity_hash = _stable_hash(continuity_plan.model_dump(
            mode="json", exclude={"plan_id", "plan_hash"}))
        if continuity_plan.plan_hash != expected_continuity_hash:
            raise ProductionHarnessError(
                "PROVIDER_REQUEST_INVALID",
                "ContinuityPlan hash does not match its material fields",
                repair_owner=RepairOwner.PLAN)
        if seed_shot.shot_id != shot_contract.shot_id \
                or reference_plan.shot_id != shot_contract.shot_id:
            raise ProductionHarnessError(
                "PROVIDER_REQUEST_INVALID", "shot/seed/reference identities disagree",
                repair_owner=RepairOwner.PLAN)
        if provider_requirements != shot_contract.provider_requirements:
            raise ProductionHarnessError(
                "PROVIDER_REQUEST_INVALID", "provider requirements differ from ShotContract",
                repair_owner=RepairOwner.SHOT)
        mode = _execution_mode(provider_requirements)
        seed_required = bool(seed_shot.seed_required or
                             provider_requirements.local_seed_input_required)
        first_required = mode in {ShotExecutionMode.IMAGE_TO_VIDEO,
                                  ShotExecutionMode.FIRST_LAST_FRAME}
        last_required = mode == ShotExecutionMode.FIRST_LAST_FRAME
        first_id = bindings.first_frame_asset_id or bindings.seed_image_asset_id
        inputs = [
            ManifestInput(input_type=ManifestInputType.SEED_IMAGE,
                          required=seed_required, asset_id=bindings.seed_image_asset_id),
            ManifestInput(input_type=ManifestInputType.FIRST_FRAME,
                          required=first_required, asset_id=first_id),
            ManifestInput(input_type=ManifestInputType.LAST_FRAME,
                          required=last_required, asset_id=bindings.last_frame_asset_id),
        ]
        inputs.extend(ManifestInput(
            input_type=ManifestInputType.PROVIDER_REFERENCE, required=True, asset_id=item)
            for item in bindings.provider_reference_asset_ids)
        if bindings.phone_UI_mode == PhoneUIExecutionMode.PROTECTED_UI_REFERENCE:
            inputs.append(ManifestInput(
                input_type=ManifestInputType.PROTECTED_UI_REFERENCE, required=True,
                asset_id=bindings.protected_UI_reference_asset_id))
        groups = [item for item in continuity_plan.groups
                  if shot_contract.shot_id in item.shot_ids]
        continuity = tuple(sorted({dimension.value for group in groups
                                   for dimension in group.required_dimensions}))
        mandatory_QA = tuple(sorted(item.dimension for item in QA_requirements.requirements
                                    if item.applicability.value == "MANDATORY"))
        repairs = [shot_contract.repair_owner, RepairOwner.VIDEO]
        if seed_required:
            repairs.append(RepairOwner.SEED)
        if bindings.phone_UI_mode == PhoneUIExecutionMode.DETERMINISTIC_SCREEN_COMPOSITE:
            repairs.append(RepairOwner.COMPOSITOR)
        allowed_repairs = tuple(dict.fromkeys(repairs))
        reference_usage = tuple(ReferenceUsageBinding(
            reference_id=item.reference_id, usage_modes=item.usage_modes)
            for item in reference_plan.references)
        negative_constraints = (
            "no marketing headline, CTA, caption, discount text, or disclaimer",
            "no exact app UI copy generated by Veo",
            "no product, package, logo, actor, wardrobe, or object mutation",
            "no unsafe action or unapproved real-person identity",
        )
        values = {
            "shot_id": shot_contract.shot_id,
            "shot_hash": shot_contract.shot_hash,
            "contract_hash": contract_hash,
            "plan_hash": plan_hash,
            "seed_shot_hash": _stable_hash(seed_shot),
            "reference_plan_hash": _stable_hash(reference_plan),
            "continuity_plan_hash": _stable_hash(continuity_plan),
            "audio_plan_hash": audio_plan.plan_hash,
            "execution_mode": mode,
            "required_inputs": tuple(inputs),
            "seed_requirement": (RequirementState.REQUIRED if seed_required
                                 else RequirementState.NOT_APPLICABLE),
            "first_frame_requirement": (RequirementState.REQUIRED if first_required
                                        else RequirementState.NOT_APPLICABLE),
            "last_frame_requirement": (RequirementState.REQUIRED if last_required
                                       else RequirementState.NOT_APPLICABLE),
            "reference_usage": reference_usage,
            "protected_product_features": seed_shot.protected_product_features,
            "protected_brand_features": shot_contract.protected_brand_requirements,
            "generated_text_policy": shot_contract.generated_text_policy,
            "generated_text_policy_hash": _stable_hash(shot_contract.generated_text_policy),
            "action_spec": shot_contract.action_spec,
            "camera_spec": shot_contract.camera_spec,
            "motion_spec": shot_contract.motion_spec,
            "composition_spec": shot_contract.composition_spec,
            "lighting_style": shot_contract.lighting_style,
            "beginning_state": ("match supplied first frame exactly" if first_id
                                else "begin from the ShotContract subject state"),
            "ending_state": ("match supplied last frame exactly"
                             if bindings.last_frame_asset_id
                             else "end on the completed ShotContract action state"),
            "continuity_requirements": continuity,
            "negative_constraints": negative_constraints,
            "provider_requirements": provider_requirements,
            "provider_capability_identity": (
                f"{provider_capabilities.provider_name}:"
                f"{provider_capabilities.model_id}:{provider_capabilities.surface.value}"),
            "provider_capability_version": provider_capabilities.capability_source_version,
            "QA_requirements": mandatory_QA,
            "allowed_repairs": allowed_repairs,
            "expected_duration_seconds": shot_contract.duration_target,
            "requested_duration_seconds": bindings.requested_duration_seconds,
            "requested_resolution": bindings.requested_resolution,
            "phone_UI_mode": bindings.phone_UI_mode,
            "consumer_language": bindings.consumer_language,
            "provider_instruction_language": bindings.provider_instruction_language,
            "safety_person_controls": bindings.safety_person_controls,
        }
        values["execution_manifest_hash"] = _stable_hash(values)
        return ShotExecutionManifest(**values)


class PromptSectionName(str, Enum):
    SUBJECT = "SUBJECT"
    ENVIRONMENT = "ENVIRONMENT"
    ACTION = "ACTION"
    CAMERA = "CAMERA"
    MOTION = "MOTION"
    COMPOSITION = "COMPOSITION"
    LIGHTING_VISUAL_STYLE = "LIGHTING / VISUAL STYLE"
    BEGINNING_STATE = "BEGINNING STATE"
    END_STATE = "END STATE"
    CONTINUITY = "CONTINUITY"
    PRODUCT_BRAND_PRESERVATION = "PRODUCT / BRAND PRESERVATION"
    PROHIBITED_VISUAL_BEHAVIOR = "PROHIBITED VISUAL BEHAVIOR"


class PromptSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: PromptSectionName
    instruction: str = Field(min_length=1)


class CanonicalVideoPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_instruction_language: str = "en"
    consumer_language: str
    sections: tuple[PromptSection, ...]
    prompt_text: str
    prompt_hash: str
    hidden_chain_of_thought_requested: bool = False
    marketing_text_payload: tuple[str, ...] = ()
    exact_UI_copy_payload: tuple[str, ...] = ()

    @model_validator(mode="after")
    def structured_prompt_only(self) -> "CanonicalVideoPrompt":
        expected = tuple(PromptSectionName)
        if tuple(item.name for item in self.sections) != expected:
            raise ValueError("canonical prompt sections are incomplete or reordered")
        rendered = "\n\n".join(f"{item.name.value}:\n{item.instruction}"
                                 for item in self.sections)
        if self.prompt_text != rendered or self.prompt_hash != _stable_hash(rendered):
            raise ValueError("prompt rendering/hash mismatch")
        if self.hidden_chain_of_thought_requested:
            raise ValueError("provider prompt cannot request hidden reasoning")
        if self.marketing_text_payload or self.exact_UI_copy_payload:
            raise ValueError("generative video cannot own marketing or exact UI copy")
        return self


_MARKETING_RENDER_DIRECTIVE = re.compile(
    r"\b(marketing headline|headline|call[ -]?to[ -]?action|cta|caption|"
    r"discount text|disclaimer|exact app ui|app ui copy)\b",
    re.IGNORECASE,
)


class CanonicalVideoPromptCompiler:
    def compile(self, shot_contract: ShotContract,
                execution_manifest: ShotExecutionManifest) -> CanonicalVideoPrompt:
        if shot_contract.shot_id != execution_manifest.shot_id \
                or shot_contract.shot_hash != execution_manifest.shot_hash:
            raise ProductionHarnessError(
                "PROVIDER_REQUEST_INVALID", "prompt inputs refer to different shots",
                repair_owner=RepairOwner.SHOT)
        visual_fields = (
            shot_contract.subject_spec, shot_contract.location_spec, shot_contract.action_spec,
            shot_contract.camera_spec, shot_contract.motion_spec,
            shot_contract.composition_spec, shot_contract.lighting_style,
        )
        if any(_MARKETING_RENDER_DIRECTIVE.search(item or "") for item in visual_fields):
            raise ProductionHarnessError(
                "PROVIDER_REQUEST_INVALID",
                "ShotContract asks generative video to render compositor-owned marketing text",
                repair_owner=RepairOwner.SHOT)
        subject = shot_contract.subject_spec
        if execution_manifest.phone_UI_mode == PhoneUIExecutionMode.ABSTRACT_PHONE_CONTEXT:
            subject += "; phone UI remains abstract and contains no legible copy"
        elif execution_manifest.phone_UI_mode == PhoneUIExecutionMode.DETERMINISTIC_SCREEN_COMPOSITE:
            subject += "; use a clean trackable blank screen; UI is composited later"
        elif execution_manifest.phone_UI_mode == PhoneUIExecutionMode.PROTECTED_UI_REFERENCE:
            subject += "; preserve the supplied protected UI appearance; invent no UI copy"
        preservation = tuple(dict.fromkeys((
            *execution_manifest.protected_product_features,
            *execution_manifest.protected_brand_features,
        )))
        sections = (
            PromptSection(name=PromptSectionName.SUBJECT, instruction=subject),
            PromptSection(name=PromptSectionName.ENVIRONMENT,
                          instruction=shot_contract.location_spec),
            PromptSection(name=PromptSectionName.ACTION,
                          instruction=execution_manifest.action_spec),
            PromptSection(name=PromptSectionName.CAMERA,
                          instruction=execution_manifest.camera_spec),
            PromptSection(name=PromptSectionName.MOTION,
                          instruction=execution_manifest.motion_spec),
            PromptSection(name=PromptSectionName.COMPOSITION,
                          instruction=execution_manifest.composition_spec),
            PromptSection(name=PromptSectionName.LIGHTING_VISUAL_STYLE,
                          instruction=execution_manifest.lighting_style),
            PromptSection(name=PromptSectionName.BEGINNING_STATE,
                          instruction=execution_manifest.beginning_state),
            PromptSection(name=PromptSectionName.END_STATE,
                          instruction=execution_manifest.ending_state),
            PromptSection(name=PromptSectionName.CONTINUITY,
                          instruction=(", ".join(execution_manifest.continuity_requirements)
                                       or "No cross-shot identity promise beyond this contract.")),
            PromptSection(name=PromptSectionName.PRODUCT_BRAND_PRESERVATION,
                          instruction=("Preserve exactly: " + "; ".join(preservation)
                                       if preservation else
                                       "No unverified product or brand feature may be invented.")),
            PromptSection(name=PromptSectionName.PROHIBITED_VISUAL_BEHAVIOR,
                          instruction=(
                              "Do not render marketing headlines, CTA, captions, discount text, "
                              "disclaimers, exact app UI copy, new brand text, mutated logos, "
                              "duplicated objects, disappearing objects, unsafe actions, or "
                              "unapproved people.")),
        )
        rendered = "\n\n".join(f"{item.name.value}:\n{item.instruction}"
                                 for item in sections)
        return CanonicalVideoPrompt(
            consumer_language=execution_manifest.consumer_language,
            sections=sections, prompt_text=rendered, prompt_hash=_stable_hash(rendered))


class VideoCompilationState(str, Enum):
    COMPILED = "COMPILED"


class CompiledVideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: VideoCompilationState = VideoCompilationState.COMPILED
    provider_name: str
    surface: ProviderSurface
    model_id: str
    shot_id: str
    shot_hash: str
    execution_manifest_hash: str
    execution_mode: ShotExecutionMode
    prompt: CanonicalVideoPrompt
    aspect_ratio: str
    duration_seconds: int
    resolution: str
    seed_image_asset_id: str = ""
    first_frame_asset_id: str = ""
    last_frame_asset_id: str = ""
    provider_reference_asset_ids: tuple[str, ...] = ()
    protected_UI_reference_asset_id: str = ""
    protected_product_features: tuple[str, ...]
    protected_brand_features: tuple[str, ...]
    negative_constraints: tuple[str, ...]
    continuity_requirements: tuple[str, ...]
    safety_person_controls: tuple[str, ...]
    generated_text_policy_hash: str
    phone_UI_mode: PhoneUIExecutionMode
    seed_QA_state: str
    required_resolution: str
    marketing_text_payload: tuple[str, ...] = ()
    exact_UI_copy_payload: tuple[str, ...] = ()
    request_hash: str

    @model_validator(mode="after")
    def deterministic_request(self) -> "CompiledVideoRequest":
        material = self.model_dump(mode="json", exclude={"request_hash"})
        if self.request_hash != _stable_hash(material):
            raise ValueError("video request hash mismatch")
        if self.marketing_text_payload or self.exact_UI_copy_payload:
            raise ValueError("video request contains compositor-owned text")
        return self


class VideoRequestCompiler(Protocol):
    def compile(
        self, shot_contract: ShotContract, execution_manifest: ShotExecutionManifest,
        provider_capabilities: VideoProviderCapabilities, *, bindings: ShotInputBindings,
        seed_verdict: Optional["SeedQAVerdict"] = None,
    ) -> CompiledVideoRequest: ...


def _capability_error(message: str, owner: RepairOwner = RepairOwner.SHOT) -> None:
    raise ProductionHarnessError(
        "UNSUPPORTED_REQUIRED_CAPABILITY", message, repair_owner=owner)


def _request_error(message: str, owner: RepairOwner = RepairOwner.SHOT) -> None:
    raise ProductionHarnessError("PROVIDER_REQUEST_INVALID", message, repair_owner=owner)


def validate_compiled_video_request(
    request: CompiledVideoRequest, manifest: ShotExecutionManifest,
    capabilities: VideoProviderCapabilities,
) -> None:
    if (request.provider_name, request.model_id, request.surface) != capabilities.identity:
        _request_error("compiled request and provider capability identities disagree")
    if request.shot_id != manifest.shot_id or request.shot_hash != manifest.shot_hash \
            or request.execution_manifest_hash != manifest.execution_manifest_hash:
        _request_error("compiled request is detached from its immutable shot manifest")
    support = {
        ShotExecutionMode.TEXT_TO_VIDEO: capabilities.text_to_video,
        ShotExecutionMode.IMAGE_TO_VIDEO: capabilities.image_to_video,
        ShotExecutionMode.FIRST_LAST_FRAME: capabilities.first_last_frame,
        ShotExecutionMode.CONVERSATIONAL_EDIT: capabilities.conversational_editing,
        ShotExecutionMode.MULTIMODAL_VIDEO: capabilities.multimodal_video,
        ShotExecutionMode.VIDEO_EXTENSION: capabilities.video_extension,
    }
    if not support[request.execution_mode]:
        _capability_error(f"{request.execution_mode.value} is unsupported on this model surface")
    if request.provider_reference_asset_ids and not capabilities.reference_images:
        _capability_error("reference-image-to-video is unsupported on configured Vertex Veo")
    if request.aspect_ratio not in capabilities.supported_aspect_ratios:
        _request_error("unsupported aspect ratio")
    if request.duration_seconds not in capabilities.supported_durations:
        _request_error("Veo duration must be exactly 4, 6, or 8 seconds")
    if request.resolution not in capabilities.supported_resolutions:
        _request_error("unsupported Veo resolution")
    if request.resolution != request.required_resolution:
        _request_error("required resolution was silently changed")
    if request.execution_mode == ShotExecutionMode.TEXT_TO_VIDEO:
        if request.first_frame_asset_id or request.last_frame_asset_id:
            _request_error("text-to-video request unexpectedly carries frame inputs")
    elif request.execution_mode == ShotExecutionMode.IMAGE_TO_VIDEO:
        if not request.first_frame_asset_id:
            _request_error("image-to-video requires a seed/first frame", RepairOwner.SEED)
        if request.last_frame_asset_id:
            _request_error("image-to-video cannot silently become first-last-frame")
    elif request.execution_mode == ShotExecutionMode.FIRST_LAST_FRAME:
        if not request.first_frame_asset_id:
            _request_error("first-last-frame requires a first frame", RepairOwner.SEED)
        if not request.last_frame_asset_id:
            _request_error("first-last-frame requires a last frame", RepairOwner.SEED)
    if manifest.seed_requirement == RequirementState.REQUIRED \
            and request.seed_QA_state != SeedTerminalState.SEED_READY.value:
        raise ProductionHarnessError(
            "SEED_BLOCKED", "a required seed has not passed every applicable QA dimension",
            repair_owner=RepairOwner.SEED)
    if request.protected_product_features != manifest.protected_product_features:
        _request_error("product identity requirement was dropped", RepairOwner.VIDEO)
    if request.protected_brand_features != manifest.protected_brand_features:
        _request_error("brand identity requirement was dropped", RepairOwner.VIDEO)
    if request.continuity_requirements != manifest.continuity_requirements:
        _request_error("continuity requirement was dropped", RepairOwner.VIDEO)
    if request.negative_constraints != manifest.negative_constraints:
        _request_error("negative constraint was dropped", RepairOwner.VIDEO)
    if request.generated_text_policy_hash != manifest.generated_text_policy_hash:
        _request_error("text prohibition policy was dropped", RepairOwner.VIDEO)
    if request.safety_person_controls != manifest.safety_person_controls:
        _request_error("safety/person controls were dropped", RepairOwner.VIDEO)
    if request.marketing_text_payload or request.exact_UI_copy_payload:
        _request_error("generative request contains compositor-owned marketing/UI text")
    if (request.phone_UI_mode == PhoneUIExecutionMode.PROTECTED_UI_REFERENCE
            and not request.protected_UI_reference_asset_id):
        _request_error("protected exact UI has no protected input", RepairOwner.SEED)
    expected_inputs = {item.input_type: item.asset_id for item in manifest.required_inputs}
    if request.seed_image_asset_id != expected_inputs.get(ManifestInputType.SEED_IMAGE, ""):
        _request_error("seed binding changed after manifest compilation", RepairOwner.SEED)
    if request.first_frame_asset_id != expected_inputs.get(ManifestInputType.FIRST_FRAME, ""):
        _request_error("first-frame binding changed after manifest compilation", RepairOwner.SEED)
    if request.last_frame_asset_id != expected_inputs.get(ManifestInputType.LAST_FRAME, ""):
        _request_error("last-frame binding changed after manifest compilation", RepairOwner.SEED)
    expected_references = tuple(item.asset_id for item in manifest.required_inputs
                                if item.input_type == ManifestInputType.PROVIDER_REFERENCE)
    if request.provider_reference_asset_ids != expected_references:
        _request_error("provider reference binding changed after manifest compilation",
                       RepairOwner.SEED)


class VertexVeo31RequestCompiler:
    """Pure translator for the conservative configured Vertex Veo 3.1 profiles."""

    _SUPPORTED_IDENTITIES = {VERTEX_VEO_31.identity, VERTEX_VEO_31_FAST.identity}

    def __init__(self, prompt_compiler: Optional[CanonicalVideoPromptCompiler] = None) -> None:
        self._prompt_compiler = prompt_compiler or CanonicalVideoPromptCompiler()

    def compile(
        self, shot_contract: ShotContract, execution_manifest: ShotExecutionManifest,
        provider_capabilities: VideoProviderCapabilities, *, bindings: ShotInputBindings,
        seed_verdict: Optional["SeedQAVerdict"] = None,
    ) -> CompiledVideoRequest:
        if provider_capabilities.identity not in self._SUPPORTED_IDENTITIES \
                or provider_capabilities.surface != ProviderSurface.VERTEX_AI:
            _capability_error("compiler accepts only reviewed Vertex Veo 3.1 model profiles")
        if execution_manifest.requested_duration_seconds is None:
            _request_error("exact provider duration is unresolved in the shot plan")
        prompt = self._prompt_compiler.compile(shot_contract, execution_manifest)
        first_id = bindings.first_frame_asset_id or bindings.seed_image_asset_id
        seed_state = (seed_verdict.terminal_state.value if seed_verdict is not None
                      else SeedTerminalState.SEED_BLOCKED.value)
        values = {
            "provider_name": provider_capabilities.provider_name,
            "surface": provider_capabilities.surface,
            "model_id": provider_capabilities.model_id,
            "shot_id": shot_contract.shot_id,
            "shot_hash": shot_contract.shot_hash,
            "execution_manifest_hash": execution_manifest.execution_manifest_hash,
            "execution_mode": execution_manifest.execution_mode,
            "prompt": prompt,
            "aspect_ratio": execution_manifest.provider_requirements.aspect_ratio,
            "duration_seconds": execution_manifest.requested_duration_seconds,
            "resolution": execution_manifest.requested_resolution,
            "seed_image_asset_id": bindings.seed_image_asset_id,
            "first_frame_asset_id": first_id,
            "last_frame_asset_id": bindings.last_frame_asset_id,
            "provider_reference_asset_ids": bindings.provider_reference_asset_ids,
            "protected_UI_reference_asset_id": bindings.protected_UI_reference_asset_id,
            "protected_product_features": execution_manifest.protected_product_features,
            "protected_brand_features": execution_manifest.protected_brand_features,
            "negative_constraints": execution_manifest.negative_constraints,
            "continuity_requirements": execution_manifest.continuity_requirements,
            "safety_person_controls": execution_manifest.safety_person_controls,
            "generated_text_policy_hash": execution_manifest.generated_text_policy_hash,
            "phone_UI_mode": execution_manifest.phone_UI_mode,
            "seed_QA_state": seed_state,
            "required_resolution": execution_manifest.requested_resolution,
            "marketing_text_payload": (),
            "exact_UI_copy_payload": (),
        }
        values["request_hash"] = _stable_hash({"state": VideoCompilationState.COMPILED, **values})
        request = CompiledVideoRequest(**values)
        validate_compiled_video_request(request, execution_manifest, provider_capabilities)
        return request


class ImageQualityTier(str, Enum):
    STANDARD_SEED = "STANDARD_SEED"
    HIGH_COMPLEXITY_HERO = "HIGH_COMPLEXITY_HERO"


class ImageEscalationReason(str, Enum):
    COMPLEX_PRODUCT_ARRANGEMENT = "COMPLEX_PRODUCT_ARRANGEMENT"
    DIFFICULT_COMPOSITION = "DIFFICULT_COMPOSITION"
    REPEATED_SEED_FIDELITY_FAILURE = "REPEATED_SEED_FIDELITY_FAILURE"
    HIGH_VALUE_HERO_FRAME = "HIGH_VALUE_HERO_FRAME"


class ImageQualityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quality_tier: ImageQualityTier
    profile: ImageProviderCapabilities
    escalation_reasons: tuple[ImageEscalationReason, ...] = ()
    pricing_proven: bool = False
    execution_authorized: bool = False
    provider_calls: int = 0

    @model_validator(mode="after")
    def current_offline_profile_only(self) -> "ImageQualityDecision":
        if self.provider_calls:
            raise ValueError("offline image router cannot call a provider")
        if self.profile.authorization != ImageProviderAuthorization.SUPPORTED_METADATA_ONLY:
            raise ValueError("deprecated image model cannot be routed")
        if self.profile.lifecycle_status != "GA":
            raise ValueError("canonical seed routing requires reviewed GA metadata")
        if self.quality_tier == ImageQualityTier.HIGH_COMPLEXITY_HERO:
            if not self.escalation_reasons:
                raise ValueError("hero escalation requires a production reason")
            if not self.pricing_proven:
                raise ValueError("hero escalation requires exact proven Pro pricing")
            if not self.execution_authorized:
                raise ValueError("hero escalation requires explicit execution authorization")
        return self


class ImageQualityRouter:
    def __init__(self, profiles: Sequence[ImageProviderCapabilities] =
                 IMAGE_PROVIDER_CAPABILITIES) -> None:
        self._profiles = tuple(profiles)

    def route(
        self, tier: ImageQualityTier,
        *, escalation_reasons: Sequence[ImageEscalationReason] = (),
        pricing_proven: bool = False,
        execution_authorized: bool = False,
    ) -> ImageQualityDecision:
        model_id = ("gemini-3.1-flash-image" if tier == ImageQualityTier.STANDARD_SEED
                    else "gemini-3-pro-image")
        profile = next((item for item in self._profiles if item.model_id == model_id), None)
        if profile is None:
            raise ProductionHarnessError(
                "UNSUPPORTED_REQUIRED_CAPABILITY", "required current image profile is absent",
                repair_owner=RepairOwner.SEED)
        return ImageQualityDecision(
            quality_tier=tier, profile=profile,
            escalation_reasons=tuple(escalation_reasons),
            pricing_proven=pricing_proven,
            execution_authorized=execution_authorized)


class CompiledSeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intended_image_provider: str
    provider_surface: str
    model_id: str
    model_lifecycle_status: str
    shot_id: str
    aspect_ratio: str
    output_resolution: str
    subject: str
    environment: str
    composition: str
    product_constraints: tuple[str, ...]
    actor_constraints: tuple[str, ...]
    continuity_constraints: tuple[str, ...]
    attached_conditioning_assets: tuple[str, ...]
    QA_reference_assets: tuple[str, ...]
    protected_UI_reference_asset_id: str = ""
    prohibited_generated_text: tuple[str, ...]
    seed_success_criteria: tuple[str, ...]
    quality_tier: ImageQualityTier
    escalation_reasons: tuple[ImageEscalationReason, ...]
    provider_calls: int = 0
    request_hash: str

    @model_validator(mode="after")
    def deterministic_offline_seed_request(self) -> "CompiledSeedRequest":
        material = self.model_dump(mode="json", exclude={"request_hash"})
        if self.request_hash != _stable_hash(material):
            raise ValueError("seed request hash mismatch")
        if self.provider_calls:
            raise ValueError("seed request compiler cannot call a provider")
        if "preview" in self.model_id or self.model_id.casefold().startswith("imagen"):
            raise ValueError("deprecated image endpoint is not canonical")
        return self


class SeedRequestCompiler:
    def __init__(self, router: Optional[ImageQualityRouter] = None) -> None:
        self._router = router or ImageQualityRouter()

    def compile(
        self, *, shot_contract: ShotContract, seed_shot: SeedShotPlan,
        reference_plan: ReferencePlan, execution_manifest: ShotExecutionManifest,
        quality_tier: ImageQualityTier = ImageQualityTier.STANDARD_SEED,
        escalation_reasons: Sequence[ImageEscalationReason] = (),
        pricing_proven: bool = False,
        execution_authorized: bool = False,
    ) -> CompiledSeedRequest:
        if shot_contract.shot_id != seed_shot.shot_id \
                or reference_plan.shot_id != seed_shot.shot_id:
            _request_error("seed compiler inputs refer to different shots", RepairOwner.SEED)
        decision = self._router.route(
            quality_tier, escalation_reasons=escalation_reasons,
            pricing_proven=pricing_proven,
            execution_authorized=execution_authorized)
        profile = decision.profile
        aspect = execution_manifest.provider_requirements.aspect_ratio
        if aspect not in profile.supported_aspect_ratios:
            _capability_error("selected image model lacks requested seed aspect ratio",
                              RepairOwner.SEED)
        output_resolution = profile.supported_output_resolutions[0]
        values = {
            "intended_image_provider": profile.provider_name,
            "provider_surface": profile.surface_name,
            "model_id": profile.model_id,
            "model_lifecycle_status": profile.lifecycle_status,
            "shot_id": shot_contract.shot_id,
            "aspect_ratio": aspect,
            "output_resolution": output_resolution,
            "subject": shot_contract.subject_spec,
            "environment": shot_contract.location_spec,
            "composition": shot_contract.composition_spec,
            "product_constraints": seed_shot.protected_product_features,
            "actor_constraints": tuple(filter(None, (seed_shot.expected_actor_identity,))),
            "continuity_constraints": execution_manifest.continuity_requirements,
            "attached_conditioning_assets": seed_shot.conditioning_references,
            "QA_reference_assets": seed_shot.QA_references,
            "protected_UI_reference_asset_id": next((
                item.asset_id for item in execution_manifest.required_inputs
                if item.input_type == ManifestInputType.PROTECTED_UI_REFERENCE), ""),
            "prohibited_generated_text": tuple(dict.fromkeys((
                *seed_shot.prohibited_text,
                "marketing headline", "CTA", "caption", "discount text",
                "disclaimer", "exact app UI copy"))),
            "seed_success_criteria": seed_shot.success_criteria,
            "quality_tier": quality_tier,
            "escalation_reasons": tuple(escalation_reasons),
            "provider_calls": 0,
        }
        values["request_hash"] = _stable_hash(values)
        return CompiledSeedRequest(**values)


class QADimensionState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_ASSESSED = "NOT_ASSESSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SeedTerminalState(str, Enum):
    SEED_READY = "SEED_READY"
    SEED_BLOCKED = "SEED_BLOCKED"


class SeedQAVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed_request_hash: str
    strategic_relevance: QADimensionState
    composition: QADimensionState
    product_fidelity: QADimensionState
    brand_fidelity: QADimensionState
    subject_fidelity: QADimensionState
    human_anatomy: QADimensionState
    action_precondition: QADimensionState
    protected_text_fidelity: QADimensionState
    generated_text_contamination: QADimensionState
    reference_alignment: QADimensionState
    continuity_readiness: QADimensionState
    visual_quality: QADimensionState
    terminal_state: SeedTerminalState
    defects: tuple[str, ...] = ()
    provider_calls: int = 0

    @model_validator(mode="after")
    def failed_or_unassessed_seed_is_blocked(self) -> "SeedQAVerdict":
        dimensions = (
            self.strategic_relevance, self.composition, self.product_fidelity,
            self.brand_fidelity, self.subject_fidelity, self.human_anatomy,
            self.action_precondition, self.protected_text_fidelity,
            self.generated_text_contamination, self.reference_alignment,
            self.continuity_readiness, self.visual_quality,
        )
        ready = all(item in {QADimensionState.PASS, QADimensionState.NOT_APPLICABLE}
                    for item in dimensions)
        expected = SeedTerminalState.SEED_READY if ready else SeedTerminalState.SEED_BLOCKED
        if self.terminal_state != expected:
            raise ValueError("seed terminal state contradicts dimension verdicts")
        if self.provider_calls:
            raise ValueError("Gate 2D.4A does not perform seed QA calls")
        return self


class FrameSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    normalized_time: float = Field(ge=0, le=1)


class VideoQASamplingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    samples: tuple[FrameSample, ...]
    dense_sampling: bool
    density_reasons: tuple[str, ...]
    VLM_calls: int = 0
    plan_hash: str

    @model_validator(mode="after")
    def deterministic_sampling(self) -> "VideoQASamplingPlan":
        if self.VLM_calls:
            raise ValueError("offline sampling plan cannot call a VLM")
        if self.plan_hash != _stable_hash(self.model_dump(
                mode="json", exclude={"plan_hash"})):
            raise ValueError("video QA sampling hash mismatch")
        required = {"opening", "early", "midpoint", "late", "ending"}
        if not required.issubset({item.label for item in self.samples}):
            raise ValueError("video QA sampling misses a required temporal anchor")
        return self


def compile_video_QA_sampling_plan(
    manifest: ShotExecutionManifest, *, product_identity_high_risk: bool = False,
    actor_identity_high_risk: bool = False, physical_action_complex: bool = False,
    mutation_suspected: bool = False,
) -> VideoQASamplingPlan:
    points = {
        "opening": 0.0, "early": 0.1, "midpoint": 0.5, "late": 0.9, "ending": 1.0,
    }
    reasons = []
    for condition, reason in (
        (product_identity_high_risk, "product identity high risk"),
        (actor_identity_high_risk, "actor identity high risk"),
        (physical_action_complex, "physical action complex"),
        (mutation_suspected, "possible temporal mutation"),
    ):
        if condition:
            reasons.append(reason)
    if reasons:
        points.update({"quarter": .25, "three_eighths": .375,
                       "five_eighths": .625, "three_quarters": .75})
    samples = tuple(FrameSample(label=label, normalized_time=value)
                    for label, value in sorted(points.items(), key=lambda item: item[1]))
    values = {
        "shot_id": manifest.shot_id, "samples": samples,
        "dense_sampling": bool(reasons), "density_reasons": tuple(reasons), "VLM_calls": 0,
    }
    values["plan_hash"] = _stable_hash(values)
    return VideoQASamplingPlan(**values)


class TemporalMutationDefect(str, Enum):
    PRODUCT_SHAPE_CHANGE = "PRODUCT_SHAPE_CHANGE"
    PACKAGING_CHANGE = "PACKAGING_CHANGE"
    LOGO_MUTATION = "LOGO_MUTATION"
    OBJECT_DUPLICATION = "OBJECT_DUPLICATION"
    OBJECT_DISAPPEARANCE = "OBJECT_DISAPPEARANCE"
    ACTOR_IDENTITY_DRIFT = "ACTOR_IDENTITY_DRIFT"
    WARDROBE_DRIFT = "WARDROBE_DRIFT"
    HAND_OBJECT_ATTACHMENT_ERROR = "HAND_OBJECT_ATTACHMENT_ERROR"


class ShotVideoTerminalState(str, Enum):
    VIDEO_PASSED = "VIDEO_PASSED"
    VIDEO_BLOCKED = "VIDEO_BLOCKED"


class ShotVideoVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_hash: str
    product_identity: QADimensionState
    brand_identity: QADimensionState
    actor_identity: QADimensionState
    subject_consistency: QADimensionState
    temporal_consistency: QADimensionState
    motion_quality: QADimensionState
    physical_plausibility: QADimensionState
    action_binding: QADimensionState
    human_fidelity: QADimensionState
    product_mutation: QADimensionState
    protected_text_fidelity: QADimensionState
    generated_text_contamination: QADimensionState
    shot_contract_alignment: QADimensionState
    temporal_mutation_defects: tuple[TemporalMutationDefect, ...] = ()
    terminal_state: ShotVideoTerminalState
    VLM_calls: int = 0

    @model_validator(mode="after")
    def terminal_video_requires_every_dimension(self) -> "ShotVideoVerdict":
        dimensions = tuple(value for key, value in self.__dict__.items()
                           if key not in {"request_hash", "temporal_mutation_defects",
                                          "terminal_state", "VLM_calls"})
        passed = all(item in {QADimensionState.PASS, QADimensionState.NOT_APPLICABLE}
                     for item in dimensions) and not self.temporal_mutation_defects
        expected = (ShotVideoTerminalState.VIDEO_PASSED if passed
                    else ShotVideoTerminalState.VIDEO_BLOCKED)
        if self.terminal_state != expected:
            raise ValueError("video terminal state contradicts QA dimensions")
        if self.VLM_calls:
            raise ValueError("Gate 2D.4A does not perform video QA calls")
        return self


class VideoFailureType(str, Enum):
    PRODUCT_IDENTITY_DRIFT = "PRODUCT_IDENTITY_DRIFT"
    PRODUCT_MUTATION = "PRODUCT_MUTATION"
    ACTION_BINDING_ERROR = "ACTION_BINDING_ERROR"
    PHYSICS_ANOMALY = "PHYSICS_ANOMALY"
    SUBJECT_IDENTITY_DRIFT = "SUBJECT_IDENTITY_DRIFT"
    GENERATED_TEXT_CONTAMINATION = "GENERATED_TEXT_CONTAMINATION"
    BRAND_IDENTITY_DRIFT = "BRAND_IDENTITY_DRIFT"


VIDEO_FAILURE_REPAIR_OWNERS: Mapping[VideoFailureType, tuple[RepairOwner, ...]] = {
    VideoFailureType.PRODUCT_IDENTITY_DRIFT: (RepairOwner.VIDEO, RepairOwner.SEED),
    VideoFailureType.PRODUCT_MUTATION: (RepairOwner.VIDEO,),
    VideoFailureType.ACTION_BINDING_ERROR: (RepairOwner.SHOT, RepairOwner.VIDEO),
    VideoFailureType.PHYSICS_ANOMALY: (RepairOwner.SHOT, RepairOwner.VIDEO),
    VideoFailureType.SUBJECT_IDENTITY_DRIFT: (RepairOwner.SEED, RepairOwner.VIDEO),
    VideoFailureType.GENERATED_TEXT_CONTAMINATION: (RepairOwner.SEED, RepairOwner.VIDEO),
    VideoFailureType.BRAND_IDENTITY_DRIFT: (RepairOwner.SEED, RepairOwner.VIDEO),
}


class VideoRepairRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    failure: VideoFailureType
    allowed_owners: tuple[RepairOwner, ...]
    strategy_regeneration_allowed: bool = False


def route_video_failure(failure: VideoFailureType) -> VideoRepairRoute:
    return VideoRepairRoute(failure=failure,
                            allowed_owners=VIDEO_FAILURE_REPAIR_OWNERS[failure])


class AudioRequestCategory(str, Enum):
    MARKETING_VOICEOVER = "MARKETING_VOICEOVER"
    NONCRITICAL_DIALOGUE = "NONCRITICAL_DIALOGUE"
    SFX = "SFX"
    AMBIENCE = "AMBIENCE"
    MUSIC = "MUSIC"


class CompiledAudioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: AudioRequestCategory
    source_artifact_type: str
    source_artifact_hash: str
    exact_lines: tuple[str, ...] = ()
    language: str = "und"
    locale: str = ""
    voice_direction: str = ""
    pronunciation_notes: tuple[str, ...] = ()
    timing_budget_seconds: Decimal = Decimal("0")
    claim_authorizations: tuple[str, ...] = ()
    semantic_rewrite_allowed: bool = False
    provider_selected: bool = False
    provider_calls: int = 0
    request_hash: str

    @model_validator(mode="after")
    def provider_neutral_and_script_bound(self) -> "CompiledAudioRequest":
        material = self.model_dump(mode="json", exclude={"request_hash"})
        if self.request_hash != _stable_hash(material):
            raise ValueError("audio request hash mismatch")
        if self.provider_calls or self.provider_selected:
            raise ValueError("Gate 2D.4A audio request is provider-neutral")
        if self.category == AudioRequestCategory.MARKETING_VOICEOVER:
            if self.source_artifact_type != "VoiceoverScript" or not self.exact_lines:
                raise ValueError("marketing voiceover must bind to VoiceoverScript")
            if self.semantic_rewrite_allowed:
                raise ValueError("TTS provider cannot rewrite marketing semantics")
        return self


def _audio_request(**values: Any) -> CompiledAudioRequest:
    draft = CompiledAudioRequest.model_construct(request_hash="", **values)
    values["request_hash"] = _stable_hash(
        draft.model_dump(mode="json", exclude={"request_hash"}))
    return CompiledAudioRequest(**values)


def compile_marketing_voiceover_request(
    script: VoiceoverScript, *, voice_direction: str,
) -> CompiledAudioRequest:
    authorizations = tuple(
        f"{line.line_id}:" + ",".join(
            claim for intent in line.claim_intents
            for claim in intent.supporting_allowed_claim_ids)
        for line in script.ordered_lines)
    return _audio_request(
        category=AudioRequestCategory.MARKETING_VOICEOVER,
        source_artifact_type="VoiceoverScript",
        source_artifact_hash=script.script_hash,
        exact_lines=tuple(item.text for item in script.ordered_lines),
        language=script.language, locale=script.locale,
        voice_direction=voice_direction,
        pronunciation_notes=script.pronunciation_notes,
        timing_budget_seconds=Decimal(str(script.time_budget)),
        claim_authorizations=authorizations,
        semantic_rewrite_allowed=False, provider_selected=False, provider_calls=0)


def compile_audio_requests(audio_plan: AudioPlan) -> tuple[CompiledAudioRequest, ...]:
    results: list[CompiledAudioRequest] = []
    if audio_plan.marketing_voiceover.planned:
        if audio_plan.voiceover_script is None:
            _request_error("planned marketing voiceover lacks VoiceoverScript", RepairOwner.AUDIO)
        results.append(compile_marketing_voiceover_request(
            audio_plan.voiceover_script,
            voice_direction=audio_plan.marketing_voiceover.direction))
    layers = (
        (audio_plan.noncritical_dialogue, AudioRequestCategory.NONCRITICAL_DIALOGUE),
        (audio_plan.SFX, AudioRequestCategory.SFX),
        (audio_plan.environment_audio, AudioRequestCategory.AMBIENCE),
        (audio_plan.music, AudioRequestCategory.MUSIC),
    )
    for layer, category in layers:
        if not layer.planned:
            continue
        source_hash = _stable_hash(layer.model_dump(mode="json"))
        results.append(_audio_request(
            category=category, source_artifact_type="AudioLayerPlan",
            source_artifact_hash=source_hash, voice_direction=layer.direction,
            semantic_rewrite_allowed=False, provider_selected=False, provider_calls=0))
    return tuple(results)


class TTSLifecycleStatus(str, Enum):
    PREVIEW = "PREVIEW"


class TTSAuthorizationStatus(str, Enum):
    NOT_YET_CANONICALLY_AUTHORIZED = "NOT_YET_CANONICALLY_AUTHORIZED"


class TTSProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_name: str
    model_id: str
    surfaces: tuple[str, ...]
    lifecycle_status: TTSLifecycleStatus
    authorization_status: TTSAuthorizationStatus
    source: str
    source_version: str


GEMINI_31_FLASH_TTS_PREVIEW = TTSProviderProfile(
    provider_name="google", model_id="gemini-3.1-flash-tts-preview",
    surfaces=("VERTEX_AI", "CLOUD_TEXT_TO_SPEECH"),
    lifecycle_status=TTSLifecycleStatus.PREVIEW,
    authorization_status=TTSAuthorizationStatus.NOT_YET_CANONICALLY_AUTHORIZED,
    source="https://docs.cloud.google.com/text-to-speech/docs/gemini-tts",
    source_version="reviewed-2026-08-24",
)


class AudioComparisonMode(str, Enum):
    NORMALIZED_EXACT = "NORMALIZED_EXACT"
    SEMANTIC_EQUIVALENCE_NO_STRONGER_CLAIM = "SEMANTIC_EQUIVALENCE_NO_STRONGER_CLAIM"


class AudioLineVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    line_id: str
    expected_text: str
    comparison_mode: AudioComparisonMode
    permitted_pronunciation_normalizations: tuple[str, ...]


class DeliveredAudioVerificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script_hash: str
    ASR_required: bool = True
    line_verifications: tuple[AudioLineVerification, ...]
    stronger_claim_code: str = "AUDIO_SCRIPT_MISMATCH"
    ASR_calls: int = 0
    plan_hash: str

    @model_validator(mode="after")
    def offline_and_deterministic(self) -> "DeliveredAudioVerificationPlan":
        if self.ASR_calls:
            raise ValueError("Gate 2D.4A does not call ASR")
        if self.plan_hash != _stable_hash(self.model_dump(mode="json", exclude={"plan_hash"})):
            raise ValueError("audio verification plan hash mismatch")
        return self


def compile_delivered_audio_verification(
    script: VoiceoverScript,
) -> DeliveredAudioVerificationPlan:
    lines = tuple(AudioLineVerification(
        line_id=item.line_id, expected_text=item.text,
        comparison_mode=(AudioComparisonMode.NORMALIZED_EXACT if item.exact_wording else
                         AudioComparisonMode.SEMANTIC_EQUIVALENCE_NO_STRONGER_CLAIM),
        permitted_pronunciation_normalizations=script.pronunciation_notes)
        for item in script.ordered_lines)
    values = {"script_hash": script.script_hash, "ASR_required": True,
              "line_verifications": lines,
              "stronger_claim_code": "AUDIO_SCRIPT_MISMATCH", "ASR_calls": 0}
    values["plan_hash"] = _stable_hash(values)
    return DeliveredAudioVerificationPlan(**values)


def normalized_spoken_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class LocaleDirection(str, Enum):
    LTR = "LTR"
    RTL = "RTL"


class SafeZoneGeometryType(str, Enum):
    NONE = "NONE"
    MASK_ASSET = "MASK_ASSET"
    NORMALIZED_GEOMETRY = "NORMALIZED_GEOMETRY"


class SafeZoneConfigurationState(str, Enum):
    VERIFIED_MASK = "VERIFIED_MASK"
    VERIFIED_GEOMETRY = "VERIFIED_GEOMETRY"
    PREVIEW_ONLY = "PREVIEW_ONLY"
    UNCONFIGURED = "UNCONFIGURED"


class SafeZonePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    platform: Platform
    placement: str
    locale_direction: LocaleDirection
    policy_version: str
    source: str
    geometry_type: SafeZoneGeometryType
    geometry_asset_reference: str = ""
    normalized_geometry: tuple[float, ...] = ()
    configuration_state: SafeZoneConfigurationState
    runtime_download_allowed: bool = False

    @model_validator(mode="after")
    def geometry_is_reviewed_not_guessed(self) -> "SafeZonePolicy":
        if self.runtime_download_allowed:
            raise ValueError("safe-zone assets cannot be downloaded at runtime")
        if self.configuration_state == SafeZoneConfigurationState.UNCONFIGURED:
            if (self.geometry_type != SafeZoneGeometryType.NONE
                    or self.geometry_asset_reference or self.normalized_geometry):
                raise ValueError("unconfigured safe zone cannot contain guessed geometry")
        elif self.configuration_state == SafeZoneConfigurationState.VERIFIED_MASK:
            if (self.geometry_type != SafeZoneGeometryType.MASK_ASSET
                    or not self.geometry_asset_reference or self.normalized_geometry):
                raise ValueError("verified mask requires one committed reviewed asset")
        elif self.configuration_state == SafeZoneConfigurationState.VERIFIED_GEOMETRY:
            if (self.geometry_type != SafeZoneGeometryType.NORMALIZED_GEOMETRY
                    or len(self.normalized_geometry) != 4):
                raise ValueError("verified geometry requires four reviewed normalized bounds")
        if not self.source or not self.policy_version:
            raise ValueError("safe-zone policy requires source and version")
        return self


TIKTOK_SAFE_ZONE_SOURCE = (
    "https://ads.tiktok.com/resources/help/article/"
    "tiktok-reservation-in-feed-ads-reach-frequency")
META_REELS_SAFE_ZONE_SOURCE = (
    "https://www.facebook.com/business/ads/facebook-instagram-reels-ads")


TIKTOK_LTR_SAFE_ZONE_UNCONFIGURED = SafeZonePolicy(
    policy_id="tiktok-in-feed-ltr-reviewed-source-no-local-mask-v1",
    platform=Platform.TIKTOK, placement="IN_FEED",
    locale_direction=LocaleDirection.LTR, policy_version="source-reviewed-2026-08-24",
    source=TIKTOK_SAFE_ZONE_SOURCE, geometry_type=SafeZoneGeometryType.NONE,
    configuration_state=SafeZoneConfigurationState.UNCONFIGURED)

TIKTOK_RTL_SAFE_ZONE_UNCONFIGURED = SafeZonePolicy(
    policy_id="tiktok-in-feed-arabic-rtl-reviewed-source-no-local-mask-v1",
    platform=Platform.TIKTOK, placement="IN_FEED",
    locale_direction=LocaleDirection.RTL, policy_version="source-reviewed-2026-08-24",
    source=TIKTOK_SAFE_ZONE_SOURCE, geometry_type=SafeZoneGeometryType.NONE,
    configuration_state=SafeZoneConfigurationState.UNCONFIGURED)

META_REELS_SAFE_ZONE_UNCONFIGURED = SafeZonePolicy(
    policy_id="meta-reels-reviewed-source-no-local-template-v1",
    platform=Platform.INSTAGRAM_REELS, placement="REELS_AD",
    locale_direction=LocaleDirection.LTR, policy_version="source-reviewed-2026-08-24",
    source=META_REELS_SAFE_ZONE_SOURCE, geometry_type=SafeZoneGeometryType.NONE,
    configuration_state=SafeZoneConfigurationState.UNCONFIGURED)


def validate_safe_zone_direction(policy: SafeZonePolicy,
                                 requested_direction: LocaleDirection) -> None:
    if policy.locale_direction != requested_direction:
        raise ProductionHarnessError(
            "SAFE_ZONE_DIRECTION_MISMATCH",
            "LTR and Arabic RTL safe-zone policies are not interchangeable",
            repair_owner=RepairOwner.PLATFORM_LAYOUT)


class PlatformPreviewState(str, Enum):
    NEEDS_MANUAL_PREVIEW = "NEEDS_MANUAL_PREVIEW"
    GEOMETRY_CHECK_READY = "GEOMETRY_CHECK_READY"


class PlatformPreviewValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: PlatformPreviewState
    geometry_checked: bool
    manual_preview_required: bool = True
    guarantee_across_all_devices: bool = False


class PlatformPreviewValidator(Protocol):
    def validate(self, compositor_plan: "CompositorPlan",
                 policy: SafeZonePolicy) -> PlatformPreviewValidation: ...


class DeterministicPlatformPreviewValidator:
    def validate(self, compositor_plan: "CompositorPlan",
                 policy: SafeZonePolicy) -> PlatformPreviewValidation:
        del compositor_plan
        configured = policy.configuration_state in {
            SafeZoneConfigurationState.VERIFIED_MASK,
            SafeZoneConfigurationState.VERIFIED_GEOMETRY,
        }
        return PlatformPreviewValidation(
            state=(PlatformPreviewState.GEOMETRY_CHECK_READY if configured
                   else PlatformPreviewState.NEEDS_MANUAL_PREVIEW),
            geometry_checked=configured, manual_preview_required=True,
            guarantee_across_all_devices=False)


class TrimPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "TrimPoint":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("trim end must follow start")
        return self


class CompositorPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    trim_points: tuple[TrimPoint, ...]
    shot_order: tuple[str, ...]
    transitions: tuple[str, ...]
    captions: tuple[str, ...]
    logo_assets: tuple[str, ...]
    CTA_overlays: tuple[str, ...]
    disclaimers: tuple[str, ...]
    end_card_overlays: tuple[str, ...]
    screen_UI_composites: tuple[str, ...]
    voiceover_request_hashes: tuple[str, ...]
    SFX_request_hashes: tuple[str, ...]
    music_request_hashes: tuple[str, ...]
    loudness_targets: tuple[str, ...]
    overlay_timing: tuple[str, ...]
    safe_zone_policy: SafeZonePolicy
    exact_advertising_typography_owner: str = "DETERMINISTIC_COMPOSITOR"
    canonical_delivery_allowed: bool
    provider_calls: int = 0
    plan_hash: str

    @model_validator(mode="after")
    def deterministic_compositor_owns_text(self) -> "CompositorPlan":
        material = self.model_dump(mode="json", exclude={"plan_id", "plan_hash"})
        if self.plan_hash != _stable_hash(material):
            raise ValueError("compositor plan hash mismatch")
        if self.provider_calls:
            raise ValueError("compositor plan cannot call providers")
        configured = self.safe_zone_policy.configuration_state in {
            SafeZoneConfigurationState.VERIFIED_MASK,
            SafeZoneConfigurationState.VERIFIED_GEOMETRY,
        }
        if self.canonical_delivery_allowed != configured:
            raise ValueError("safe-zone readiness and compositor delivery state disagree")
        return self


def compile_compositor_plan(
    plan: CanonicalReelExecutionPlan, *, safe_zone_policy: SafeZonePolicy,
    logo_assets: Sequence[str] = (), screen_UI_composites: Sequence[str] = (),
) -> CompositorPlan:
    if plan.platform_profile.platform != safe_zone_policy.platform:
        raise ProductionHarnessError(
            "SAFE_ZONE_POLICY_MISMATCH", "safe-zone platform differs from creative plan",
            repair_owner=RepairOwner.PLATFORM_LAYOUT)
    audio = compile_audio_requests(plan.audio_plan)
    cta = tuple(item.text for item in plan.overlay_plan.items
                if item.semantic_role == OverlaySemanticRole.CTA)
    end_card = tuple(item.text for item in plan.overlay_plan.items
                     if item.semantic_role == OverlaySemanticRole.END_CARD)
    captions = tuple(item.text for item in plan.overlay_plan.items
                     if item.semantic_role == OverlaySemanticRole.CAPTION)
    disclaimers = tuple(item.text for item in plan.overlay_plan.items
                        if item.semantic_role == OverlaySemanticRole.DISCLAIMER)
    trims = tuple(TrimPoint(shot_id=shot.shot_id, start_seconds=0,
                            end_seconds=shot.duration_target)
                  for shot in plan.shot_contracts)
    values = {
        "trim_points": trims,
        "shot_order": tuple(item.shot_id for item in plan.shot_contracts),
        "transitions": tuple("CUT" for _ in plan.shot_contracts[:-1]),
        "captions": captions, "logo_assets": tuple(logo_assets), "CTA_overlays": cta,
        "disclaimers": disclaimers, "end_card_overlays": end_card,
        "screen_UI_composites": tuple(screen_UI_composites),
        "voiceover_request_hashes": tuple(item.request_hash for item in audio
            if item.category == AudioRequestCategory.MARKETING_VOICEOVER),
        "SFX_request_hashes": tuple(item.request_hash for item in audio
            if item.category == AudioRequestCategory.SFX),
        "music_request_hashes": tuple(item.request_hash for item in audio
            if item.category == AudioRequestCategory.MUSIC),
        "loudness_targets": ("integrated loudness target must be configured before delivery",
                             "true peak target must be configured before delivery"),
        "overlay_timing": tuple(
            f"{item.overlay_id}:{item.temporal_range[0]}-{item.temporal_range[1]}"
            for item in plan.overlay_plan.items),
        "safe_zone_policy": safe_zone_policy,
        "exact_advertising_typography_owner": "DETERMINISTIC_COMPOSITOR",
        "canonical_delivery_allowed": safe_zone_policy.configuration_state in {
            SafeZoneConfigurationState.VERIFIED_MASK,
            SafeZoneConfigurationState.VERIFIED_GEOMETRY},
        "provider_calls": 0,
    }
    digest = _stable_hash(values)
    return CompositorPlan(plan_id="cmp_" + digest[:24], plan_hash=digest, **values)


class ShotOperationIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_version_id: str
    contract_hash: str
    plan_hash: str
    shot_hash: str
    execution_manifest_hash: str
    provider: str
    model: str
    request_hash: str
    operation_id: str

    @model_validator(mode="after")
    def deterministic_identity(self) -> "ShotOperationIdentity":
        material = self.model_dump(mode="json", exclude={"operation_id"})
        if self.operation_id != _stable_hash(material):
            raise ValueError("shot operation identity mismatch")
        return self


def compile_operation_identity(
    *, strategy_version_id: str, manifest: ShotExecutionManifest,
    request: CompiledVideoRequest,
) -> ShotOperationIdentity:
    values = {
        "strategy_version_id": strategy_version_id,
        "contract_hash": manifest.contract_hash, "plan_hash": manifest.plan_hash,
        "shot_hash": manifest.shot_hash,
        "execution_manifest_hash": manifest.execution_manifest_hash,
        "provider": request.provider_name, "model": request.model_id,
        "request_hash": request.request_hash,
    }
    values["operation_id"] = _stable_hash(values)
    return ShotOperationIdentity(**values)


class RetryTrigger(str, Enum):
    REQUEST_INVALID = "REQUEST_INVALID"
    PROVIDER_TRANSIENT_ERROR = "PROVIDER_TRANSIENT_ERROR"
    SEED_QA_FAIL = "SEED_QA_FAIL"
    VIDEO_QA_PRODUCT_DRIFT = "VIDEO_QA_PRODUCT_DRIFT"
    CAPABILITY_ERROR = "CAPABILITY_ERROR"
    CLAIM_STRATEGY_ERROR = "CLAIM_STRATEGY_ERROR"


class RetryAction(str, Enum):
    NO_PROVIDER_CALL = "NO_PROVIDER_CALL"
    RETRY_SAME_EXACT_REQUEST = "RETRY_SAME_EXACT_REQUEST"
    TARGETED_SEED_REPAIR = "TARGETED_SEED_REPAIR"
    VIDEO_RETRY_OR_SEED_REPAIR = "VIDEO_RETRY_OR_SEED_REPAIR"
    RETURN_TO_SHOT_OR_PLAN = "RETURN_TO_SHOT_OR_PLAN"
    RETURN_TO_CONTRACT = "RETURN_TO_CONTRACT"


class ShotRetryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger: RetryTrigger
    action: RetryAction
    provider_retry_allowed: bool
    required_request_hash: str = ""
    repair_owners: tuple[RepairOwner, ...]


class ShotRetryPolicy:
    def decide(
        self, trigger: RetryTrigger, *, original_request_hash: str = "",
        proposed_request_hash: str = "",
    ) -> ShotRetryDecision:
        if trigger == RetryTrigger.PROVIDER_TRANSIENT_ERROR:
            if not original_request_hash or proposed_request_hash != original_request_hash:
                raise ProductionHarnessError(
                    "RETRY_SEMANTICS_CHANGED",
                    "transient retry must preserve the exact provider request hash",
                    repair_owner=RepairOwner.VIDEO)
            return ShotRetryDecision(
                trigger=trigger, action=RetryAction.RETRY_SAME_EXACT_REQUEST,
                provider_retry_allowed=True, required_request_hash=original_request_hash,
                repair_owners=(RepairOwner.VIDEO,))
        mapping = {
            RetryTrigger.REQUEST_INVALID: (
                RetryAction.NO_PROVIDER_CALL, (RepairOwner.SHOT,)),
            RetryTrigger.SEED_QA_FAIL: (
                RetryAction.TARGETED_SEED_REPAIR, (RepairOwner.SEED, RepairOwner.SHOT)),
            RetryTrigger.VIDEO_QA_PRODUCT_DRIFT: (
                RetryAction.VIDEO_RETRY_OR_SEED_REPAIR, (RepairOwner.VIDEO, RepairOwner.SEED)),
            RetryTrigger.CAPABILITY_ERROR: (
                RetryAction.RETURN_TO_SHOT_OR_PLAN, (RepairOwner.SHOT,)),
            RetryTrigger.CLAIM_STRATEGY_ERROR: (
                RetryAction.RETURN_TO_CONTRACT, (RepairOwner.CONTRACT,)),
        }
        action, owners = mapping[trigger]
        return ShotRetryDecision(
            trigger=trigger, action=action, provider_retry_allowed=False,
            repair_owners=owners)


class RetryBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed_attempts_per_shot: int = Field(ge=0, le=10)
    video_attempts_per_shot: int = Field(ge=0, le=10)
    audio_attempts: int = Field(ge=0, le=10)
    QA_calls: int = Field(ge=0, le=50)
    total_retry_cost_ceiling_usd: Decimal = Field(ge=0)

    @field_validator("total_retry_cost_ceiling_usd")
    @classmethod
    def finite_cost_ceiling(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("retry cost ceiling must be finite")
        return value


class FallbackKind(str, Enum):
    NONE = "NONE"
    KEN_BURNS_STATIC = "KEN_BURNS_STATIC"
    RAW_WEBSITE_IMAGE = "RAW_WEBSITE_IMAGE"


class DeliverableStatus(str, Enum):
    PASSED_CANONICAL_REEL = "PASSED_CANONICAL_REEL"
    DEGRADED_PREVIEW = "DEGRADED_PREVIEW"
    BLOCKED = "BLOCKED"


class DeliverableDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: DeliverableStatus
    fallback_kind: FallbackKind = FallbackKind.NONE
    all_video_verdicts_passed: bool = False

    @model_validator(mode="after")
    def fallback_never_passes_canonical(self) -> "DeliverableDecision":
        if self.fallback_kind != FallbackKind.NONE \
                and self.status != DeliverableStatus.DEGRADED_PREVIEW:
            raise ValueError("static/raw fallback is only a DEGRADED_PREVIEW")
        if self.status == DeliverableStatus.PASSED_CANONICAL_REEL \
                and not self.all_video_verdicts_passed:
            raise ValueError("blocked/unverified video cannot be a canonical deliverable")
        return self


class ShotDryRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    execution_mode: ShotExecutionMode
    provider_model_candidate: str
    execution_manifest: ShotExecutionManifest
    compiled_seed_request: Optional[CompiledSeedRequest] = None
    compiled_video_request: Optional[CompiledVideoRequest] = None
    prompt_preview: str
    input_assets: tuple[str, ...]
    required_capabilities: ProviderRequirements
    expected_duration_seconds: float
    QA_requirements: tuple[str, ...]
    predicted_repair_boundaries: tuple[RepairOwner, ...]
    compilation_state: str
    blocker_code: str = ""


class ExecutionDryRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_mode: str = "OFFLINE_PLAN_FIXTURE_NOT_LIVE_AUTHORITY"
    live_strategy_authority_satisfied: bool = False
    source_plan_hash: str
    shots: tuple[ShotDryRunResult, ...]
    operation_identity_state: str = "WAITING_FOR_LIVE_STRATEGY_BINDING"
    provider_calls: int = 0
    network_calls: int = 0
    DB_reads: int = 0
    DB_mutations: int = 0
    spend_usd: Decimal = Decimal("0")
    executable: bool = False
    report_hash: str

    @model_validator(mode="after")
    def offline_firewall(self) -> "ExecutionDryRunReport":
        if (self.live_strategy_authority_satisfied or self.provider_calls or self.network_calls
                or self.DB_reads or self.DB_mutations or self.spend_usd != 0 or self.executable):
            raise ValueError("execution dry-run crossed an offline authority boundary")
        material = self.model_dump(mode="json", exclude={"report_hash"})
        if self.report_hash != _stable_hash(material):
            raise ValueError("dry-run report hash mismatch")
        return self


def _offline_phone_mode(shot: ShotContract) -> PhoneUIExecutionMode:
    text = " ".join((shot.subject_spec, shot.action_spec)).casefold()
    return (PhoneUIExecutionMode.DETERMINISTIC_SCREEN_COMPOSITE
            if any(token in text for token in ("phone", "app", "screen", "interface"))
            else PhoneUIExecutionMode.NONE)


def dry_run_execution_plan(
    plan: CanonicalReelExecutionPlan, *,
    provider_capabilities: VideoProviderCapabilities = VERTEX_VEO_31,
) -> ExecutionDryRunReport:
    seeds = {item.shot_id: item for item in plan.seed_plan.shots}
    refs = {item.shot_id: item for item in plan.reference_plans}
    manifest_compiler = ShotExecutionManifestCompiler()
    prompt_compiler = CanonicalVideoPromptCompiler()
    seed_compiler = SeedRequestCompiler()
    video_compiler = VertexVeo31RequestCompiler(prompt_compiler)
    results = []
    for shot in plan.shot_contracts:
        exact_duration = (int(shot.duration_target) if float(shot.duration_target).is_integer()
                          else None)
        bindings = ShotInputBindings(
            requested_duration_seconds=exact_duration,
            requested_resolution=shot.provider_requirements.resolution_floor,
            phone_UI_mode=_offline_phone_mode(shot),
            consumer_language="und")
        manifest = manifest_compiler.compile(
            shot_contract=shot, seed_shot=seeds[shot.shot_id],
            reference_plan=refs[shot.shot_id], continuity_plan=plan.continuity_plan,
            audio_plan=plan.audio_plan, provider_requirements=shot.provider_requirements,
            provider_capabilities=provider_capabilities,
            contract_hash=plan.contract_hash, plan_hash=plan.plan_hash,
            QA_requirements=plan.QA_requirements, bindings=bindings)
        prompt_text = ""
        prompt_error: Optional[ProductionHarnessError] = None
        try:
            prompt_text = prompt_compiler.compile(shot, manifest).prompt_text
        except ProductionHarnessError as error:
            prompt_error = error
        seed_request = None
        if manifest.seed_requirement == RequirementState.REQUIRED:
            seed_request = seed_compiler.compile(
                shot_contract=shot, seed_shot=seeds[shot.shot_id],
                reference_plan=refs[shot.shot_id], execution_manifest=manifest)
        video_request = None
        state = "COMPILED"
        blocker = ""
        try:
            if prompt_error is not None:
                raise prompt_error
            video_request = video_compiler.compile(
                shot, manifest, provider_capabilities, bindings=bindings,
                seed_verdict=None)
        except ProductionHarnessError as error:
            state = "BLOCKED_BEFORE_PROVIDER"
            blocker = error.code
        results.append(ShotDryRunResult(
            shot_id=shot.shot_id, execution_mode=manifest.execution_mode,
            provider_model_candidate=manifest.provider_capability_identity,
            execution_manifest=manifest, compiled_seed_request=seed_request,
            compiled_video_request=video_request, prompt_preview=prompt_text,
            input_assets=tuple(item.asset_id for item in manifest.required_inputs if item.asset_id),
            required_capabilities=shot.provider_requirements,
            expected_duration_seconds=shot.duration_target,
            QA_requirements=manifest.QA_requirements,
            predicted_repair_boundaries=manifest.allowed_repairs,
            compilation_state=state, blocker_code=blocker))
    values = {
        "source_plan_hash": plan.plan_hash, "shots": tuple(results),
        "provider_calls": 0, "network_calls": 0, "DB_reads": 0, "DB_mutations": 0,
        "spend_usd": Decimal("0"), "executable": False,
    }
    values["report_hash"] = _stable_hash({
        "authority_mode": "OFFLINE_PLAN_FIXTURE_NOT_LIVE_AUTHORITY",
        "live_strategy_authority_satisfied": False,
        **values,
        "operation_identity_state": "WAITING_FOR_LIVE_STRATEGY_BINDING",
    })
    return ExecutionDryRunReport(**values)


FAILURE_BENCHMARK_CASES = (
    "valid_image_to_video",
    "valid_first_last_frame",
    "invalid_duration_5_seconds",
    "unsupported_reference_image_to_video_vertex",
    "unsupported_video_extension",
    "missing_seed_for_image_to_video",
    "missing_last_frame",
    "marketing_text_inside_veo",
    "exact_app_UI_delegated_to_veo",
    "product_identity_requirement_dropped",
    "brand_constraint_dropped",
    "retry_changes_request_semantics",
    "raw_photo_canonical_fallback",
    "CTA_embedded_in_generative_video",
    "LTR_safe_zone_for_Arabic_RTL",
    "guessed_safe_zone_geometry",
    "deprecated_image_preview_selected",
    "Imagen_selected_after_shutdown",
    "failed_seed_sent_to_video",
    "blocked_video_returned_as_deliverable",
    "request_hash_material_only",
    "transient_retry_same_request_hash",
    "unsupported_capability_no_retry",
    "audio_provider_semantic_rewrite",
    "compositor_owns_exact_CTA",
)


__all__ = [
    "AudioComparisonMode", "AudioLineVerification", "AudioRequestCategory",
    "CanonicalVideoPrompt", "CanonicalVideoPromptCompiler", "CompiledAudioRequest",
    "CompiledSeedRequest", "CompiledVideoRequest", "CompositorPlan", "DeliverableDecision",
    "DeliverableStatus", "DeliveredAudioVerificationPlan", "DeterministicPlatformPreviewValidator",
    "ExecutionDryRunReport", "FAILURE_BENCHMARK_CASES", "FallbackKind", "FrameSample",
    "GEMINI_31_FLASH_TTS_PREVIEW", "ImageEscalationReason", "ImageQualityDecision",
    "ImageQualityRouter", "ImageQualityTier", "LocaleDirection", "META_REELS_SAFE_ZONE_UNCONFIGURED",
    "ManifestInput", "ManifestInputType", "PhoneUIExecutionMode", "PlatformPreviewValidation",
    "PlatformPreviewValidator", "ProductionHarnessError", "QADimensionState", "ReferenceUsageBinding",
    "RequirementState", "RetryAction", "RetryBudget", "RetryTrigger", "SafeZoneConfigurationState",
    "SafeZoneGeometryType", "SafeZonePolicy", "SeedQAVerdict", "SeedRequestCompiler",
    "SeedTerminalState", "ShotDryRunResult", "ShotExecutionManifest",
    "ShotExecutionManifestCompiler", "ShotInputBindings", "ShotOperationIdentity",
    "ShotRetryDecision", "ShotRetryPolicy", "ShotVideoTerminalState", "ShotVideoVerdict",
    "TIKTOK_LTR_SAFE_ZONE_UNCONFIGURED", "TIKTOK_RTL_SAFE_ZONE_UNCONFIGURED",
    "TTSAuthorizationStatus", "TTSLifecycleStatus", "TTSProviderProfile",
    "TemporalMutationDefect", "TrimPoint", "VIDEO_FAILURE_REPAIR_OWNERS",
    "VertexVeo31RequestCompiler", "VideoCompilationState", "VideoFailureType",
    "VideoQASamplingPlan", "VideoRepairRoute", "VideoRequestCompiler",
    "compile_audio_requests", "compile_compositor_plan", "compile_delivered_audio_verification",
    "compile_marketing_voiceover_request", "compile_operation_identity",
    "compile_video_QA_sampling_plan", "dry_run_execution_plan", "normalized_spoken_text",
    "route_video_failure", "validate_compiled_video_request", "validate_safe_zone_direction",
]
