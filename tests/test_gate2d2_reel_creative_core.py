from __future__ import annotations

import os
import socket

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
    BeatRole,
    ConceptDistinctnessState,
    ConceptTournament,
    CreativeDefect,
    FitnessState,
    GeneratedTextCategory,
    GeneratedTextDisposition,
    GeneratedTextPolicy,
    GeneratedTextRule,
    GeometryState,
    PlanReadiness,
    Platform,
    ProductPresencePolicy,
    ProviderRequirements,
    QAApplicability,
    ReferenceAsset,
    ReferencePlan,
    ReferenceType,
    ReferenceUsageMode,
    SafeZoneGeometry,
    SafeZonePolicy,
    ShotExecutionMode,
    ShotExecutionState,
    ShotModeRouter,
    TournamentDecision,
    UNCONFIGURED_SAFE_ZONES,
    assess_concept_distinctness,
    buffalo_fixture_concepts,
    buffalo_fixture_references,
    compile_QA_requirements,
    compile_audio_plan,
    compile_blueprint,
    compile_canonical_creative_plan,
    compile_continuity_plan,
    compile_coverage_graph,
    compile_overlay_plan,
    compile_reference_and_seed_plans,
    compile_shot_contracts,
    compile_voiceover_script,
    create_creative_concept,
    creative_boundaries,
    evaluate_concept,
    platform_profile,
    targeted_repair_plan,
    validate_QA_requirements,
    validate_blueprint,
    validate_reference_plan,
    validate_shot_repairability,
)
from reel.canonical_execution import (
    DefectType,
    GEMINI_OMNI_FLASH_EXPERIMENTAL,
    ProviderAuthorizationStatus,
    ProviderSurface,
    RepairOwner,
    VERTEX_VEO_31,
    VideoProviderCapabilities,
)


BUFFALO_STRATEGY_ID = "52651d10-5fb2-46b9-89f0-bf0eec05e622"


@pytest.fixture(autouse=True)
def _canonical_reel_network_quarantine(monkeypatch):
    """Gate-local belt: credentials absent and every socket attempt is a test failure."""
    for key in (
        "SERPER_API_KEY", "SEARCHAPI_API_KEY", "GOOGLE_CSE_API_KEY", "GOOGLE_CSE_KEY",
        "APIFY_TOKEN", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY",
        "VERTEX_API_KEY", "VEO_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Gate 2D.2 attempted outbound network I/O")

    monkeypatch.setattr(socket.socket, "connect", unexpected, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", unexpected, raising=False)


def _claim(claim_id: str, statement: str, *, limitations=()):
    return {
        "claim_id": claim_id,
        "statement": statement,
        "epistemic_type": "INFERENCE",
        "verification_status": "SUPPORTED",
        "supporting_evidence_ids": ["ev_" + claim_id],
        "limitations": list(limitations),
    }


def _contract():
    context = {
        "strategy_version_id": BUFFALO_STRATEGY_ID,
        "run_kind": "strategy", "run_status": "succeeded",
        "run_brand_id": "brand-buffalo", "brand_id": "brand-buffalo",
        "is_current": True, "approval_state": "approved",
        "profile_snapshot_id": "profile-buffalo",
        "run_input_profile_id": "profile-buffalo",
        "profile": {
            "organization_name": "Buffalo Burger", "languages": ["en"],
            "existing_ctas": [{"value": "Start ordering"}, {"value": "View menu"}],
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
                "unresolved_unknowns": ["One complaint is not evidence of prevalence."],
                "options": [{
                    "option_id": "pos_mobile_deals",
                    "target": "mobile app users looking for deals",
                    "frame_of_reference": "restaurant",
                    "consumer_value": "Convenient mobile app with deals and personalization",
                    "reason_to_believe": "",
                    "consumer_value_claim_ids": ["cl_value"],
                    "reason_to_believe_claim_ids": [],
                    "differentiation_claim_ids": ["cl_competitor"],
                    "internal_supporting_claim_ids": [
                        "cl_target", "cl_frame", "cl_review", "cl_competitor", "cl_complaint"],
                }],
            },
            "claims": [
                _claim("cl_value", "Convenient mobile app with deals and personalization"),
                _claim("cl_target", "mobile app users looking for deals"),
                _claim("cl_frame", "restaurant"),
                _claim("cl_review", "The profile contains a 4.7 customer rating"),
                _claim("cl_competitor", "CTA count exceeds a competitor comparison baseline"),
                _claim("cl_complaint", "One customer complained about service at one branch",
                       limitations=("One observation; never generalize to prevalence.",)),
            ],
            "verification": {"status": "PASS_WITH_DISCLOSURE",
                             "eligible_for_approval": True},
        }},
    }
    return ReelCreativeContractCompiler().compile(context)


