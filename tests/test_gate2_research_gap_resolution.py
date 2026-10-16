from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from api.read_model import BrandReadModel
from strategy.positioning.adapters import adapt_existing_intelligence
from strategy.positioning.contracts import (
    Availability,
    Claim,
    EpistemicType,
    EvidenceRecord,
    EvidenceStrength,
    ResearchQuestion,
    SufficiencyStatus,
    ValueStatus,
    VerificationStatus,
)
from strategy.positioning.objective import objective_from_mapping
from strategy.positioning.projection import project_approved_strategy
from strategy.positioning.service import build_positioning_strategy

from test_gate2b_positioning_contracts import _objective, _strong_brand


def _canonical(brand: dict | None = None) -> dict:
    build = build_positioning_strategy(
        brand or _strong_brand(),
        objective=objective_from_mapping(_objective()),
        requested_scope={"geography": ["Qatar"], "market": "", "channel": ""},
    )
    return build.output["canonical_strategy"]


def _assessment(canonical: dict) -> dict:
    return canonical["research_sufficiency_assessment"]


def _item(assessment: dict, requirement_id: str) -> dict:
    return next(
        row for row in assessment["requirement_results"]
        if row["requirement_id"] == requirement_id
    )


def _research_api():
    from strategy.positioning.contracts import (
        ResearchCriticality,
        ResolutionAction,
        ResearchRequirementVerdict,
    )
    from strategy.positioning.research import (
        RESEARCH_POLICY_ID,
        RESEARCH_POLICY_VERSION,
        evaluate_research_sufficiency,
    )

    return SimpleNamespace(
        criticality=ResearchCriticality,
        action=ResolutionAction,
        verdict=ResearchRequirementVerdict,
        policy_id=RESEARCH_POLICY_ID,
        policy_version=RESEARCH_POLICY_VERSION,
        evaluate=evaluate_research_sufficiency,
    )


def _requirement(
    *,
    requirement_id: str = "req_custom",
    accepted_source_classes: tuple[str, ...] = ("CUSTOMER_VOICE",),
    required_claim_roles: tuple[str, ...] = ("CUSTOMER_RELEVANCE",),
    acceptable_epistemic_types: tuple[EpistemicType, ...] = (
        EpistemicType.DIRECT_OBSERVATION,
    ),
    acceptable_verification_statuses: tuple[VerificationStatus, ...] = (
        VerificationStatus.SUPPORTED,
        VerificationStatus.CORROBORATED,
    ),
    minimum_evidence_strength: EvidenceStrength = EvidenceStrength.MODERATE,
    minimum_independent_origins: int = 1,
    owner_input_only: bool = False,
    allow_disclosure: bool = False,
    scope: dict | None = None,
):
    from strategy.positioning.contracts import EvidenceRequirement, ResearchCriticality

    return EvidenceRequirement(
        requirement_id=requirement_id,
        requirement_type="CUSTOM_POLICY_REQUIREMENT",
        required=True,
        status=Availability.UNKNOWN,
        decision_dependency="Final positioning selection",
        criticality=ResearchCriticality.BLOCKING,
        accepted_source_classes=accepted_source_classes,
        required_claim_roles=required_claim_roles,
        acceptable_epistemic_types=acceptable_epistemic_types,
        acceptable_verification_statuses=acceptable_verification_statuses,
        minimum_evidence_strength=minimum_evidence_strength,
        minimum_independent_origins=minimum_independent_origins,
        subject_ref="brand_test",
        scope=scope or {"geography": ["Qatar"], "market": "", "channel": ""},
        owner_input_only=owner_input_only,
        allow_disclosure=allow_disclosure,
    )


def _question(requirement) -> ResearchQuestion:
    return ResearchQuestion(
        question_id="rq_custom",
        objective_id="obj_test",
        question="Is the requirement sufficient for final positioning selection?",
        scope={"geography": ["Qatar"], "market": "", "channel": ""},
        evidence_requirements=(requirement,),
        sufficiency_status=SufficiencyStatus.NOT_ASSESSED,
    )


