from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from reel.canonical_media_certification import (
    BillingCatalogSkuEvidence,
    BillingEvidenceAuthority,
    BillingModelMappingStatus,
    CANONICAL_QA_PROVIDER,
    CANONICAL_QA_TOKEN_ENVELOPES,
    CANONICAL_QA_TOTAL_WORST_CASE_USD,
    CANONICAL_VOICE_VERIFICATION_PLAN,
    CURRENT_INSTAGRAM_READINESS,
    CURRENT_VERTEX_VEO_SKUS,
    CertificationReadinessState,
    GATE_2D4C_SIDE_EFFECT_AUDIT,
    IntegrationCapabilityState,
    META_REELS_SAFE_ZONE_ASSET,
    PlatformAssetReviewStatus,
    PricingCertificationStatus,
    QAPurpose,
    SafeZoneAssetProvenance,
    SurfacePricingProvenance,
    TIKTOK_LTR_SAFE_ZONE_ASSET,
    TIKTOK_RTL_SAFE_ZONE_ASSET,
    VERTEX_GEMINI_25_FLASH_QA_PRICING,
    VERTEX_PRICING_REPAIR_RESULT,
    VERTEX_SURFACE_PRICING_PROVENANCE,
    VERTEX_VEO_31_CAPABILITY_RECONCILIATION,
    VERTEX_VEO_31_FAST_CAPABILITY_RECONCILIATION,
    VERTEX_VEO_31_LITE_PROFILE,
    VERTEX_VEO_31_MODEL_MAPPING,
    VERTEX_VEO_SKU_REGISTRY,
    calculate_QA_worst_case_cost,
    evaluate_media_certification_readiness,
    gate_2d4c_certification_bundle,
    require_input_within_provider_count,
)
from reel.canonical_media_preconditions import (
    DEFAULT_MEDIA_PRICING_REGISTRY,
    MediaBillingUnit,
    MediaOutputModality,
    MediaPreconditionError,
    ProductSurface,
    VERTEX_GEMINI_25_FLASH_QA_IDENTITY,
    VideoAudioMode,
    price_key,
    stable_hash,
)


def test_official_redirect_provenance_rejects_arbitrary_cross_surface_alias():
    values = VERTEX_SURFACE_PRICING_PROVENANCE.model_dump(
        mode="python", exclude={"evidence_hash"})
    values["referring_official_product"] = "Arbitrary Product"
    with pytest.raises(ValidationError, match="arbitrary page or product"):
        SurfacePricingProvenance(**values, evidence_hash=stable_hash(values))


def test_vertex_pricing_redirect_is_represented_explicitly():
    record = VERTEX_SURFACE_PRICING_PROVENANCE
    assert record.requested_official_url.endswith("/vertex-ai/generative-ai/pricing")
    assert record.final_redirect_url.endswith("/generative-ai/pricing")
    assert record.requested_official_url != record.final_redirect_url
    assert record.referring_official_product == "Vertex AI"


def test_billing_catalog_service_identity_is_preserved():
    assert VERTEX_SURFACE_PRICING_PROVENANCE.billing_catalog_service == "C7E2-9256-1C43"
    assert {item.service_id for item in CURRENT_VERTEX_VEO_SKUS} == {"C7E2-9256-1C43"}


def test_public_sku_price_read_preserves_display_unit_and_missing_catalog_fields():
    record = VERTEX_VEO_SKU_REGISTRY.resolve(
        service_id="C7E2-9256-1C43", sku_id="4D50-275D-FC3F")
    assert record is not None
    assert record.description == "Veo 3 Video Generation"
    assert record.unit_price == Decimal("0.20")
    assert record.display_unit == "count"
    assert record.usage_unit is record.base_unit is record.effective_time is None


def test_count_page_UI_cannot_override_catalog_usage_unit_to_second():
    values = CURRENT_VERTEX_VEO_SKUS[0].model_dump(mode="python")
    values["canonical_billing_unit"] = MediaBillingUnit.SECOND
    values["mapping_status"] = BillingModelMappingStatus.CERTIFIED
    values["usage_unit"] = "second"
    values["base_unit"] = "second"
    values["base_unit_conversion_factor"] = Decimal("1")
    with pytest.raises(ValidationError, match="public table display cannot certify SECOND"):
        BillingCatalogSkuEvidence(**values)


def test_wrong_sku_cannot_price_a_request():
    assert VERTEX_VEO_SKU_REGISTRY.resolve(
        service_id="C7E2-9256-1C43", sku_id="NOT-THE-REQUEST-SKU") is None
    assert VERTEX_VEO_SKU_REGISTRY.resolve(
        service_id="AEFD-7695-64FA", sku_id="4D50-275D-FC3F") is None


