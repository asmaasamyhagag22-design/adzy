from __future__ import annotations

from dataclasses import replace

import pytest

from strategy.positioning.adapters import adapt_existing_intelligence
from strategy.positioning.contracts import (
    Availability, Claim, EpistemicType, EvidenceRecord, EvidenceRequirement, EvidenceStrength,
    KnownValue, ObjectiveType, PositioningOption, ResearchQuestion, StrategicDecision,
    SufficiencyStatus, ValueStatus, VerificationStatus,
)
from strategy.positioning.objective import objective_from_legacy_text, objective_from_mapping
from strategy.positioning.service import build_positioning_strategy
from strategy.positioning.verifier import PositioningDecisionVerifier


def _field(value: str, url: str = "https://brand.test/about") -> dict:
    return {"value": value, "source_type": "extracted", "confidence": "high",
            "evidence": [{"page_url": url, "quote": value, "extractor": "fixture"}]}


def _strong_brand(country: str = "Qatar") -> dict:
    return {
        "domain_key": "brand_test",
        "_profile": {
            "source_url": "https://brand.test", "name": _field("Test Brand"),
            "category": _field("Business services"),
            "value_propositions": [_field("Fast specialist onboarding"),
                                   _field("Transparent delivery milestones")],
            "trust_signals": [_field("Published delivery process")],
            "audience_signals": [_field("Operations teams managing launch deadlines")],
            "locations": [{"country": country}],
        },
        "_full_result": {
            "generated_at": "2026-08-01T00:00:00+00:00",
            "marketing_intel": {"pain_points": [{
                "value": "Launch handoffs are difficult to coordinate",
                "evidence": [{"page_url": "https://social.test/comment/1",
                              "quote": "Handoffs are difficult", "extractor": "ig_snapshot"}]}]},
            "competitor_intel": {"Peer": {"usps": [{
                "value": "Peer onboarding emphasizes a broad self-service catalog",
                "evidence": [{"page_url": "https://peer.test/about",
                              "quote": "Broad self-service onboarding catalog",
                              "extractor": "fixture"}]}]}},
        },
    }


def _objective(geography: list[str] | None = None) -> dict:
    unknown = {"status": "UNKNOWN", "value": None}
    return {
        "schema_version": 1, "objective_id": "obj_test", "origin": "USER_EXPLICIT",
        "business": {
            "type": "QUALIFIED_LEADS_GROWTH", "description": "Increase qualified launch leads",
            "metric": unknown, "baseline": unknown, "target": unknown,
            "time_horizon": unknown, "geography": geography or ["Qatar"],
            "market": "", "constraints": [],
        },
        "marketing_role": "DEMAND_CAPTURE",
        "behavioral_objective": unknown,
        "media_optimization": {"status": "NOT_SET", "value": None},
    }


def test_explicit_objective_rejects_empty_and_preserves_unknowns():
    with pytest.raises(ValueError):
        objective_from_mapping({"business": {"type": "", "description": ""}})
    objective = objective_from_mapping(_objective())
    assert objective.metric == KnownValue(ValueStatus.UNKNOWN, None)
    assert objective.baseline.status == objective.target.status == ValueStatus.UNKNOWN
    assert objective.media_optimization.status == ValueStatus.NOT_SET


def test_old_free_text_maps_only_the_safe_case_and_asks_for_ambiguous_or_empty():
    awareness, state = objective_from_legacy_text("brand awareness")
    assert state == "MAPPED" and awareness.business_type == ObjectiveType.AWARENESS_GROWTH
    retention, state = objective_from_legacy_text("customer retention")
    assert state == "ASK_OWNER" and retention.business_type == ObjectiveType.OTHER
    empty, state = objective_from_legacy_text("let AI infer")
    assert empty is None and state == "ASK_OWNER"