def _bundle(*, configured_zone=False):
    contract = _contract()
    profile = platform_profile(Platform.TIKTOK)
    concepts = buffalo_fixture_concepts(contract)
    product, brand = buffalo_fixture_references()
    zone = None
    if configured_zone:
        zone = SafeZonePolicy(
            policy_id=profile.safe_zone_policy_id, version="fixture-v1",
            geometry_state=GeometryState.CONFIGURED,
            geometry=SafeZoneGeometry(top=.1, right=.1, bottom=.15, left=.1),
            source_notes=("test fixture geometry",))
    result = compile_canonical_creative_plan(
        contract, profile, concepts, safe_zone=zone,
        product_reference=product, brand_reference=brand,
        finite_cost_envelope_known=configured_zone)
    assert result.plan is not None
    return contract, concepts, result


def _intent(text: str, *, surface=ClaimSurface.ON_SCREEN_COPY,
            claim_type=ClaimType.FACTUAL, claim_ids=()):
    return ClaimIntent(
        surface=surface, text=text, supporting_allowed_claim_ids=tuple(claim_ids),
        claim_type=claim_type, factual=True, expressive=False,
        exact_wording_required=False,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED)


def _rehash_concept(concept, **updates):
    values = concept.model_dump(mode="python", exclude={"concept_id", "concept_fingerprint"})
    concept_id = updates.pop("concept_id", concept.concept_id)
    values.update(updates)
    return create_creative_concept(concept_id=concept_id, **values)


def _fake_video_capabilities(**updates):
    values = dict(
        provider_name="fake", model_id="fake-video", surface=ProviderSurface.FAKE_TEST,
        authorization_status=ProviderAuthorizationStatus.AUTHORIZED_PROFILE,
        text_to_video=True, image_to_video=True, first_last_frame=True,
        reference_images=True, video_extension=False, conversational_editing=True,
        multimodal_video=True, native_audio=True, supported_aspect_ratios=("9:16",),
        supported_durations=(4, 6, 8), supported_resolutions=("720p", "1080p"),
        local_seed_supported=True, identity_control_mode="fixture", cost_known=False,
        capability_source="offline-fixture", capability_source_version="1")
    values.update(updates)
    return VideoProviderCapabilities(**values)


# Platform + authority -------------------------------------------------------------------------


def test_platform_profiles_are_typed_and_profile_specific():
    tiktok = platform_profile(Platform.TIKTOK)
    instagram = platform_profile(Platform.INSTAGRAM_REELS)
    youtube = platform_profile(Platform.YOUTUBE_SHORTS)
    assert tiktok.aspect_ratio == "9:16" and tiktok.proposition_window.deadline_seconds == 3
    assert instagram.proposition_window.deadline_seconds is None
    assert youtube.proposition_window.deadline_seconds is None
    assert len({item.profile_id for item in (tiktok, instagram, youtube)}) == 3


def test_safe_zone_unknown_remains_unknown_without_invented_coordinates():
    policy = UNCONFIGURED_SAFE_ZONES[Platform.TIKTOK]
    assert policy.geometry_state == GeometryState.UNCONFIGURED
    assert policy.geometry is None
    with pytest.raises(ValidationError):
        policy.model_copy(update={"geometry": SafeZoneGeometry(
            top=.1, right=.1, bottom=.1, left=.1)}).model_validate(
                policy.model_dump() | {"geometry": {"top": .1, "right": .1,
                                                     "bottom": .1, "left": .1}})


def test_creative_boundaries_exclude_internal_evidence():
    contract = _contract()
    boundary = creative_boundaries(contract)
    assert boundary.immutable_target == "mobile app users looking for deals"
    assert "internal_supporting_evidence" not in type(boundary).model_fields
    assert boundary.exact_cta == "Start ordering"


# Concept distinctness + tournament -------------------------------------------------------------


