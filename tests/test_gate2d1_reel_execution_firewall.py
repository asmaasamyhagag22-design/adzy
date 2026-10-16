from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from reel.canonical_contract import (
    ClaimAuthorizationState,
    ClaimIntent,
    ClaimSurface,
    ClaimType,
    ClaimVerificationStatus,
    ReelContractError,
    ReelCreativeContractCompiler,
    authorize_claim,
)
from reel.canonical_execution import (
    ApprovalUseRegistry,
    AuthorizedCostTracker,
    CanonicalArtifactCandidate,
    CanonicalProviderConstraints,
    CanonicalReelExecutionError,
    CanonicalReelExecutionPreferences,
    FinalReelVerdict,
    GEMINI_API_VEO_PRICING_2026_08_23,
    GEMINI_OMNI_FLASH_EXPERIMENTAL,
    GeneratedMode,
    PaidProviderCategory,
    PlannedProviderCost,
    ProviderAuthorizationStatus,
    ProviderPricing,
    ProviderSelection,
    ProviderSurface,
    ReelExecutionState,
    ReelExecutionStateRecord,
    RequiredVideoCapabilities,
    TerminalVerdict,
    VERTEX_VEO_31,
    VerdictDimension,
    VideoProviderCapabilities,
    begin_authorized_execution,
    build_cost_envelope,
    build_execution_plan,
    execute_authorized_paid_boundary,
    issue_execution_approval,
    prepare_canonical_reel,
    require_provable_provider_cost,
    select_canonical_provider,
    terminal_delivery,
    validate_constraint_preserving_retry,
    validate_execution_approval,
)


BUFFALO_STRATEGY_ID = "52651d10-5fb2-46b9-89f0-bf0eec05e622"
BRAND_ID = "a258784-5d84-4e62-83b8-075a7a8aa14f"
PROFILE_ID = "ef13b340-8234-4704-8776-d5db2b9dbc31"


def _claim(claim_id: str, statement: str, *, limitations=()):
    return {
        "claim_id": claim_id,
        "statement": statement,
        "epistemic_type": "INFERENCE",
        "verification_status": "SUPPORTED",
        "supporting_evidence_ids": ["ev_" + claim_id],
        "limitations": list(limitations),
    }


