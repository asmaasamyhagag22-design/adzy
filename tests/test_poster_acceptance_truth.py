"""Canonical Poster visual-QA acceptance is explicit, complete, and content-bound.

Hermetic by construction: frozen bytes and typed QA objects only. No renderer, provider,
network, OCR, LLM, database, or storage service is called.
"""
from __future__ import annotations

import base64
from copy import deepcopy

import pytest

from poster.vision_qa import (
    POSTER_QA_POLICY_ID,
    POSTER_QA_POLICY_VERSION,
    PosterQAVerdict,
    QA_FAIL,
    QA_PASS,
    QA_UNVERIFIED,
    decide_poster_acceptance,
    poster_acceptance_from_output,
    poster_attempt_identity,
    required_poster_qa_policy,
)


ATTEMPT_A = "22222222-2222-4222-8222-222222222222"
ATTEMPT_B = "33333333-3333-4333-8333-333333333333"
PNG_A = b"frozen-poster-A"
PNG_B = b"frozen-poster-B"


def _policy(**updates):
    values = dict(arabic=False, logo_composited=False,
                  expect_real_products=False, product_named=False)
    values.update(updates)
    return required_poster_qa_policy(**values)


def _identity(payload=PNG_A, *, attempt_id=ATTEMPT_A, candidate_id="candidate-A"):
    return poster_attempt_identity(
        payload, attempt_id=attempt_id, candidate_id=candidate_id)


def _verdict(*, overall=True, checked=True, payload=PNG_A, attempt_id=ATTEMPT_A,
             candidate_id="candidate-A", policy=None, completed=None, **updates):
    policy = policy or _policy()
    values = dict(
        checked=checked, overall_pass=overall, critic_pass=overall,
        has_latin_text=False, cta_clipped=False, on_brand_color=True,
        candid_violation=False, logo_ok=True, single_focal=True,
        strong_typography=True, cta_prominent=True, script_wellformed=True,
        invented_product=False, third_party_mark=False, placement_logical=True,
        text_lines=["Frozen headline", "Learn more"], garbled_text=False,
        product_count=0, score=8,
        reason="clean" if overall is True else "frozen rejection",
        qa_source="poster.vision_qa.poster_vision_qa",
        policy_id=policy.policy_id, policy_version=policy.version,
        policy_context=dict(policy.context),
        mandatory_checks_expected=list(policy.mandatory_checks),
        mandatory_checks_completed=(list(policy.mandatory_checks)
                                    if completed is None else list(completed)),
        non_applicable_checks=dict(policy.non_applicable_checks),
        attempt_identity=_identity(
            payload, attempt_id=attempt_id, candidate_id=candidate_id),
    )
    values.update(updates)
    return PosterQAVerdict(**values)


def _decision(qa, *, identity=None, policy=None, current=None):
    policy = policy or _policy()
    identity = identity or _identity()
    return decide_poster_acceptance(
        qa, attempt_identity=identity, required_policy=policy,
        current_attempt_identity=current or identity)


def _output(qa, payload=PNG_A, *, attempt_id=ATTEMPT_A, qa_passed=None):
    value = {
        "image_b64": base64.b64encode(payload).decode("ascii"),
        "generation_attempt_id": attempt_id,
        "visual_qa": qa.model_dump(mode="json") if qa is not None else None,
    }
    if qa_passed is not None:
        value["qa_passed"] = qa_passed
    return value


@pytest.mark.parametrize("qa", [None, PosterQAVerdict(), _verdict(overall=None, checked=False)])
def test_missing_skipped_or_none_qa_is_unverified_and_never_accepted(qa):
    decision = _decision(qa)
    assert decision.qa_status == QA_UNVERIFIED
    assert decision.qa_executed is False
    assert decision.accepted is False
    assert decision.acceptance_eligible is False
    assert decision.publishable is False
    assert decision.deliverable is False
    assert decision.explanation


def test_evaluator_exception_or_unavailability_is_fail_closed():
    qa = _verdict(overall=None, checked=False, reason="QA call failed (TimeoutError)")
    decision = _decision(qa)
    assert decision.qa_status == QA_UNVERIFIED
    assert "QA_NOT_EXECUTED" in decision.reason_codes
    assert not decision.accepted


