from __future__ import annotations

from dataclasses import replace

from strategy.positioning.adapters import adapt_existing_intelligence
from strategy.positioning.contracts import (
    Availability, Claim, EvidenceStrength, VerificationStatus,
)
from strategy.positioning.verifier import PositioningDecisionVerifier

from test_gate2b_positioning_contracts import _verifier_fixture


def _codes(result) -> set[str]:
    return {defect["code"] for defect in result.defects}


def _linked_fixture():
    evidence, claims, question, decision = _verifier_fixture()
    evidence = (
        replace(evidence[0], raw_value="fast specialist onboarding",
                excerpt="fast specialist onboarding"),
        replace(evidence[1], raw_value="handoffs are difficult",
                excerpt="handoffs are difficult",
                limitations=("Single customer signal; prevalence is not established.",)),
        replace(evidence[2], raw_value="peer onboarding uses a self-service catalog",
                excerpt="peer onboarding uses a self-service catalog"),
    )
    claims = (
        replace(claims[0], statement="fast specialist onboarding"),
        replace(claims[1], statement="handoffs are difficult",
                evidence_strength=EvidenceStrength.WEAK,
                limitations=("Single customer signal; prevalence is not established.",)),
        replace(claims[2], statement="peer onboarding uses a self-service catalog"),
    )
    selected = replace(
        decision.options[0],
        target="operations teams; customer signal: handoffs are difficult",
        differentiated_value=("fast specialist onboarding; available alternative contrast: "
                              "peer onboarding uses a self-service catalog"),
    )
    alternative = replace(
        decision.options[1],
        target="operations teams; customer signal: handoffs are difficult",
        differentiated_value=("fast specialist onboarding; available alternative contrast: "
                              "peer onboarding uses a self-service catalog"),
    )
    return evidence, claims, question, replace(
        decision, options=(selected, alternative),
        rationale_claim_ids=tuple(c.claim_id for c in claims),
        accepted_claim_ids=tuple(c.claim_id for c in claims))


def test_swot_url_is_transport_not_customer_or_trend_semantics():
    brand = {"domain_key": "brand", "_swot_raw": {
        "strengths": [], "opportunities": [{
            "text": "A category topic is receiving new coverage",
            "evidence": "industry trend signal",
            "citation": ["https://news.test/trend"],
            "claim_strength": "directional_not_validated",
        }], "threats": [], "weaknesses": [{
            "text": "The branch team was difficult to deal with",
            "evidence": "complaint theme in own-brand customer voice",
            "citation": ["https://social.test/comment/1"],
            "claim_strength": "directional_not_validated",
        }, {
            "text": "Try our new menu",
            "evidence": "brand-authored social post",
            "citation": ["https://social.test/post/2"],
            "claim_strength": "directional_not_validated",
        }]}}

    adapted = adapt_existing_intelligence(brand, originating_swot_run_id="swot-1")
    by_url = {e.source_url: e for e in adapted.evidence if e.source_url}
    assert by_url["https://social.test/comment/1"].source_class == "CUSTOMER_VOICE"
    assert "prevalence" in " ".join(by_url["https://social.test/comment/1"].limitations)
    assert by_url["https://social.test/post/2"].source_class != "CUSTOMER_VOICE"
    assert by_url["https://news.test/trend"].source_class == "TREND_SIGNAL"


def test_profile_compound_claim_is_split_to_directly_quoted_atomic_claims():
    brand = {"domain_key": "brand", "_profile": {"description": {
        "value": "Fast delivery and nationwide support",
        "source_type": "inferred",
        "evidence": [
            {"page_url": "https://brand.test/delivery", "quote": "Fast delivery"},
            {"page_url": "https://brand.test/support", "quote": "Nationwide support"},
        ],
    }}}
    adapted = adapt_existing_intelligence(brand)
    brand_claims = [c for c in adapted.claims
                    if c.scope.get("role") == "BRAND_CAPABILITY"]
    assert {c.statement for c in brand_claims} == {"Fast delivery", "Nationwide support"}
    assert all(c.verification_status == VerificationStatus.SUPPORTED for c in brand_claims)
    assert all(len(c.supporting_evidence_ids) == 1 for c in brand_claims)
    assert "Fast delivery and nationwide support" not in {c.statement for c in brand_claims}


