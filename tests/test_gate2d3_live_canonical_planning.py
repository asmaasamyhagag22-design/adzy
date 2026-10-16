from __future__ import annotations

import socket
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from reel.canonical_contract import (
    ClaimIntent,
    ClaimSurface,
    ClaimType,
    ClaimVerificationStatus,
    ReelCreativeContractCompiler,
)
from reel.canonical_creative import (
    ConceptDistinctnessState,
    CreativeDefect,
    FitnessState,
    Platform,
    ReferenceType,
    ShotExecutionDecision,
    ShotExecutionMode,
    ShotExecutionState,
    ShotModeRouter,
    TournamentDecision,
    UNCONFIGURED_SAFE_ZONES,
    assess_concept_distinctness,
    buffalo_fixture_concepts,
    buffalo_fixture_references,
    compile_blueprint,
    compile_canonical_creative_plan,
    create_creative_concept,
    evaluate_concept,
    platform_profile,
)
from reel.canonical_execution import (
    DefectType,
    PricingUnit,
    PricingVerificationStatus,
    ProviderBillingMode,
    ProviderSurface,
    RepairOwner,
    TextProviderPricingSnapshot,
    VERTEX_VEO_31,
)
from reel.canonical_live_planning import (
    BlindCriticBatch,
    CanonicalInputInspection,
    CapabilityRepairState,
    ConceptCritique,
    CreativeContextItem,
    CriticDimensions,
    CriticState,
    GoogleVertexStructuredTextProvider,
    LiveConceptBatch,
    LivePlanAudit,
    LivePlanState,
    LivePlanningError,
    LiveShotContract,
    LiveStructuredCallController,
    NextGateCostItem,
    NextGateCostState,
    PhoneAppSceneMode,
    PhysicalActionState,
    PlanningCostState,
    PlanningPurpose,
    ProvenanceClass,
    SafeAssetRecord,
    StructuredPlanningRequest,
    StructuredProviderResponse,
    TextModelConfiguration,
    TextModelStatus,
    VoiceoverChoice,
    audit_live_references,
    brand_swap_test,
    build_blind_critic_request,
    build_next_gate_cost_envelope,
    build_planning_cost_envelope,
    build_proposer_request,
    compile_live_audio_plan,
    create_creative_context_pack,
    decide_capability_aware_repair,
    generic_asset_sequence_defect,
    render_storyboard_preview,
    run_live_tournament,
    selective_repair_concept_ids,
)


BUFFALO_STRATEGY_ID = "52651d10-5fb2-46b9-89f0-bf0eec05e622"