def test_concept_paraphrases_fail_substantive_distinctness():
    contract = _contract()
    base = buffalo_fixture_concepts(contract)[0]
    variants = tuple(base.model_copy(update={
        "concept_id": f"paraphrase_{index}",
        "creative_thesis": title,
    }) for index, title in enumerate((
        "Use app quickly", "Quick app ordering", "Fast order through app"), 1))
    result = assess_concept_distinctness(variants)
    assert result.state == ConceptDistinctnessState.NOT_DISTINCT
    assert result.defects[0].defect_type == DefectType.CREATIVE_CONCEPTS_NOT_DISTINCT
    tournament = ConceptTournament().run(
        contract, platform_profile(Platform.TIKTOK), variants)
    assert tournament.decision == TournamentDecision.NO_VIABLE_CONCEPT


def test_two_defensible_creative_treatments_are_valid():
    contract = _contract()
    concepts = buffalo_fixture_concepts(contract)[:2]
    assert assess_concept_distinctness(concepts).state == ConceptDistinctnessState.DISTINCT
    result = ConceptTournament().run(contract, platform_profile(Platform.TIKTOK), concepts)
    assert result.decision in {TournamentDecision.SELECTED,
                               TournamentDecision.TIE_REQUIRES_REVIEW}


def test_buffalo_tournament_uses_hard_gates_then_ordered_preferences():
    contract = _contract()
    result = ConceptTournament().run(
        contract, platform_profile(Platform.TIKTOK), buffalo_fixture_concepts(contract))
    assert result.decision == TournamentDecision.SELECTED
    assert result.selected_concept_id == "buffalo_product_first_demo"
    assert any("no weights" in line for line in result.explanation)


@pytest.mark.parametrize(("field", "value", "expected_defect"), [
    ("declared_target", "everyone who eats", DefectType.STRATEGY_DRIFT),
    ("preserved_consumer_value", "The fastest delivery", DefectType.STRATEGY_DRIFT),
    ("declared_CTA", "Order now", DefectType.CTA_MISMATCH),
])
def test_concept_cannot_rewrite_locked_strategy(field, value, expected_defect):
    contract = _contract()
    concept = _rehash_concept(buffalo_fixture_concepts(contract)[0], **{field: value})
    result = evaluate_concept(concept, contract, platform_profile(Platform.TIKTOK))
    assert result.strategy_alignment.state == FitnessState.FAIL
    assert expected_defect in {item.defect_type for item in result.defects}


def test_internal_evidence_cannot_enter_concept_copy():
    contract = _contract()
    concept = _rehash_concept(
        buffalo_fixture_concepts(contract)[0],
        claim_intents=(_intent("CTA count exceeds a competitor comparison baseline",
                               claim_ids=("cl_value",)),))
    result = evaluate_concept(concept, contract, platform_profile(Platform.TIKTOK))
    assert result.claim_safety.state == FitnessState.FAIL
    assert DefectType.INTERNAL_EVIDENCE_LEAK in {item.defect_type for item in result.defects}


def test_visual_implication_runs_through_claim_firewall():
    contract = _contract()
    intent = _intent(
        "before/after transformation without an approved factual claim",
        surface=ClaimSurface.VISUAL_IMPLICATION, claim_type=ClaimType.VISUAL,
        claim_ids=("cl_value",))
    concept = _rehash_concept(
        buffalo_fixture_concepts(contract)[0], visual_implication_intents=(intent,))
    result = evaluate_concept(concept, contract, platform_profile(Platform.TIKTOK))
    assert DefectType.VISUAL_METAPHOR_CONFLICTS_WITH_TRUTH in {
        item.defect_type for item in result.defects}


# Blueprint, coverage, and shots ---------------------------------------------------------------


def test_proofless_contract_cannot_create_proof_beat():
    contract = _contract()
    concept = _rehash_concept(
        buffalo_fixture_concepts(contract)[0],
        planned_beat_roles=(BeatRole.HOOK, BeatRole.PROOF, BeatRole.CTA))
    with pytest.raises(Exception) as caught:
        compile_blueprint(concept, contract, platform_profile(Platform.TIKTOK))
    assert getattr(caught.value, "code", "") == "NO_APPROVED_CONSUMER_PROOF"