def _evidence(
    *,
    evidence_id: str = "ev_custom",
    availability: Availability = Availability.AVAILABLE,
    source_class: str = "CUSTOMER_VOICE",
    geography: tuple[str, ...] = ("Qatar",),
    market: str = "",
    channel: str = "",
    origin_group: str | None = "origin_customer_1",
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        availability=availability,
        source_class=source_class,
        source_ref=f"fixture:{evidence_id}",
        source_url=f"https://evidence.test/{evidence_id}",
        subject_ref="brand_test",
        captured_at=None,
        observed_at=None,
        observed_at_status=ValueStatus.UNKNOWN,
        market=market,
        geography=geography,
        channel=channel,
        time_scope="",
        raw_value="One customer reported a difficult handoff.",
        excerpt="One customer reported a difficult handoff.",
        freshness="CURRENT",
        relevance="RELEVANT",
        reliability="SOURCE_DEPENDENT",
        limitations=("Single observed occurrence; prevalence is unknown.",),
        origin_group=origin_group,
    )


def _claim(
    evidence_id: str = "ev_custom",
    *,
    claim_id: str = "cl_custom",
    role: str = "CUSTOMER_RELEVANCE",
    epistemic_type: EpistemicType = EpistemicType.DIRECT_OBSERVATION,
    strength: EvidenceStrength = EvidenceStrength.MODERATE,
    verification: VerificationStatus = VerificationStatus.SUPPORTED,
    statement: str = "One customer reported a difficult handoff.",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=statement,
        epistemic_type=epistemic_type,
        scope={"role": role},
        supporting_evidence_ids=(evidence_id,),
        verification_status=verification,
        evidence_strength=strength,
        limitations=("Occurrence only; prevalence is unknown.",),
    )


def test_missing_customer_voice_produces_typed_collect_specific_action():
    brand = _strong_brand()
    brand["_full_result"]["marketing_intel"]["pain_points"] = []

    assessment = _assessment(_canonical(brand))
    row = _item(assessment, "req_customer_relevance")

    assert row["blocking"] is True
    assert row["current_status"] == "UNMET"
    assert row["reason_code"] == "NO_QUALIFYING_EVIDENCE"
    assert row["resolution_action"] == "COLLECT_SPECIFIC"
    assert row["required_evidence_class"] == ["CUSTOMER_VOICE"]
    assert row["decision_dependency"]
    assert row["action_details"]
    assert row["stop_condition"]["description"]


def test_reason_to_believe_is_explicit_and_value_promise_is_not_proof_fallback():
    canonical = _canonical()
    assessment = _assessment(canonical)
    decision = canonical["decision"]
    claims = {row["claim_id"]: row for row in canonical["claims"]}
    selected = next(
        row for row in decision["options"]
        if row["option_id"] == decision["selected_option_id"]
    )

    assert _item(assessment, "req_reason_to_believe")["current_status"] == "SATISFIED"
    assert selected["reason_to_believe_claim_ids"]
    assert set(selected["reason_to_believe_claim_ids"]).isdisjoint(
        selected["consumer_value_claim_ids"]
    )
    assert all(
        claims[claim_id]["scope"]["role"] == "REASON_TO_BELIEVE"
        for claim_id in selected["reason_to_believe_claim_ids"]
    )
    assert selected["reason_to_believe"] != selected["consumer_value"]


