from __future__ import annotations

import io
import json
import socket
from decimal import Decimal

import pytest
from pydantic import ValidationError

from reel.canonical_contract import (
    ClaimIntent,
    ClaimSurface,
    ClaimType,
    ClaimVerificationStatus,
    ReelCreativeContractCompiler,
)
from reel.canonical_creative import (
    CANONICAL_GENERATED_TEXT_POLICY,
    IMAGE_PROVIDER_CAPABILITIES,
    ActorMode,
    AudioCategory,
    AudioLayerPlan,
    AudioPlan,
    BeatRole,
    ContinuityDimension,
    ContinuityGroup,
    ContinuityPlan,
    ImageProviderAuthorization,
    Platform,
    ProductPresencePolicy,
    ProductionComplexity,
    ProviderRequirements,
    QAApplicability,
    QARequirement,
    ReferenceAsset,
    ReferencePlan,
    ReferenceType,
    ReferenceUsageMode,
    RepairabilityIntent,
    SeedShotPlan,
    ShotContract,
    ShotRepairability,
    VoiceoverLine,
    VoiceoverScript,
    compile_canonical_creative_plan,
    create_creative_concept,
    platform_profile,
)
from reel.canonical_execution import (
    ProviderSurface,
    RepairOwner,
    VERTEX_VEO_31,
    VERTEX_VEO_31_FAST,
)
from reel.canonical_production import (
    FAILURE_BENCHMARK_CASES,
    GEMINI_31_FLASH_TTS_PREVIEW,
    META_REELS_SAFE_ZONE_UNCONFIGURED,
    TIKTOK_LTR_SAFE_ZONE_UNCONFIGURED,
    TIKTOK_RTL_SAFE_ZONE_UNCONFIGURED,
    AudioRequestCategory,
    CanonicalVideoPromptCompiler,
    CompiledAudioRequest,
    DeliverableDecision,
    DeliverableStatus,
    FallbackKind,
    ImageEscalationReason,
    ImageQualityDecision,
    ImageQualityRouter,
    ImageQualityTier,
    LocaleDirection,
    PhoneUIExecutionMode,
    ProductionHarnessError,
    QADimensionState,
    RequirementState,
    RetryAction,
    RetryBudget,
    RetryTrigger,
    SafeZoneConfigurationState,
    SafeZoneGeometryType,
    SafeZonePolicy,
    SeedQAVerdict,
    SeedRequestCompiler,
    SeedTerminalState,
    ShotExecutionManifestCompiler,
    ShotInputBindings,
    ShotRetryPolicy,
    ShotVideoTerminalState,
    ShotVideoVerdict,
    TemporalMutationDefect,
    VertexVeo31RequestCompiler,
    VideoFailureType,
    compile_audio_requests,
    compile_compositor_plan,
    compile_delivered_audio_verification,
    compile_operation_identity,
    compile_video_QA_sampling_plan,
    dry_run_execution_plan,
    route_video_failure,
    validate_compiled_video_request,
    validate_safe_zone_direction,
)
from reel.canonical_production_cli import main as dry_run_main