def test_tiktok_late_proposition_is_detected_but_other_profiles_do_not_invent_deadline():
    contract = _contract()
    concept = buffalo_fixture_concepts(contract)[2]
    tiktok = platform_profile(Platform.TIKTOK)
    blueprint = compile_blueprint(concept, contract, tiktok)
    beats = tuple(item.model_copy(update={
        "communicates_proposition": item.role == BeatRole.VALUE_DEMONSTRATION,
        "start_seconds": 5.0 if item.role == BeatRole.VALUE_DEMONSTRATION else item.start_seconds,
    }) for item in blueprint.beat_sequence)
    late = blueprint.model_copy(update={"beat_sequence": beats})
    assert DefectType.PROPOSITION_TOO_LATE in {
        item.defect_type for item in validate_blueprint(late, contract, tiktok)}
    instagram = platform_profile(Platform.INSTAGRAM_REELS)
    instagram_blueprint = late.model_copy(update={"proposition_deadline": None})
    assert DefectType.PROPOSITION_TOO_LATE not in {
        item.defect_type for item in validate_blueprint(
            instagram_blueprint, contract, instagram)}


def test_coverage_graph_has_primary_and_backup_hook_coverage():
    contract = _contract()
    concept = buffalo_fixture_concepts(contract)[2]
    blueprint = compile_blueprint(concept, contract, platform_profile(Platform.TIKTOK))
    graph = compile_coverage_graph(blueprint, concept)
    hook_nodes = [item for item in graph.nodes if item.beat_id == blueprint.beat_sequence[0].beat_id]
    assert {item.primary_or_backup.value for item in hook_nodes} == {"PRIMARY", "BACKUP"}
    assert any(edge.relationship == "BACKUP_ALTERNATIVE" for edge in graph.edges)


def test_shot_contract_hash_is_deterministic_and_is_not_a_provider_prompt():
    contract = _contract()
    concept = buffalo_fixture_concepts(contract)[2]
    profile = platform_profile(Platform.TIKTOK)
    blueprint = compile_blueprint(concept, contract, profile)
    graph = compile_coverage_graph(blueprint, concept)
    first = compile_shot_contracts(graph, blueprint, concept, profile)
    second = compile_shot_contracts(graph, blueprint, concept, profile)
    assert [item.shot_hash for item in first] == [item.shot_hash for item in second]
    assert "prompt" not in type(first[0]).model_fields


# Text, references, image seam, audio, overlay, continuity --------------------------------------


def test_marketing_copy_is_forbidden_in_generated_video():
    rule = CANONICAL_GENERATED_TEXT_POLICY.rule_for(GeneratedTextCategory.MARKETING_COPY)
    assert rule.disposition == GeneratedTextDisposition.NEVER_RENDER_IN_GENERATIVE_VIDEO
    bad_rules = tuple(
        item.model_copy(update={"disposition":
            GeneratedTextDisposition.MAY_EXIST_IF_IDENTITY_FIDELITY_VERIFIED})
        if item.category == GeneratedTextCategory.MARKETING_COPY else item
        for item in CANONICAL_GENERATED_TEXT_POLICY.rules)
    with pytest.raises(ValidationError):
        GeneratedTextPolicy(policy_id="unsafe", rules=bad_rules)


def test_protected_brand_text_has_a_separate_verifiable_policy():
    rule = CANONICAL_GENERATED_TEXT_POLICY.rule_for(
        GeneratedTextCategory.PROTECTED_BRAND_TEXT)
    assert rule.disposition == \
        GeneratedTextDisposition.MAY_EXIST_IF_IDENTITY_FIDELITY_VERIFIED
    assert rule.disposition != CANONICAL_GENERATED_TEXT_POLICY.rule_for(
        GeneratedTextCategory.MARKETING_COPY).disposition


def test_reference_plan_cannot_claim_unsupported_vertex_provider_reference():
    reference = ReferenceAsset(
        reference_id="product", reference_type=ReferenceType.PRODUCT_REFERENCE,
        usage_modes=(ReferenceUsageMode.PROVIDER_REFERENCE,), source_hash="sha",
        source_note="fixture")
    plan = ReferencePlan(shot_id="shot", references=(reference,),
                         provider_attachment_claimed=True)
    with pytest.raises(Exception) as caught:
        validate_reference_plan(plan, VERTEX_VEO_31)
    assert getattr(caught.value, "code", "") == "UNSUPPORTED_REQUIRED_CAPABILITY"


def test_vertex_reference_is_downgraded_truthfully_to_seed_and_QA_modes():
    contract, concepts, compiled = _bundle()
    del contract, concepts
    for plan in compiled.plan.reference_plans:
        assert not plan.provider_attachment_claimed
        assert all(ReferenceUsageMode.PROVIDER_REFERENCE not in item.usage_modes
                   for item in plan.references)


