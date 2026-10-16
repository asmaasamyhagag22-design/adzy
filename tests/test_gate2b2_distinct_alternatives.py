from __future__ import annotations

from dataclasses import replace

from strategy.positioning.contracts import (
    Availability, Claim, EpistemicType, EvidenceRecord, EvidenceRequirement, EvidenceStrength,
    PositioningOption, ResearchQuestion, StrategicDecision, SufficiencyStatus, ValueStatus,
    VerificationStatus,
)
from strategy.positioning.distinctness import compare_positioning_options
from strategy.positioning.objective import objective_from_mapping
from strategy.positioning.service import build_positioning_strategy
from strategy.positioning.verifier import PositioningDecisionVerifier

from test_gate2b_positioning_contracts import _field, _objective


def _evidence(evidence_id: str, source_class: str, excerpt: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id, availability=Availability.AVAILABLE,
        source_class=source_class, source_ref=evidence_id,
        source_url=f"https://evidence.test/{evidence_id}", subject_ref="brand",
        captured_at=None, observed_at=None, observed_at_status=ValueStatus.UNKNOWN,
        market="", geography=("Qatar",), channel="fixture", time_scope="",
        raw_value=excerpt, excerpt=excerpt, freshness="CURRENT", relevance="RELEVANT",
        reliability="SOURCE_DEPENDENT",
        limitations=(("Single observed customer signal; prevalence is not established.",)
                     if source_class == "CUSTOMER_VOICE" else ()),
    )


def _claim(claim_id: str, evidence: EvidenceRecord, role: str) -> Claim:
    return Claim(
        claim_id=claim_id, statement=evidence.excerpt,
        epistemic_type=EpistemicType.SOURCE_ASSERTION, scope={"role": role},
        supporting_evidence_ids=(evidence.evidence_id,),
        verification_status=VerificationStatus.SUPPORTED,
        evidence_strength=(EvidenceStrength.WEAK
                           if evidence.source_class == "CUSTOMER_VOICE"
                           else EvidenceStrength.MODERATE),
        limitations=evidence.limitations,
    )


def _option(
    option_id: str, *, target: str, value: str, proof: str,
    support: tuple[str, ...], consumer_value: str | None = None,
) -> PositioningOption:
    return PositioningOption(
        option_id=option_id,
        positioning_statement=f"For {target}, choose {value}, because {proof}.",
        target=target, frame_of_reference="quick-service restaurant",
        differentiated_value=value, reason_to_believe=proof,
        supporting_claim_ids=support, hypothesis_claim_ids=(),
        missing_evidence_requirement_ids=(),
        trade_offs=(f"Choosing {value} deprioritizes the other evidenced value.",),
        why_it_may_be_rejected="Reject if this is not the stronger first-trial trigger.",
        consumer_value=consumer_value if consumer_value is not None else value,
        consumer_value_claim_ids=(support[0],),
        differentiation_claim_ids=(support[-1],),
        reason_to_believe_claim_ids=(support[0],),
        internal_supporting_claim_ids=support[1:],
    )


def _verified_fixture(options: tuple[PositioningOption, ...]):
    brand = _evidence("ev_brand", "BRAND_OWNED", "mobile convenience")
    customer = _evidence(
        "ev_customer", "CUSTOMER_VOICE", "first-time users report difficult ordering")
    competitor = _evidence(
        "ev_competitor", "COMPETITOR_ANALYSIS", "mobile convenience differs from peer catalog")
    evidence = (brand, customer, competitor)
    claims = (
        _claim("cl_brand", brand, "BRAND_CAPABILITY"),
        _claim("cl_customer", customer, "CUSTOMER_RELEVANCE"),
        _claim("cl_competitor", competitor, "COMPETITIVE_DIFFERENTIATION"),
    )
    question = ResearchQuestion(
        question_id="rq", objective_id="obj", question="Which positioning?",
        scope={"geography": ["Qatar"]},
        evidence_requirements=tuple(EvidenceRequirement(
            f"req_{index}", kind, True, Availability.AVAILABLE)
            for index, kind in enumerate((
                "BRAND_CAPABILITY_PROOF", "CUSTOMER_RELEVANCE",
                "COMPETITIVE_DIFFERENTIATION"))),
        sufficiency_status=SufficiencyStatus.SUFFICIENT,
    )
    decision = StrategicDecision(
        decision_id="decision", objective_id="obj", research_question_id="rq",
        decision_state="DRAFT", selected_option_id=options[0].option_id, options=options,
        rationale="The selected option is the strongest evidenced first-trial trigger.",
        rationale_claim_ids=tuple(c.claim_id for c in claims),
        accepted_claim_ids=tuple(c.claim_id for c in claims),
        unresolved_unknowns=(), contradictions=(), verification_result_id="",
    )
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims, decision=decision)
    return result