def test_model_to_sku_mapping_must_be_certified_for_cost():
    assert VERTEX_VEO_31_MODEL_MAPPING.status == \
        BillingModelMappingStatus.CORROBORATED_BUT_NOT_EXACT
    assert not any(item.canonical_price_certified for item in CURRENT_VERTEX_VEO_SKUS)
    assert VERTEX_PRICING_REPAIR_RESULT.state == PricingCertificationStatus.UNRESOLVED


@pytest.mark.parametrize("profile", [
    VERTEX_VEO_31_CAPABILITY_RECONCILIATION,
    VERTEX_VEO_31_FAST_CAPABILITY_RECONCILIATION,
])
def test_reference_and_extension_remain_disabled_while_unresolved(profile):
    assert profile.reference_images == \
        IntegrationCapabilityState.UNRESOLVED_FOR_CURRENT_GA_SURFACE
    assert profile.video_extension == \
        IntegrationCapabilityState.PREVIEW_HISTORY_PRESENT_BUT_NOT_AUTHORIZED
    assert profile.reference_images_execution_enabled is False
    assert profile.video_extension_execution_enabled is False


def test_preview_history_does_not_silently_become_GA_capability():
    profile = VERTEX_VEO_31_CAPABILITY_RECONCILIATION
    values = profile.model_dump(mode="python")
    values["video_extension"] = IntegrationCapabilityState.GA_SUPPORTED
    with pytest.raises(ValidationError, match="collapsed into GA"):
        type(profile)(**values)


def test_model_native_audio_capability_is_not_adzy_audio_policy():
    profile = VERTEX_VEO_31_CAPABILITY_RECONCILIATION
    assert profile.model_native_audio == \
        IntegrationCapabilityState.MODEL_CAPABILITY_SUPPORTED_POLICY_DISABLED
    assert profile.adzy_audio_execution_policy.value == "VIDEO_ONLY"
    assert profile.native_marketing_speech_enabled is False


def test_veo_lite_remains_preview_candidate_not_default():
    lite = VERTEX_VEO_31_LITE_PROFILE
    assert lite.identity.model_id == "veo-3.1-lite-generate-001"
    assert lite.lifecycle_state == IntegrationCapabilityState.PREVIEW_CANDIDATE
    assert lite.routing_authorized is lite.canonical_default is False
    assert lite.identity.pricing_version == "COST_NOT_PROVABLE"


def test_QA_provider_has_exact_vertex_surface_and_standard_billing():
    identity = CANONICAL_QA_PROVIDER.identity
    assert identity == VERTEX_GEMINI_25_FLASH_QA_IDENTITY
    assert identity.product_surface == ProductSurface.VERTEX_AI_GENERATIVE_AI
    assert identity.model_id == "gemini-2.5-flash"
    assert identity.billing_mode == "STANDARD"


def test_QA_token_bounds_are_finite_and_deliberately_bounded():
    assert {item.purpose for item in CANONICAL_QA_TOKEN_ENVELOPES} == set(QAPurpose)
    assert all(0 < item.max_input_tokens <= 32_768 for item in CANONICAL_QA_TOKEN_ENVELOPES)
    assert all(0 < item.maximum_calls <= 8 for item in CANONICAL_QA_TOKEN_ENVELOPES)
    assert all(item.provider_count_tokens_required_before_call
               for item in CANONICAL_QA_TOKEN_ENVELOPES)


def test_excessive_provider_token_count_blocks_before_QA_generation():
    envelope = next(item for item in CANONICAL_QA_TOKEN_ENVELOPES
                    if item.purpose == QAPurpose.SEED_QA)
    require_input_within_provider_count(
        envelope=envelope, actual_input_tokens=envelope.max_input_tokens)
    with pytest.raises(MediaPreconditionError) as caught:
        require_input_within_provider_count(
            envelope=envelope, actual_input_tokens=envelope.max_input_tokens + 1)
    assert caught.value.code == "QA_INPUT_TOKEN_BOUND_EXCEEDED"


def test_reasoning_output_is_included_in_QA_cost():
    envelope = next(item for item in CANONICAL_QA_TOKEN_ENVELOPES
                    if item.purpose == QAPurpose.FINAL_REEL_QA)
    cost = calculate_QA_worst_case_cost(envelope)
    visible_only = (Decimal(envelope.max_output_tokens) * Decimal("2.50")
                    / Decimal("1000000"))
    assert cost.output_and_reasoning_cost_usd > visible_only
    assert cost.worst_case_cost_usd == Decimal("0.0251904")