def test_deprecated_preview_image_model_is_not_canonical_default():
    defaults = [item.model_id for item in IMAGE_PROVIDER_CAPABILITIES if item.canonical_default]
    assert defaults == ["gemini-3.1-flash-image"]
    preview = next(item for item in IMAGE_PROVIDER_CAPABILITIES
                   if item.model_id == "gemini-3-pro-image-preview")
    assert preview.canonical_default is False


def test_audio_plan_separates_exact_marketing_VO_and_exact_CTA():
    contract = _contract()
    concept = buffalo_fixture_concepts(contract)[2]
    audio = compile_audio_plan(contract, concept)
    script = compile_voiceover_script(contract)
    assert audio.marketing_voiceover.exact_approved_artifact_required is True
    assert audio.environment_audio.category.value == "ENVIRONMENT_AUDIO"
    assert audio.SFX.category.value == "SFX"
    assert script.CTA_line == "Start ordering"
    assert script.ordered_lines[-1].text == "Start ordering"
    assert not any("best" in item.text.casefold() for item in script.ordered_lines)


def test_overlay_has_exact_CTA_but_no_invented_safe_zone_geometry():
    contract = _contract()
    concept = buffalo_fixture_concepts(contract)[2]
    blueprint = compile_blueprint(concept, contract, platform_profile(Platform.TIKTOK))
    overlay = compile_overlay_plan(
        contract, blueprint, UNCONFIGURED_SAFE_ZONES[Platform.TIKTOK])
    assert overlay.items[0].text == "Start ordering"
    assert overlay.items[0].exact_geometry is None
    assert overlay.geometry_state == GeometryState.UNCONFIGURED


def test_human_QA_is_mandatory_when_hands_or_actor_appear():
    contract = _contract()
    concept = buffalo_fixture_concepts(contract)[2]
    audio = compile_audio_plan(contract, concept)
    qa = compile_QA_requirements(concept, audio)
    assert qa.for_dimension("human_fidelity").applicability == QAApplicability.MANDATORY
    no_actor = buffalo_fixture_concepts(contract)[1]
    qa_no_actor = compile_QA_requirements(no_actor, compile_audio_plan(contract, no_actor))
    assert qa_no_actor.for_dimension("human_fidelity").applicability == \
        QAApplicability.NOT_APPLICABLE


def test_continuity_plan_declares_QA_not_textual_guarantees():
    _contract_value, _concepts, result = _bundle()
    assert result.plan.continuity_plan.later_QA_required is True
    assert all(item.textual_hints_are_not_guarantees
               for item in result.plan.continuity_plan.groups)


# Capability router + execution plan ------------------------------------------------------------


def test_image_to_video_routing_works_when_capability_exists():
    _contract_value, _concepts, result = _bundle()
    shot = next(item for item in result.plan.shot_contracts
                if item.provider_requirements.requires_image_to_video
                and not item.provider_requirements.requires_first_last_frame)
    decision = ShotModeRouter((VERTEX_VEO_31,)).route(shot)
    assert decision.state == ShotExecutionState.ELIGIBLE
    assert decision.execution_mode == ShotExecutionMode.IMAGE_TO_VIDEO


def test_first_last_routing_requires_actual_surface_capability():
    _contract_value, _concepts, result = _bundle()
    shot = next(item for item in result.plan.shot_contracts
                if item.provider_requirements.requires_first_last_frame)
    assert ShotModeRouter((VERTEX_VEO_31,)).route(shot).state == ShotExecutionState.ELIGIBLE
    incapable = _fake_video_capabilities(first_last_frame=False)
    blocked = ShotModeRouter((incapable,)).route(shot)
    assert blocked.state == ShotExecutionState.UNSUPPORTED_REQUIRED_CAPABILITY
    assert "first_last_frame" in blocked.missing_capabilities


def test_no_authorized_provider_yields_typed_block_and_omni_is_not_selected():
    _contract_value, _concepts, result = _bundle()
    shot = result.plan.shot_contracts[0]
    experimental = _fake_video_capabilities(
        provider_name="google", model_id="experimental-fixture",
        authorization_status=ProviderAuthorizationStatus.EXPERIMENTAL_NOT_YET_AUTHORIZED)
    decision = ShotModeRouter((experimental,)).route(shot)
    assert decision.state == ShotExecutionState.NO_AUTHORIZED_PROVIDER
    assert decision.eligible_provider_identities == ()
    assert decision.would_be_capable_if_authorized
    assert all("gemini-omni-flash-preview" not in identity
               for item in result.plan.provider_eligibility_decisions
               for identity in item.eligible_provider_identities)
    assert GEMINI_OMNI_FLASH_EXPERIMENTAL.authorization_status == \
        ProviderAuthorizationStatus.EXPERIMENTAL_NOT_YET_AUTHORIZED