@pytest.mark.parametrize("policy_id,version", [
    ("unknown-poster-policy", POSTER_QA_POLICY_VERSION),
    (POSTER_QA_POLICY_ID, POSTER_QA_POLICY_VERSION + 1),
])
def test_unknown_policy_or_version_fails_closed(policy_id, version):
    qa = _verdict(policy_id=policy_id, policy_version=version)
    decision = _decision(qa)
    assert decision.qa_status == QA_UNVERIFIED
    assert "QA_POLICY_UNKNOWN" in decision.reason_codes
    assert not decision.accepted


def test_explicit_fail_remains_fail_and_not_accepted():
    decision = _decision(_verdict(overall=False, critic_pass=False))
    assert decision.qa_status == QA_FAIL
    assert decision.qa_executed is True
    assert not decision.accepted and not decision.publishable


def test_explicit_complete_authoritative_pass_is_accepted_and_publishable():
    decision = _decision(_verdict())
    assert decision.qa_status == QA_PASS
    assert decision.qa_executed is True
    assert decision.mandatory_checks_expected == decision.mandatory_checks_completed
    assert decision.acceptance_eligible is True
    assert decision.accepted is True
    assert decision.publishable is True and decision.deliverable is True


@pytest.mark.parametrize("missing", ["text_lines", "score", "critic_pass"])
def test_pass_like_result_missing_mandatory_measurement_is_unverified(missing):
    policy = _policy()
    completed = [name for name in policy.mandatory_checks if name != missing]
    decision = _decision(_verdict(policy=policy, completed=completed), policy=policy)
    assert decision.qa_status == QA_UNVERIFIED
    assert "QA_MANDATORY_CHECKS_MISSING" in decision.reason_codes
    assert missing in decision.mandatory_checks_missing
    assert not decision.accepted


def test_mandatory_not_checked_dimension_is_not_accepted():
    policy = _policy()
    completed = [name for name in policy.mandatory_checks if name != "garbled_text"]
    decision = _decision(_verdict(policy=policy, completed=completed), policy=policy)
    assert decision.qa_status == QA_UNVERIFIED
    assert "garbled_text" in decision.mandatory_checks_missing


def test_explicit_non_applicable_check_with_policy_reason_does_not_block():
    policy = _policy(logo_composited=True, expect_real_products=False)
    assert policy.non_applicable_checks["logo_ok"]
    assert policy.non_applicable_checks["placement_logical"]
    decision = _decision(_verdict(policy=policy), policy=policy)
    assert decision.qa_status == QA_PASS
    assert decision.accepted


@pytest.mark.parametrize("qa", [None, _verdict(overall=None), _verdict(overall=False)])
def test_persistence_projection_never_turns_missing_unverified_or_fail_into_true(qa):
    decision = poster_acceptance_from_output(_output(qa, qa_passed=True))
    assert decision.qa_passed is False
    assert decision.accepted is False


def test_repeated_finalization_input_cannot_elevate_unverified():
    output = _output(_verdict(overall=None), qa_passed=True)
    first = poster_acceptance_from_output(output)
    second = poster_acceptance_from_output(deepcopy(output))
    assert first == second
    assert first.qa_status == QA_UNVERIFIED and not first.qa_passed


def test_caller_supplied_pass_boolean_or_status_cannot_override_truth():
    output = _output(_verdict(overall=None), qa_passed=True)
    output.update({"qa_status": "pass", "accepted": True, "publishable": True})
    decision = poster_acceptance_from_output(output)
    assert decision.qa_status == QA_UNVERIFIED
    assert not decision.qa_passed and not decision.accepted and not decision.publishable


def test_qa_for_attempt_a_cannot_approve_attempt_b():
    qa = _verdict(attempt_id=ATTEMPT_A)
    identity_b = _identity(attempt_id=ATTEMPT_B)
    decision = _decision(qa, identity=identity_b, current=identity_b)
    assert decision.qa_status == QA_UNVERIFIED
    assert "QA_ATTEMPT_ID_MISMATCH" in decision.reason_codes
    assert not decision.accepted


def test_mutating_poster_bytes_after_qa_invalidates_the_old_result():
    qa = _verdict(payload=PNG_A)
    current = _identity(PNG_B)
    decision = _decision(qa, current=current)
    assert decision.qa_status == QA_UNVERIFIED
    assert "QA_CONTENT_MISMATCH" in decision.reason_codes
    assert not decision.accepted