@pytest.fixture(autouse=True)
def _no_real_providers(monkeypatch):
    for key in (
        "OPENAI_API_KEY", "SERPER_API_KEY", "SEARCHAPI_API_KEY", "GOOGLE_CSE_API_KEY",
        "GOOGLE_CSE_KEY", "APIFY_TOKEN", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "ELEVENLABS_API_KEY", "VERTEX_API_KEY", "VEO_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Gate 2D.3 offline tests attempted network I/O")

    monkeypatch.setattr(socket.socket, "connect", unexpected, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", unexpected, raising=False)


def _claim(claim_id: str, statement: str):
    return {
        "claim_id": claim_id, "statement": statement, "epistemic_type": "INFERENCE",
        "verification_status": "SUPPORTED", "supporting_evidence_ids": ["ev_" + claim_id],
        "limitations": [],
    }


def _contract():
    return ReelCreativeContractCompiler().compile({
        "strategy_version_id": BUFFALO_STRATEGY_ID,
        "run_kind": "strategy", "run_status": "succeeded",
        "run_brand_id": "brand-buffalo", "brand_id": "brand-buffalo",
        "is_current": True, "approval_state": "approved",
        "profile_snapshot_id": "profile-buffalo", "run_input_profile_id": "profile-buffalo",
        "profile": {
            "organization_name": "Buffalo Burger", "languages": ["en"],
            "existing_ctas": [{"value": "Start ordering"}],
            "tone_of_voice": {"value": "bright and direct"},
            "visual": {"palette_hex": ["#f5a623", "#111111"],
                       "logo_url": "https://example.test/logo.png"},
        },
        "requested_config": {"objective": {
            "objective_type": "CONVERSION",
            "business": {"description": "Increase first orders through the mobile app"},
            "exact_cta": "Start ordering",
        }},
        "output": {"canonical_strategy": {
            "decision": {
                "decision_id": "dec_buffalo_mobile_value",
                "selected_option_id": "pos_mobile_deals",
                "options": [{
                    "option_id": "pos_mobile_deals",
                    "target": "mobile app users looking for deals",
                    "frame_of_reference": "restaurant",
                    "consumer_value": "Convenient mobile app with deals and personalization",
                    "reason_to_believe": "",
                    "consumer_value_claim_ids": ["cl_value"],
                    "reason_to_believe_claim_ids": [],
                    "internal_supporting_claim_ids": ["cl_review", "cl_competitor"],
                    "differentiation_claim_ids": ["cl_competitor"],
                }],
            },
            "claims": [
                _claim("cl_value", "Convenient mobile app with deals and personalization"),
                _claim("cl_review", "The profile contains a 4.7 customer rating"),
                _claim("cl_competitor", "CTA count exceeds a peer comparison baseline"),
            ],
            "verification": {"status": "PASS_WITH_DISCLOSURE",
                             "eligible_for_approval": True},
        }},
    })


def _context(*, include_internal=True):
    contract = _contract()
    items = [
        CreativeContextItem(
            key="brand_name", value="Buffalo Burger",
            provenance_class=ProvenanceClass.RENDER_SAFE,
            source_id="profile-buffalo"),
        CreativeContextItem(
            key="offering_names", value=("Buffalo burger",),
            provenance_class=ProvenanceClass.RENDER_SAFE,
            source_id="profile-buffalo"),
        CreativeContextItem(
            key="product_reference_asset_ids", value=("asset-product-1",),
            provenance_class=ProvenanceClass.REFERENCE_ONLY,
            source_id="asset-ledger", description="owned product image"),
    ]
    if include_internal:
        items.append(CreativeContextItem(
            key="internal_evidence", value="4.7 rating and peer averages",
            provenance_class=ProvenanceClass.NOT_RENDERABLE,
            source_id="strategy-internal"))
    return create_creative_context_pack(
        strategy_version_id=contract.strategy_version_id,
        reel_contract_hash=contract.contract_hash, items=items)


def _concepts():
    return buffalo_fixture_concepts(_contract(), platform=Platform.INSTAGRAM_REELS)


def _rehash(concept, **updates):
    values = concept.model_dump(mode="python", exclude={"concept_id", "concept_fingerprint"})
    concept_id = updates.pop("concept_id", concept.concept_id)
    values.update(updates)
    return create_creative_concept(concept_id=concept_id, **values)


def _configuration(*, proven=True, expensive=False):
    pricing = None
    status = TextModelStatus.CONFIGURED_PRICE_UNVERIFIED
    if proven:
        rate = Decimal("1000") if expensive else Decimal("1")
        pricing = TextProviderPricingSnapshot(
            provider_name="fake-google", surface=ProviderSurface.FAKE_TEST,
            model_id="fake-structured-text", billing_mode=ProviderBillingMode.STANDARD,
            currency="USD", unit=PricingUnit.PER_1M_TOKENS,
            input_text_image_video_usd_per_million=rate,
            output_text_response_reasoning_usd_per_million=rate,
            cached_input_usd_per_million=rate,
            pricing_source_url="https://cloud.google.com/offline-test-price-ledger",
            pricing_verified_date="2026-08-23",
            pricing_status=PricingVerificationStatus.VERIFIED_OFFICIAL_SOURCE,
            pricing_version_id="test-v1")
        status = TextModelStatus.CONFIGURED_SUPPORTED
    return TextModelConfiguration(
        provider="fake-google", surface=ProviderSurface.FAKE_TEST,
        model_id="fake-structured-text", status=status,
        supports_structured_output=True, suitable_for_creative_reasoning=True,
        pricing=pricing)


class _FakeProvider:
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[StructuredPlanningRequest] = []

    def generate_structured(self, request, response_model):
        self.calls.append(request)
        payload = self.responses.pop(0)
        if isinstance(payload, BaseModel):
            payload = payload.model_dump(mode="python")
        return StructuredProviderResponse(
            payload=payload, provider_timestamp=datetime.now(timezone.utc),
            input_tokens=100, output_tokens=100, thinking_tokens=25,
            model_id="fake-structured-text", concise_rationale="external summary")


def _request(purpose=PlanningPurpose.CONCEPT_PROPOSER, retry_index=0):
    budget = {item.purpose: item for item in build_planning_cost_envelope(
        _configuration()).call_budgets}[purpose]
    return StructuredPlanningRequest(
        purpose=purpose, system_instruction="structured only", payload={"bounded": True},
        input_artifact_hashes=("artifact-hash",),
        maximum_output_tokens=budget.maximum_output_tokens,
        maximum_thinking_tokens=budget.maximum_thinking_tokens,
        retry_index=retry_index)


def _critic(*, strong_label="A", weak_label="B"):
    values = []
    for index, _concept in enumerate(_concepts()):
        label = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[index]
        state = CriticState.STRONG if label == strong_label else (
            CriticState.WEAK if label == weak_label else CriticState.ACCEPTABLE)
        values.append(ConceptCritique(
            anonymized_label=label,
            dimensions=CriticDimensions(**{field: state for field in
                                             CriticDimensions.model_fields}),
            strongest_creative_reason="clear progression",
            biggest_weakness="reference dependency",
            specific_repair_suggestion="make the first action more concrete"))
    return BlindCriticBatch(critiques=tuple(values), concise_rationale="qualitative only")


def _compiled_plan():
    contract = _contract()
    product, brand = buffalo_fixture_references()
    result = compile_canonical_creative_plan(
        contract, platform_profile(Platform.INSTAGRAM_REELS), _concepts(),
        product_reference=product, brand_reference=brand)
    assert result.plan is not None
    return result.plan


def _live_shot(shot=None, **updates):
    shot = shot or _compiled_plan().shot_contracts[0]
    values = dict(
        shot=shot,
        beginning_visual_state="A real hand rests beside a branded product and locked phone plate",
        ending_visual_state="The hand completes one tap while product stays fixed in frame",
        product_role="The real product anchors the app-choice action",
        physical_action_state=PhysicalActionState.PLAUSIBLE,
        action_bindings=("right hand -> phone", "product -> table",),
        phone_app_scene_mode=PhoneAppSceneMode.DETERMINISTIC_SCREEN_COMPOSITE_REQUIRED,
        generative_ui_typography_allowed=False,
        main_QA_risk="Hand-to-screen binding and protected product identity",
    )
    values.update(updates)
    return LiveShotContract(**values)


# 1
def test_live_proposer_cannot_receive_internal_evidence():
    contract = _contract()
    envelope = build_planning_cost_envelope(_configuration())
    request = build_proposer_request(
        contract=contract, context=_context(),
        profile=platform_profile(Platform.INSTAGRAM_REELS),
        budget=envelope.call_budgets[0])
    serialized = str(request.payload).casefold()
    assert "internal_supporting_evidence" not in serialized
    assert "peer averages" not in serialized and "4.7 rating" not in serialized


# 2
def test_creative_context_pack_render_safe_filtering():
    projected = _context().planner_projection()
    assert projected["render_safe"]["brand_name"] == "Buffalo Burger"
    assert projected["reference_only"][0]["asset_ids"] == ("asset-product-1",)
    assert "internal_evidence" not in projected["render_safe"]


# 3
def test_structured_schema_validation_rejects_free_or_invalid_payload():
    provider = _FakeProvider([{"markdown": "```json nope```"}])
    controller = LiveStructuredCallController(
        configuration=_configuration(),
        envelope=build_planning_cost_envelope(_configuration()), provider=provider)
    with pytest.raises(LivePlanningError, match="concepts") as error:
        controller.call(_request(), LiveConceptBatch)
    assert error.value.code == "LIVE_PROPOSER_SCHEMA_INVALID"
    assert controller.traces[0].structured_output_valid is False


# 4
def test_schema_retry_is_targeted_and_capped_at_one():
    provider = _FakeProvider([{}, {}])
    controller = LiveStructuredCallController(
        configuration=_configuration(),
        envelope=build_planning_cost_envelope(_configuration()), provider=provider)
    with pytest.raises(LivePlanningError) as error:
        controller.call(_request(), LiveConceptBatch, allow_schema_retry=True)
    assert error.value.code == "LIVE_PROPOSER_SCHEMA_INVALID"
    assert controller.provider_calls == 2
    assert provider.calls[1].retry_index == 1
    assert "targeted_schema_repair" in provider.calls[1].payload


# 5
def test_total_live_call_count_cannot_exceed_three():
    class Probe(BaseModel):
        model_config = ConfigDict(extra="forbid")
        ok: bool

    provider = _FakeProvider([{"ok": True}] * 3)
    controller = LiveStructuredCallController(
        configuration=_configuration(),
        envelope=build_planning_cost_envelope(_configuration()), provider=provider)
    for purpose in PlanningPurpose:
        assert controller.call(_request(purpose), Probe).ok
    with pytest.raises(LivePlanningError) as error:
        controller.call(_request(), Probe)
    assert error.value.code == "LIVE_CALL_LIMIT_EXCEEDED"
    assert controller.provider_calls == 3


# 6
def test_planning_cost_cap_is_enforced_before_call():
    configuration = _configuration(expensive=True)
    envelope = build_planning_cost_envelope(configuration)
    provider = _FakeProvider([])
    controller = LiveStructuredCallController(
        configuration=configuration, envelope=envelope, provider=provider)
    assert envelope.state == PlanningCostState.COST_CAP_EXCEEDED
    with pytest.raises(LivePlanningError):
        controller.call(_request(), LiveConceptBatch)
    assert provider.calls == []


# 7
def test_duplicate_live_concepts_are_repaired_selectively():
    concepts = list(_concepts())
    duplicate = _rehash(concepts[0], concept_id="duplicate-treatment")
    candidates = (concepts[0], duplicate, concepts[2])
    result = assess_concept_distinctness(candidates)
    assert result.state == ConceptDistinctnessState.NOT_DISTINCT
    assert selective_repair_concept_ids(candidates, result) == ("duplicate-treatment",)


# 8
def test_blind_critic_schema_cannot_authorize_claims():
    payload = _critic().model_dump(mode="python")
    payload["critiques"][0]["claim_authorization"] = "AUTHORIZED"
    with pytest.raises(ValidationError):
        BlindCriticBatch.model_validate(payload)


# 9
def test_blind_critic_schema_cannot_change_strategy():
    payload = _critic().model_dump(mode="python")
    payload["target"] = "everyone"
    with pytest.raises(ValidationError):
        BlindCriticBatch.model_validate(payload)


# 10
def test_deterministic_tournament_retains_strategy_and_claim_authority():
    concepts = list(_concepts())
    concepts[0] = _rehash(concepts[0], declared_CTA="Buy now")
    critic = _critic(strong_label="A", weak_label="B")
    result = run_live_tournament(
        contract=_contract(), profile=platform_profile(Platform.INSTAGRAM_REELS),
        concepts=concepts, critic=critic)
    assert result.selected_concept_id != concepts[0].concept_id
    bad_fitness = next(item for item in result.canonical_tournament.fitness
                       if item.concept_id == concepts[0].concept_id)
    assert bad_fitness.strategy_alignment.state == FitnessState.FAIL


# 11
def test_generic_brand_swap_concept_is_detected():
    generic = _rehash(
        _concepts()[0], creative_thesis="A quick restaurant ordering journey",
        hook_mechanism="POV phone tap", narrative_device="ordering journey",
        value_demonstration="Convenient mobile app with deals and personalization",
        product_role="a restaurant product closes the sequence",
        brand_entry="generic logo end card", visual_grammar="social vertical cuts",
        reveal_pattern="phone to product")
    result = brand_swap_test(generic, _context())
    assert not result.brand_specific
    assert result.defect.defect_type == DefectType.CONCEPT_TOO_GENERIC


# 12
def test_proofless_buffalo_blueprint_has_no_proof_beat():
    contract = _contract()
    blueprint = compile_blueprint(
        _concepts()[0], contract, platform_profile(Platform.INSTAGRAM_REELS))
    assert contract.proof_points == ()
    assert all(beat.role.value != "PROOF" for beat in blueprint.beat_sequence)


# 13
def test_visual_implication_goes_through_claim_firewall():
    concept = _concepts()[0]
    unsafe = ClaimIntent(
        surface=ClaimSurface.VISUAL_IMPLICATION,
        semantic_intent="A phone tap instantly teleports food to the user",
        supporting_allowed_claim_ids=(), claim_type=ClaimType.VISUAL,
        factual=True, expressive=False, exact_wording_required=False,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED)
    concept = _rehash(concept, visual_implication_intents=(unsafe,))
    fitness = evaluate_concept(
        concept, _contract(), platform_profile(Platform.INSTAGRAM_REELS))
    assert fitness.claim_safety.state == FitnessState.FAIL


# 14
def test_exact_cta_is_retained_in_live_blueprint():
    contract = _contract()
    blueprint = compile_blueprint(
        _concepts()[0], contract, platform_profile(Platform.INSTAGRAM_REELS))
    assert blueprint.CTA_policy.exact_text == "Start ordering"


# 15
def test_exact_app_ui_is_not_delegated_to_generative_video():
    with pytest.raises(ValidationError, match="exact app UI"):
        _live_shot(generative_ui_typography_allowed=True)


# 16
def test_missing_required_reference_reduces_execution_feasibility():
    shot = _compiled_plan().shot_contracts[0]
    result = audit_live_references(shot, assets=())
    assert result.execution_fitness_reduced == bool(shot.reference_requirements)
    if shot.reference_requirements:
        assert all(item.defect_type == DefectType.MISSING_REQUIRED_REFERENCE
                   for item in result.defects)


# 17
def test_unsupported_video_capability_is_not_invented():
    shot = _compiled_plan().shot_contracts[0]
    requirements = shot.provider_requirements.model_copy(update={
        "requires_reference_images": True,
    })
    shot = shot.model_copy(update={"provider_requirements": requirements})
    decision = ShotModeRouter((VERTEX_VEO_31,)).route(shot)
    assert decision.state != ShotExecutionState.ELIGIBLE
    assert not decision.eligible_provider_identities


# 18
def test_capability_aware_plan_repair_preserves_the_same_idea_when_possible():
    decision = ShotExecutionDecision(
        shot_id="shot-1", state=ShotExecutionState.UNSUPPORTED_REQUIRED_CAPABILITY,
        execution_mode=ShotExecutionMode.FIRST_LAST_FRAME,
        missing_capabilities=("first_last_frame",), explanation="unsupported")
    repair = decide_capability_aware_repair(
        _concepts()[0], (decision,), supported_reexpression_preserves_idea=True)
    blocked = decide_capability_aware_repair(
        _concepts()[0], (decision,), supported_reexpression_preserves_idea=False)
    assert repair.state == CapabilityRepairState.PLAN_REPAIR
    assert blocked.state == CapabilityRepairState.NO_AUTHORIZED_PROVIDER_FOR_CONCEPT


# 19
def test_optional_voiceover_does_not_become_mandatory():
    audio = compile_live_audio_plan(
        _contract(), choice=VoiceoverChoice.NO_VOICEOVER,
        music_direction="light rhythmic bed")
    assert audio.voiceover_script is None
    assert audio.choice == VoiceoverChoice.NO_VOICEOVER


# 20
def test_locale_uncertainty_does_not_infer_a_dialect():
    audio = compile_live_audio_plan(
        _contract(), choice=VoiceoverChoice.NO_VOICEOVER,
        music_direction="light rhythmic bed")
    assert audio.owner_language_choice_required
    assert audio.locale == ""
    assert audio.defects[0].defect_type == DefectType.LANGUAGE_VARIETY_NEEDS_OWNER_CHOICE


# 21
def test_storyboard_preview_is_derived_from_typed_artifacts():
    plan = _compiled_plan()
    live_shots = tuple(_live_shot(item) for item in plan.shot_contracts)
    assets = (SafeAssetRecord(
        asset_id="asset-product-1", source="profile asset ledger",
        asset_type=ReferenceType.PRODUCT_REFERENCE, description="owned product photo",
        source_hash="a" * 64, consumer_visible=True, provider_compatible=False,
        QA_only=False),)
    audits = tuple(audit_live_references(item, assets) for item in plan.shot_contracts)
    audio = compile_live_audio_plan(
        _contract(), choice=VoiceoverChoice.NO_VOICEOVER,
        music_direction="light rhythmic bed")
    preview = render_storyboard_preview(
        blueprint=plan.blueprint, live_shots=live_shots,
        overlay_plan=plan.overlay_plan,
        mode_decisions=plan.provider_eligibility_decisions,
        reference_audits=audits, audio_plan=audio)
    assert "TIME:" in preview and "WHAT WE SEE:" in preview
    assert "GENERATION MODE:" in preview
    assert plan.shot_contracts[0].shot_id in preview


# 22
def test_next_gate_cost_envelope_is_finite_only_when_every_surface_is_priced():
    priced = NextGateCostItem(
        category="SEED_IMAGE", provider="fake", surface=ProviderSurface.FAKE_TEST,
        model_id="image-test", base_units=Decimal("2"), worst_case_units=Decimal("4"),
        unit="CALL", unit_price_usd=Decimal("0.01"), pricing_source="test-ledger",
        pricing_version="v1")
    finite = build_next_gate_cost_envelope((priced,))
    unknown = build_next_gate_cost_envelope((priced.model_copy(update={
        "surface": ProviderSurface.VERTEX_AI, "model_id": "veo-3.1-generate-001",
        "unit_price_usd": None, "pricing_source": "", "pricing_version": ""}),))
    assert finite.state == NextGateCostState.FINITE
    assert finite.base_plan_cost_usd == Decimal("0.02")
    assert unknown.state == NextGateCostState.COST_NOT_PROVABLE
    assert unknown.worst_case_authorized_cost_usd is None


# 23
def test_gate_audit_forbids_image_video_audio_and_search_calls():
    with pytest.raises(ValidationError):
        _blocked_audit(media_provider_calls=1)
    with pytest.raises(ValidationError):
        _blocked_audit(search_or_grounding_calls=1)

    captured = {}

    class Models:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                parsed={"ok": True}, model_version="gemini-test",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=10, candidates_token_count=5,
                    thoughts_token_count=2))

    class Probe(BaseModel):
        ok: bool

    pricing = TextProviderPricingSnapshot(
        provider_name="google", surface=ProviderSurface.VERTEX_AI,
        model_id="gemini-test", billing_mode=ProviderBillingMode.STANDARD,
        currency="USD", unit=PricingUnit.PER_1M_TOKENS,
        input_text_image_video_usd_per_million=Decimal("1"),
        output_text_response_reasoning_usd_per_million=Decimal("1"),
        cached_input_usd_per_million=Decimal("0.1"),
        pricing_source_url="https://cloud.google.com/offline-versioned-test-ledger",
        pricing_verified_date="2026-08-23",
        pricing_status=PricingVerificationStatus.VERIFIED_OFFICIAL_SOURCE,
        pricing_version_id="v1")
    configuration = TextModelConfiguration(
        provider="google", surface=ProviderSurface.VERTEX_AI, model_id="gemini-test",
        status=TextModelStatus.CONFIGURED_SUPPORTED, supports_structured_output=True,
        suitable_for_creative_reasoning=True, configured_project="test-project",
        configured_location="us-central1", pricing=pricing)
    envelope = build_planning_cost_envelope(configuration)
    provider = GoogleVertexStructuredTextProvider(
        configuration, envelope, client=SimpleNamespace(models=Models()))
    response = provider.generate_structured(_request(), Probe)
    assert response.payload == {"ok": True}
    assert captured["config"].response_mime_type == "application/json"
    assert captured["config"].response_schema is Probe
    assert captured["config"].tools is None