def test_local_seed_requirement_blocks_provider_that_cannot_accept_it():
    _contract_value, _concepts, result = _bundle()
    shot = next(item for item in result.plan.shot_contracts
                if item.provider_requirements.local_seed_input_required)
    incapable = _fake_video_capabilities(local_seed_supported=False)
    decision = ShotModeRouter((incapable,)).route(shot)
    assert decision.state == ShotExecutionState.UNSUPPORTED_REQUIRED_CAPABILITY
    assert "local_seed_supported" in decision.missing_capabilities


def test_execution_plan_hash_is_deterministic_and_has_zero_calls():
    contract = _contract()
    profile = platform_profile(Platform.TIKTOK)
    concepts = buffalo_fixture_concepts(contract)
    product, brand = buffalo_fixture_references()
    first = compile_canonical_creative_plan(
        contract, profile, concepts, product_reference=product, brand_reference=brand)
    second = compile_canonical_creative_plan(
        contract, profile, concepts, product_reference=product, brand_reference=brand)
    assert first.plan.plan_hash == second.plan.plan_hash
    assert first.plan.provider_calls == 0 == second.provider_calls
    assert first.billable is False


def test_plan_is_creatively_valid_but_discloses_unknown_safe_zone():
    _contract_value, _concepts, result = _bundle()
    assert result.plan.creative_valid is True
    assert result.readiness == PlanReadiness.SAFE_ZONE_CONFIGURATION_REQUIRED
    assert result.plan.executable_now is False


def test_configured_safe_zone_advances_same_architecture_to_plan_ready():
    _contract_value, _concepts, result = _bundle(configured_zone=True)
    assert result.readiness == PlanReadiness.PLAN_READY
    assert result.plan.executable_now is False  # approval is deliberately not consumed in 2D.2
    assert all(item.state == ShotExecutionState.ELIGIBLE
               for item in result.plan.provider_eligibility_decisions)


def test_planned_paid_categories_are_declared_without_cost_invention():
    _contract_value, _concepts, result = _bundle()
    assert {item.value for item in result.plan.planned_paid_categories} == {
        "VIDEO", "SEED_IMAGE", "TTS", "SFX"}
    assert result.plan.cost_state == "NOT_ASSESSED_NO_PRICING_IN_GATE_2D2"


def test_modular_shot_defect_targets_only_the_affected_shot():
    _contract_value, concepts, result = _bundle()
    shot = result.plan.shot_contracts[0]
    defect = CreativeDefect(
        defect_type=DefectType.UNEXECUTABLE_SHOT_PLAN,
        repair_owner=RepairOwner.SHOT, shot_id=shot.shot_id,
        detail="fixture failure")
    repair = targeted_repair_plan(defect)
    assert repair.target_ids == (shot.shot_id,)
    assert repair.repair_owner == RepairOwner.SHOT
    assert repair.invalidates_strategy is False
    assert concepts[2].repairability_intent.value == "MODULAR_REPAIRABLE"


def test_provider_requirements_are_metadata_not_final_generation_prompts():
    _contract_value, _concepts, result = _bundle()
    requirements = result.plan.provider_requirements[0]
    assert "prompt" not in type(requirements).model_fields
    assert requirements.aspect_ratio == "9:16"
    assert set(requirements.allowed_durations) == {4, 6, 8}


def test_fixture_proposer_and_planner_perform_no_provider_or_network_calls():
    from reel.canonical_creative import FixtureConceptProposer

    contract = _contract()
    profile = platform_profile(Platform.TIKTOK)
    proposer = FixtureConceptProposer(buffalo_fixture_concepts(contract))
    proposals = proposer.propose(contract.planner_authority(), profile)
    product, brand = buffalo_fixture_references()
    result = compile_canonical_creative_plan(
        contract, profile, proposals, product_reference=product, brand_reference=brand)
    assert proposer.calls == 1
    assert result.provider_calls == 0 and result.plan.provider_calls == 0
    assert os.environ["BASEERA_NO_SPEND"] == "1"


# Adversarial benchmark: 20 specified attacks --------------------------------------------------


def test_adversarial_01_three_paraphrases_get_smallest_concept_owner():
    contract = _contract()
    base = buffalo_fixture_concepts(contract)[0]
    concepts = tuple(base.model_copy(update={"concept_id": f"same_{i}"}) for i in range(3))
    result = assess_concept_distinctness(concepts)
    assert result.defects[0].repair_owner == RepairOwner.CONCEPT