def test_audio_rate_does_not_reuse_generic_multimodal_input_price():
    pricing = VERTEX_GEMINI_25_FLASH_QA_PRICING
    assert pricing.audio_input_usd_per_million == Decimal("1.00")
    assert pricing.text_image_video_input_usd_per_million == Decimal("0.30")
    assert pricing.audio_input_usd_per_million != \
        pricing.text_image_video_input_usd_per_million


def test_voice_verification_is_finite_and_provider_neutral_in_role():
    plan = CANONICAL_VOICE_VERIFICATION_PLAN
    assert plan.provider_role == "VOICE_VERIFICATION"
    assert plan.current_implementation.model_id == "gemini-2.5-flash"
    assert plan.pricing_proven and not plan.provider_called
    assert plan.maximum_voice_duration_seconds == 60
    assert plan.maximum_verification_retries == 1
    cost = calculate_QA_worst_case_cost(plan.token_envelope)
    assert cost.worst_case_cost_usd == Decimal("0.031744")


def test_QA_lifecycle_requires_replacement_before_retirement():
    assert CANONICAL_QA_PROVIDER.retirement_date == "2026-10-16"
    assert CANONICAL_QA_PROVIDER.replacement_required_before == "2026-10-01"
    assert CANONICAL_QA_PROVIDER.contracts_provider_neutral


def test_QA_records_are_resolvable_only_by_exact_media_price_identity():
    audio_key = price_key(
        VERTEX_GEMINI_25_FLASH_QA_IDENTITY, MediaOutputModality.AUDIO_INPUT,
        VideoAudioMode.NOT_APPLICABLE, "AUDIO")
    audio = DEFAULT_MEDIA_PRICING_REGISTRY.resolve(audio_key)
    assert audio is not None and audio.unit_price == Decimal("1.00")
    wrong = audio_key.model_copy(update={"billing_mode": "FLEX_BATCH"})
    assert DEFAULT_MEDIA_PRICING_REGISTRY.resolve(wrong) is None


def test_meta_template_presence_does_not_auto_verify_geometry():
    asset = META_REELS_SAFE_ZONE_ASSET
    assert asset.review_status == PlatformAssetReviewStatus.PREVIEW_ONLY
    assert asset.deterministic_geometry_available is False
    assert asset.local_asset_path == asset.sha256 == ""


def test_instagram_execution_can_be_ready_while_delivery_is_not_ready():
    report = evaluate_media_certification_readiness(
        platform="INSTAGRAM_REELS", provider_request_compatible=True,
        used_capabilities_certified=True, video_pricing_certified=True,
        seed_pricing_available_or_deferred=True, QA_pricing_finite=True,
        retry_budget_finite=True, safe_zone_verified=False,
        compositor_geometry_verified=False, final_overlays_present=False,
        final_QA_passed=False, live_plan_present=False)
    assert report.execution_readiness == CertificationReadinessState.READY
    assert report.delivery_readiness == CertificationReadinessState.NOT_READY
    assert "SAFE_ZONE_NOT_VERIFIED" in report.blockers


def test_current_instagram_execution_is_blocked_by_vertex_price_not_safe_zone_requirement():
    report = CURRENT_INSTAGRAM_READINESS
    assert report.execution_readiness == CertificationReadinessState.NOT_READY
    assert "VERTEX_VIDEO_PRICING_NOT_CERTIFIED" in report.blockers
    # Safe zone is a delivery condition and appears after all six execution conditions.
    assert report.safe_zone_verified is False


def test_tiktok_LTR_and_arabic_RTL_assets_remain_separate():
    ltr, rtl = TIKTOK_LTR_SAFE_ZONE_ASSET, TIKTOK_RTL_SAFE_ZONE_ASSET
    assert ltr.asset_id != rtl.asset_id
    assert ltr.language_direction == "LTR"
    assert rtl.language_direction == "RTL_ARABIC"
    assert ltr.direct_asset_url != rtl.direct_asset_url
    assert ltr.review_status == rtl.review_status == PlatformAssetReviewStatus.PREVIEW_ONLY