def test_unused_voc_still_fails_but_material_customer_link_satisfies_structure():
    evidence, claims, question, decision = _linked_fixture()
    unlinked = replace(
        decision,
        options=(replace(decision.options[0], target="operations teams"), decision.options[1]))
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims, decision=unlinked)
    assert "MISSING_CUSTOMER_EVIDENCE" in _codes(result)

    linked = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims, decision=decision)
    assert "MISSING_CUSTOMER_EVIDENCE" not in _codes(linked)
    assert linked.rubric["customer_relevance"].value == "PARTIAL"


def test_one_customer_signal_cannot_be_laundered_into_prevalence():
    evidence, claims, question, decision = _linked_fixture()
    prevalent_claim = replace(
        claims[1], statement="Customers commonly report difficult handoffs")
    claims = (claims[0], prevalent_claim, claims[2])
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims, decision=decision)
    assert any("single customer signal" in d["detail"].lower() for d in result.defects)
    assert result.eligible_for_approval is False


def test_competitor_claim_must_materially_appear_in_differentiated_value():
    evidence, claims, question, decision = _linked_fixture()
    unrelated = replace(
        decision,
        options=(replace(decision.options[0], differentiated_value="fast specialist delivery"),
                 decision.options[1]))
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims, decision=unrelated)
    assert "POSITIONING_NOT_DIFFERENTIATED" in _codes(result)

    linked = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims, decision=decision)
    assert "POSITIONING_NOT_DIFFERENTIATED" not in _codes(linked)


def test_partial_excerpt_cannot_support_compound_claim_or_decision_lineage():
    evidence, claims, question, decision = _linked_fixture()
    compound = replace(
        claims[0], statement="fast specialist onboarding and nationwide support",
        verification_status=VerificationStatus.SUPPORTED)
    claims = (compound, claims[1], claims[2])
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=evidence, claims=claims, decision=decision)
    assert "UNSUPPORTED_PREMISE" in _codes(result)
    assert any("whole claim" in d["detail"] for d in result.defects)
    assert result.eligible_for_approval is False


def test_absent_customer_or_competitor_class_still_blocks_approval():
    evidence, claims, question, decision = _linked_fixture()
    no_customer_evidence = tuple(e for e in evidence if e.source_class != "CUSTOMER_VOICE")
    no_customer_claims = tuple(c for c in claims if c.claim_id != claims[1].claim_id)
    no_customer_decision = replace(
        decision,
        options=tuple(replace(o, supporting_claim_ids=tuple(
            cid for cid in o.supporting_claim_ids if cid != claims[1].claim_id))
                      for o in decision.options),
        rationale_claim_ids=tuple(c.claim_id for c in no_customer_claims),
        accepted_claim_ids=tuple(c.claim_id for c in no_customer_claims))
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=no_customer_evidence, claims=no_customer_claims, decision=no_customer_decision)
    assert "MISSING_CUSTOMER_EVIDENCE" in _codes(result)

    no_comp_evidence = tuple(e for e in evidence if e.source_class != "COMPETITOR_ANALYSIS")
    no_comp_claims = tuple(c for c in claims if c.claim_id != claims[2].claim_id)
    no_comp_decision = replace(
        decision,
        options=tuple(replace(o, supporting_claim_ids=tuple(
            cid for cid in o.supporting_claim_ids if cid != claims[2].claim_id))
                      for o in decision.options),
        rationale_claim_ids=tuple(c.claim_id for c in no_comp_claims),
        accepted_claim_ids=tuple(c.claim_id for c in no_comp_claims))
    result = PositioningDecisionVerifier().verify(
        objective={"objective_id": "obj"}, research_question=question,
        evidence=no_comp_evidence, claims=no_comp_claims, decision=no_comp_decision)
    assert "MISSING_COMPETITOR_EVIDENCE" in _codes(result)
    assert "POSITIONING_NOT_DIFFERENTIATED" in _codes(result)