def test_generated_unverified_asset_remains_identifiable_without_acceptance():
    decision = _decision(_verdict(overall=None))
    assert decision.generated is True
    assert decision.attempt_identity.content_sha256 == _identity().content_sha256
    assert decision.lifecycle_status == QA_UNVERIFIED
    assert not decision.accepted


def test_calendar_or_legacy_metadata_without_authoritative_qa_is_unverified():
    raw = {
        "image_b64": base64.b64encode(PNG_A).decode("ascii"),
        "qa_status": "pass", "qa_passed": True, "accepted": True,
    }
    decision = poster_acceptance_from_output(raw)
    assert decision.qa_status == QA_UNVERIFIED
    assert decision.generated and not decision.accepted and not decision.publishable


def test_frozen_no_glyph_class_fixture_is_rejected():
    qa = _verdict(
        overall=False, critic_pass=False, garbled_text=True,
        text_lines=["NO GLYPH", "Frozen headline"],
        reason="NO GLYPH is malformed text",
    )
    decision = _decision(qa)
    assert decision.qa_status == QA_FAIL
    assert "garbled_text" in decision.failed_mandatory_checks
    assert not decision.accepted


def test_better_of_acceptance_candidates_exclude_fail_and_unverified():
    from poster.pipeline import PosterGenResult, _attempt_rank
    from poster.concept import CreativeConcept
    from poster.schemas import PosterBrief, PosterDesignSpec

    def result(qa, score):
        qa = qa.model_copy(update={"score": score})
        decision = _decision(qa)
        return PosterGenResult(
            poster_path="p.png", filename="p.png", image_base64="",
            brief=PosterBrief(business_name="Frozen", category="test", headline="Headline"),
            spec=PosterDesignSpec(), concept=CreativeConcept(), brand_dna=None,
            background_path="b.png", model_used="m", prompt="", qa=qa,
            acceptance=decision,
        )

    passing = result(_verdict(), 7)
    unverified = result(_verdict(overall=None), 10)
    failed = result(_verdict(overall=False, critic_pass=False), 10)
    assert _attempt_rank(passing) > _attempt_rank(unverified)
    assert _attempt_rank(passing) > _attempt_rank(failed)
    assert [r for r in (unverified, failed) if r.acceptance.accepted] == []


def test_retry_exhaustion_truth_is_not_rewritten_by_projection():
    final = _verdict(overall=None, reason="retry budget exhausted; evaluator unavailable")
    decision = poster_acceptance_from_output(_output(final, qa_passed=True))
    assert decision.qa_status == QA_UNVERIFIED
    assert "exhausted" in final.reason
    assert not decision.qa_passed


def test_historical_output_remains_readable_without_invented_qa():
    old = {"image_b64": base64.b64encode(PNG_A).decode("ascii"), "filename": "old.png"}
    decision = poster_acceptance_from_output(old)
    assert decision.generated is True
    assert decision.qa_status == QA_UNVERIFIED
    assert "QA_RESULT_MISSING" in decision.reason_codes
    assert not decision.accepted


def test_policy_and_attempt_identity_are_serializable_and_exact():
    decision = _decision(_verdict())
    payload = decision.model_dump(mode="json")
    assert payload["policy_id"] == POSTER_QA_POLICY_ID
    assert payload["policy_version"] == POSTER_QA_POLICY_VERSION
    assert payload["attempt_identity"]["attempt_id"] == ATTEMPT_A
    assert payload["attempt_identity"]["candidate_id"] == "candidate-A"
    assert payload["attempt_identity"]["content_sha256"] == _identity().content_sha256


def test_pure_acceptance_boundary_makes_zero_network_provider_llm_or_db_calls(monkeypatch):
    import socket
    import urllib.request

    calls = {"external": 0}

    def forbidden(*args, **kwargs):
        calls["external"] += 1
        raise AssertionError("external boundary was called")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    try:
        import requests
        monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    except ImportError:
        pass
    import business_profile.llm as llm
    monkeypatch.setattr(llm, "default_caller", forbidden)

    decision = poster_acceptance_from_output(_output(_verdict()))
    assert decision.accepted is True
    assert calls == {"external": 0}