def test_self_assertion_cannot_satisfy_policy_requiring_verified_direct_observation():
    api = _research_api()
    requirement = _requirement(
        accepted_source_classes=("BRAND_OWNED",),
        required_claim_roles=("REASON_TO_BELIEVE",),
        acceptable_epistemic_types=(EpistemicType.DIRECT_OBSERVATION,),
        minimum_evidence_strength=EvidenceStrength.STRONG,
    )
    evidence = _evidence(source_class="BRAND_OWNED")
    claim = _claim(
        role="REASON_TO_BELIEVE",
        epistemic_type=EpistemicType.SOURCE_ASSERTION,
        strength=EvidenceStrength.MODERATE,
        statement="The brand says it has a published process.",
    )

    assessment = api.evaluate(_question(requirement), (evidence,), (claim,))
    row = assessment.requirement_results[0]

    assert row.current_status == api.verdict.UNMET
    assert row.reason_code.value == "EPISTEMIC_STRENGTH_TOO_WEAK"
    assert row.qualifying_evidence_ids == ()
    assert assessment.eligible_for_final_selection is False


def test_owner_only_unknown_routes_to_ask_owner():
    api = _research_api()
    requirement = _requirement(
        requirement_id="req_owner_objective",
        accepted_source_classes=("OWNER_INPUT",),
        required_claim_roles=("OWNER_CONTEXT",),
        acceptable_epistemic_types=(EpistemicType.HUMAN_ASSERTION,),
        minimum_evidence_strength=EvidenceStrength.WEAK,
        owner_input_only=True,
    )

    assessment = api.evaluate(_question(requirement), (), ())
    row = assessment.requirement_results[0]

    assert row.resolution_action == api.action.ASK_OWNER
    assert row.blocking is True


@pytest.mark.parametrize(
    "availability",
    [
        Availability.UNKNOWN,
        Availability.NOT_COLLECTED,
        Availability.COLLECTION_FAILED,
        Availability.NO_EVIDENCE_FOUND,
    ],
)
def test_nonavailable_evidence_never_satisfies_a_mandatory_requirement(availability):
    api = _research_api()
    requirement = _requirement()
    evidence = _evidence(availability=availability)
    claim = _claim()

    assessment = api.evaluate(_question(requirement), (evidence,), (claim,))
    row = assessment.requirement_results[0]

    assert row.current_status != api.verdict.SATISFIED
    assert row.qualifying_evidence_ids == ()
    assert assessment.eligible_for_final_selection is False


def test_evaluator_exception_serializes_fail_closed_assessment(monkeypatch):
    import strategy.positioning.service as service

    def broken(*_args, **_kwargs):
        raise RuntimeError("fixture evaluator failure")

    monkeypatch.setattr(service, "evaluate_research_sufficiency", broken, raising=False)
    canonical = _canonical()
    assessment = _assessment(canonical)

    assert assessment["execution_status"] == "UNVERIFIED"
    assert assessment["overall_status"] == "CANNOT_PROCEED"
    assert assessment["eligible_for_final_selection"] is False
    assert canonical["decision"]["decision_state"] == "BLOCKED"
    assert canonical["decision"]["selected_option_id"] == ""
    assert canonical["verification"]["eligible_for_approval"] is False


def test_evaluator_nonexecution_cannot_fall_back_to_presence_heuristics(monkeypatch):
    import strategy.positioning.service as service

    monkeypatch.setattr(
        service, "evaluate_research_sufficiency", lambda *_a, **_k: None, raising=False
    )
    canonical = _canonical()

    assert _assessment(canonical)["execution_status"] == "UNVERIFIED"
    assert canonical["decision"]["selection_status"] != "FINAL"
    assert canonical["verification"]["eligible_for_approval"] is False


def test_missing_evaluator_inputs_return_unverified_instead_of_raising():
    api = _research_api()

    assessment = api.evaluate(_question(_requirement()), None, None)

    assert assessment.execution_status.value == "UNVERIFIED"
    assert assessment.overall_status == SufficiencyStatus.CANNOT_PROCEED
    assert assessment.eligible_for_final_selection is False


