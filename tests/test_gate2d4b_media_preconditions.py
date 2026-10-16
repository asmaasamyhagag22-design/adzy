from __future__ import annotations

import hashlib
import json
import socket
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from reel.canonical_creative import CanonicalReelExecutionPlan
from reel.canonical_media_preconditions import (
    AGENT_PLATFORM_FLASH_IMAGE_IDENTITY,
    AGENT_PLATFORM_PRO_IMAGE_IDENTITY,
    AGENT_PLATFORM_VEO_31_IDENTITY,
    CLOUD_TTS_FLASH_IDENTITY,
    CONFIGURED_VERTEX_CAPABILITY_MATRIX,
    CONFIGURABLE_ECONOMIC_ROUTING_POLICY,
    DEFAULT_MEDIA_PRICING_REGISTRY,
    UNCONFIGURED_SAFE_ZONE_REGISTRY,
    UNSET_OWNER_MEDIA_BUDGET_POLICY,
    VERTEX_VEO_31_FAST_IDENTITY,
    VERTEX_VEO_31_IDENTITY,
    AuxiliaryMediaWork,
    CanonicalMediaCostPlanner,
    CapabilityProofKind,
    CapabilityStatus,
    CoordinateBasis,
    GeometryValidationState,
    LanguageDirection,
    MediaBillingUnit,
    MediaCostCategory,
    MediaCostState,
    MediaOutputModality,
    MediaPreconditionError,
    MediaPriceKey,
    MediaPricingRecord,
    MediaPricingRegistry,
    MediaPricingStatus,
    NormalizedSafeRect,
    OverlayBoundingBox,
    PlatformGeometryValidator,
    ProductSurface,
    PricedUnitComponent,
    ProviderDecisionSet,
    ProviderSurfaceIdentity,
    ReadinessState,
    SDKCompatibilityState,
    SafeZoneAssetRecord,
    SafeZoneAssetRegistry,
    SafeZoneReviewStatus,
    ShotMediaProviderDecision,
    VideoAudioMode,
    canonical_capability_evidence,
    certify_google_genai_video_request_offline,
    evaluate_media_readiness,
    price_key,
    stable_hash,
)
from reel.canonical_production import RetryBudget
from tests.test_gate2d4a_offline_production_harness import (
    _generic_offline_plan,
    _video_request,
)