def buffalo_strategy_context() -> dict:
    return {
        "strategy_version_id": BUFFALO_STRATEGY_ID,
        "run_kind": "strategy",
        "run_status": "succeeded",
        "run_brand_id": BRAND_ID,
        "brand_id": BRAND_ID,
        "domain_reference": "buffaloburger_com",
        "is_current": True,
        "approval_state": "approved",
        "profile_snapshot_id": PROFILE_ID,
        "run_input_profile_id": PROFILE_ID,
        "profile": {
            "organization_name": "Buffalo Burger",
            "languages": ["en"],
            "existing_ctas": [{"value": "Start ordering"}, {"value": "View menu"}],
            "tone_of_voice": {"value": "bright and direct"},
            "visual": {"palette_hex": ["#f5a623", "#111111"],
                       "logo_url": "https://example.test/logo.png"},
            # These profile facts exist but are not contract proof.
            "reviews": [{"value": "Great deals"}],
            "ratings": [{"value": "4.7"}],
        },
        "requested_config": {"objective": {
            "objective_type": "CONVERSION",
            "business": {"description": "Increase first orders through the mobile app"},
            "behavioral_objective": {"value": "Start an app order"},
            "exact_cta": "Start ordering",
        }},
        "output": {"canonical_strategy": {
            "decision": {
                "decision_id": "dec_buffalo_mobile_value",
                "decision_type": "POSITIONING",
                "selected_option_id": "pos_mobile_deals",
                "unresolved_unknowns": ["One complaint is not evidence of prevalence."],
                "options": [{
                    "option_id": "pos_mobile_deals",
                    "target": "mobile app users looking for deals",
                    "frame_of_reference": "restaurant",
                    "positioning_statement": "Mobile ordering value",
                    "consumer_value": "Convenient mobile app with deals and personalization",
                    "reason_to_believe": "",
                    "supporting_claim_ids": [
                        "cl_value", "cl_target", "cl_frame", "cl_review",
                        "cl_competitor", "cl_complaint"],
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


def _contract():
    return ReelCreativeContractCompiler().compile(buffalo_strategy_context())


def _vertex_preferences(**updates):
    values = {
        "provider": ProviderSelection(
            provider_name="google", model_id="veo-3.1-generate-001",
            surface=ProviderSurface.VERTEX_AI),
    }
    values.update(updates)
    return CanonicalReelExecutionPreferences(**values)


def _intent(surface: ClaimSurface, text: str, *, claim_type=ClaimType.FACTUAL,
            factual=True, expressive=False, claim_ids=()):
    return ClaimIntent(
        surface=surface, text=text, supporting_allowed_claim_ids=tuple(claim_ids),
        claim_type=claim_type, factual=factual, expressive=expressive,
        exact_wording_required=False,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED)


def _fake_capabilities() -> VideoProviderCapabilities:
    return VideoProviderCapabilities(
        provider_name="fake", model_id="fake-video-v1", surface=ProviderSurface.FAKE_TEST,
        authorization_status=ProviderAuthorizationStatus.AUTHORIZED_PROFILE,
        text_to_video=True, image_to_video=True, first_last_frame=True,
        reference_images=True, video_extension=False, conversational_editing=False,
        native_audio=False, supported_aspect_ratios=("9:16",),
        supported_durations=(4, 6, 8), supported_resolutions=("1080p",),
        local_seed_supported=True, identity_control_mode="TEST_IDENTITY_LOCK",
        cost_known=True, capability_source="test-fixture",
        capability_source_version="1")


def _fake_selection() -> ProviderSelection:
    return ProviderSelection(
        provider_name="fake", model_id="fake-video-v1", surface=ProviderSurface.FAKE_TEST)


def _cost_envelope():
    price = ProviderPricing(
        provider_name="fake", model_id="fake-video-v1", surface=ProviderSurface.FAKE_TEST,
        resolution="1080p", pricing_version="fake-price-v1", effective_date="2026-08-23",
        unit="SECOND", unit_price_usd=Decimal("0.10"), source="test-fixture")
    item = PlannedProviderCost(
        category=PaidProviderCategory.VIDEO, pricing=price,
        maximum_units=Decimal("8"), maximum_cost_usd=Decimal("0.80"))
    return build_cost_envelope(
        pricing_version="fake-price-v1",
        planned_categories=[PaidProviderCategory.VIDEO], items=[item])


def _plan(contract=None):
    contract = contract or _contract()
    return build_execution_plan(
        contract=contract, creative_plan_hash="creative-plan-sha256",
        provider=_fake_selection(),
        output_mode=GeneratedMode.FULLY_GENERATED,
        cost_envelope=_cost_envelope())


def _approval(contract=None, plan=None, *, key=b"test-signing-key"):
    contract = contract or _contract()
    plan = plan or _plan(contract)
    now = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)
    return issue_execution_approval(
        plan=plan, approver_user_id="owner-1", issued_at=now,
        expires_at=now + timedelta(hours=1), signing_key=key), now


def _all_pass() -> FinalReelVerdict:
    values = {field: VerdictDimension.PASS
              for field in FinalReelVerdict.model_fields if field != "defects"}
    return FinalReelVerdict(**values)


# 1, 2 — exact approved authority ---------------------------------------------------------------

def test_canonical_reel_requires_an_exact_strategy_version():
    context = buffalo_strategy_context()
    context["strategy_version_id"] = ""
    with pytest.raises(ReelContractError) as caught:
        ReelCreativeContractCompiler().compile(context)
    assert caught.value.code == "STRATEGY_VERSION_MISSING"


@pytest.mark.parametrize(("field", "value", "code"), [
    ("approval_state", "pending", "STRATEGY_NOT_APPROVED"),
    ("is_current", False, "STRATEGY_VERSION_STALE"),
    ("run_status", "failed", "STRATEGY_VERSION_INVALID"),
    ("run_brand_id", "another-brand", "STRATEGY_WRONG_BRAND"),
])
def test_wrong_or_unapproved_strategy_fails_closed(field, value, code):
    context = buffalo_strategy_context()
    context[field] = value
    with pytest.raises(ReelContractError) as caught:
        ReelCreativeContractCompiler().compile(context)
    assert caught.value.code == code


# 3–7 — deterministic locked contract and zero proof -------------------------------------------

def test_contract_hash_is_deterministic_and_excludes_runtime_preferences():
    first = prepare_canonical_reel(buffalo_strategy_context(), _vertex_preferences(
        creative_style_preference="cinematic"))
    second = prepare_canonical_reel(buffalo_strategy_context(), _vertex_preferences(
        creative_style_preference="documentary", music_preference="quiet"))
    assert first.reel_contract.contract_hash == second.reel_contract.contract_hash
    assert first.reel_contract.contract_id == second.reel_contract.contract_id


@pytest.mark.parametrize("override", [
    {"target": "everyone"},
    {"consumer_value": "cheapest burgers"},
    {"exact_cta": "Learn more"},
    {"proof_points": ["4.7 rating"]},
])
def test_execution_preferences_cannot_override_locked_strategy(override):
    with pytest.raises(ValidationError):
        CanonicalReelExecutionPreferences(**{
            "provider": _vertex_preferences().provider, **override})


def test_buffalo_primary_message_and_exact_cta_are_preserved():
    contract = _contract()
    assert contract.strategy_version_id == BUFFALO_STRATEGY_ID
    assert contract.target == "mobile app users looking for deals"
    assert contract.frame_of_reference == "restaurant"
    assert contract.consumer_value == contract.primary_message == (
        "Convenient mobile app with deals and personalization")
    assert contract.exact_cta == "Start ordering"


def test_exact_cta_cannot_be_rewritten_and_the_exact_text_is_authorized():
    contract = _contract()
    bad = authorize_claim(_intent(
        ClaimSurface.END_CARD, "Learn more", claim_type=ClaimType.CTA,
        factual=False), contract)
    good = authorize_claim(_intent(
        ClaimSurface.END_CARD, "Start ordering", claim_type=ClaimType.CTA,
        factual=False), contract)
    assert bad.state == ClaimAuthorizationState.REQUIRES_REPAIR
    assert bad.code == "CTA_MISMATCH"
    assert good.state == ClaimAuthorizationState.AUTHORIZED


def test_empty_proof_points_remain_empty_and_message_is_not_relabelled_proof():
    contract = _contract()
    result = authorize_claim(_intent(
        ClaimSurface.ON_SCREEN_COPY, contract.primary_message,
        claim_type=ClaimType.PROOF, claim_ids=("cl_value",)), contract)
    assert contract.proof_points == ()
    assert result.state == ClaimAuthorizationState.BLOCKED
    assert result.code == "NO_APPROVED_CONSUMER_PROOF"


# 8–12 — internal evidence and claim surfaces --------------------------------------------------

@pytest.mark.parametrize("surface", [
    ClaimSurface.VOICE_OVER, ClaimSurface.CAPTION, ClaimSurface.END_CARD,
    ClaimSurface.PACKAGE_SIGN_COPY,
])
def test_internal_evidence_cannot_render_on_textual_surfaces(surface):
    result = authorize_claim(_intent(
        surface, "The profile contains a 4.7 customer rating"), _contract())
    assert result.state != ClaimAuthorizationState.AUTHORIZED
    assert result.state != ClaimAuthorizationState.EXPRESSIVE_NONFACTUAL


def test_planner_projection_structurally_excludes_internal_evidence():
    contract = _contract()
    rendered = contract.planner_authority().model_dump(mode="json")
    assert "internal_supporting_evidence" not in rendered
    assert all(v.consumer_renderable is False for v in contract.internal_supporting_evidence)


@pytest.mark.parametrize("text", [
    "Rated 4.7 by customers",
    "Our CTA count beats the competitor baseline",
    "Most customers complain about service at this branch",
])
def test_reviews_competitor_metrics_and_single_complaints_cannot_become_proof(text):
    result = authorize_claim(_intent(
        ClaimSurface.VOICE_OVER, text, claim_type=ClaimType.PROOF), _contract())
    assert result.state == ClaimAuthorizationState.BLOCKED


def test_visual_implication_is_a_claim_surface_and_enters_the_firewall():
    result = authorize_claim(ClaimIntent(
        surface=ClaimSurface.VISUAL_IMPLICATION,
        semantic_intent="before and after physical transformation",
        claim_type=ClaimType.VISUAL, factual=True, expressive=False,
        exact_wording_required=False,
        verification_status=ClaimVerificationStatus.NOT_VERIFIED), _contract())
    assert result.state == ClaimAuthorizationState.BLOCKED
    assert result.code == "PROHIBITED_VISUAL_IMPLICATION"


def test_claim_verifier_exception_is_claim_verification_error_and_blocks():
    def explode(_intent):
        raise RuntimeError("checker unavailable")

    result = authorize_claim(_intent(
        ClaimSurface.HOOK, "A brighter ordering moment", claim_type=ClaimType.EXPRESSIVE,
        factual=False, expressive=True), _contract(), supplemental_checker=explode)
    assert result.state == ClaimAuthorizationState.BLOCKED
    assert result.code == "CLAIM_VERIFICATION_ERROR"


# 13–16 — canonical route/provider isolation ---------------------------------------------------

def test_canonical_route_cannot_fall_back_to_a_business_profile(monkeypatch):
    from api.routes import canonical_reel as route

    class Missing:
        def canonical_strategy_context(self, **_kwargs):
            return None

    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(route, "get_persistence", lambda: Missing())
    monkeypatch.setattr(route, "prepare_canonical_reel", forbidden)
    request = route.CanonicalReelPrepareRequest(
        domain_key="buffaloburger_com", strategy_version_id=BUFFALO_STRATEGY_ID,
        execution_preferences=_vertex_preferences())
    with pytest.raises(HTTPException) as caught:
        route.prepare_reel(request, user={"id": "owner-1"})
    assert caught.value.status_code == 404
    assert called is False


def test_canonical_request_has_no_profile_or_strategy_override_fields():
    from api.routes.canonical_reel import CanonicalReelPrepareRequest

    with pytest.raises(ValidationError):
        CanonicalReelPrepareRequest(
            domain_key="buffaloburger_com", strategy_version_id=BUFFALO_STRATEGY_ID,
            execution_preferences=_vertex_preferences(), profile={"ratings": [5]})


def test_canonical_provider_selection_never_silently_switches():
    with pytest.raises(CanonicalReelExecutionError) as caught:
        select_canonical_provider(
            ProviderSelection(provider_name="aiml", model_id="veo", surface="AIML_API"),
            RequiredVideoCapabilities())
    assert caught.value.code == "PROVIDER_NOT_EXPLICITLY_AVAILABLE"


def test_execution_plan_cannot_price_one_provider_and_authorize_another():
    with pytest.raises(CanonicalReelExecutionError) as caught:
        build_execution_plan(
            contract=_contract(), creative_plan_hash="plan-hash",
            provider=ProviderSelection(
                provider_name="other", model_id="other-video",
                surface=ProviderSurface.AIML_API),
            output_mode=GeneratedMode.FULLY_GENERATED,
            cost_envelope=_cost_envelope())
    assert caught.value.code == "PRICING_IDENTITY_MISMATCH"


def test_unsupported_required_capability_blocks_before_execution():
    with pytest.raises(CanonicalReelExecutionError) as caught:
        select_canonical_provider(
            _vertex_preferences().provider,
            RequiredVideoCapabilities(reference_images=True))
    assert caught.value.code == "UNSUPPORTED_REQUIRED_CAPABILITY"


def test_constraint_dropping_retry_is_forbidden():
    original = CanonicalProviderConstraints(
        identity_constraint="same person", required_starting_frame_hash="seed-sha",
        product_anchor="real app screen", brand_constraints=("orange palette",),
        safety_constraints=("adult only",), negative_constraints=("no text",),
        approval_id="approval-1")
    retry = original.model_copy(update={"identity_constraint": ""})
    with pytest.raises(CanonicalReelExecutionError) as caught:
        validate_constraint_preserving_retry(original=original, retry=retry)
    assert caught.value.code == "CAPABILITY_DROPPING_RETRY_FORBIDDEN"


def test_vertex_profile_records_surface_truth_not_generic_sdk_union():
    assert VERTEX_VEO_31.text_to_video and VERTEX_VEO_31.image_to_video
    assert VERTEX_VEO_31.first_last_frame
    assert VERTEX_VEO_31.supported_aspect_ratios[-1] == "9:16"
    assert VERTEX_VEO_31.supported_durations == (4, 6, 8)
    assert VERTEX_VEO_31.reference_images is False
    assert VERTEX_VEO_31.video_extension is False
    assert "google-genai-2.8.0" in VERTEX_VEO_31.capability_source_version


def test_omni_flash_is_represented_but_not_authorized_or_registered():
    assert GEMINI_OMNI_FLASH_EXPERIMENTAL.authorization_status == (
        ProviderAuthorizationStatus.EXPERIMENTAL_NOT_YET_AUTHORIZED)
    assert GEMINI_OMNI_FLASH_EXPERIMENTAL.identity not in {
        VERTEX_VEO_31.identity}
    with pytest.raises(CanonicalReelExecutionError):
        select_canonical_provider(
            ProviderSelection(provider_name="google", model_id="gemini-omni-flash-preview",
                              surface=ProviderSurface.GEMINI_API),
            RequiredVideoCapabilities())


# 17–24 — generated mode, approval, finite cost, terminal firewall ------------------------------

@pytest.mark.parametrize(("mode", "failure_code"), [
    (GeneratedMode.RAW_PHOTO_LEGACY, "RAW_PHOTO_FALLBACK_NOT_CANONICAL"),
    (GeneratedMode.DEGRADED_PREVIEW, "DEGRADED_PREVIEW_NOT_CANONICAL"),
])
def test_raw_or_degraded_fallback_cannot_be_canonical_success(mode, failure_code):
    response = terminal_delivery(CanonicalArtifactCandidate(
        mode=mode, video_base64="REAL-BYTES", reel_path="film.mp4"), _all_pass())
    assert response.delivered is False
    assert response.terminal_verdict == TerminalVerdict.BLOCKED
    assert response.failure_code == failure_code
    assert response.video_base64 is None and response.reel_path is None


def test_paid_fake_provider_refuses_without_authorization_before_calling():
    called = False

    def paid():
        nonlocal called
        called = True

    with pytest.raises(CanonicalReelExecutionError) as caught:
        execute_authorized_paid_boundary(
            paid, authorization=None, contract=_contract(), plan=_plan(), signing_key=b"key",
            cost_tracker=AuthorizedCostTracker(), category=PaidProviderCategory.VIDEO,
            provider=_fake_selection(),
            maximum_call_cost_usd=Decimal("0.10"))
    assert caught.value.code == "EXECUTION_APPROVAL_REQUIRED"
    assert called is False


def test_approval_binds_contract_hash():
    contract = _contract()
    plan = _plan(contract)
    approval, now = _approval(contract, plan)
    changed_contract = contract.model_copy(update={"contract_hash": "another-contract"})
    with pytest.raises(CanonicalReelExecutionError) as caught:
        validate_execution_approval(
            approval=approval, contract=changed_contract, plan=plan,
            signing_key=b"test-signing-key", now=now)
    assert caught.value.code == "APPROVAL_CONTRACT_MISMATCH"


def test_approval_binds_execution_plan_hash():
    contract = _contract()
    plan = _plan(contract)
    approval, now = _approval(contract, plan)
    changed_plan = plan.model_copy(update={"execution_plan_hash": "another-plan"})
    with pytest.raises(CanonicalReelExecutionError) as caught:
        validate_execution_approval(
            approval=approval, contract=contract, plan=changed_plan,
            signing_key=b"test-signing-key", now=now)
    assert caught.value.code == "APPROVAL_PLAN_MISMATCH"


def test_approval_cost_cap_is_finite_and_covers_the_whole_envelope():
    contract = _contract()
    plan = _plan(contract)
    approval, _now = _approval(contract, plan)
    assert approval.maximum_approved_cost_usd == Decimal("0.80")
    assert approval.maximum_approved_cost_usd.is_finite()
    with pytest.raises(CanonicalReelExecutionError) as caught:
        issue_execution_approval(
            plan=plan, approver_user_id="owner", signing_key=b"key",
            issued_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
            expires_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
            maximum_approved_cost_usd=Decimal("0.79"))
    assert caught.value.code == "APPROVAL_COST_CAP_TOO_LOW"


def test_unknown_or_cross_surface_price_is_not_eligible_for_execution():
    with pytest.raises(CanonicalReelExecutionError) as caught:
        require_provable_provider_cost(
            capabilities=VERTEX_VEO_31,
            pricing=GEMINI_API_VEO_PRICING_2026_08_23[0])
    assert caught.value.code == "COST_NOT_PROVABLE"


def test_valid_approval_is_one_shot_at_the_paid_boundary():
    contract = _contract()
    plan = _plan(contract)
    approval, now = _approval(contract, plan)
    registry = ApprovalUseRegistry()
    authorization = begin_authorized_execution(
        approval=approval, contract=contract, plan=plan, signing_key=b"test-signing-key",
        use_registry=registry, now=now)
    tracker = AuthorizedCostTracker()
    calls = []
    assert execute_authorized_paid_boundary(
        lambda: calls.append("called") or "ok", authorization=authorization,
        contract=contract, plan=plan, signing_key=b"test-signing-key",
        cost_tracker=tracker, category=PaidProviderCategory.VIDEO,
        provider=_fake_selection(),
        maximum_call_cost_usd=Decimal("0.10"), now=now) == "ok"
    # One approval authorizes all finite calls in this execution plan; its one-shot property
    # prevents starting a second execution, not the second planned provider boundary.
    assert execute_authorized_paid_boundary(
        lambda: calls.append("second planned call") or "ok", authorization=authorization,
        contract=contract, plan=plan, signing_key=b"test-signing-key",
        cost_tracker=tracker, category=PaidProviderCategory.VIDEO,
        provider=_fake_selection(),
        maximum_call_cost_usd=Decimal("0.10"), now=now) == "ok"
    with pytest.raises(CanonicalReelExecutionError) as caught:
        begin_authorized_execution(
            approval=approval, contract=contract, plan=plan,
            signing_key=b"test-signing-key", use_registry=registry, now=now)
    assert caught.value.code == "APPROVAL_ALREADY_USED"
    assert calls == ["called", "second planned call"]
    assert tracker.reserved_total_usd == Decimal("0.20")


def test_blocked_verdict_never_returns_existing_reel_bytes_or_path():
    verdict = FinalReelVerdict(strategy_alignment=VerdictDimension.FAIL)
    response = terminal_delivery(CanonicalArtifactCandidate(
        mode=GeneratedMode.FULLY_GENERATED,
        video_base64="MP4-EXISTS", reel_path="outputs/reels/exists.mp4"), verdict)
    assert response.terminal_verdict == TerminalVerdict.BLOCKED
    assert response.delivered is False
    assert response.video_base64 is None and response.reel_path is None


def test_mandatory_not_assessed_dimension_prevents_terminal_passed():
    values = {field: VerdictDimension.PASS
              for field in FinalReelVerdict.model_fields if field != "defects"}
    values["audio_script_integrity"] = VerdictDimension.NOT_ASSESSED
    assert FinalReelVerdict(**values).terminal_verdict() == TerminalVerdict.BLOCKED


def test_mandatory_not_applicable_cannot_launder_a_missing_strategy_gate():
    values = {field: VerdictDimension.PASS
              for field in FinalReelVerdict.model_fields if field != "defects"}
    values["strategy_alignment"] = VerdictDimension.NOT_APPLICABLE
    assert FinalReelVerdict(**values).terminal_verdict() == TerminalVerdict.BLOCKED


def test_only_fully_assessed_passed_reel_can_deliver():
    response = terminal_delivery(CanonicalArtifactCandidate(
        mode=GeneratedMode.FULLY_GENERATED,
        video_base64="MP4", reel_path="film.mp4"), _all_pass())
    assert response.delivered is True
    assert response.terminal_verdict == TerminalVerdict.PASSED


def test_execution_state_machine_separates_creative_validity_from_job_status():
    record = ReelExecutionStateRecord(state=ReelExecutionState.CONTRACT_READY)
    for state in (ReelExecutionState.PLAN_READY, ReelExecutionState.AWAITING_APPROVAL,
                  ReelExecutionState.AUTHORIZED, ReelExecutionState.EXECUTING,
                  ReelExecutionState.VERIFYING, ReelExecutionState.PASSED):
        record = record.transition(state)
    assert record.state == ReelExecutionState.PASSED
    with pytest.raises(CanonicalReelExecutionError):
        record.transition(ReelExecutionState.EXECUTING)


# 25 — API/CLI authority parity and zero-provider preparation ----------------------------------

def test_canonical_api_and_cli_use_the_same_authority_without_provider_calls(monkeypatch):
    from api.routes import canonical_reel as route
    from reel import canonical_cli

    class Store:
        def canonical_strategy_context(self, **_kwargs):
            return buffalo_strategy_context()

    monkeypatch.setattr(route, "get_persistence", lambda: Store())
    request = route.CanonicalReelPrepareRequest(
        domain_key="buffaloburger_com", strategy_version_id=BUFFALO_STRATEGY_ID,
        execution_preferences=_vertex_preferences())
    api_result = route.prepare_reel(request, user={"id": "owner-1"})

    stdout = io.StringIO()
    code = canonical_cli.main([
        "--domain-key", "buffaloburger_com",
        "--strategy-version-id", BUFFALO_STRATEGY_ID,
        "--user-id", "owner-1",
        "--provider", "google",
        "--model", "veo-3.1-generate-001",
        "--surface", "VERTEX_AI",
    ], persistence=Store(), stdout=stdout, stderr=io.StringIO())
    cli_result = json.loads(stdout.getvalue())

    assert code == 0
    assert api_result.reel_contract.contract_hash == cli_result["reel_contract"]["contract_hash"]
    assert api_result.planner_authority.model_dump(mode="json") == cli_result["planner_authority"]
    assert api_result.state == ReelExecutionState.CONTRACT_READY
    assert api_result.billable is False and api_result.provider_calls == 0
    assert cli_result["billable"] is False and cli_result["provider_calls"] == 0


def test_public_reel_cli_requires_an_explicit_canonical_or_legacy_mode(monkeypatch):
    import sys
    import reel.__main__ as entry

    monkeypatch.setattr(sys, "argv", ["reel", "some-profile.json"])
    assert entry.main() == 2


def test_canonical_api_route_is_mounted_separately_from_the_legacy_route():
    from api.main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/reel/canonical/prepare" in paths
    assert "/api/reel/from-profile" in paths
    assert "/api/reel/canonical/prepare" != "/api/reel/from-profile"