def test_incomplete_typed_assessment_is_rejected_fail_closed(monkeypatch):
    import strategy.positioning.service as service

    real_evaluate = service.evaluate_research_sufficiency

    def incomplete(question, evidence, claims):
        complete = real_evaluate(question, evidence, claims)
        return replace(
            complete,
            requirement_results=(),
            blocking_requirement_ids=(),
            eligible_for_final_selection=True,
        )

    monkeypatch.setattr(service, "evaluate_research_sufficiency", incomplete)
    canonical = _canonical()

    assert _assessment(canonical)["execution_status"] == "UNVERIFIED"
    assert canonical["decision"]["decision_state"] == "BLOCKED"
    assert canonical["verification"]["eligible_for_approval"] is False


def test_wrong_scope_is_a_limitation_not_a_contradiction_or_qualification():
    api = _research_api()
    requirement = _requirement(scope={"geography": ["Qatar"], "market": "", "channel": ""})
    evidence = _evidence(geography=("Egypt",))
    claim = _claim()

    assessment = api.evaluate(_question(requirement), (evidence,), (claim,))
    row = assessment.requirement_results[0]

    assert row.current_status == api.verdict.UNMET
    assert row.reason_code.value == "SCOPE_MISMATCH"
    assert row.qualifying_evidence_ids == ()
    assert "SCOPE_MISMATCH" in row.limitation_codes
    assert "CONTRADICTION" not in row.limitation_codes


def test_same_origin_duplicates_count_once():
    api = _research_api()
    requirement = _requirement(minimum_independent_origins=2)
    evidence = (
        _evidence(evidence_id="ev_one", origin_group="one_saved_origin"),
        _evidence(evidence_id="ev_two", origin_group="one_saved_origin"),
    )
    claims = (
        _claim("ev_one", claim_id="cl_one"),
        _claim("ev_two", claim_id="cl_two"),
    )

    row = api.evaluate(_question(requirement), evidence, claims).requirement_results[0]

    assert row.independent_origin_count == 1
    assert row.independence_status.value == "INSUFFICIENT"
    assert row.current_status == api.verdict.UNMET


def test_different_urls_with_unknown_origin_do_not_prove_independence():
    api = _research_api()
    requirement = _requirement(minimum_independent_origins=2)
    evidence = (
        _evidence(evidence_id="ev_one", origin_group=None),
        _evidence(evidence_id="ev_two", origin_group=None),
    )
    claims = (
        _claim("ev_one", claim_id="cl_one"),
        _claim("ev_two", claim_id="cl_two"),
    )

    row = api.evaluate(_question(requirement), evidence, claims).requirement_results[0]

    assert row.independent_origin_count == 0
    assert row.independence_status.value == "UNKNOWN"
    assert row.current_status != api.verdict.SATISFIED


def test_one_customer_observation_stays_occurrence_not_prevalence():
    api = _research_api()
    requirement = _requirement(minimum_independent_origins=1)

    assessment = api.evaluate(
        _question(requirement), (_evidence(),), (_claim(),)
    )
    row = assessment.requirement_results[0]

    assert row.current_status == api.verdict.SATISFIED
    joined = " ".join((*row.limitations, *assessment.limitations)).lower()
    assert "occurrence" in joined
    assert "prevalence" in joined


def test_blocking_gap_makes_options_provisional_and_removes_final_selection():
    brand = _strong_brand()
    brand["_full_result"]["marketing_intel"]["pain_points"] = []
    canonical = _canonical(brand)
    decision = canonical["decision"]

    assert _assessment(canonical)["blocking_requirement_ids"]
    assert decision["decision_state"] == "BLOCKED"
    assert decision["selection_status"] == "PROVISIONAL"
    assert decision["selected_option_id"] == ""
    assert decision["provisional_option_id"] == "pos_1"
    assert all(row["option_status"] == "PROVISIONAL" for row in decision["options"])
    assert canonical["verification"]["eligible_for_approval"] is False