@pytest.fixture(autouse=True)
def _offline_certification_firewall(monkeypatch):
    for key in (
        "GOOGLE_API_KEY", "GEMINI_API_KEY", "VEO_API_KEY", "VERTEX_API_KEY",
        "OPENAI_API_KEY", "BASEERA_DATABASE_URL", "DATABASE_URL", "PERSISTENCE_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Gate 2D.4B attempted outbound network I/O")

    monkeypatch.setattr(socket.socket, "connect", unexpected, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", unexpected, raising=False)


def _synthetic_identity(model_id: str) -> ProviderSurfaceIdentity:
    return ProviderSurfaceIdentity(
        provider="offline-test", product_surface=ProductSurface.OFFLINE_SYNTHETIC_TEST,
        API_surface="offline-arithmetic", model_id=model_id, region="none",
        billing_mode="SYNTHETIC", SDK_family="none", SDK_version="none",
        endpoint_family="none", capability_version="synthetic-v1",
        pricing_version="synthetic-v1")


SYN_VIDEO = _synthetic_identity("synthetic-video-standard")
SYN_FAST = _synthetic_identity("synthetic-video-fast")
SYN_SEED = _synthetic_identity("synthetic-seed-standard")
SYN_HERO = _synthetic_identity("synthetic-seed-hero")
SYN_QA = _synthetic_identity("synthetic-qa")
SYN_VOICE = _synthetic_identity("synthetic-voice")


def _synthetic_record(key: MediaPriceKey, rate: str, *,
                      unit: MediaBillingUnit = MediaBillingUnit.CALL,
                      units_per_artifact: str | None = None) -> MediaPricingRecord:
    return MediaPricingRecord(
        key=key, price_unit=unit, unit_price=Decimal(rate), currency="USD",
        units_per_artifact=(Decimal(units_per_artifact)
                            if units_per_artifact is not None else None),
        official_source="synthetic://gate-2d4b-arithmetic-fixture",
        verified_date="2026-08-24", pricing_version="synthetic-arithmetic-v1",
        status=MediaPricingStatus.SYNTHETIC_ARITHMETIC_ONLY,
        notes="SYNTHETIC_COST_EXAMPLE_NOT_LIVE_PLAN")


def _synthetic_registry(*, include_QA=True) -> MediaPricingRegistry:
    records = [
        _synthetic_record(
            price_key(SYN_VIDEO, MediaOutputModality.VIDEO,
                      VideoAudioMode.VIDEO_ONLY, "720p"),
            "0.20", unit=MediaBillingUnit.SECOND),
        _synthetic_record(
            price_key(SYN_VIDEO, MediaOutputModality.VIDEO_AUDIO,
                      VideoAudioMode.VIDEO_PLUS_AUDIO, "720p"),
            "0.40", unit=MediaBillingUnit.SECOND),
        _synthetic_record(
            price_key(SYN_VIDEO, MediaOutputModality.VIDEO,
                      VideoAudioMode.VIDEO_ONLY, "1080p"),
            "0.30", unit=MediaBillingUnit.SECOND),
        _synthetic_record(
            price_key(SYN_VIDEO, MediaOutputModality.VIDEO_AUDIO,
                      VideoAudioMode.VIDEO_PLUS_AUDIO, "1080p"),
            "0.50", unit=MediaBillingUnit.SECOND),
        _synthetic_record(
            price_key(SYN_FAST, MediaOutputModality.VIDEO,
                      VideoAudioMode.VIDEO_ONLY, "720p"),
            "0.08", unit=MediaBillingUnit.SECOND),
        _synthetic_record(
            price_key(SYN_SEED, MediaOutputModality.IMAGE_OUTPUT,
                      VideoAudioMode.NOT_APPLICABLE, "1K"),
            "0.05", units_per_artifact="1"),
        _synthetic_record(
            price_key(SYN_SEED, MediaOutputModality.TEXT_INPUT,
                      VideoAudioMode.NOT_APPLICABLE, "ANY"), "0"),
        _synthetic_record(
            price_key(SYN_SEED, MediaOutputModality.IMAGE_INPUT,
                      VideoAudioMode.NOT_APPLICABLE, "ANY"), "0"),
        _synthetic_record(
            price_key(SYN_HERO, MediaOutputModality.IMAGE_OUTPUT,
                      VideoAudioMode.NOT_APPLICABLE, "1K"),
            "0.15", units_per_artifact="1"),
        _synthetic_record(
            price_key(SYN_HERO, MediaOutputModality.TEXT_INPUT,
                      VideoAudioMode.NOT_APPLICABLE, "ANY"), "0"),
        _synthetic_record(
            price_key(SYN_HERO, MediaOutputModality.IMAGE_INPUT,
                      VideoAudioMode.NOT_APPLICABLE, "ANY"), "0"),
        _synthetic_record(
            price_key(SYN_VOICE, MediaOutputModality.AUDIO_OUTPUT,
                      VideoAudioMode.NOT_APPLICABLE, "N/A"), "0.02"),
        _synthetic_record(
            price_key(SYN_VOICE, MediaOutputModality.TEXT_INPUT,
                      VideoAudioMode.NOT_APPLICABLE, "N/A"), "0"),
    ]
    if include_QA:
        records.extend((
            _synthetic_record(
                price_key(SYN_QA, MediaOutputModality.TEXT_INPUT,
                          VideoAudioMode.NOT_APPLICABLE, "N/A"), "0"),
            _synthetic_record(
                price_key(SYN_QA, MediaOutputModality.TEXT_OUTPUT,
                          VideoAudioMode.NOT_APPLICABLE, "N/A"), "0.01"),
        ))
    return MediaPricingRegistry(records, allow_synthetic=True)


def _three_shot_plan() -> CanonicalReelExecutionPlan:
    source = _generic_offline_plan()
    shots = source.shot_contracts[:3]
    shot_ids = {item.shot_id for item in shots}
    updates = {
        "shot_contracts": shots,
        "reference_plans": tuple(item for item in source.reference_plans
                                 if item.shot_id in shot_ids),
        "seed_plan": source.seed_plan.model_copy(update={
            "shots": tuple(item for item in source.seed_plan.shots
                           if item.shot_id in shot_ids)}),
        "provider_requirements": source.provider_requirements[:3],
        "provider_eligibility_decisions": tuple(
            item for item in source.provider_eligibility_decisions if item.shot_id in shot_ids),
    }
    data = source.model_dump(mode="python", exclude={"plan_hash"})
    data.update(updates)
    material = {key: value for key, value in data.items() if key != "plan_id"}
    data["plan_hash"] = stable_hash(material)
    return CanonicalReelExecutionPlan(**data)


def _decision_set(
    plan: CanonicalReelExecutionPlan, *, duration: int = 4, hero_shot: int | None = None,
    video_retry_shot: int | None = None, seed_retry_shot: int | None = None,
    native_audio: bool = False, video_identity: ProviderSurfaceIdentity = SYN_VIDEO,
    resolution: str = "720p",
) -> ProviderDecisionSet:
    seed_by_id = {item.shot_id: item.seed_required for item in plan.seed_plan.shots}
    audio_mode = (VideoAudioMode.VIDEO_PLUS_AUDIO if native_audio
                  else VideoAudioMode.VIDEO_ONLY)
    modality = (MediaOutputModality.VIDEO_AUDIO if native_audio
                else MediaOutputModality.VIDEO)
    decisions = []
    for index, shot in enumerate(plan.shot_contracts):
        hero = hero_shot == index
        seed_identity = SYN_HERO if hero else SYN_SEED
        decisions.append(ShotMediaProviderDecision(
            shot_id=shot.shot_id, execution_mode="IMAGE_TO_VIDEO",
            video_surface_identity=video_identity,
            video_price_key=price_key(video_identity, modality, audio_mode, resolution),
            duration_seconds=duration, resolution=resolution, audio_mode=audio_mode,
            seed_required=seed_by_id[shot.shot_id],
            seed_surface_identity=seed_identity if seed_by_id[shot.shot_id] else None,
            seed_price_key=(price_key(seed_identity, MediaOutputModality.IMAGE_OUTPUT,
                                      VideoAudioMode.NOT_APPLICABLE, "1K")
                            if seed_by_id[shot.shot_id] else None),
            seed_resolution="1K" if seed_by_id[shot.shot_id] else "",
            seed_input_components=((
                PricedUnitComponent(
                    price_key=price_key(seed_identity, MediaOutputModality.TEXT_INPUT,
                                        VideoAudioMode.NOT_APPLICABLE, "ANY"),
                    units_per_attempt=Decimal("1"), reason="synthetic prompt input"),
                PricedUnitComponent(
                    price_key=price_key(seed_identity, MediaOutputModality.IMAGE_INPUT,
                                        VideoAudioMode.NOT_APPLICABLE, "ANY"),
                    units_per_attempt=Decimal("1"), reason="synthetic reference input"),
            ) if seed_by_id[shot.shot_id] else ()),
            authorized_video_attempts=2 if video_retry_shot == index else 1,
            authorized_seed_attempts=2 if seed_retry_shot == index else 1,
            routing_factors=("required_fidelity", "identity_risk", "motion_complexity")))
    qa_input_key = price_key(SYN_QA, MediaOutputModality.TEXT_INPUT,
                             VideoAudioMode.NOT_APPLICABLE, "N/A")
    qa_output_key = price_key(SYN_QA, MediaOutputModality.TEXT_OUTPUT,
                              VideoAudioMode.NOT_APPLICABLE, "N/A")
    voice_input_key = price_key(SYN_VOICE, MediaOutputModality.TEXT_INPUT,
                                VideoAudioMode.NOT_APPLICABLE, "N/A")
    voice_output_key = price_key(SYN_VOICE, MediaOutputModality.AUDIO_OUTPUT,
                                 VideoAudioMode.NOT_APPLICABLE, "N/A")
    auxiliary = (
        AuxiliaryMediaWork(category=MediaCostCategory.SEED_QA, price_key=qa_input_key,
                           base_units=Decimal("3"), worst_case_units=Decimal("3"),
                           reason="synthetic seed QA input"),
        AuxiliaryMediaWork(category=MediaCostCategory.SEED_QA, price_key=qa_output_key,
                           base_units=Decimal("3"), worst_case_units=Decimal("3"),
                           reason="synthetic seed QA output/reasoning"),
        AuxiliaryMediaWork(category=MediaCostCategory.VIDEO_QA, price_key=qa_input_key,
                           base_units=Decimal("3"), worst_case_units=Decimal("3"),
                           reason="synthetic video QA input"),
        AuxiliaryMediaWork(category=MediaCostCategory.VIDEO_QA, price_key=qa_output_key,
                           base_units=Decimal("3"), worst_case_units=Decimal("3"),
                           reason="synthetic video QA output/reasoning"),
        AuxiliaryMediaWork(category=MediaCostCategory.VOICE_GENERATION,
                           price_key=voice_input_key,
                           base_units=Decimal("1"), worst_case_units=Decimal("1"),
                           reason="synthetic voice text input"),
        AuxiliaryMediaWork(category=MediaCostCategory.VOICE_GENERATION,
                           price_key=voice_output_key,
                           base_units=Decimal("1"), worst_case_units=Decimal("1"),
                           reason="synthetic voice audio output"),
        AuxiliaryMediaWork(category=MediaCostCategory.VOICE_VERIFICATION,
                           price_key=qa_input_key,
                           base_units=Decimal("1"), worst_case_units=Decimal("1"),
                           reason="synthetic voice verification input"),
        AuxiliaryMediaWork(category=MediaCostCategory.VOICE_VERIFICATION,
                           price_key=qa_output_key,
                           base_units=Decimal("1"), worst_case_units=Decimal("1"),
                           reason="synthetic voice verification output"),
        AuxiliaryMediaWork(category=MediaCostCategory.FINAL_REEL_QA,
                           price_key=qa_input_key,
                           base_units=Decimal("1"), worst_case_units=Decimal("1"),
                           reason="synthetic final QA input"),
        AuxiliaryMediaWork(category=MediaCostCategory.FINAL_REEL_QA,
                           price_key=qa_output_key,
                           base_units=Decimal("1"), worst_case_units=Decimal("1"),
                           reason="synthetic final QA output/reasoning"),
    )
    return ProviderDecisionSet(
        authority_label="SYNTHETIC_COST_EXAMPLE_NOT_LIVE_PLAN",
        shot_decisions=tuple(decisions), auxiliary_work=auxiliary)


def _budget(*, attempts=1) -> RetryBudget:
    return RetryBudget(
        seed_attempts_per_shot=attempts, video_attempts_per_shot=attempts,
        audio_attempts=1, QA_calls=20,
        total_retry_cost_ceiling_usd=Decimal("100"))


def _cost(plan, decisions, *, attempts=1, include_QA=True):
    return CanonicalMediaCostPlanner(_synthetic_registry(include_QA=include_QA)).plan(
        plan, decisions, _budget(attempts=attempts))


def _verified_safe_zone(tmp_path) -> SafeZoneAssetRecord:
    asset = tmp_path / "verified-safe-zone.txt"
    asset.write_bytes(b"reviewed offline geometry fixture")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    return SafeZoneAssetRecord(
        policy_id="VERIFIED_TEST_GEOMETRY", platform="INSTAGRAM_REELS",
        placement="REELS", aspect_ratio="9:16", language_direction=LanguageDirection.LTR,
        source="https://www.facebook.com/business/ads/facebook-instagram-reels-ads",
        source_version_date="2026-08-24", local_asset_path=asset.name,
        local_asset_hash=digest, review_status=SafeZoneReviewStatus.VERIFIED,
        coordinate_basis=CoordinateBasis.NORMALIZED_RECT,
        intended_overlay_categories=("CAPTIONS", "CTA"),
        normalized_safe_rect=NormalizedSafeRect(
            left=Decimal("0.1"), top=Decimal("0.1"), right=Decimal("0.9"),
            bottom=Decimal("0.9")))


def test_surface_identity_is_more_than_provider_and_model():
    assert VERTEX_VEO_31_IDENTITY.model_id == AGENT_PLATFORM_VEO_31_IDENTITY.model_id
    assert VERTEX_VEO_31_IDENTITY.identity != AGENT_PLATFORM_VEO_31_IDENTITY.identity
    assert VERTEX_VEO_31_IDENTITY.product_surface == \
        ProductSurface.VERTEX_AI_GENERATIVE_AI


def test_provider_surface_identity_has_every_required_axis():
    dumped = VERTEX_VEO_31_IDENTITY.model_dump()
    assert set(dumped) == {
        "provider", "product_surface", "API_surface", "model_id", "region",
        "billing_mode", "SDK_family", "SDK_version", "endpoint_family",
        "capability_version", "pricing_version"}


def test_capability_evidence_is_hashed_and_surface_scoped():
    evidence = canonical_capability_evidence()
    assert len(evidence) == 24
    assert all(item.evidence_hash == stable_hash(
        item.model_dump(mode="json", exclude={"evidence_hash"})) for item in evidence)


def test_vertex_reference_and_extension_remain_unknown_and_fail_closed():
    evidence = canonical_capability_evidence()
    records = [item for item in evidence
               if item.provider_surface_identity.product_surface ==
               ProductSurface.VERTEX_AI_GENERATIVE_AI
               and item.capability_name in {"REFERENCE_IMAGE_TO_VIDEO", "VIDEO_EXTENSION"}]
    assert {item.status for item in records} == {CapabilityStatus.UNKNOWN}
    assert all(item.proof_kind == CapabilityProofKind.DOCUMENTATION_DIVERGENCE
               for item in records)
    assert all(not row.reference_images and not row.video_extension
               for row in CONFIGURED_VERTEX_CAPABILITY_MATRIX)


def test_agent_platform_evidence_cannot_upgrade_vertex_matrix():
    evidence = canonical_capability_evidence()
    agent = [item for item in evidence
             if item.provider_surface_identity.product_surface ==
             ProductSurface.GEMINI_ENTERPRISE_AGENT_PLATFORM
             and item.capability_name == "REFERENCE_IMAGE_TO_VIDEO"]
    assert agent and all(item.status == CapabilityStatus.SUPPORTED for item in agent)
    assert all(not row.reference_images for row in CONFIGURED_VERTEX_CAPABILITY_MATRIX)


def test_agent_platform_extension_support_cannot_upgrade_vertex_profile():
    agent = [item for item in canonical_capability_evidence()
             if item.provider_surface_identity.product_surface ==
             ProductSurface.GEMINI_ENTERPRISE_AGENT_PLATFORM
             and item.capability_name == "VIDEO_EXTENSION"]
    assert agent and all(item.status == CapabilityStatus.SUPPORTED for item in agent)
    assert all(not row.video_extension for row in CONFIGURED_VERTEX_CAPABILITY_MATRIX)


def test_wrong_surface_model_document_cannot_satisfy_vertex_capability_gate():
    agent = next(item for item in canonical_capability_evidence()
                 if item.provider_surface_identity.product_surface ==
                 ProductSurface.GEMINI_ENTERPRISE_AGENT_PLATFORM)
    assert agent.provider_surface_identity.surface_model_identity != \
        VERTEX_VEO_31_IDENTITY.surface_model_identity
    assert agent.official_source_url != next(
        item.official_source_url for item in canonical_capability_evidence()
        if item.provider_surface_identity == VERTEX_VEO_31_IDENTITY)


def test_no_unverified_veo_lite_or_omni_authorization_is_added():
    ids = {row.identity.model_id for row in CONFIGURED_VERTEX_CAPABILITY_MATRIX}
    assert ids == {"veo-3.1-generate-001", "veo-3.1-fast-generate-001"}
    assert CONFIGURABLE_ECONOMIC_ROUTING_POLICY.policy_frozen is False


@pytest.mark.parametrize("mode", ["text", "image", "first_last"])
def test_installed_SDK_serializes_each_execution_mode_without_client(mode):
    _shot, _manifest, _bindings, request = _video_request(mode=mode)
    result = certify_google_genai_video_request_offline(
        request, identity=VERTEX_VEO_31_IDENTITY)
    assert result.state == SDKCompatibilityState.SERIALIZATION_PROVEN_OFFLINE
    assert result.installed_google_genai_version == "2.8.0"
    assert result.provider_calls == result.network_calls == 0
    assert (result.serialized_request["image"] is not None) == (mode != "text")
    assert ("lastFrame" in result.serialized_request["config"]) == (mode == "first_last")


@pytest.mark.parametrize("duration", [4, 6, 8])
def test_installed_SDK_serializes_each_canonical_duration(duration):
    _shot, _manifest, _bindings, request = _video_request(mode="image", duration=duration)
    result = certify_google_genai_video_request_offline(
        request, identity=VERTEX_VEO_31_IDENTITY)
    assert result.serialized_request["config"]["durationSeconds"] == duration


@pytest.mark.parametrize("resolution", ["720p", "1080p"])
def test_installed_SDK_serializes_canonical_resolutions(resolution):
    _shot, _manifest, _bindings, request = _video_request(
        mode="image", resolution=resolution)
    result = certify_google_genai_video_request_offline(
        request, identity=VERTEX_VEO_31_IDENTITY)
    assert result.serialized_request["config"]["resolution"] == resolution


def test_installed_SDK_serializes_9_16_and_person_negative_storage_fields():
    _shot, _manifest, _bindings, request = _video_request(mode="image")
    result = certify_google_genai_video_request_offline(
        request, identity=VERTEX_VEO_31_IDENTITY)
    config = result.serialized_request["config"]
    assert config["aspectRatio"] == "9:16"
    assert config["personGeneration"] in {"allow_adult", "disallow"}
    assert "negativePrompt" in config and "outputGcsUri" in config


def test_SDK_schema_presence_is_not_model_capability_proof():
    _shot, _manifest, _bindings, request = _video_request(mode="image")
    result = certify_google_genai_video_request_offline(
        request, identity=VERTEX_VEO_31_IDENTITY)
    assert "reference_images" in result.SDK_fields_not_model_capabilities
    assert all(not row.reference_images for row in CONFIGURED_VERTEX_CAPABILITY_MATRIX)


def test_redacted_SDK_snapshot_contains_no_prompt_or_asset_identifier():
    _shot, _manifest, _bindings, request = _video_request(mode="first_last")
    result = certify_google_genai_video_request_offline(
        request, identity=VERTEX_VEO_31_IDENTITY)
    raw = str(result.serialized_request)
    assert request.prompt.prompt_text not in raw
    assert request.first_frame_asset_id not in raw
    assert result.serialization_hash == stable_hash(result.serialized_request)


def test_wrong_model_cannot_pass_SDK_certification():
    _shot, _manifest, _bindings, request = _video_request(mode="image")
    with pytest.raises(MediaPreconditionError) as caught:
        certify_google_genai_video_request_offline(
            request, identity=VERTEX_VEO_31_FAST_IDENTITY)
    assert caught.value.code == "SURFACE_IDENTITY_MISMATCH"


def test_exact_agent_platform_image_price_resolves():
    key = price_key(AGENT_PLATFORM_FLASH_IMAGE_IDENTITY,
                    MediaOutputModality.IMAGE_OUTPUT,
                    VideoAudioMode.NOT_APPLICABLE, "1K")
    record = DEFAULT_MEDIA_PRICING_REGISTRY.resolve(key)
    assert record is not None and record.unit_price == Decimal("60.00")
    assert record.cost(Decimal("1120")) == Decimal("0.0672")


@pytest.mark.parametrize("mutation", ["model", "surface", "resolution", "audio"])
def test_wrong_exact_pricing_tuple_blocks(mutation):
    key = price_key(AGENT_PLATFORM_FLASH_IMAGE_IDENTITY,
                    MediaOutputModality.IMAGE_OUTPUT,
                    VideoAudioMode.NOT_APPLICABLE, "1K")
    values = key.model_dump(mode="python")
    if mutation == "model":
        values["model_id"] = "wrong-model"
    elif mutation == "surface":
        values["product_surface"] = ProductSurface.GEMINI_DEVELOPER_API
    elif mutation == "resolution":
        values["resolution"] = "8K"
    else:
        values["audio_mode"] = VideoAudioMode.VIDEO_ONLY
    assert DEFAULT_MEDIA_PRICING_REGISTRY.resolve(MediaPriceKey(**values)) is None


def test_agent_platform_veo_observation_is_not_vertex_or_verified_second_price():
    agent_key = price_key(AGENT_PLATFORM_VEO_31_IDENTITY,
                          MediaOutputModality.VIDEO,
                          VideoAudioMode.VIDEO_ONLY, "720p")
    observed = DEFAULT_MEDIA_PRICING_REGISTRY.resolve(agent_key, require_verified=False)
    assert observed is not None
    assert observed.price_unit == MediaBillingUnit.UNKNOWN_COUNT
    assert observed.status == MediaPricingStatus.UNPROVEN_BILLING_UNIT
    assert DEFAULT_MEDIA_PRICING_REGISTRY.resolve(agent_key) is None
    vertex_key = price_key(VERTEX_VEO_31_IDENTITY, MediaOutputModality.VIDEO,
                           VideoAudioMode.VIDEO_ONLY, "720p")
    assert DEFAULT_MEDIA_PRICING_REGISTRY.resolve(vertex_key) is None


def test_gemini_developer_API_price_cannot_satisfy_vertex_price():
    developer_identity = AGENT_PLATFORM_VEO_31_IDENTITY.model_copy(update={
        "product_surface": ProductSurface.GEMINI_DEVELOPER_API,
        "API_surface": "generativelanguage.googleapis.com",
        "billing_mode": "DEVELOPER_PAYGO"})
    developer_key = price_key(developer_identity, MediaOutputModality.VIDEO,
                              VideoAudioMode.VIDEO_ONLY, "720p")
    developer_record = _synthetic_record(
        developer_key, "0.20", unit=MediaBillingUnit.SECOND)
    registry = MediaPricingRegistry((developer_record,), allow_synthetic=True)
    vertex_key = price_key(VERTEX_VEO_31_IDENTITY, MediaOutputModality.VIDEO,
                           VideoAudioMode.VIDEO_ONLY, "720p")
    assert registry.resolve(developer_key) is not None
    assert registry.resolve(vertex_key) is None


def test_same_model_on_different_surface_has_different_pricing_identity():
    agent_key = price_key(AGENT_PLATFORM_VEO_31_IDENTITY, MediaOutputModality.VIDEO,
                          VideoAudioMode.VIDEO_ONLY, "720p")
    vertex_key = price_key(VERTEX_VEO_31_IDENTITY, MediaOutputModality.VIDEO,
                           VideoAudioMode.VIDEO_ONLY, "720p")
    assert agent_key.model_id == vertex_key.model_id
    assert agent_key.identity != vertex_key.identity


def test_cloud_TTS_price_does_not_satisfy_gemini_API_TTS():
    cloud_key = price_key(CLOUD_TTS_FLASH_IDENTITY, MediaOutputModality.AUDIO_OUTPUT,
                          VideoAudioMode.NOT_APPLICABLE, "N/A")
    assert DEFAULT_MEDIA_PRICING_REGISTRY.resolve(cloud_key) is not None
    wrong = cloud_key.model_copy(update={
        "product_surface": ProductSurface.GEMINI_DEVELOPER_API,
        "API_surface": "generativelanguage.googleapis.com"})
    assert DEFAULT_MEDIA_PRICING_REGISTRY.resolve(wrong) is None


def test_cloud_TTS_output_math_is_half_mill_per_second_plus_input():
    key = price_key(CLOUD_TTS_FLASH_IDENTITY, MediaOutputModality.AUDIO_OUTPUT,
                    VideoAudioMode.NOT_APPLICABLE, "N/A")
    record = DEFAULT_MEDIA_PRICING_REGISTRY.resolve(key)
    assert record is not None
    assert record.cost(Decimal("25")) == Decimal("0.0005")


def test_hero_Pro_pricing_is_not_certified_as_complete():
    key = price_key(AGENT_PLATFORM_PRO_IMAGE_IDENTITY,
                    MediaOutputModality.IMAGE_OUTPUT,
                    VideoAudioMode.NOT_APPLICABLE, "1K")
    assert DEFAULT_MEDIA_PRICING_REGISTRY.resolve(key) is None
    assert DEFAULT_MEDIA_PRICING_REGISTRY.resolve(
        key, require_verified=False).status == MediaPricingStatus.UNPROVEN_SURFACE_IDENTITY


def test_expired_snapshot_can_be_configured_to_block():
    record = _synthetic_record(
        price_key(SYN_QA, MediaOutputModality.QA_CALL,
                  VideoAudioMode.NOT_APPLICABLE, "N/A"), "0.01").model_copy(update={
                      "review_expires": "2026-08-23"})
    registry = MediaPricingRegistry((record,), allow_synthetic=True)
    assert registry.resolve(record.key) is None
    assert registry.resolve(record.key, block_expired=False) is not None


def test_synthetic_3_shot_12_second_base_and_worst_case():
    plan = _three_shot_plan()
    envelope = _cost(plan, _decision_set(plan, duration=4))
    assert envelope.authority_label == "SYNTHETIC_COST_EXAMPLE_NOT_LIVE_PLAN"
    assert envelope.state == MediaCostState.COST_PROVABLE
    assert envelope.video_base_cost == envelope.video_worst_case_cost == Decimal("2.40")
    assert envelope.total_base_cost == envelope.total_worst_case_cost == Decimal("2.65")


def test_synthetic_3_shot_18_second_base_and_worst_case():
    plan = _three_shot_plan()
    envelope = _cost(plan, _decision_set(plan, duration=6))
    assert envelope.video_base_cost == Decimal("3.60")
    assert envelope.total_base_cost == Decimal("3.85")


def test_synthetic_hero_escalation_has_separate_price_identity():
    plan = _three_shot_plan()
    regular = _cost(plan, _decision_set(plan, duration=4))
    hero = _cost(plan, _decision_set(plan, duration=4, hero_shot=0))
    assert hero.seed_base_cost - regular.seed_base_cost == Decimal("0.10")
    assert hero.total_base_cost == Decimal("2.75")


def test_seed_output_price_alone_cannot_hide_free_prompt_input():
    plan = _three_shot_plan()
    first = _decision_set(plan).shot_decisions[0]
    with pytest.raises(ValidationError, match="prompt text input"):
        ShotMediaProviderDecision(**first.model_dump(
            mode="python", exclude={"seed_input_components"}))


def test_conditioning_reference_input_must_be_priced():
    plan = _three_shot_plan()
    decisions = _decision_set(plan)
    first = decisions.shot_decisions[0]
    text_only = first.model_copy(update={
        "seed_input_components": tuple(
            item for item in first.seed_input_components
            if item.price_key.output_modality == MediaOutputModality.TEXT_INPUT)})
    changed = decisions.model_copy(update={
        "shot_decisions": (text_only, *decisions.shot_decisions[1:])})
    envelope = _cost(plan, changed)
    assert envelope.state == MediaCostState.COST_NOT_PROVABLE
    assert envelope.seed_base_cost is None


def test_one_targeted_video_retry_only_changes_that_shot_worst_case():
    plan = _three_shot_plan()
    envelope = _cost(
        plan, _decision_set(plan, duration=4, video_retry_shot=1), attempts=2)
    assert envelope.video_base_cost == Decimal("2.40")
    assert envelope.video_worst_case_cost == Decimal("3.20")
    assert envelope.total_base_cost == Decimal("2.65")
    assert envelope.total_worst_case_cost == Decimal("3.45")


def test_one_seed_retry_does_not_double_count_video_or_other_seeds():
    plan = _three_shot_plan()
    envelope = _cost(plan, _decision_set(plan, seed_retry_shot=2), attempts=2)
    assert envelope.seed_base_cost == Decimal("0.15")
    assert envelope.seed_worst_case_cost == Decimal("0.20")
    assert envelope.video_base_cost == envelope.video_worst_case_cost
    assert envelope.total_worst_case_cost - envelope.total_base_cost == Decimal("0.05")


def test_unpriced_QA_keeps_complete_totals_unknown():
    plan = _three_shot_plan()
    envelope = _cost(plan, _decision_set(plan), include_QA=False)
    assert envelope.state == MediaCostState.COST_NOT_PROVABLE
    assert MediaCostCategory.SEED_QA in envelope.unpriced_categories
    assert envelope.total_base_cost is envelope.total_worst_case_cost is None


def test_QA_input_price_does_not_make_reasoning_output_free():
    plan = _three_shot_plan()
    input_key = price_key(SYN_QA, MediaOutputModality.TEXT_INPUT,
                          VideoAudioMode.NOT_APPLICABLE, "N/A")
    output_key = price_key(SYN_QA, MediaOutputModality.TEXT_OUTPUT,
                           VideoAudioMode.NOT_APPLICABLE, "N/A")
    registry = MediaPricingRegistry((
        _synthetic_record(input_key, "1", unit=MediaBillingUnit.PER_1M_TOKENS),
    ), allow_synthetic=True)
    decisions = _decision_set(plan).model_copy(update={
        "auxiliary_work": (
            AuxiliaryMediaWork(category=MediaCostCategory.VIDEO_QA, price_key=input_key,
                               base_units=Decimal("1000"), worst_case_units=Decimal("1000"),
                               reason="QA input tokens"),
            AuxiliaryMediaWork(category=MediaCostCategory.VIDEO_QA, price_key=output_key,
                               base_units=Decimal("500"), worst_case_units=Decimal("500"),
                               reason="visible output and reasoning tokens"),),
        "mandatory_categories": (MediaCostCategory.VIDEO_QA,)})
    envelope = CanonicalMediaCostPlanner(registry).plan(plan, decisions, _budget())
    assert envelope.state == MediaCostState.COST_NOT_PROVABLE
    assert envelope.video_qa_cost is None


def test_native_audio_is_an_explicit_different_price_and_not_free():
    plan = _three_shot_plan()
    video_only = _cost(plan, _decision_set(plan, native_audio=False))
    with_audio = _cost(plan, _decision_set(plan, native_audio=True))
    assert video_only.video_base_cost == Decimal("2.40")
    assert with_audio.video_base_cost == Decimal("4.80")


def test_voice_audio_output_price_without_text_input_keeps_total_unknown():
    plan = _three_shot_plan()
    decisions = _decision_set(plan)
    no_voice_input = decisions.model_copy(update={
        "auxiliary_work": tuple(
            item for item in decisions.auxiliary_work
            if not (item.category == MediaCostCategory.VOICE_GENERATION
                    and item.price_key.output_modality == MediaOutputModality.TEXT_INPUT))})
    envelope = _cost(plan, no_voice_input)
    assert envelope.state == MediaCostState.COST_NOT_PROVABLE
    assert envelope.voice_cost is None


def test_video_audio_mode_cannot_disagree_with_pricing_key():
    key = price_key(SYN_VIDEO, MediaOutputModality.VIDEO,
                    VideoAudioMode.VIDEO_ONLY, "720p")
    with pytest.raises(ValidationError, match="audio decision"):
        ShotMediaProviderDecision(
            shot_id="shot", execution_mode="T2V", video_surface_identity=SYN_VIDEO,
            video_price_key=key, duration_seconds=4, resolution="720p",
            audio_mode=VideoAudioMode.VIDEO_PLUS_AUDIO, seed_required=False)


def test_per_shot_retry_cannot_exceed_global_finite_budget():
    plan = _three_shot_plan()
    decisions = _decision_set(plan, video_retry_shot=0)
    with pytest.raises(MediaPreconditionError) as caught:
        _cost(plan, decisions, attempts=1)
    assert caught.value.code == "SHOT_RETRY_EXCEEDS_BUDGET"


def test_cost_sensitivity_factors_are_provider_free_arithmetic():
    plan = _three_shot_plan()
    base = _cost(plan, _decision_set(plan)).total_worst_case_cost
    factors = {
        "clip_seconds": _cost(plan, _decision_set(plan, duration=6)).total_worst_case_cost,
        "resolution": _cost(
            plan, _decision_set(plan, resolution="1080p")).total_worst_case_cost,
        "model_tier": _cost(plan, _decision_set(plan, video_identity=SYN_FAST)).total_worst_case_cost,
        "retries": _cost(plan, _decision_set(plan, video_retry_shot=0), attempts=2).total_worst_case_cost,
        "number_of_seeds": base,
        "QA_calls": base,
        "native_audio": _cost(plan, _decision_set(plan, native_audio=True)).total_worst_case_cost,
    }
    assert all(value is not None for value in factors.values())
    deltas = {name: value - base for name, value in factors.items()}
    assert max(deltas, key=deltas.get) == "native_audio"
    assert deltas["resolution"] == Decimal("1.20")


def test_safe_zone_registry_keeps_instagram_unconfigured():
    policy = UNCONFIGURED_SAFE_ZONE_REGISTRY.resolve(
        platform="INSTAGRAM_REELS", placement="REELS", aspect_ratio="9:16",
        language_direction=LanguageDirection.LTR)
    assert policy is not None and policy.review_status == SafeZoneReviewStatus.UNCONFIGURED
    assert policy.local_asset_path == "" and policy.normalized_safe_rect is None


def test_tiktok_LTR_and_RTL_are_independent_unconfigured_identities():
    ltr = UNCONFIGURED_SAFE_ZONE_REGISTRY.resolve(
        platform="TIKTOK_REELS", placement="IN_FEED", aspect_ratio="9:16",
        language_direction=LanguageDirection.LTR)
    rtl = UNCONFIGURED_SAFE_ZONE_REGISTRY.resolve(
        platform="TIKTOK_REELS", placement="IN_FEED", aspect_ratio="9:16",
        language_direction=LanguageDirection.RTL)
    assert ltr.policy_id == "TIKTOK_REELS_LTR"
    assert rtl.policy_id == "TIKTOK_REELS_ARABIC_RTL"
    assert ltr.policy_id != rtl.policy_id
    assert ltr.normalized_safe_rect is rtl.normalized_safe_rect is None


def test_unconfigured_geometry_is_not_a_pass():
    policy = UNCONFIGURED_SAFE_ZONE_REGISTRY.resolve(
        platform="INSTAGRAM_REELS", placement="REELS", aspect_ratio="9:16",
        language_direction=LanguageDirection.LTR)
    result = PlatformGeometryValidator().validate(
        canvas_width=1080, canvas_height=1920, overlays=(), policy=policy,
        locale_direction=LanguageDirection.LTR)
    assert result.state == GeometryValidationState.NOT_CONFIGURED


def test_verified_geometry_passes_only_inside_reviewed_rectangle(tmp_path):
    policy = _verified_safe_zone(tmp_path)
    overlay = OverlayBoundingBox(
        overlay_id="caption", category="CAPTIONS",
        left_px=200, top_px=300, right_px=800, bottom_px=1500)
    result = PlatformGeometryValidator().validate(
        canvas_width=1080, canvas_height=1920, overlays=(overlay,), policy=policy,
        locale_direction=LanguageDirection.LTR)
    assert result.state == GeometryValidationState.PASS


def test_verified_geometry_fails_outside_or_wrong_direction(tmp_path):
    policy = _verified_safe_zone(tmp_path)
    outside = OverlayBoundingBox(
        overlay_id="cta", category="CTA", left_px=0, top_px=0,
        right_px=300, bottom_px=200)
    validator = PlatformGeometryValidator()
    assert validator.validate(
        canvas_width=1080, canvas_height=1920, overlays=(outside,), policy=policy,
        locale_direction=LanguageDirection.LTR).state == GeometryValidationState.FAIL
    assert validator.validate(
        canvas_width=1080, canvas_height=1920, overlays=(), policy=policy,
        locale_direction=LanguageDirection.RTL).state == GeometryValidationState.FAIL


def test_verified_local_safe_zone_hash_is_checked(tmp_path):
    policy = _verified_safe_zone(tmp_path)
    registry = SafeZoneAssetRegistry((policy,))
    assert registry.verify_local_assets(tmp_path) == ()
    (tmp_path / policy.local_asset_path).write_bytes(b"mutated")
    assert registry.verify_local_assets(tmp_path) == (
        "VERIFIED_TEST_GEOMETRY:LOCAL_ASSET_HASH_MISMATCH",)


def test_media_budget_policy_is_a_seam_not_an_invented_owner_budget():
    policy = UNSET_OWNER_MEDIA_BUDGET_POLICY
    assert policy.max_base_cost is policy.max_worst_case_cost is None
    assert policy.approval_required_above is None
    assert policy.model_escalation_allowed is policy.audio_escalation_allowed is False


def test_current_real_surface_preconditions_fail_closed_before_live_plan():
    plan = _three_shot_plan()
    seed_by_id = {item.shot_id: item.seed_required for item in plan.seed_plan.shots}
    decisions = ProviderDecisionSet(
        authority_label="OFFLINE_FIXTURE_NOT_LIVE_AUTHORITY",
        shot_decisions=tuple(ShotMediaProviderDecision(
            shot_id=shot.shot_id, execution_mode="IMAGE_TO_VIDEO",
            video_surface_identity=VERTEX_VEO_31_IDENTITY,
            video_price_key=price_key(
                VERTEX_VEO_31_IDENTITY, MediaOutputModality.VIDEO,
                VideoAudioMode.VIDEO_ONLY, "720p"),
            duration_seconds=4, resolution="720p", audio_mode=VideoAudioMode.VIDEO_ONLY,
            seed_required=seed_by_id[shot.shot_id],
            seed_surface_identity=(AGENT_PLATFORM_FLASH_IMAGE_IDENTITY
                                   if seed_by_id[shot.shot_id] else None),
            seed_price_key=(price_key(
                AGENT_PLATFORM_FLASH_IMAGE_IDENTITY, MediaOutputModality.IMAGE_OUTPUT,
                VideoAudioMode.NOT_APPLICABLE, "1K")
                            if seed_by_id[shot.shot_id] else None),
            seed_resolution="1K" if seed_by_id[shot.shot_id] else "",
            seed_input_components=((
                PricedUnitComponent(
                    price_key=price_key(
                        AGENT_PLATFORM_FLASH_IMAGE_IDENTITY,
                        MediaOutputModality.TEXT_INPUT,
                        VideoAudioMode.NOT_APPLICABLE, "ANY"),
                    units_per_attempt=Decimal("100"), reason="offline prompt bound"),
                PricedUnitComponent(
                    price_key=price_key(
                        AGENT_PLATFORM_FLASH_IMAGE_IDENTITY,
                        MediaOutputModality.IMAGE_INPUT,
                        VideoAudioMode.NOT_APPLICABLE, "ANY"),
                    units_per_attempt=Decimal("1120"), reason="one reference image"),
            ) if seed_by_id[shot.shot_id] else ()))
            for shot in plan.shot_contracts),
        auxiliary_work=(), mandatory_categories=tuple(MediaCostCategory))
    envelope = CanonicalMediaCostPlanner().plan(plan, decisions, _budget())
    assert envelope.state == MediaCostState.COST_NOT_PROVABLE
    assert MediaCostCategory.VIDEO_GENERATION in envelope.unpriced_categories


def test_execution_and_delivery_readiness_remain_separate(tmp_path):
    plan = _three_shot_plan()
    envelope = _cost(plan, _decision_set(plan))
    configured = _verified_safe_zone(tmp_path)
    geometry = PlatformGeometryValidator().validate(
        canvas_width=1080, canvas_height=1920, overlays=(), policy=configured,
        locale_direction=LanguageDirection.LTR)
    report = evaluate_media_readiness(
        capability_proven=True, request_serialization_proven=True,
        cost_envelope=envelope, retry_budget=_budget(), safe_zone=configured,
        geometry_result=geometry, final_platform_requirements_proven=False)
    assert report.execution_readiness == ReadinessState.READY
    assert report.delivery_readiness == ReadinessState.NOT_READY
    assert report.provider_calls == report.database_reads == report.database_mutations == 0
    assert report.spend_usd == 0


def test_current_unconfigured_safe_zone_blocks_delivery_even_if_other_fixtures_pass():
    plan = _three_shot_plan()
    envelope = _cost(plan, _decision_set(plan))
    policy = UNCONFIGURED_SAFE_ZONE_REGISTRY.resolve(
        platform="INSTAGRAM_REELS", placement="REELS", aspect_ratio="9:16",
        language_direction=LanguageDirection.LTR)
    geometry = PlatformGeometryValidator().validate(
        canvas_width=1080, canvas_height=1920, overlays=(), policy=policy,
        locale_direction=LanguageDirection.LTR)
    report = evaluate_media_readiness(
        capability_proven=True, request_serialization_proven=True,
        cost_envelope=envelope, retry_budget=_budget(), safe_zone=policy,
        geometry_result=geometry, final_platform_requirements_proven=False)
    assert report.execution_readiness == ReadinessState.READY
    assert report.delivery_readiness == ReadinessState.NOT_READY
    assert "SAFE_ZONE_UNCONFIGURED" in report.blockers


def test_all_gate_fixtures_remain_explicitly_non_live():
    plan = _three_shot_plan()
    decisions = _decision_set(plan)
    assert "NOT_LIVE_PLAN" in decisions.authority_label
    assert decisions.provider_calls == 0
    assert plan.provider_calls == 0


def test_certification_artifact_bundle_is_valid_and_redacted():
    root = Path("artifacts/reel_media_preconditions")
    files = tuple(sorted(root.glob("*.json")))
    assert len(files) == 8
    documents = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in files}
    snapshots = documents["request_compatibility_snapshots.json"]
    assert all(stable_hash(item["snapshot"]) == item["serialization_hash"]
               for item in snapshots["cases"])
    assert snapshots["provider_calls"] == snapshots["network_calls"] == 0
    raw = json.dumps(documents, ensure_ascii=False)
    assert "SYNTHETIC_COST_EXAMPLE_NOT_LIVE_PLAN" in raw
    assert '"Buffalo_plan_ready": true' not in raw


def test_side_effect_audit_records_zero_calls_mutations_and_spend():
    audit = json.loads(Path(
        "artifacts/reel_media_preconditions/side_effect_audit.json").read_text(
            encoding="utf-8"))
    assert audit["generative_provider_calls"] == 0
    assert audit["database_reads"] == audit["database_mutations"] == 0
    assert audit["media_generated"] == audit["spend_usd"] == 0