def test_downloaded_platform_asset_requires_checksum_and_full_provenance():
    with pytest.raises(ValidationError, match="checksum/provenance"):
        SafeZoneAssetProvenance(
            asset_id="BROKEN", platform="TEST", placement="IN_FEED", aspect_ratio="9:16",
            language_direction="LTR", official_page_url="https://example.test/official",
            direct_asset_url="https://example.test/asset.zip",
            original_filename="asset.zip", content_type="application/zip",
            local_asset_path="assets/asset.zip",
            review_status=PlatformAssetReviewStatus.OFFICIAL_TEMPLATE_STORED,
            notes="missing retrieval timestamp and checksum")


def test_stored_platform_asset_checksum_is_verified(tmp_path):
    payload = b"review-only-safe-zone-template"
    path = tmp_path / "asset.zip"
    path.write_bytes(payload)
    digest = __import__("hashlib").sha256(payload).hexdigest()
    asset = SafeZoneAssetProvenance(
        asset_id="REVIEW_COPY", platform="TEST", placement="IN_FEED", aspect_ratio="9:16",
        language_direction="LTR", official_page_url="https://example.test/official",
        direct_asset_url="https://example.test/asset.zip",
        retrieved_timestamp="2026-08-24T00:00:00+02:00",
        original_filename="asset.zip", content_type="application/zip",
        local_asset_path="asset.zip", sha256=digest,
        review_status=PlatformAssetReviewStatus.OFFICIAL_TEMPLATE_STORED,
        notes="synthetic checksum behavior test")
    assert asset.verify_local_copy(tmp_path)
    path.write_bytes(b"changed")
    assert not asset.verify_local_copy(tmp_path)


def test_public_vertex_sku_crosscheck_values_and_4k_are_preserved():
    by_id = {item.sku_id: item for item in CURRENT_VERTEX_VEO_SKUS}
    assert by_id["4D50-275D-FC3F"].unit_price == Decimal("0.20")
    assert by_id["F64B-2A78-7BAE"].unit_price == Decimal("0.40")
    assert by_id["083C-91E7-7B9A"].unit_price == Decimal("0.25")
    assert by_id["3653-1168-5D76"].unit_price == Decimal("0.30")
    assert all(item.display_unit == "count" for item in by_id.values())


def test_vertex_lite_sku_is_not_borrowed_from_gemini_API_service():
    assert "NO_VERTEX_AI_LITE_SKU" in VERTEX_PRICING_REPAIR_RESULT.vertex_lite_sku_status
    assert "DIFFERENT SERVICE" in VERTEX_PRICING_REPAIR_RESULT.vertex_lite_sku_status


def test_all_four_QA_categories_have_finite_combined_ceiling():
    assert CANONICAL_QA_TOTAL_WORST_CASE_USD == Decimal("0.2695168")
    assert CANONICAL_QA_TOTAL_WORST_CASE_USD.is_finite()


def test_no_provider_generation_calls_were_made():
    audit = GATE_2D4C_SIDE_EFFECT_AUDIT
    assert audit["provider_generation_calls"] == 0
    assert audit["Veo_calls"] == audit["Gemini_QA_calls"] == 0
    assert audit["image_calls"] == audit["TTS_calls"] == 0


def test_media_spend_remains_exactly_zero():
    assert Decimal(GATE_2D4C_SIDE_EFFECT_AUDIT["spend_usd"]) == 0
    assert CURRENT_INSTAGRAM_READINESS.spend_usd == 0


def test_no_database_mutation_or_approval_consumption():
    audit = GATE_2D4C_SIDE_EFFECT_AUDIT
    assert audit["database_reads"] == audit["database_mutations"] == 0
    assert audit["execution_approval_consumed"] is False


def test_certification_bundle_is_json_serializable_and_fail_closed():
    raw = json.dumps(gate_2d4c_certification_bundle(), ensure_ascii=False)
    assert "OFFLINE_CERTIFICATION_REPAIR_NOT_LIVE_AUTHORITY" in raw
    assert "CORROBORATED_BUT_NOT_EXACT" in raw
    assert "VERTEX_VIDEO_PRICING_NOT_CERTIFIED" in raw
    assert '"provider_generation_calls": 0' in raw


def test_checked_in_artifact_bundle_matches_current_certification_truth():
    root = Path("artifacts/reel_media_certification_repair")
    files = tuple(sorted(root.glob("*.json")))
    assert len(files) == 8
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    raw = json.dumps(documents, ensure_ascii=False)
    assert "VERTEX_MEDIA_PRICE_STILL_UNRESOLVED" in raw
    assert "OFFLINE_CERTIFICATION_REPAIR_NOT_LIVE_AUTHORITY" in raw
    assert '"provider_generation_calls": 0' in raw
    assert '"spend_usd": "0"' in raw