def test_disclosure_cannot_waive_a_blocking_requirement():
    api = _research_api()
    requirement = _requirement(allow_disclosure=True)

    assessment = api.evaluate(_question(requirement), (), ())
    row = assessment.requirement_results[0]

    assert row.blocking is True
    assert row.resolution_action != api.action.PROCEED_WITH_DISCLOSURE
    assert assessment.eligible_for_final_selection is False


def test_nonblocking_disclosure_and_blocking_defer_are_typed_actions():
    api = _research_api()
    nonblocking = replace(
        _requirement(),
        required=False,
        criticality=api.criticality.DECISION_LIMITING,
        allow_disclosure=True,
    )
    blocking_rtb = replace(
        _requirement(
            accepted_source_classes=("BRAND_OWNED",),
            required_claim_roles=("REASON_TO_BELIEVE",),
        ),
        requirement_type="REASON_TO_BELIEVE_PROOF",
    )

    disclosed = api.evaluate(_question(nonblocking), (), ())
    deferred = api.evaluate(_question(blocking_rtb), (), ())

    assert disclosed.requirement_results[0].resolution_action == (
        api.action.PROCEED_WITH_DISCLOSURE
    )
    assert disclosed.eligible_for_final_selection is True
    assert deferred.requirement_results[0].resolution_action == api.action.DEFER_DECISION
    assert deferred.eligible_for_final_selection is False


def test_sufficient_case_preserves_final_choice_and_exact_existing_rationale():
    canonical = _canonical()
    assessment = _assessment(canonical)
    decision = canonical["decision"]

    assert assessment["execution_status"] == "EXECUTED"
    assert assessment["overall_status"] == "SUFFICIENT"
    assert assessment["eligible_for_final_selection"] is True
    assert assessment["policy_id"] and assessment["policy_version"]
    assert decision["selected_option_id"] == "pos_1"
    assert decision["selection_status"] == "FINAL"
    assert decision["options"][0]["option_status"] == "SELECTED"
    assert decision["rationale"] == (
        "The provisional selection prioritizes Fast specialist onboarding as the evidenced "
        "value most directly related to QUALIFIED_LEADS_GROWTH (Increase qualified launch "
        "leads); the other options retain different evidenced trial triggers and explicit "
        "trade-offs."
    )
    assert canonical["verification"]["eligible_for_approval"] is True


def test_unknown_policy_version_fails_closed():
    api = _research_api()
    assessment = api.evaluate(
        _question(_requirement()),
        (_evidence(),),
        (_claim(),),
        policy_id=api.policy_id,
        policy_version="unknown-policy-version",
    )

    assert assessment.execution_status.value == "UNVERIFIED"
    assert assessment.overall_status == SufficiencyStatus.CANNOT_PROCEED
    assert assessment.eligible_for_final_selection is False


def test_every_resolution_item_is_machine_readable_and_policy_bound():
    assessment = _assessment(_canonical())
    mandatory = {
        "requirement_id",
        "decision_dependency",
        "criticality",
        "current_status",
        "blocking",
        "reason_code",
        "reason_detail",
        "evidence_considered_ids",
        "qualifying_evidence_ids",
        "required_evidence_class",
        "required_epistemic_strength",
        "subject_ref",
        "scope",
        "independence_status",
        "independent_origin_count",
        "resolution_action",
        "action_details",
        "stop_condition",
        "unknowns",
        "policy_id",
        "policy_version",
    }

    assert assessment["requirement_results"]
    for row in assessment["requirement_results"]:
        assert mandatory <= row.keys()
        assert row["policy_id"] == assessment["policy_id"]
        assert row["policy_version"] == assessment["policy_version"]
        assert row["stop_condition"]["kind"]
        assert row["stop_condition"]["description"]