@pytest.fixture(autouse=True)
def _offline_firewall(monkeypatch):
    for key in (
        "GOOGLE_API_KEY", "GEMINI_API_KEY", "VEO_API_KEY", "VERTEX_API_KEY",
        "ELEVENLABS_API_KEY", "OPENAI_API_KEY", "BASEERA_DATABASE_URL",
        "DATABASE_URL", "PERSISTENCE_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Gate 2D.4A attempted outbound network I/O")

    monkeypatch.setattr(socket.socket, "connect", unexpected, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", unexpected, raising=False)


def _stable(value):
    def convert(item):
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if hasattr(item, "value"):
            return item.value
        if isinstance(item, dict):
            return {str(k): convert(v) for k, v in item.items()}
        if isinstance(item, (tuple, list)):
            return [convert(v) for v in item]
        return item

    import hashlib
    raw = json.dumps(convert(value), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _shot(*, mode="image", duration=4, action="A hand places the product on the table"):
    requirements = ProviderRequirements(
        requires_text_to_video=(mode == "text"),
        requires_image_to_video=mode in {"image", "first_last"},
        requires_first_last_frame=(mode == "first_last"),
        requires_video_extension=(mode == "extension"),
        requires_identity_consistency=mode != "text",
        allowed_durations=(4, 6, 8), aspect_ratio="9:16",
        resolution_floor="720p", local_seed_input_required=mode in {"image", "first_last"})
    values = {
        "shot_id": "shot_offline_fixture", "coverage_id": "coverage_fixture",
        "beat_id": "beat_fixture", "strategic_role": "VISUAL_EXECUTION_ONLY",
        "creative_purpose": "offline production compiler fixture",
        "duration_target": float(duration), "subject_spec": "A sealed red product package",
        "product_spec": "VISIBLE_AND_VERIFIABLE",
        "location_spec": "A neutral studio tabletop", "actor_spec": "HANDS_ONLY",
        "action_spec": action, "camera_spec": "locked macro camera",
        "composition_spec": "vertical product-centered composition",
        "motion_spec": "one physically plausible hand movement",
        "lighting_style": "soft daylight", "continuity_group": "group_fixture",
        "protected_identity_requirements": ("same hands",),
        "protected_brand_requirements": ("red package", "round seal", "logo geometry"),
        "claim_intents": (), "visual_implication_intents": (),
        "generated_text_policy": CANONICAL_GENERATED_TEXT_POLICY,
        "protected_text_policy": "preserve physical brand text only",
        "audio_intent": "natural placement sound",
        "overlay_reservation": "compositor-owned overlays only",
        "reference_requirements": (ReferenceType.PRODUCT_REFERENCE,
                                   ReferenceType.BRAND_REFERENCE),
        "seed_requirements": ("LOCAL_SEED_REQUIRED" if mode in {"image", "first_last"}
                              else "NO_SEED_REQUIRED"),
        "provider_requirements": requirements,
        "success_criteria": ("product remains recognizable",),
        "failure_conditions": ("product mutation", "generated text"),
        "repair_owner": RepairOwner.SHOT,
        "repairability": ShotRepairability(
            isolated_repair_boundaries=("coverage_fixture",),
            previous_shot_dependency="continuity QA", next_shot_dependency="continuity QA",
            can_regenerate_alone=True, invalidates_audio=False,
            invalidates_captions=False, invalidates_continuity=True),
    }
    values["shot_hash"] = _stable(values)
    return ShotContract(**values)


def _seed_shot(shot, *, required=None):
    return SeedShotPlan(
        shot_id=shot.shot_id,
        seed_required=(shot.provider_requirements.local_seed_input_required
                       if required is None else required),
        conditioning_references=("product_ref",), QA_references=("product_ref", "brand_ref"),
        expected_composition=shot.composition_spec,
        protected_product_features=("red package", "round seal"),
        prohibited_text=("marketing copy", "CTA", "captions", "disclaimers"),
        expected_actor_identity="same hands", expected_place_identity="",
        success_criteria=shot.success_criteria)


def _references(shot, *, provider_reference=False):
    modes = ((ReferenceUsageMode.PROVIDER_REFERENCE,) if provider_reference else
             (ReferenceUsageMode.SEED_CONDITIONING, ReferenceUsageMode.QA_REFERENCE))
    asset = ReferenceAsset(
        reference_id="product_ref", reference_type=ReferenceType.PRODUCT_REFERENCE,
        usage_modes=modes, source_hash="a" * 64, source_note="offline fixture")
    return ReferencePlan(
        shot_id=shot.shot_id, references=(asset,),
        provider_attachment_claimed=provider_reference)


def _continuity(shot):
    values = {
        "groups": (ContinuityGroup(
            group_id="group_fixture", shot_ids=(shot.shot_id,),
            required_dimensions=(ContinuityDimension.PRODUCT,
                                 ContinuityDimension.PACKAGING)),),
        "later_QA_required": True,
    }
    return ContinuityPlan(
        plan_id="continuity_fixture", plan_hash=_stable(values), **values)


def _QA_requirements():
    dimensions = (
        "strategy_alignment", "claim_safety", "creative_fitness", "product_fidelity",
        "brand_fidelity", "human_fidelity", "physical_plausibility", "motion_quality",
        "temporal_consistency", "cross_scene_continuity", "audio_script_integrity",
        "caption_integrity", "platform_compliance", "container_integrity",
    )
    from reel.canonical_creative import FinalQARequirements
    return FinalQARequirements(requirements=tuple(QARequirement(
        dimension=item, applicability=QAApplicability.MANDATORY, reason="offline fixture")
        for item in dimensions))


def _audio_plan():
    values = {
        "environment_audio": AudioLayerPlan(
            category=AudioCategory.ENVIRONMENT_AUDIO, planned=True,
            direction="neutral ambience"),
        "SFX": AudioLayerPlan(category=AudioCategory.SFX, planned=True,
                              direction="one placement sound"),
        "music": AudioLayerPlan(category=AudioCategory.MUSIC, planned=False,
                                 direction="none"),
        "marketing_voiceover": AudioLayerPlan(
            category=AudioCategory.MARKETING_VOICEOVER, planned=False,
            direction="none"),
        "noncritical_dialogue": AudioLayerPlan(
            category=AudioCategory.NONCRITICAL_DIALOGUE, planned=False,
            direction="none"),
        "voiceover_script": None,
        "provider_selected": False,
    }
    return AudioPlan(
        plan_id="audio_fixture", plan_hash=_stable(values), **values)


def _bindings(*, mode="image", duration=4, resolution="720p", references=(),
              phone=PhoneUIExecutionMode.NONE):
    values = dict(
        seed_image_asset_id="seed_asset" if mode in {"image", "first_last"} else "",
        first_frame_asset_id="first_asset" if mode in {"image", "first_last"} else "",
        last_frame_asset_id="last_asset" if mode == "first_last" else "",
        provider_reference_asset_ids=tuple(references), requested_duration_seconds=duration,
        requested_resolution=resolution, phone_UI_mode=phone, consumer_language="ar")
    if phone == PhoneUIExecutionMode.PROTECTED_UI_REFERENCE:
        values["protected_UI_reference_asset_id"] = "protected_ui_asset"
    return ShotInputBindings(**values)


def _manifest(*, mode="image", duration=4, action=None, bindings=None,
              provider_reference=False):
    shot = _shot(mode=mode, duration=duration,
                 action=action or "A hand places the product on the table")
    bindings = bindings or _bindings(mode=mode, duration=duration)
    manifest = ShotExecutionManifestCompiler().compile(
        shot_contract=shot, seed_shot=_seed_shot(shot),
        reference_plan=_references(shot, provider_reference=provider_reference),
        continuity_plan=_continuity(shot), audio_plan=_audio_plan(),
        provider_requirements=shot.provider_requirements,
        provider_capabilities=VERTEX_VEO_31,
        contract_hash="contract_hash_fixture", plan_hash="plan_hash_fixture",
        QA_requirements=_QA_requirements(), bindings=bindings)
    return shot, manifest, bindings


def _seed_verdict(*, passed=True):
    values = {name: QADimensionState.PASS for name in (
        "strategic_relevance", "composition", "product_fidelity", "brand_fidelity",
        "subject_fidelity", "human_anatomy", "action_precondition",
        "protected_text_fidelity", "generated_text_contamination", "reference_alignment",
        "continuity_readiness", "visual_quality")}
    if not passed:
        values["product_fidelity"] = QADimensionState.FAIL
    return SeedQAVerdict(
        seed_request_hash="seed_request_hash", **values,
        terminal_state=(SeedTerminalState.SEED_READY if passed
                        else SeedTerminalState.SEED_BLOCKED))


def _video_request(*, mode="image", duration=4, action=None, references=(), phone=None,
                   seed_passed=True, resolution="720p"):
    phone = phone or PhoneUIExecutionMode.NONE
    bindings = _bindings(mode=mode, duration=duration, resolution=resolution,
                         references=references, phone=phone)
    shot, manifest, bindings = _manifest(
        mode=mode, duration=duration, action=action, bindings=bindings,
        provider_reference=bool(references))
    request = VertexVeo31RequestCompiler().compile(
        shot, manifest, VERTEX_VEO_31, bindings=bindings,
        seed_verdict=(_seed_verdict(passed=seed_passed)
                      if manifest.seed_requirement == RequirementState.REQUIRED else None))
    return shot, manifest, bindings, request


def _rehash_shot(shot, **updates):
    values = shot.model_dump(mode="python", exclude={"shot_hash"})
    values.update(updates)
    values["shot_hash"] = _stable(values)
    return ShotContract(**values)


# Offline failure benchmark 1-25 ---------------------------------------------------------------


def test_01_valid_image_to_video_compiles():
    _shot_value, _manifest_value, _bindings_value, request = _video_request(mode="image")
    assert request.execution_mode.value == "IMAGE_TO_VIDEO"
    assert request.first_frame_asset_id == "first_asset"


def test_02_valid_first_last_frame_compiles():
    *_, request = _video_request(mode="first_last")
    assert request.first_frame_asset_id and request.last_frame_asset_id


def test_03_invalid_duration_five_seconds_is_rejected():
    with pytest.raises(ProductionHarnessError) as error:
        _video_request(mode="text", duration=5)
    assert error.value.code == "PROVIDER_REQUEST_INVALID"


def test_04_vertex_reference_image_to_video_is_unsupported():
    with pytest.raises(ProductionHarnessError) as error:
        _video_request(mode="image", references=("provider_ref",))
    assert error.value.code == "UNSUPPORTED_REQUIRED_CAPABILITY"


def test_05_vertex_video_extension_is_unsupported():
    with pytest.raises(ProductionHarnessError) as error:
        _video_request(mode="extension")
    assert error.value.code == "UNSUPPORTED_REQUIRED_CAPABILITY"


def test_06_missing_seed_blocks_image_to_video():
    bindings = _bindings(mode="image").model_copy(update={
        "seed_image_asset_id": "", "first_frame_asset_id": ""})
    shot, manifest, _ = _manifest(mode="image", bindings=bindings)
    with pytest.raises(ProductionHarnessError) as error:
        VertexVeo31RequestCompiler().compile(
            shot, manifest, VERTEX_VEO_31, bindings=bindings,
            seed_verdict=_seed_verdict())
    assert error.value.code == "PROVIDER_REQUEST_INVALID"


def test_07_missing_last_frame_is_rejected():
    bindings = _bindings(mode="first_last").model_copy(update={"last_frame_asset_id": ""})
    shot, manifest, _ = _manifest(mode="first_last", bindings=bindings)
    with pytest.raises(ProductionHarnessError) as error:
        VertexVeo31RequestCompiler().compile(
            shot, manifest, VERTEX_VEO_31, bindings=bindings,
            seed_verdict=_seed_verdict())
    assert error.value.code == "PROVIDER_REQUEST_INVALID"


def test_08_marketing_headline_cannot_enter_veo_prompt():
    shot, manifest, _ = _manifest(
        mode="text", action="Render a marketing headline over the product")
    with pytest.raises(ProductionHarnessError) as error:
        CanonicalVideoPromptCompiler().compile(shot, manifest)
    assert error.value.code == "PROVIDER_REQUEST_INVALID"


def test_09_exact_app_UI_cannot_be_delegated_to_veo():
    shot, manifest, _ = _manifest(
        mode="text", action="Show exact app UI copy on the phone")
    with pytest.raises(ProductionHarnessError):
        CanonicalVideoPromptCompiler().compile(shot, manifest)


def test_10_product_identity_requirement_cannot_be_dropped():
    _shot_value, manifest, _bindings_value, request = _video_request()
    weakened = request.model_copy(update={"protected_product_features": ()})
    with pytest.raises(ProductionHarnessError) as error:
        validate_compiled_video_request(weakened, manifest, VERTEX_VEO_31)
    assert "product identity" in str(error.value)


def test_11_brand_constraint_cannot_be_dropped():
    _shot_value, manifest, _bindings_value, request = _video_request()
    weakened = request.model_copy(update={"protected_brand_features": ()})
    with pytest.raises(ProductionHarnessError) as error:
        validate_compiled_video_request(weakened, manifest, VERTEX_VEO_31)
    assert "brand identity" in str(error.value)


def test_12_retry_cannot_change_request_semantics():
    with pytest.raises(ProductionHarnessError) as error:
        ShotRetryPolicy().decide(
            RetryTrigger.PROVIDER_TRANSIENT_ERROR,
            original_request_hash="original", proposed_request_hash="changed")
    assert error.value.code == "RETRY_SEMANTICS_CHANGED"


def test_13_raw_photo_fallback_cannot_pass_canonical():
    with pytest.raises(ValidationError):
        DeliverableDecision(
            status=DeliverableStatus.PASSED_CANONICAL_REEL,
            fallback_kind=FallbackKind.RAW_WEBSITE_IMAGE,
            all_video_verdicts_passed=True)


def test_14_CTA_cannot_be_embedded_in_generative_video():
    shot, manifest, _ = _manifest(mode="text", action="Display CTA inside the video")
    with pytest.raises(ProductionHarnessError):
        CanonicalVideoPromptCompiler().compile(shot, manifest)


def test_15_LTR_safe_zone_cannot_be_used_for_Arabic_RTL():
    with pytest.raises(ProductionHarnessError) as error:
        validate_safe_zone_direction(
            TIKTOK_LTR_SAFE_ZONE_UNCONFIGURED, LocaleDirection.RTL)
    assert error.value.code == "SAFE_ZONE_DIRECTION_MISMATCH"


def test_16_safe_zone_geometry_cannot_be_guessed():
    with pytest.raises(ValidationError):
        SafeZonePolicy(
            policy_id="guessed", platform=Platform.TIKTOK, placement="IN_FEED",
            locale_direction=LocaleDirection.RTL, policy_version="none",
            source="https://ads.tiktok.com/help", geometry_type=SafeZoneGeometryType.NONE,
            normalized_geometry=(.1, .1, .1, .1),
            configuration_state=SafeZoneConfigurationState.UNCONFIGURED)


def test_17_deprecated_image_preview_model_is_excluded():
    profile = next(item for item in IMAGE_PROVIDER_CAPABILITIES
                   if item.model_id == "gemini-3.1-flash-image-preview")
    with pytest.raises(ValidationError):
        ImageQualityDecision(quality_tier=ImageQualityTier.STANDARD_SEED, profile=profile)


def test_18_Imagen_family_is_excluded_after_shutdown():
    profile = next(item for item in IMAGE_PROVIDER_CAPABILITIES
                   if item.model_id == "imagen")
    with pytest.raises(ValidationError):
        ImageQualityDecision(quality_tier=ImageQualityTier.STANDARD_SEED, profile=profile)


def test_19_failed_seed_never_enters_video_generation():
    with pytest.raises(ProductionHarnessError) as error:
        _video_request(seed_passed=False)
    assert error.value.code == "SEED_BLOCKED"


def test_20_blocked_video_cannot_be_returned_as_deliverable():
    with pytest.raises(ValidationError):
        DeliverableDecision(
            status=DeliverableStatus.PASSED_CANONICAL_REEL,
            all_video_verdicts_passed=False)


def test_21_request_hash_changes_only_for_material_fields():
    *_left, request_a = _video_request(mode="text", duration=4)
    *_same, request_b = _video_request(mode="text", duration=4)
    *_changed, request_c = _video_request(mode="text", duration=6)
    assert request_a.request_hash == request_b.request_hash
    assert request_a.request_hash != request_c.request_hash


def test_22_transient_retry_preserves_same_request_hash():
    decision = ShotRetryPolicy().decide(
        RetryTrigger.PROVIDER_TRANSIENT_ERROR,
        original_request_hash="same", proposed_request_hash="same")
    assert decision.action == RetryAction.RETRY_SAME_EXACT_REQUEST
    assert decision.provider_retry_allowed is True


def test_23_unsupported_capability_does_not_retry():
    decision = ShotRetryPolicy().decide(RetryTrigger.CAPABILITY_ERROR)
    assert decision.provider_retry_allowed is False
    assert decision.action == RetryAction.RETURN_TO_SHOT_OR_PLAN


def test_24_audio_provider_cannot_attempt_semantic_rewrite():
    script = _voiceover_script()
    from reel.canonical_production import compile_marketing_voiceover_request
    request = compile_marketing_voiceover_request(script, voice_direction="warm")
    bad = request.model_dump(mode="python")
    bad["semantic_rewrite_allowed"] = True
    with pytest.raises(ValidationError):
        CompiledAudioRequest(**bad)


def test_25_compositor_owns_exact_CTA():
    plan = _generic_offline_plan()
    compositor = compile_compositor_plan(
        plan, safe_zone_policy=TIKTOK_LTR_SAFE_ZONE_UNCONFIGURED)
    assert compositor.CTA_overlays == ("Browse collection",)
    assert compositor.exact_advertising_typography_owner == "DETERMINISTIC_COMPOSITOR"
    assert compositor.canonical_delivery_allowed is False


# Additional contract/CLI coverage ----------------------------------------------------------------


def _voiceover_script():
    line = VoiceoverLine(
        line_id="line_value", text="Explore the collection",
        time_budget_seconds=2, claim_intents=(), exact_wording=True)
    material = {
        "language": "en", "locale": "en-US",
        "ordered_lines": [line.model_dump(mode="json")], "time_budget": 3.0,
        "pronunciation_notes": (), "CTA_line": "Browse collection",
    }
    return VoiceoverScript(
        script_id="offline_voiceover", script_hash=_stable(material), **material)


def _contract():
    context = {
        "strategy_version_id": "offline-fixture-not-live-authority",
        "run_kind": "strategy", "run_status": "succeeded",
        "run_brand_id": "brand-fixture", "brand_id": "brand-fixture",
        "is_current": True, "approval_state": "approved",
        "profile_snapshot_id": "profile-fixture",
        "run_input_profile_id": "profile-fixture",
        "profile": {"organization_name": "Example Shop", "languages": ["en"],
                    "existing_ctas": [{"value": "Browse collection"}]},
        "requested_config": {"objective": {
            "objective_type": "CONVERSION",
            "business": {"description": "Help visitors explore a verified collection"},
            "exact_cta": "Browse collection"}},
        "output": {"canonical_strategy": {
            "decision": {"decision_id": "decision-offline-fixture",
                         "selected_option_id": "option-fixture", "options": [{
                "option_id": "option-fixture", "target": "online collection explorers",
                "frame_of_reference": "online shop", "consumer_value": "Easy collection browsing",
                "reason_to_believe": "", "consumer_value_claim_ids": ["claim-value"],
                "reason_to_believe_claim_ids": [], "differentiation_claim_ids": [],
                "internal_supporting_claim_ids": []}]},
            "claims": [{"claim_id": "claim-value", "statement": "Easy collection browsing",
                        "epistemic_type": "INFERENCE", "verification_status": "SUPPORTED",
                        "supporting_evidence_ids": ["evidence-fixture"], "limitations": []}],
            "verification": {"eligible_for_approval": True}}},
    }
    return ReelCreativeContractCompiler().compile(context)


def _generic_offline_plan():
    contract = _contract()
    intent = ClaimIntent(
        surface=ClaimSurface.ON_SCREEN_COPY, text=contract.consumer_value,
        supporting_allowed_claim_ids=contract.allowed_claim_ids[:1],
        claim_type=ClaimType.MESSAGE, factual=True, expressive=False,
        exact_wording_required=False,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED)
    visual = ClaimIntent(
        surface=ClaimSurface.VISUAL_IMPLICATION,
        semantic_intent="A product collection is explored",
        supporting_allowed_claim_ids=contract.allowed_claim_ids[:1],
        claim_type=ClaimType.VISUAL, factual=True, expressive=False,
        exact_wording_required=False,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED)
    shared = dict(
        reel_contract_hash=contract.contract_hash, platform=Platform.TIKTOK,
        audience_tension=contract.target, value_demonstration=contract.consumer_value,
        claim_intents=(intent,), visual_implication_intents=(visual,),
        expected_duration_range=(15.0, 15.0), required_capabilities=("IMAGE_TO_VIDEO",),
        creative_risks=("product identity drift",), why_it_may_fail=("mutation",),
        declared_target=contract.target, preserved_consumer_value=contract.consumer_value,
        declared_CTA=contract.exact_cta,
        repairability_intent=RepairabilityIntent.MODULAR_REPAIRABLE,
        planned_beat_roles=(BeatRole.HOOK, BeatRole.PRODUCT_REVEAL,
                            BeatRole.VALUE_DEMONSTRATION, BeatRole.PAYOFF, BeatRole.CTA))
    concepts = (
        create_creative_concept(
            **shared, concept_id="fixture_macro", creative_thesis="Macro product path",
            hook_mechanism="Immediate macro product motion", narrative_device="reverse reveal",
            product_role="product opens and closes the sequence", brand_entry="product opening",
            emotional_lever="clarity", visual_grammar="locked macro cuts",
            reveal_pattern="macro to collection to macro", actor_mode=ActorMode.HANDS_ONLY,
            pacing_intent="controlled modular cuts", audio_direction="natural sound",
            CTA_transition="cut to deterministic end card",
            production_complexity=ProductionComplexity.LOW,
            product_presence_policy=ProductPresencePolicy.OPENING),
        create_creative_concept(
            **shared, concept_id="fixture_pov", creative_thesis="POV collection path",
            hook_mechanism="POV hand begins exploring", narrative_device="first-person flow",
            product_role="product resolves the browsing path", brand_entry="verified product reveal",
            emotional_lever="ease", visual_grammar="POV handheld sequence",
            reveal_pattern="hand to collection to product", actor_mode=ActorMode.HANDS_ONLY,
            pacing_intent="quick POV cuts", audio_direction="soft rhythm",
            CTA_transition="gesture into deterministic end card",
            production_complexity=ProductionComplexity.MODERATE,
            product_presence_policy=ProductPresencePolicy.EARLY),
        create_creative_concept(
            **shared, concept_id="fixture_graphic", creative_thesis="Graphic object reveal",
            hook_mechanism="Object appears through a clean reveal", narrative_device="graphic reveal",
            product_role="product anchors a no-actor sequence", brand_entry="package reveal",
            emotional_lever="curiosity", visual_grammar="graphic match cuts",
            reveal_pattern="shape to package to collection", actor_mode=ActorMode.NO_ACTOR,
            pacing_intent="measured graphic cuts", audio_direction="light accents",
            CTA_transition="graphic wipe to deterministic end card",
            production_complexity=ProductionComplexity.LOW,
            product_presence_policy=ProductPresencePolicy.DISTRIBUTED),
    )
    product = ReferenceAsset(
        reference_id="fixture-product", reference_type=ReferenceType.PRODUCT_REFERENCE,
        usage_modes=(ReferenceUsageMode.SEED_CONDITIONING, ReferenceUsageMode.QA_REFERENCE),
        source_hash="b" * 64, source_note="offline product fixture")
    brand = ReferenceAsset(
        reference_id="fixture-brand", reference_type=ReferenceType.BRAND_REFERENCE,
        usage_modes=(ReferenceUsageMode.QA_REFERENCE, ReferenceUsageMode.PROTECTED_ASSET),
        source_hash="c" * 64, source_note="offline brand fixture")
    result = compile_canonical_creative_plan(
        contract, platform_profile(Platform.TIKTOK), concepts,
        product_reference=product, brand_reference=brand)
    assert result.plan is not None
    return result.plan


def test_manifest_and_prompt_hashes_are_deterministic_and_structured():
    shot, manifest_a, bindings = _manifest(mode="image")
    _shot_value, manifest_b, _bindings_value = _manifest(mode="image")
    assert manifest_a.execution_manifest_hash == manifest_b.execution_manifest_hash
    prompt = CanonicalVideoPromptCompiler().compile(shot, manifest_a)
    assert len(prompt.sections) == 12
    assert prompt.provider_instruction_language == "en"
    assert prompt.consumer_language == "ar"
    assert prompt.hidden_chain_of_thought_requested is False


def test_fast_Veo_profile_compiles_without_calling_it():
    shot, manifest, bindings = _manifest(mode="image")
    manifest = ShotExecutionManifestCompiler().compile(
        shot_contract=shot, seed_shot=_seed_shot(shot), reference_plan=_references(shot),
        continuity_plan=_continuity(shot), audio_plan=_audio_plan(),
        provider_requirements=shot.provider_requirements,
        provider_capabilities=VERTEX_VEO_31_FAST,
        contract_hash="contract_hash_fixture", plan_hash="plan_hash_fixture",
        QA_requirements=_QA_requirements(), bindings=bindings)
    request = VertexVeo31RequestCompiler().compile(
        shot, manifest, VERTEX_VEO_31_FAST, bindings=bindings,
        seed_verdict=_seed_verdict())
    assert request.model_id == "veo-3.1-fast-generate-001"


def test_seed_request_uses_current_standard_and_hero_models():
    shot, manifest, _bindings_value = _manifest()
    standard = SeedRequestCompiler().compile(
        shot_contract=shot, seed_shot=_seed_shot(shot), reference_plan=_references(shot),
        execution_manifest=manifest)
    assert standard.model_id == "gemini-3.1-flash-image"
    with pytest.raises(ValidationError, match="exact proven Pro pricing"):
        SeedRequestCompiler().compile(
            shot_contract=shot, seed_shot=_seed_shot(shot), reference_plan=_references(shot),
            execution_manifest=manifest, quality_tier=ImageQualityTier.HIGH_COMPLEXITY_HERO,
            escalation_reasons=(ImageEscalationReason.DIFFICULT_COMPOSITION,))
    hero = SeedRequestCompiler().compile(
        shot_contract=shot, seed_shot=_seed_shot(shot), reference_plan=_references(shot),
        execution_manifest=manifest, quality_tier=ImageQualityTier.HIGH_COMPLEXITY_HERO,
        escalation_reasons=(ImageEscalationReason.DIFFICULT_COMPOSITION,),
        pricing_proven=True, execution_authorized=True)
    assert hero.model_id == "gemini-3-pro-image"
    assert standard.provider_calls == hero.provider_calls == 0


def test_image_metadata_is_GA_agent_platform_and_preview_models_are_never_defaults():
    current = {item.model_id: item for item in IMAGE_PROVIDER_CAPABILITIES
               if item.authorization == ImageProviderAuthorization.SUPPORTED_METADATA_ONLY}
    assert current["gemini-3.1-flash-image"].lifecycle_status == "GA"
    assert current["gemini-3-pro-image"].lifecycle_status == "GA"
    assert {item.model_id for item in IMAGE_PROVIDER_CAPABILITIES if item.canonical_default} == {
        "gemini-3.1-flash-image"}
    assert "gemini-3.1-flash-lite-image" not in current


def test_seed_QA_schema_blocks_not_assessed_dimensions():
    values = {name: QADimensionState.NOT_ASSESSED for name in (
        "strategic_relevance", "composition", "product_fidelity", "brand_fidelity",
        "subject_fidelity", "human_anatomy", "action_precondition",
        "protected_text_fidelity", "generated_text_contamination", "reference_alignment",
        "continuity_readiness", "visual_quality")}
    verdict = SeedQAVerdict(
        seed_request_hash="seed", **values, terminal_state=SeedTerminalState.SEED_BLOCKED)
    assert verdict.terminal_state == SeedTerminalState.SEED_BLOCKED


def test_video_QA_sampling_is_dense_for_temporal_risk():
    _shot_value, manifest, _bindings_value = _manifest()
    plan = compile_video_QA_sampling_plan(
        manifest, product_identity_high_risk=True, mutation_suspected=True)
    assert plan.dense_sampling is True
    assert len(plan.samples) == 9
    assert plan.VLM_calls == 0


def test_video_verdict_and_temporal_mutations_are_video_owned():
    dimensions = {name: QADimensionState.PASS for name in (
        "product_identity", "brand_identity", "actor_identity", "subject_consistency",
        "temporal_consistency", "motion_quality", "physical_plausibility", "action_binding",
        "human_fidelity", "product_mutation", "protected_text_fidelity",
        "generated_text_contamination", "shot_contract_alignment")}
    dimensions["product_mutation"] = QADimensionState.FAIL
    verdict = ShotVideoVerdict(
        request_hash="video", **dimensions,
        temporal_mutation_defects=(TemporalMutationDefect.PACKAGING_CHANGE,),
        terminal_state=ShotVideoTerminalState.VIDEO_BLOCKED)
    assert verdict.terminal_state == ShotVideoTerminalState.VIDEO_BLOCKED
    assert route_video_failure(VideoFailureType.PRODUCT_MUTATION).allowed_owners == (
        RepairOwner.VIDEO,)


def test_audio_requests_bind_exact_script_and_verification_without_ASR():
    plan = _generic_offline_plan()
    requests = compile_audio_requests(plan.audio_plan)
    voice = next(item for item in requests
                 if item.category == AudioRequestCategory.MARKETING_VOICEOVER)
    assert voice.source_artifact_type == "VoiceoverScript"
    assert voice.semantic_rewrite_allowed is False
    verification = compile_delivered_audio_verification(plan.audio_plan.voiceover_script)
    assert verification.ASR_required is True
    assert verification.ASR_calls == 0


def test_TTS_candidate_is_preview_and_not_authorized():
    assert GEMINI_31_FLASH_TTS_PREVIEW.model_id == "gemini-3.1-flash-tts-preview"
    assert GEMINI_31_FLASH_TTS_PREVIEW.lifecycle_status.value == "PREVIEW"
    assert GEMINI_31_FLASH_TTS_PREVIEW.authorization_status.value == (
        "NOT_YET_CANONICALLY_AUTHORIZED")


def test_safe_zone_sources_are_versioned_but_geometry_remains_unconfigured():
    for policy in (TIKTOK_LTR_SAFE_ZONE_UNCONFIGURED,
                   TIKTOK_RTL_SAFE_ZONE_UNCONFIGURED,
                   META_REELS_SAFE_ZONE_UNCONFIGURED):
        assert policy.source.startswith("https://")
        assert policy.policy_version
        assert policy.configuration_state == SafeZoneConfigurationState.UNCONFIGURED
        assert not policy.normalized_geometry
        assert not policy.geometry_asset_reference


def test_operation_identity_is_stable_and_binds_every_material_hash():
    _shot_value, manifest, _bindings_value, request = _video_request()
    identity_a = compile_operation_identity(
        strategy_version_id="offline-id", manifest=manifest, request=request)
    identity_b = compile_operation_identity(
        strategy_version_id="offline-id", manifest=manifest, request=request)
    assert identity_a == identity_b
    assert identity_a.request_hash == request.request_hash


def test_retry_budget_is_finite_and_rejects_infinity():
    budget = RetryBudget(
        seed_attempts_per_shot=2, video_attempts_per_shot=2,
        audio_attempts=1, QA_calls=6, total_retry_cost_ceiling_usd=Decimal("1.50"))
    assert budget.total_retry_cost_ceiling_usd == Decimal("1.50")
    with pytest.raises(ValidationError):
        RetryBudget(seed_attempts_per_shot=2, video_attempts_per_shot=2,
                    audio_attempts=1, QA_calls=6,
                    total_retry_cost_ceiling_usd=Decimal("Infinity"))


def test_dry_run_report_is_not_live_authority_and_has_zero_side_effects():
    report = dry_run_execution_plan(_generic_offline_plan())
    assert report.authority_mode == "OFFLINE_PLAN_FIXTURE_NOT_LIVE_AUTHORITY"
    assert report.live_strategy_authority_satisfied is False
    assert report.provider_calls == report.network_calls == 0
    assert report.DB_reads == report.DB_mutations == 0
    assert report.spend_usd == 0
    assert report.executable is False


def test_execution_dry_run_CLI_reads_fixture_and_never_executes(tmp_path):
    plan_path = tmp_path / "offline-plan.json"
    plan_path.write_text(json.dumps(
        _generic_offline_plan().model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    output = io.StringIO()
    error = io.StringIO()
    assert dry_run_main([str(plan_path)], stdout=output, stderr=error) == 0
    payload = json.loads(output.getvalue())
    assert payload["authority_mode"] == "OFFLINE_PLAN_FIXTURE_NOT_LIVE_AUTHORITY"
    assert payload["provider_calls"] == payload["network_calls"] == 0
    assert payload["spend_usd"] == "0"
    assert error.getvalue() == ""


def test_failure_benchmark_has_exactly_the_25_requested_cases():
    assert len(FAILURE_BENCHMARK_CASES) == 25
    assert len(set(FAILURE_BENCHMARK_CASES)) == 25


@pytest.mark.parametrize(("field", "weakened"), (
    ("continuity_requirements", ()),
    ("negative_constraints", ()),
    ("safety_person_controls", ()),
    ("generated_text_policy_hash", "different-policy"),
    ("resolution", "1080p"),
))
def test_constraint_preserving_validation_rejects_every_silent_drop(field, weakened):
    _shot_value, manifest, _bindings_value, request = _video_request()
    changed = request.model_copy(update={field: weakened})
    with pytest.raises(ProductionHarnessError) as error:
        validate_compiled_video_request(changed, manifest, VERTEX_VEO_31)
    assert error.value.code == "PROVIDER_REQUEST_INVALID"