@pytest.mark.parametrize("text", ["Get a guaranteed 50% discount", "The best burger"])
def test_adversarial_02_03_unsupported_discount_and_best_claims(text):
    contract = _contract()
    concept = _rehash_concept(
        buffalo_fixture_concepts(contract)[0], claim_intents=(_intent(text),))
    result = evaluate_concept(concept, contract, platform_profile(Platform.TIKTOK))
    assert DefectType.UNSUPPORTED_CLAIM in {item.defect_type for item in result.defects}
    assert next(item for item in result.defects
                if item.defect_type == DefectType.UNSUPPORTED_CLAIM).repair_owner == \
        RepairOwner.CONTRACT


@pytest.mark.parametrize("text", [
    "CTA count exceeds a competitor comparison baseline",
    "One customer complained about service at one branch, therefore complaints are common",
])
def test_adversarial_04_05_internal_metric_or_complaint_cannot_be_generalized(text):
    contract = _contract()
    concept = _rehash_concept(
        buffalo_fixture_concepts(contract)[0],
        claim_intents=(_intent(text, claim_ids=("cl_value",)),))
    result = evaluate_concept(concept, contract, platform_profile(Platform.TIKTOK))
    assert DefectType.INTERNAL_EVIDENCE_LEAK in {item.defect_type for item in result.defects}


def test_adversarial_06_proof_beat_without_proof_is_contract_block():
    test_proofless_contract_cannot_create_proof_beat()


def test_adversarial_07_product_first_appearance_after_hook_window():
    contract = _contract()
    profile = platform_profile(Platform.TIKTOK)
    concept = buffalo_fixture_concepts(contract)[2]
    blueprint = compile_blueprint(concept, contract, profile)
    beats = tuple(item.model_copy(update={
        "product_visible": item.start_seconds >= 8}) for item in blueprint.beat_sequence)
    late = blueprint.model_copy(update={"beat_sequence": beats})
    defects = validate_blueprint(late, contract, profile)
    match = next(item for item in defects
                 if item.defect_type == DefectType.PRODUCT_APPEARS_TOO_LATE)
    assert match.repair_owner == RepairOwner.ARC


def test_adversarial_08_tiktok_late_proposition_is_arc_repair():
    contract = _contract()
    profile = platform_profile(Platform.TIKTOK)
    concept = buffalo_fixture_concepts(contract)[2]
    blueprint = compile_blueprint(concept, contract, profile)
    beats = tuple(item.model_copy(update={
        "communicates_proposition": item.start_seconds >= 8})
        for item in blueprint.beat_sequence)
    defects = validate_blueprint(
        blueprint.model_copy(update={"beat_sequence": beats}), contract, profile)
    match = next(item for item in defects
                 if item.defect_type == DefectType.PROPOSITION_TOO_LATE)
    assert match.repair_owner == RepairOwner.ARC


def test_adversarial_09_actor_cannot_mark_human_fidelity_not_applicable():
    contract = _contract()
    concept = buffalo_fixture_concepts(contract)[2]
    qa = compile_QA_requirements(concept, compile_audio_plan(contract, concept))
    requirements = tuple(item.model_copy(update={
        "applicability": QAApplicability.NOT_APPLICABLE})
        if item.dimension == "human_fidelity" else item for item in qa.requirements)
    defects = validate_QA_requirements(
        concept, qa.model_copy(update={"requirements": requirements}))
    assert defects[0].defect_type == DefectType.TERMINAL_VERIFICATION_INCOMPLETE


def test_adversarial_10_unsupported_provider_reference_is_blocked():
    test_reference_plan_cannot_claim_unsupported_vertex_provider_reference()


def test_adversarial_11_local_seed_provider_mismatch_is_shot_block():
    test_local_seed_requirement_blocks_provider_that_cannot_accept_it()


def test_adversarial_12_marketing_text_cannot_be_delegated_to_video_model():
    test_marketing_copy_is_forbidden_in_generated_video()


def test_adversarial_13_rewritten_CTA_is_contract_repair():
    contract = _contract()
    concept = _rehash_concept(
        buffalo_fixture_concepts(contract)[0], declared_CTA="Order immediately")
    result = evaluate_concept(concept, contract, platform_profile(Platform.TIKTOK))
    defect = next(item for item in result.defects if item.defect_type == DefectType.CTA_MISMATCH)
    assert defect.repair_owner == RepairOwner.CONTRACT