# 24
def test_gate_audit_cannot_claim_execution_approval_was_consumed():
    with pytest.raises(ValidationError):
        _blocked_audit(approval_consumed=True)


# 25
def test_gate_audit_cannot_claim_a_db_schema_modification():
    with pytest.raises(ValidationError):
        _blocked_audit(DB_schema_modified=True)


def _blocked_audit(**updates):
    configuration = TextModelConfiguration(
        provider="google", surface=ProviderSurface.VERTEX_AI,
        model_id="gemini-2.5-flash",
        status=TextModelStatus.CONFIGURED_PRICE_UNVERIFIED,
        supports_structured_output=True, suitable_for_creative_reasoning=True,
        configured_project="generate-504115", configured_location="us-central1")
    values = dict(
        state=LivePlanState.LIVE_REEL_PLAN_BLOCKED,
        canonical_input=CanonicalInputInspection(
            requested_strategy_version_id=BUFFALO_STRATEGY_ID,
            real_strategy_loaded=False, source="runtime persistence inspection",
            blocker_codes=("CANONICAL_STRATEGY_UNAVAILABLE",)),
        text_model_configuration=configuration,
        planning_cost_envelope=build_planning_cost_envelope(configuration),
        blocker_codes=("CANONICAL_STRATEGY_UNAVAILABLE", "COST_NOT_PROVABLE"),
    )
    values.update(updates)
    return LivePlanAudit(**values)