def _base_options() -> tuple[PositioningOption, PositioningOption]:
    support = ("cl_brand", "cl_customer", "cl_competitor")
    first = _option(
        "pos_1",
        target="first-time users; customer signal: first-time users report difficult ordering",
        value="mobile convenience", proof="mobile convenience", support=support)
    second = replace(first, option_id="pos_2")
    return first, second


def test_exact_duplicate_alternatives_fail_with_inspectable_fields():
    result = _verified_fixture(_base_options())
    defect = next(d for d in result.defects
                  if d["code"] == "POSITIONING_ALTERNATIVES_NOT_DISTINCT")
    assert defect["compared_option_ids"] == ["pos_1", "pos_2"]
    assert defect["fields_considered_equivalent"] == [
        "target", "frame_of_reference", "consumer_value",
        "differentiated_value", "reason_to_believe",
    ]


def test_paraphrased_materially_equivalent_alternatives_fail():
    first, second = _base_options()
    second = replace(
        second,
        target="users trying for the first time; customer signal: users report difficult ordering",
        consumer_value="easy ordering through the mobile application",
        differentiated_value="easy ordering through the mobile application",
        reason_to_believe="a convenient mobile app",
    )
    first = replace(
        first, consumer_value="convenient ordering with the mobile app",
        differentiated_value="convenient ordering with the mobile app",
        reason_to_believe="mobile application convenience",
    )
    assert compare_positioning_options(first, second).materially_duplicate is True


def test_distinct_target_alternatives_pass_when_both_targets_are_evidence_backed():
    first, second = _base_options()
    second = replace(
        second, target=("deal-seeking first-time users; customer signal: "
                        "first-time users report difficult ordering"))
    comparison = compare_positioning_options(first, second)
    assert comparison.materially_duplicate is False
    assert comparison.differing_fields == ("target",)


def test_distinct_value_propositions_pass_when_evidence_supported():
    first, second = _base_options()
    second = replace(
        second, consumer_value="loyalty points and rewards",
        differentiated_value="loyalty points and rewards",
        reason_to_believe="loyalty points and rewards")
    comparison = compare_positioning_options(first, second)
    assert comparison.materially_duplicate is False
    assert {"consumer_value", "differentiated_value", "reason_to_believe"} <= set(
        comparison.differing_fields)


def test_differing_wording_positioning_and_tradeoff_alone_do_not_pass():
    first, second = _base_options()
    second = replace(
        second,
        positioning_statement="Different prose around the same direction.",
        trade_offs=("This sentence is worded differently.",),
        why_it_may_be_rejected="Different prose again.",
    )
    assert compare_positioning_options(first, second).materially_duplicate is True


def test_proposer_does_not_invent_an_unevidenced_option_for_diversity():
    brand = {
        "domain_key": "single-evidence",
        "_profile": {
            "source_url": "https://single.test",
            "category": _field("restaurant"),
            "value_propositions": [
                _field("One evidenced value"), _field("One evidenced value")],
            "audience_signals": [_field("first-time users")],
        },
    }
    build = build_positioning_strategy(
        brand, objective=objective_from_mapping(_objective()),
        requested_scope={"geography": ["Qatar"]})
    canonical = build.output["canonical_strategy"]
    nonempty = {option["consumer_value"] for option in canonical["decision"]["options"]
                if option["consumer_value"]}
    assert nonempty == {"One evidenced value"}
    assert canonical["verification"]["eligible_for_approval"] is False


def test_duplicate_defect_always_blocks_approval_eligibility():
    result = _verified_fixture(_base_options())
    assert result.eligible_for_approval is False
    assert result.status.value == "NEEDS_REPAIR"