def test_epistemic_adapter_does_not_upgrade_brand_or_owner_statements():
    brand = _strong_brand()
    brand["owner_inputs"] = {"constraint": "Do not reposition as a low-cost provider"}
    adapted = adapt_existing_intelligence(brand)
    brand_claims = [c for c in adapted.claims if c.scope.get("role") == "BRAND_CAPABILITY"]
    owner_claims = [c for c in adapted.claims if c.scope.get("role") == "OWNER_CONTEXT"]
    structural = [c for c in adapted.claims if c.scope.get("role") == "STRUCTURAL_CONTEXT"]
    assert brand_claims and all(c.epistemic_type == EpistemicType.SOURCE_ASSERTION
                                for c in brand_claims)
    assert owner_claims and all(c.epistemic_type == EpistemicType.HUMAN_ASSERTION
                                for c in owner_claims)
    assert structural and all(c.epistemic_type == EpistemicType.DIRECT_OBSERVATION
                              for c in structural)
    assert {a.value for a in Availability} == {
        "AVAILABLE", "UNKNOWN", "NOT_COLLECTED", "COLLECTION_FAILED",
        "NO_EVIDENCE_FOUND", "NOT_APPLICABLE"}


def test_service_returns_alternatives_tradeoffs_and_complete_lineage():
    objective = objective_from_mapping(_objective())
    build = build_positioning_strategy(
        _strong_brand(), objective=objective,
        requested_scope={"geography": ["Qatar"]}, swot_run_id="")
    canonical = build.output["canonical_strategy"]
    decision = canonical["decision"]
    evidence_ids = {e["evidence_id"] for e in canonical["evidence"]}
    claim_ids = {c["claim_id"] for c in canonical["claims"]}
    assert len(decision["options"]) in (2, 3)
    assert all(o["trade_offs"] and o["why_it_may_be_rejected"] for o in decision["options"])
    assert all(set(o["supporting_claim_ids"]) <= claim_ids for o in decision["options"])
    assert all(set(c["supporting_evidence_ids"]) <= evidence_ids
               for c in canonical["claims"])
    assert canonical["verification"]["eligible_for_approval"] is True
    assert build.requested_config["objective"]["business"]["metric"] == {
        "status": "UNKNOWN", "value": None}


def test_trial_value_prefers_declared_value_proposition_over_branch_ui_copy():
    brand = _strong_brand()
    brand["_profile"]["description"] = _field("Delivery from All (General branch)")
    brand["_profile"]["value_propositions"] = [
        _field("Convenient mobile app with deals and personalization"),
        _field("Hot offers"),
    ]
    brand["_profile"]["other_unique_insights"] = [
        _field("Try an easy new delivery app experience with offers and variety"),
    ]
    objective_payload = _objective()
    objective_payload["business"]["type"] = "TRIAL_GROWTH"
    objective_payload["business"]["description"] = "Increase first-time trial orders"

    build = build_positioning_strategy(
        brand, objective=objective_from_mapping(objective_payload),
        requested_scope={"geography": ["Qatar"]})
    selected = build.output["canonical_strategy"]["decision"]["options"][0]

    assert selected["consumer_value"] == (
        "Convenient mobile app with deals and personalization")
    assert "General branch" not in selected["consumer_value"]
    assert selected["consumer_value_claim_ids"]
    claims = {claim["claim_id"]: claim
              for claim in build.output["canonical_strategy"]["claims"]}
    consumer_claim = claims[selected["consumer_value_claim_ids"][0]]
    assert consumer_claim["scope"]["profile_field"] == "value_propositions"