def test_old_strategy_run_without_assessment_remains_readable_when_approved():
    old = _canonical()
    old.pop("research_sufficiency_assessment")
    for row in old["decision"]["options"]:
        row.pop("option_status")
    old["decision"].pop("selection_status")
    old["decision"].pop("provisional_option_id")

    projected = project_approved_strategy(
        {"canonical_strategy": old}, run_id="old-run", approval_state="approved"
    )

    assert projected is not None
    assert projected["strategy_version_id"] == "old-run"


def test_new_current_run_does_not_inherit_old_run_approval():
    canonical = _canonical()
    old_run = SimpleNamespace(
        id="run-old",
        output={"canonical_strategy": canonical},
        requested_config={"objective": _objective()},
    )
    new_run = SimpleNamespace(
        id="run-new",
        output={"canonical_strategy": canonical},
        requested_config={"objective": _objective()},
    )
    brand = SimpleNamespace(
        id="brand-id",
        current_profile_id=None,
        source_url="https://brand.test",
        domain_key="brand_test",
        owner_id="owner-id",
        created_at=None,
        updated_at=None,
    )

    class Service:
        current = old_run
        decision = SimpleNamespace(decision="approved")

        def current_successful_run(self, *, kind, **_kwargs):
            return self.current if kind == "strategy" else None

        def decision_for_current_strategy(self, **_kwargs):
            return self.decision

        def approval_for_current_swot(self, **_kwargs):
            return None

        def owner_inputs(self, **_kwargs):
            return {}

        def list_competitors(self, **_kwargs):
            return []

    service = Service()
    read_model = BrandReadModel(service)
    old_document = read_model.document(brand=brand, user_id="owner-id")
    service.current = new_run
    service.decision = None
    new_document = read_model.document(brand=brand, user_id="owner-id")

    assert old_document["strategy_version_id"] == "run-old"
    assert old_document["strategy_approval_state"] == "approved"
    assert new_document["strategy_run_id"] == "run-new"
    assert new_document["strategy_version_id"] == ""
    assert new_document["strategy_approval_state"] == "pending"
    assert new_document["has_strategy"] is False


def test_build_is_offline_and_contacts_no_network_or_provider(monkeypatch):
    import socket
    from api import webapp_analysis
    from competitor import discovery
    from db import session as db_session

    contacts: list[str] = []

    def forbidden(kind):
        def fail(*_args, **_kwargs):
            contacts.append(kind)
            raise AssertionError(f"{kind} is forbidden")
        return fail

    monkeypatch.setattr(socket, "socket", forbidden("network"))
    monkeypatch.setattr(webapp_analysis, "_caller", forbidden("provider"))
    monkeypatch.setattr(
        webapp_analysis, "fetch_trends_for_brand", forbidden("collector")
    )
    monkeypatch.setattr(
        discovery, "discover_competitors", forbidden("collector")
    )
    monkeypatch.setattr(
        db_session, "get_session_factory", forbidden("production_db_factory")
    )

    canonical = _canonical()

    assert canonical["research_sufficiency_assessment"]
    assert contacts == []


def test_adapter_does_not_infer_origin_group_from_source_url():
    adapted = adapt_existing_intelligence(_strong_brand())

    assert adapted.evidence
    assert all(row.origin_group is None for row in adapted.evidence)


def test_offline_benchmark_exposes_gap_resolution_without_quality_claims():
    from strategy.positioning.benchmark import run_offline_benchmark

    result = run_offline_benchmark()

    assert result["quality_preference_measured"] is False
    assert result["provider_backed"] is False
    for row in result["cases"]:
        candidate = row["B"]
        assert {
            "requirement_coverage",
            "blocking_gaps",
            "resolution_actions",
            "policy_id",
            "policy_version",
            "unknown_count",
            "eligible_for_approval",
            "selection_status",
            "provisional_options",
            "final_options",
            "verifier_defects",
            "compatibility",
            "latency_ms",
        } <= candidate.keys()
        assert candidate["provider_calls"] == 0
        assert candidate["cost_usd"] == 0
