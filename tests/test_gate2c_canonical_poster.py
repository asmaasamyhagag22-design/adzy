from __future__ import annotations

import base64
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest

from poster.strategy_contract import (
    CreativePreflight,
    CreativePreflightReason,
    CreativeContractCompiler,
    CreativeContractError,
    CreativeRequest,
    PosterStrategyVerifier,
    repair_scope_for,
    strategy_repair_concept,
)


STRATEGY_ID = "f23798c8-4c9e-4242-95f8-c977c2606732"
BRAND_ID = "0f0f0dfd-f295-4705-9dc7-c8576adb80ee"
PROFILE_ID = "6901a15d-1ec7-490e-ac42-a230f63f2bc2"


def _claim(claim_id, statement, *, strength="MODERATE", limitations=None):
    return {
        "claim_id": claim_id,
        "statement": statement,
        "epistemic_type": "INFERENCE",
        "verification_status": "SUPPORTED",
        "evidence_strength": strength,
        "supporting_evidence_ids": ["ev_" + claim_id[-4:]],
        "limitations": list(limitations or []),
    }


def _context():
    claim_ids = ["cl_target", "cl_frame", "cl_complaint", "cl_value", "cl_rtb", "cl_diff"]
    claims = [
        _claim("cl_target", "customers interested in loyalty programs and rewards"),
        _claim("cl_frame", "restaurant"),
        _claim("cl_complaint", "الفرع اللي في the isle تعاملهم وحش اووي وتعاملهم صعب",
               strength="WEAK",
               limitations=["Single observed customer signal; occurrence is established, but prevalence is not."]),
        _claim("cl_value", "Elevated fast food with natural ingredients"),
        _claim("cl_rtb", "Uses natural ingredients"),
        _claim("cl_diff", "Your review volume (2093) is above the peer average (161.8, n=6).",
               strength="STRONG",
               limitations=["Aggregate comparison; peer identities are not serialized."]),
    ]
    return {
        "strategy_version_id": STRATEGY_ID,
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
            "languages": ["en", "ar"],
            "existing_ctas": [{"value": "Order Now"}, {"value": "View Menu"}],
            "offerings": [{"name": "OLD SCHOOL"}, {"name": "THE SECRET BURGER"}],
            "tone_of_voice": {"value": "friendly"},
            "visual": {"palette_hex": ["#e46c0c", "#1c1c1b"]},
        },
        "requested_config": {"objective": {
            "business": {"description": "Increase first-time orders"},
            "behavioral_objective": {"status": "KNOWN", "value": "Place a first order"},
        }},
        "output": {"canonical_strategy": {
            "decision": {
                "decision_id": "dec_buffalo",
                "decision_type": "POSITIONING",
                "objective_id": "obj_buffalo",
                "selected_option_id": "pos_1",
                "unresolved_unknowns": ["Business objective metric is UNKNOWN."],
                "options": [{
                    "option_id": "pos_1",
                    "target": ("customers interested in loyalty programs and rewards; customer "
                               "signal (one observation, not prevalence): الفرع اللي في the isle "
                               "تعاملهم وحش اووي وتعاملهم صعب"),
                    "frame_of_reference": "restaurant",
                    "positioning_statement": "Approved Buffalo positioning",
                    "consumer_value": "Elevated fast food with natural ingredients",
                    "differentiated_value": (
                        "Your review volume (2093) is above the peer average (161.8, n=6)."),
                    "reason_to_believe": "Uses natural ingredients",
                    "supporting_claim_ids": claim_ids,
                    "consumer_value_claim_ids": ["cl_value"],
                    "differentiation_claim_ids": ["cl_diff"],
                    "reason_to_believe_claim_ids": ["cl_rtb"],
                    "internal_supporting_claim_ids": [
                        "cl_target", "cl_frame", "cl_complaint", "cl_diff"],
                }],
            },
            "claims": claims,
            "verification": {
                "status": "PASS_WITH_DISCLOSURE",
                "eligible_for_approval": True,
            },
        }},
    }


def _contract(**request):
    return CreativeContractCompiler().compile(_context(), request=CreativeRequest(**request))