def _verifier_fixture(evidence_geography: tuple[str, ...] = ("Qatar",)):
    evidence = tuple(EvidenceRecord(
        evidence_id=f"e_{kind}", availability=Availability.AVAILABLE,
        source_class=source, source_ref=kind, source_url=f"https://{kind}.test",
        subject_ref="brand", captured_at=None, observed_at=None,
        observed_at_status=ValueStatus.UNKNOWN, market="", geography=evidence_geography,
        channel="", time_scope="", raw_value=kind, excerpt=kind, freshness="CURRENT",
        relevance="RELEVANT", reliability="SOURCE_DEPENDENT")
        for kind, source in (("brand", "BRAND_OWNED"), ("customer", "CUSTOMER_VOICE"),
                             ("competitor", "COMPETITOR_ANALYSIS")))
    claims = tuple(Claim(
        claim_id=f"c_{e.evidence_id}", statement=e.raw_value,
        epistemic_type=EpistemicType.SOURCE_ASSERTION,
        scope={"role": "fixture"}, supporting_evidence_ids=(e.evidence_id,),
        verification_status=VerificationStatus.SUPPORTED,
        evidence_strength=EvidenceStrength.MODERATE) for e in evidence)
    option = PositioningOption(
        option_id="one", positioning_statement="For target, value in category, because proof.",
        target="target", frame_of_reference="category", differentiated_value="value",
        reason_to_believe="proof", supporting_claim_ids=tuple(c.claim_id for c in claims),
        hypothesis_claim_ids=(), missing_evidence_requirement_ids=(),
        trade_offs=("Focus narrows the addressable audience.",),
        why_it_may_be_rejected="Reject if the focus is too narrow.")
    alternative = replace(option, option_id="two", positioning_statement="Alternative positioning",
                          trade_offs=("Broader message weakens specificity.",),
                          why_it_may_be_rejected="Reject if specificity is required.")
    question = ResearchQuestion(
        question_id="rq", objective_id="obj", question="Which positioning?",
        scope={"geography": ["Qatar"]}, evidence_requirements=tuple(
            EvidenceRequirement(f"r{i}", kind, True, Availability.AVAILABLE)
            for i, kind in enumerate(("BRAND_CAPABILITY_PROOF", "CUSTOMER_RELEVANCE",
                                      "COMPETITIVE_DIFFERENTIATION"))),
        sufficiency_status=SufficiencyStatus.SUFFICIENT)
    decision = StrategicDecision(
        decision_id="d", objective_id="obj", research_question_id="rq",
        decision_state="DRAFT", selected_option_id="one", options=(option, alternative),
        rationale="Evidence-backed provisional choice.",
        rationale_claim_ids=tuple(c.claim_id for c in claims),
        accepted_claim_ids=tuple(c.claim_id for c in claims), unresolved_unknowns=(),
        contradictions=(), verification_result_id="")
    return evidence, claims, question, decision


def test_broken_claim_and_evidence_references_fail_lineage():
    evidence, claims, question, decision = _verifier_fixture()
    broken_option = replace(decision.options[0], supporting_claim_ids=("does_not_exist",))
    broken_decision = replace(decision, options=(broken_option, decision.options[1]))
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims, decision=broken_decision)
    assert result.status.value == "FAIL"
    assert any("Broken Claim reference" in d["detail"] for d in result.defects)

    broken_claim = replace(claims[0], supporting_evidence_ids=("missing_evidence",))
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=(broken_claim, *claims[1:]), decision=decision)
    assert result.status.value == "FAIL"
    assert any("Broken Evidence reference" in d["detail"] for d in result.defects)


def test_wrong_scope_and_missing_competitor_evidence_block_approval():
    evidence, claims, question, decision = _verifier_fixture(("Egypt",))
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims, decision=decision)
    assert result.eligible_for_approval is False
    assert "SCOPE_MISMATCH" in {d["code"] for d in result.defects}

    evidence = tuple(e for e in evidence if e.source_class != "COMPETITOR_ANALYSIS")
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims[:-1], decision=decision)
    assert result.eligible_for_approval is False
    assert "MISSING_COMPETITOR_EVIDENCE" in {d["code"] for d in result.defects}


def test_thin_evidence_never_forces_approval():
    brand = {"domain_key": "thin", "_profile": {
        "source_url": "https://thin.test", "value_propositions": [_field("One self-claim")]}}
    build = build_positioning_strategy(
        brand, objective=objective_from_mapping(_objective()),
        requested_scope={"geography": ["Qatar"]})
    canonical = build.output["canonical_strategy"]
    assert len(canonical["decision"]["options"]) == 2
    assert canonical["verification"]["eligible_for_approval"] is False
    assert canonical["decision"]["decision_state"] == "BLOCKED"
    assert canonical["decision"]["selected_option_id"] == ""
    assert canonical["decision"]["provisional_option_id"] == "pos_1"