def test_adversarial_14_same_visual_for_every_beat_is_arc_repair():
    contract = _contract()
    profile = platform_profile(Platform.TIKTOK)
    concept = buffalo_fixture_concepts(contract)[2]
    blueprint = compile_blueprint(concept, contract, profile)
    beats = tuple(item.model_copy(update={"visual_treatment": "same locked shot"})
                  for item in blueprint.beat_sequence)
    defects = validate_blueprint(
        blueprint.model_copy(update={"beat_sequence": beats}), contract, profile)
    defect = next(item for item in defects if item.defect_type == DefectType.SHOT_REDUNDANCY)
    assert defect.repair_owner == RepairOwner.ARC


def test_adversarial_15_product_disappears_after_opening():
    contract = _contract()
    profile = platform_profile(Platform.TIKTOK)
    concept = _rehash_concept(
        buffalo_fixture_concepts(contract)[2],
        product_presence_policy=ProductPresencePolicy.CONTINUOUS_WHERE_PLAUSIBLE)
    blueprint = compile_blueprint(concept, contract, profile)
    beats = tuple(item.model_copy(update={"product_visible": index == 0})
                  for index, item in enumerate(blueprint.beat_sequence))
    defects = validate_blueprint(
        blueprint.model_copy(update={"beat_sequence": beats}), contract, profile)
    assert DefectType.VALUE_NOT_DEMONSTRATED in {item.defect_type for item in defects}


def test_adversarial_16_before_after_superiority_implication():
    contract = _contract()
    intent = _intent(
        "before/after transformation without an approved factual claim",
        surface=ClaimSurface.VISUAL_IMPLICATION, claim_type=ClaimType.VISUAL,
        claim_ids=("cl_value",))
    concept = _rehash_concept(
        buffalo_fixture_concepts(contract)[0], visual_implication_intents=(intent,))
    defects = evaluate_concept(
        concept, contract, platform_profile(Platform.TIKTOK)).defects
    match = next(item for item in defects
                 if item.defect_type == DefectType.VISUAL_METAPHOR_CONFLICTS_WITH_TRUTH)
    assert match.repair_owner == RepairOwner.CONCEPT


def test_adversarial_17_nonisolated_shot_in_modular_plan_targets_one_shot():
    _contract_value, concepts, result = _bundle()
    shot = result.plan.shot_contracts[0]
    broken = shot.model_copy(update={
        "repairability": shot.repairability.model_copy(update={"can_regenerate_alone": False})})
    defects = validate_shot_repairability(broken, concepts[2])
    assert defects[0].defect_type == DefectType.UNEXECUTABLE_SHOT_PLAN
    assert defects[0].shot_id == shot.shot_id


def test_adversarial_18_unconfigured_safe_zone_rejects_invented_geometry():
    with pytest.raises(ValidationError):
        SafeZonePolicy(
            policy_id="bad", version="1", geometry_state=GeometryState.UNCONFIGURED,
            geometry=SafeZoneGeometry(top=.1, right=.1, bottom=.1, left=.1))


def test_adversarial_19_strategically_correct_but_visually_unexecutable():
    contract = _contract()
    concept = _rehash_concept(
        buffalo_fixture_concepts(contract)[0],
        required_capabilities=("VIDEO_EXTENSION", "REFERENCE_IMAGES"))
    result = evaluate_concept(
        concept, contract, platform_profile(Platform.TIKTOK),
        provider_registry=(VERTEX_VEO_31, GEMINI_OMNI_FLASH_EXPERIMENTAL))
    assert result.strategy_alignment.state == FitnessState.PASS
    assert result.visual_feasibility.state == FitnessState.FAIL
    assert DefectType.UNEXECUTABLE_SHOT_PLAN in {item.defect_type for item in result.defects}


def test_adversarial_20_beautiful_concept_with_strategy_drift_fails_stage_A():
    contract = _contract()
    concepts = list(buffalo_fixture_concepts(contract))
    concepts[2] = _rehash_concept(concepts[2], declared_target="premium fine-dining patrons")
    result = ConceptTournament().run(contract, platform_profile(Platform.TIKTOK), concepts)
    fitness = next(item for item in result.fitness
                   if item.concept_id == concepts[2].concept_id)
    assert fitness.strategy_alignment.state == FitnessState.FAIL
    assert concepts[2].concept_id in result.discarded_concept_ids