def _concept(contract, **updates):
    values = {
        "audience": contract.target,
        "single_message": contract.primary_message,
        "core_benefit": contract.consumer_value,
        "proof_points": [v.statement for v in contract.proof_points],
        "headline": "Make every order count",
        "subheadline": contract.primary_message,
        "cta": contract.cta_direction,
        "language": contract.language,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _brief(contract, **updates):
    values = {
        "headline": "Make every order count",
        "subheadline": contract.primary_message,
        "cta_text": contract.cta_direction,
        "offerings": [v.statement for v in contract.proof_points],
    }
    values.update(updates)
    return SimpleNamespace(**values)


def test_approved_strategy_compiles_deterministically_and_keeps_exact_version():
    first = _contract()
    second = _contract()
    assert first.creative_contract_id == second.creative_contract_id
    assert first.strategy_version_id == STRATEGY_ID
    assert first.profile_snapshot_id == PROFILE_ID
    assert first.allowed_claim_ids == [
        "cl_target", "cl_frame", "cl_complaint", "cl_value", "cl_rtb", "cl_diff"]
    assert first.primary_message == "Elevated fast food with natural ingredients"
    assert "review volume" not in first.primary_message
    assert [v.claim_id for v in first.differentiation_evidence] == ["cl_diff"]
    assert {v.claim_id for v in first.internal_supporting_evidence} == {
        "cl_target", "cl_frame", "cl_complaint", "cl_diff"}
    assert first.preflight.status == "READY"
    assert first.consumer_facing_disclosures == []
    assert any("prevalence" in v for v in first.prohibited_or_unverified_claims)


@pytest.mark.parametrize(("field", "value", "code"), [
    ("approval_state", "rejected", "STRATEGY_NOT_APPROVED"),
    ("run_brand_id", "other-brand", "STRATEGY_WRONG_BRAND"),
    ("is_current", False, "STRATEGY_VERSION_STALE"),
])
def test_unapproved_wrong_brand_and_stale_strategies_fail_closed(field, value, code):
    context = _context()
    context[field] = value
    with pytest.raises(CreativeContractError) as exc:
        CreativeContractCompiler().compile(context)
    assert exc.value.code == code


def test_compatible_product_and_cta_are_allowed_but_strategy_override_conflicts():
    contract = _contract(product_focus="OLD SCHOOL", cta_preference="Order Now")
    assert contract.product_offer_focus == "OLD SCHOOL"
    with pytest.raises(CreativeContractError) as exc:
        _contract(requested_target="everyone who likes burgers")
    assert exc.value.code == "CREATIVE_REQUEST_STRATEGY_CONFLICT"


def test_default_cta_uses_first_evidenced_action_matching_explicit_objective():
    context = _context()
    context["profile"]["existing_ctas"] = [
        {"value": "Cart"}, {"value": "View Menu"}, {"value": "Start ordering"},
        {"value": "Place order"},
    ]
    contract = CreativeContractCompiler().compile(context)
    assert contract.cta_direction == "Start ordering"


def test_postgres_strategy_context_carries_the_persisted_requested_objective(monkeypatch):
    from api.persistence import PostgresPersistence

    brand_id = UUID(BRAND_ID)
    run_id = UUID(STRATEGY_ID)
    profile_id = UUID(PROFILE_ID)
    user_id = UUID("11111111-1111-4111-8111-111111111111")
    requested_config = {"objective": {
        "business": {"description": "Increase first-time orders"},
        "behavioral_objective": {"status": "KNOWN", "value": "Place a first order"},
    }}
    run = SimpleNamespace(
        id=run_id, kind="strategy", status="succeeded", brand_id=brand_id,
        input_profile_id=profile_id, requested_config=requested_config,
        output={"canonical_strategy": {}},
    )
    profile = SimpleNamespace(id=profile_id, profile={"name": "Buffalo Burger"})
    service = SimpleNamespace(
        run_for_user=lambda **_kwargs: run,
        current_successful_run=lambda **_kwargs: run,
        decision_for_current_strategy=lambda **_kwargs: SimpleNamespace(decision="approved"),
        profile_version=lambda **_kwargs: profile,
    )
    provider = PostgresPersistence(service)
    monkeypatch.setattr(
        provider, "_brand_row",
        lambda **_kwargs: SimpleNamespace(id=brand_id, domain_key="buffaloburger_com"))

    context = provider.canonical_strategy_context(
        domain_key="buffaloburger_com", user_id=str(user_id), run_id=str(run_id))

    assert context["requested_config"] == requested_config
    with pytest.raises(CreativeContractError) as exc:
        _contract(requested_factual_claims=["Number one burger in Egypt"])
    assert exc.value.code == "CREATIVE_REQUEST_STRATEGY_CONFLICT"


def test_unsupported_number_and_complaint_prevalence_are_blocked():
    contract = _contract()
    verifier = PosterStrategyVerifier()
    unsupported = verifier.verify(
        contract=contract, concept=_concept(contract),
        brief=_brief(contract, headline="10,000 happy customers"), language="en")
    prevalence = verifier.verify(
        contract=contract, concept=_concept(contract),
        brief=_brief(contract, headline="Most customers hate The Isle service"), language="en")
    assert "UNSUPPORTED_POSTER_CLAIM" in {v.code for v in unsupported.defects}
    assert "UNSUPPORTED_POSTER_CLAIM" in {v.code for v in prevalence.defects}


def test_internal_review_metric_cannot_be_used_as_visible_proof():
    contract = _contract()
    result = PosterStrategyVerifier().verify(
        contract=contract, concept=_concept(contract),
        brief=_brief(contract, offerings=[contract.differentiated_value]), language="en")
    assert "PROOF_MESSAGE_MISMATCH" in {v.code for v in result.defects}


def test_old_positioning_without_consumer_value_is_not_creative_ready():
    context = _context()
    selected = context["output"]["canonical_strategy"]["decision"]["options"][0]
    selected.pop("consumer_value")
    selected.pop("consumer_value_claim_ids")
    with pytest.raises(CreativeContractError) as exc:
        CreativeContractCompiler().compile(context)
    assert exc.value.code == "STRATEGY_NOT_CREATIVE_READY"
    assert exc.value.preflight_status == "STRATEGY_NOT_CREATIVE_READY"


def test_unrelated_proof_requires_strategy_repair_not_poster_rewrite():
    context = _context()
    selected = context["output"]["canonical_strategy"]["decision"]["options"][0]
    selected["reason_to_believe"] = "restaurant"
    selected["reason_to_believe_claim_ids"] = ["cl_frame"]
    with pytest.raises(CreativeContractError) as exc:
        CreativeContractCompiler().compile(context)
    assert exc.value.code == "PROOF_MESSAGE_MISMATCH"
    assert exc.value.preflight_status == "NEEDS_REPAIR"


def test_nonready_preflight_refuses_before_any_image_provider_call(monkeypatch):
    from api import webapp_images as images

    called = False

    def provider(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be reached")

    contract = _contract().model_copy(update={
        "preflight": CreativePreflight(
            status="NEEDS_REPAIR",
            reasons=[CreativePreflightReason(
                code="PROOF_MESSAGE_MISMATCH", field="proof_points",
                detail="fixture mismatch")],
        )
    })
    monkeypatch.setattr(images, "_render_real", provider)
    monkeypatch.setattr(images, "paid_images_enabled", lambda: True)

    with pytest.raises(CreativeContractError) as exc:
        images.generate_image({"domain_key": "buffaloburger_com"}, creative_contract=contract)

    assert exc.value.preflight_status == "NEEDS_REPAIR"
    assert called is False


@pytest.mark.parametrize(("concept_updates", "brief_updates", "code"), [
    ({"audience": "new customers"}, {}, "TARGET_DRIFT"),
    ({"single_message": "cheap burgers"}, {}, "MESSAGE_DRIFT"),
    ({"core_benefit": "lowest price"}, {}, "POSITIONING_DRIFT"),
    ({}, {"cta_text": "Buy immediately"}, "CTA_CONFLICT"),
])
def test_drift_verifier_detects_each_strategic_boundary(concept_updates, brief_updates, code):
    contract = _contract()
    result = PosterStrategyVerifier().verify(
        contract=contract, concept=_concept(contract, **concept_updates),
        brief=_brief(contract, **brief_updates), language="en")
    assert code in {v.code for v in result.defects}


def test_claim_ids_survive_verification_and_targeted_repair_keeps_lineage():
    contract = _contract()
    repaired = strategy_repair_concept(_concept(contract, audience="wrong"), contract)
    result = PosterStrategyVerifier().verify(
        contract=contract, concept=repaired,
        brief=_brief(contract, headline=contract.primary_message,
                     subheadline=contract.reason_to_believe), language="en")
    assert result.overall_pass is True
    assert {"cl_value", "cl_rtb"}.issubset(set(result.claim_ids_used))
    assert contract.strategy_version_id == STRATEGY_ID
    assert repair_scope_for("UNSUPPORTED_POSTER_CLAIM") == "COPY_ONLY"
    assert repair_scope_for("VISUAL_QA_FAILURE") == "EXISTING_VISUAL_REPAIR"


def test_gallery_metadata_keeps_canonical_lineage(monkeypatch):
    from api.routes import generate as route

    captured = {}
    run_id = "11111111-1111-4111-8111-111111111111"
    attempt_id = "22222222-2222-4222-8222-222222222222"

    class Store:
        def supports_gallery_writes(self):
            return True

        def begin_canonical_poster_run(self, *, domain_key, user_id, contract):
            captured["begun"] = {"domain_key": domain_key, "user_id": user_id,
                                 "contract_id": contract["creative_contract_id"]}
            return {"run_id": run_id, "attempt_id": attempt_id}

        def record_gallery_entry(self, *, entry, user_id):
            captured.update(entry)
            captured["recorded_user_id"] = user_id
            return {"asset_id": "33333333-3333-4333-8333-333333333333"}

    contract = _contract()
    monkeypatch.setattr(route, "get_persistence", lambda: Store())
    def generated(*args, **kwargs):
        captured["renderer_run_id"] = kwargs["generation_run_id"]
        captured["renderer_attempt_id"] = kwargs["generation_attempt_id"]
        from poster.vision_qa import (
            POSTER_QA_SOURCE, PosterQAVerdict, poster_acceptance_from_output,
            poster_attempt_identity, required_poster_qa_policy,
        )
        payload = b"png"
        policy = required_poster_qa_policy(
            arabic=False, logo_composited=False,
            expect_real_products=False, product_named=False)
        qa = PosterQAVerdict(
            checked=True, overall_pass=True, critic_pass=True,
            text_lines=["Approved message", "Order Now"], score=8, reason="clean",
            qa_source=POSTER_QA_SOURCE, policy_id=policy.policy_id,
            policy_version=policy.version, policy_context=dict(policy.context),
            mandatory_checks_expected=list(policy.mandatory_checks),
            mandatory_checks_completed=list(policy.mandatory_checks),
            non_applicable_checks=dict(policy.non_applicable_checks),
            attempt_identity=poster_attempt_identity(
                payload, attempt_id=kwargs["generation_attempt_id"],
                candidate_id="canonical-candidate"),
        )
        out = {
        "image_b64": base64.b64encode(payload).decode(),
        "filename": "poster.png", "is_preview": False,
        "trust_violation": False, "visual_qa": qa.model_dump(mode="json"),
        "canonical": True, "strategy_grounded": True,
        "strategy_version_id": contract.strategy_version_id,
        "creative_contract_id": contract.creative_contract_id,
        "objective_id": contract.objective_id, "decision_id": contract.decision_id,
        "profile_snapshot_id": contract.profile_snapshot_id,
        "generation_run_id": kwargs["generation_run_id"],
        "generation_attempt_id": kwargs["generation_attempt_id"],
        "claim_ids_used": ["cl_value"],
        "creative_contract": contract.model_dump(mode="json"),
        "poster_brief": CreativeContractCompiler.poster_brief(contract).model_dump(mode="json"),
        "strategy_verification": {"overall_pass": True}, "repair_history": [],
        "consumer_facing_disclosures": [], "why_this_poster": {},
        }
        decision = poster_acceptance_from_output(
            out, expected_attempt_id=kwargs["generation_attempt_id"])
        out["acceptance_decision"] = decision.model_dump(mode="json")
        out.update(route.images.acceptance_disclosure(decision))
        return out
    monkeypatch.setattr(route.images, "generate_image", generated)
    output = route._emit(
        {"organization_name": "Buffalo Burger", "domain_key": "buffaloburger_com"},
        headline="", subtext="", cta="Order Now", style="bold",
        content_type="brand_poster", owner_id="owner-1", creative_contract=contract)
    assert captured["strategy_version_id"] == STRATEGY_ID
    assert captured["creative_contract_id"] == contract.creative_contract_id
    assert captured["claim_ids_used"] == ["cl_value"]
    assert captured["strategy_grounded"] is True
    assert captured["recorded_user_id"] == "owner-1"
    assert captured["renderer_run_id"] == captured["generation_run_id"] == run_id
    assert captured["renderer_attempt_id"] == captured["generation_attempt_id"] == attempt_id
    assert captured["begun"]["contract_id"] == contract.creative_contract_id
    assert output["asset_id"] == "33333333-3333-4333-8333-333333333333"


def test_legacy_gallery_entry_is_never_canonically_labelled(monkeypatch):
    from api.routes import generate as route

    captured = {}

    class Store:
        def supports_gallery_writes(self):
            return True

        def record_gallery_entry(self, *, entry, user_id):
            captured.update(entry)

    monkeypatch.setattr(route, "get_persistence", lambda: Store())
    monkeypatch.setattr(route.images, "generate_image", lambda *a, **k: {
        "image_b64": base64.b64encode(b"legacy").decode(),
        "filename": "legacy.png", "is_preview": True,
        "trust_violation": False, "qa_status": "not_checked", "qa_failed": False,
        "qa_note": "not checked", "canonical": False, "strategy_grounded": False,
    })
    route._emit({"organization_name": "Legacy", "domain_key": "legacy_com"},
                headline="", subtext="", cta="", style="",
                content_type="post", owner_id="owner-1")
    assert captured["canonical"] is False
    assert captured["strategy_grounded"] is False
    assert captured["strategy_version_id"] == ""


def test_canonical_provider_failure_is_typed_and_closes_the_real_run(monkeypatch):
    from fastapi import HTTPException
    from api.routes import generate as route

    contract = _contract()
    run_id = "44444444-4444-4444-8444-444444444444"
    failed = {}

    class Store:
        def supports_gallery_writes(self):
            return True

        def begin_canonical_poster_run(self, **kwargs):
            return {"run_id": run_id,
                    "attempt_id": "55555555-5555-4555-8555-555555555555"}

        def fail_canonical_poster_run(self, *, run_id, attempt_id, user_id, error):
            failed.update({"run_id": run_id, "attempt_id": attempt_id,
                           "user_id": user_id, "error": error})

    monkeypatch.setattr(route, "get_persistence", lambda: Store())
    monkeypatch.setattr(route.images, "generate_image",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret credential")))

    with pytest.raises(HTTPException) as caught:
        route._emit(
            {"organization_name": "Buffalo Burger", "domain_key": "buffaloburger_com"},
            headline="", subtext="", cta="Order Now", style="product_hero",
            content_type="brand_poster", owner_id="owner-1", creative_contract=contract)

    assert caught.value.status_code == 502
    assert caught.value.detail == {
        "code": "CANONICAL_POSTER_GENERATION_FAILED",
        "message": "The canonical poster provider did not produce an accepted image.",
        "generation_run_id": run_id,
        "retryable": True,
    }
    assert "credential" not in str(caught.value.detail).lower()
    assert failed["run_id"] == run_id and failed["user_id"] == "owner-1"
    assert failed["attempt_id"] == "55555555-5555-4555-8555-555555555555"
