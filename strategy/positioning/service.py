"""Pure orchestration for Brand + explicit Objective -> verified positioning payload."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping
from uuid import uuid4

from .adapters import AdaptedIntelligence, adapt_existing_intelligence
from .contracts import (
    ApprovalState, AssessmentExecutionStatus, Availability, BusinessObjective, Claim,
    DecisionSelectionStatus, DecisionState, EpistemicType, EvidenceRequirement,
    EvidenceStrength, OptionStatus, PositioningOption, ResearchCriticality,
    ResearchQuestion, ResearchRequirementVerdict, ResearchSufficiencyAssessment,
    StrategicDecision, SufficiencyStatus, VerificationStatus, VerifierStatus,
    objective_to_dict, to_dict,
)
from .distinctness import normalize_material, semantic_overlap
from .research import (
    evaluate_research_sufficiency,
    failed_sufficiency_assessment,
)
from .verifier import PositioningDecisionVerifier


_OBJECTIVE_VALUE_SIGNALS = {
    "QUALIFIED_LEADS_GROWTH": ("service", "specialist", "process", "consult", "catering", "business"),
    "REVENUE_GROWTH": ("offer", "product", "service", "catering", "delivery", "purchase"),
    "NEW_MARKET_ENTRY": ("community", "accessible", "international", "market", "location"),
    "TRIAL_GROWTH": (
        "experience", "try", "offer", "event", "delivery", "welcoming",
        "convenient", "easy", "app", "order", "deal", "new", "menu", "variety",
    ),
    "REPEAT_PURCHASE_GROWTH": ("loyalty", "program", "community", "repeat", "membership"),
    "AWARENESS_GROWTH": ("unique", "story", "culture", "community", "mission", "concept"),
}

_CONSUMER_VALUE_SOURCE_PRIORITY = {
    "value_propositions": 0,
    "tagline": 1,
    "other_unique_insights": 2,
    "description": 3,
}


@dataclass(frozen=True, slots=True)
class CanonicalStrategyBuild:
    """The pure build result passed to the persistence adapter by the job runner."""
    output: Mapping[str, Any]
    requested_config: Mapping[str, Any]
    effective_config: Mapping[str, Any]
    dependencies: tuple[tuple[str, str], ...] = ()
    schema_version: int = 1


def _role_claims(adapted: AdaptedIntelligence, role: str) -> list[Claim]:
    return [c for c in adapted.claims if str(c.scope.get("role") or "") == role]


def _terms(text: str) -> set[str]:
    stop = {"with", "from", "that", "this", "their", "brand", "business", "service",
            "services", "customer", "customers", "market", "offers", "people"}
    return {w for w in re.findall(r"\w+", str(text or "").lower())
            if len(w) >= 4 and w not in stop}


def _claim_group_key(claim: Claim, evidence_by_id: Mapping[str, Any]) -> str:
    """Collapse several quotes from one stored premise without merging unrelated evidence."""
    field = str(claim.scope.get("profile_field") or "")
    raw_values = []
    for evidence_id in claim.supporting_evidence_ids:
        row = evidence_by_id.get(evidence_id)
        raw = getattr(row, "raw_value", None)
        if isinstance(raw, (str, int, float)) and str(raw).strip():
            raw_values.append(str(raw))
    material = raw_values[0] if raw_values else claim.statement
    return f"{field}|{normalize_material(material)}"


def _claim_source_material(claim: Claim, evidence_by_id: Mapping[str, Any]) -> str:
    values = [claim.statement]
    for evidence_id in claim.supporting_evidence_ids:
        row = evidence_by_id.get(evidence_id)
        raw = getattr(row, "raw_value", None)
        if isinstance(raw, (str, int, float)) and str(raw).strip():
            values.append(str(raw))
    return " ".join(values)


def _unique_claims(
    claims: list[Claim], evidence_by_id: Mapping[str, Any],
) -> list[Claim]:
    unique: list[Claim] = []
    seen: set[str] = set()
    for claim in claims:
        key = _claim_group_key(claim, evidence_by_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(claim)
    return unique


def _related_claim(
    anchor: Claim | None, candidates: list[Claim], evidence_by_id: Mapping[str, Any],
    *, fallback_index: int = 0, require_overlap: bool = False,
) -> Claim | None:
    if not anchor or not candidates:
        return None
    anchor_text = anchor.statement
    anchor_source = _claim_source_material(anchor, evidence_by_id)
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (
            -semantic_overlap(anchor_text, pair[1].statement),
            -semantic_overlap(
                anchor_source, _claim_source_material(pair[1], evidence_by_id)),
            pair[0],
        ),
    )
    overlap = semantic_overlap(anchor_text, ranked[0][1].statement)
    if overlap:
        return ranked[0][1]
    if require_overlap:
        return None
    return candidates[fallback_index % len(candidates)]


def _research_question(
    objective: BusinessObjective, adapted: AdaptedIntelligence, requested_scope: Mapping[str, Any],
) -> ResearchQuestion:
    brand = bool(_role_claims(adapted, "BRAND_CAPABILITY"))
    customer = any(e.availability == Availability.AVAILABLE and e.source_class == "CUSTOMER_VOICE"
                   for e in adapted.evidence)
    competitor = any(e.availability == Availability.AVAILABLE
                     and e.source_class == "COMPETITOR_ANALYSIS" for e in adapted.evidence)
    reason_to_believe = bool(_role_claims(adapted, "REASON_TO_BELIEVE"))
    measured = objective.metric.status.value == "KNOWN"
    subject_ref = next(
        (row.subject_ref for row in adapted.evidence if row.subject_ref),
        "",
    )
    states = {
        "req_brand_capability": Availability.AVAILABLE if brand else Availability.NO_EVIDENCE_FOUND,
        "req_customer_relevance": Availability.AVAILABLE if customer else Availability.NOT_COLLECTED,
        "req_competitive_differentiation": (
            Availability.AVAILABLE if competitor else Availability.NOT_COLLECTED),
        "req_reason_to_believe": (
            Availability.AVAILABLE if reason_to_believe else Availability.NO_EVIDENCE_FOUND),
        "req_objective_measurement": Availability.AVAILABLE if measured else Availability.UNKNOWN,
    }
    requirements = (
        EvidenceRequirement(
            "req_brand_capability", "BRAND_CAPABILITY_PROOF", True,
            states["req_brand_capability"],
            decision_dependency="The selected positioning's evidenced consumer value",
            criticality=ResearchCriticality.BLOCKING,
            accepted_source_classes=("BRAND_OWNED",),
            required_claim_roles=("BRAND_CAPABILITY",),
            acceptable_epistemic_types=(
                EpistemicType.SOURCE_ASSERTION, EpistemicType.INFERENCE,
                EpistemicType.DIRECT_OBSERVATION),
            acceptable_verification_statuses=(
                VerificationStatus.SUPPORTED, VerificationStatus.CORROBORATED,
                VerificationStatus.PARTIALLY_SUPPORTED),
            minimum_evidence_strength=EvidenceStrength.WEAK,
            subject_ref=subject_ref, scope=dict(requested_scope)),
        EvidenceRequirement(
            "req_customer_relevance", "CUSTOMER_RELEVANCE", True,
            states["req_customer_relevance"],
            decision_dependency="The selected target/customer problem",
            criticality=ResearchCriticality.BLOCKING,
            accepted_source_classes=("CUSTOMER_VOICE",),
            required_claim_roles=("CUSTOMER_RELEVANCE",),
            acceptable_epistemic_types=(
                EpistemicType.SOURCE_ASSERTION, EpistemicType.INFERENCE,
                EpistemicType.DIRECT_OBSERVATION),
            acceptable_verification_statuses=(
                VerificationStatus.SUPPORTED, VerificationStatus.CORROBORATED,
                VerificationStatus.PARTIALLY_SUPPORTED),
            minimum_evidence_strength=EvidenceStrength.WEAK,
            subject_ref=subject_ref, scope=dict(requested_scope)),
        EvidenceRequirement(
            "req_competitive_differentiation", "COMPETITIVE_DIFFERENTIATION", True,
            states["req_competitive_differentiation"],
            decision_dependency="The selected differentiated value",
            criticality=ResearchCriticality.BLOCKING,
            accepted_source_classes=("COMPETITOR_ANALYSIS",),
            required_claim_roles=("COMPETITIVE_DIFFERENTIATION",),
            acceptable_epistemic_types=(
                EpistemicType.SOURCE_ASSERTION, EpistemicType.INFERENCE,
                EpistemicType.DIRECT_OBSERVATION),
            acceptable_verification_statuses=(
                VerificationStatus.SUPPORTED, VerificationStatus.CORROBORATED,
                VerificationStatus.PARTIALLY_SUPPORTED),
            minimum_evidence_strength=EvidenceStrength.WEAK,
            subject_ref=subject_ref, scope=dict(requested_scope)),
        EvidenceRequirement(
            "req_reason_to_believe", "REASON_TO_BELIEVE_PROOF", True,
            states["req_reason_to_believe"],
            decision_dependency="The selected reason to believe",
            criticality=ResearchCriticality.BLOCKING,
            accepted_source_classes=("BRAND_OWNED",),
            required_claim_roles=("REASON_TO_BELIEVE",),
            acceptable_epistemic_types=(
                EpistemicType.SOURCE_ASSERTION, EpistemicType.INFERENCE,
                EpistemicType.DIRECT_OBSERVATION),
            acceptable_verification_statuses=(
                VerificationStatus.SUPPORTED, VerificationStatus.CORROBORATED,
                VerificationStatus.PARTIALLY_SUPPORTED),
            minimum_evidence_strength=EvidenceStrength.MODERATE,
            subject_ref=subject_ref, scope=dict(requested_scope)),
        EvidenceRequirement(
            "req_objective_measurement", "OBJECTIVE_MEASUREMENT", False,
            states["req_objective_measurement"],
            decision_dependency="Future outcome measurement, not final positioning selection",
            criticality=ResearchCriticality.DECISION_LIMITING,
            accepted_source_classes=("OWNER_INPUT",),
            required_claim_roles=("OWNER_CONTEXT",),
            acceptable_epistemic_types=(EpistemicType.HUMAN_ASSERTION,),
            acceptable_verification_statuses=(
                VerificationStatus.SUPPORTED, VerificationStatus.CORROBORATED),
            minimum_evidence_strength=EvidenceStrength.WEAK,
            subject_ref=subject_ref, scope=dict(requested_scope),
            allow_disclosure=True, owner_input_only=True),
    )
    unknowns = []
    for name, value in (
        ("metric", objective.metric), ("baseline", objective.baseline),
        ("target", objective.target), ("time_horizon", objective.time_horizon),
    ):
        if value.status.value != "KNOWN":
            unknowns.append(f"Business objective {name} is {value.status.value}.")
    return ResearchQuestion(
        question_id=f"rq_{uuid4().hex}", objective_id=objective.objective_id,
        question=("Which positioning should this brand use to best support the explicit business "
                  "objective within the requested market/scope?"),
        scope=dict(requested_scope), evidence_requirements=requirements,
        sufficiency_status=SufficiencyStatus.NOT_ASSESSED,
        unmet_requirement_ids=(), unknowns=tuple(unknowns), stop_reason="")


def _propose(
    objective: BusinessObjective,
    question: ResearchQuestion,
    adapted: AdaptedIntelligence,
    assessment: ResearchSufficiencyAssessment,
) -> tuple[tuple[Claim, ...], StrategicDecision]:
    claims = list(adapted.claims)
    evidence_by_id = {e.evidence_id: e for e in adapted.evidence}
    capability = _unique_claims(
        _role_claims(adapted, "BRAND_CAPABILITY"), evidence_by_id)
    customer = _role_claims(adapted, "CUSTOMER_RELEVANCE")
    voice = _unique_claims([claim for claim in customer if any(
        evidence_by_id.get(eid) is not None
        and evidence_by_id[eid].source_class == "CUSTOMER_VOICE"
        for eid in claim.supporting_evidence_ids)], evidence_by_id)
    audience = _unique_claims(
        [claim for claim in customer if claim not in voice], evidence_by_id)
    frames = _unique_claims(
        _role_claims(adapted, "FRAME_OF_REFERENCE"), evidence_by_id)
    proofs = _unique_claims(
        _role_claims(adapted, "REASON_TO_BELIEVE"), evidence_by_id)
    competitor = _unique_claims(
        _role_claims(adapted, "COMPETITIVE_DIFFERENTIATION"), evidence_by_id)
    signals = _OBJECTIVE_VALUE_SIGNALS.get(objective.business_type.value, ())
    ranked = list(enumerate(capability))
    ranked.sort(key=lambda pair: (
        _CONSUMER_VALUE_SOURCE_PRIORITY.get(
            str(pair[1].scope.get("profile_field") or ""), 4),
        -sum(token in pair[1].statement.lower() for token in signals),
        pair[0]))
    values = [claim for _index, claim in ranked[:3]]
    while len(values) < 2:
        # Empty fields remain empty instead of manufacturing a capability. The option still exists
        # so the verifier can report the exact missing premise and preserve the two-option contract.
        values.append(None)

    options: list[PositioningOption] = []
    for index, value_claim in enumerate(values[:3], start=1):
        target_claim = _related_claim(
            value_claim, audience, evidence_by_id, fallback_index=index - 1)
        frame_claim = _related_claim(
            value_claim, frames, evidence_by_id, fallback_index=0)
        proof_claim = _related_claim(
            value_claim, proofs, evidence_by_id, require_overlap=True)
        if proof_claim is None and proofs:
            # A real RTB claim may be selected without lexical overlap, but the consumer value
            # itself can never become its own proof fallback.
            proof_claim = proofs[(index - 1) % len(proofs)]
        customer_context = " ".join(filter(None, (
            value_claim.statement if value_claim else "",
            target_claim.statement if target_claim else "",
            frame_claim.statement if frame_claim else "",
            proof_claim.statement if proof_claim else "",
            " ".join(signals),
        )))
        context_terms = _terms(customer_context)
        voice_claim = None
        if voice:
            ranked_voice = sorted(
                enumerate(voice),
                key=lambda pair: (-len(context_terms & _terms(pair[1].statement)), pair[0]))
            if context_terms & _terms(ranked_voice[0][1].statement):
                voice_claim = ranked_voice[0][1]
            elif objective.business_type.value == "TRIAL_GROWTH":
                # A scoped own-brand complaint is directly relevant to the first-use experience,
                # even when its source language has no lexical overlap with the brand profile.
                # It remains one occurrence, not a prevalence claim.
                voice_claim = next((claim for claim in voice
                                    if claim.scope.get("customer_subject") == "own_brand"
                                    and claim.scope.get("customer_voice_kind") == "complaint"), None)
        competitor_claim = None
        if competitor:
            usable_competitor = [claim for claim in competitor
                                 if claim.verification_status in {
                                     VerificationStatus.SUPPORTED,
                                     VerificationStatus.CORROBORATED,
                                     VerificationStatus.PARTIALLY_SUPPORTED}]
            ranked_competitor = sorted(
                enumerate(usable_competitor),
                key=lambda pair: (
                    -semantic_overlap(value_claim.statement, pair[1].statement),
                    0 if pair[1].evidence_strength == EvidenceStrength.STRONG else 1,
                    pair[0]))
            if ranked_competitor:
                candidate = ranked_competitor[0][1]
                if semantic_overlap(value_claim.statement, candidate.statement):
                    competitor_claim = candidate
        hypothesis = Claim(
            claim_id=f"cl_hyp_{uuid4().hex}",
            statement=(f"This positioning may support: {objective.description}"),
            epistemic_type=EpistemicType.HYPOTHESIS,
            scope={"role": "EXPECTED_OBJECTIVE_EFFECT", "objective_id": objective.objective_id},
            supporting_evidence_ids=(), verification_status=VerificationStatus.NOT_TESTED,
            evidence_strength=EvidenceStrength.UNKNOWN,
            limitations=("Future outcome; requires measurement after approval and activation.",))
        claims.append(hypothesis)

        support = []
        for claim in (target_claim, voice_claim, frame_claim, value_claim, proof_claim,
                      competitor_claim):
            if claim is not None and claim.claim_id not in support:
                support.append(claim.claim_id)
        target_parts = []
        if target_claim:
            target_parts.append(target_claim.statement)
        if voice_claim:
            target_parts.append(
                f"customer signal (one observation, not prevalence): {voice_claim.statement}")
        target = "; ".join(target_parts)
        frame = frame_claim.statement if frame_claim else ""
        value = value_claim.statement if value_claim else ""
        proof = proof_claim.statement if proof_claim else ""
        statement = (f"For {target}, {value} within {frame}, with {proof} as the reason to believe."
                     if all((target, value, frame, proof)) else "")
        other_value = next(
            (other.statement for other in values if other is not None and other is not value_claim),
            "the other evidenced value proposition",
        )
        options.append(PositioningOption(
            option_id=f"pos_{index}", positioning_statement=statement,
            target=target, frame_of_reference=frame, differentiated_value=value,
            reason_to_believe=proof, supporting_claim_ids=tuple(support),
            hypothesis_claim_ids=(hypothesis.claim_id,),
            missing_evidence_requirement_ids=question.unmet_requirement_ids,
            trade_offs=(
                f"Concentrating on {value} deprioritizes {other_value} as the primary trial "
                "trigger.",),
            why_it_may_be_rejected=(
                "Reject if owner review does not accept this target/value concentration or if "
                "measurement does not support it as the stronger first-trial trigger."),
            # The objective-ranked brand value is the customer-facing promise. A competitor
            # metric can prove strategic difference, but can never silently replace this field.
            consumer_value=(value_claim.statement if value_claim else ""),
            consumer_value_claim_ids=((value_claim.claim_id,) if value_claim else ()),
            differentiation_claim_ids=(
                (competitor_claim.claim_id,) if competitor_claim else ()),
            reason_to_believe_claim_ids=((proof_claim.claim_id,) if proof_claim else ()),
            internal_supporting_claim_ids=tuple(
                claim.claim_id for claim in (target_claim, voice_claim, frame_claim,
                                              competitor_claim)
                if claim is not None),
            option_status=(
                OptionStatus.SELECTED
                if assessment.eligible_for_final_selection and index == 1
                else OptionStatus.ALTERNATIVE
                if assessment.eligible_for_final_selection
                else OptionStatus.PROVISIONAL
            ),
        ))

    selected = options[0]
    final_selection = assessment.eligible_for_final_selection
    accepted = tuple(c for c in selected.supporting_claim_ids
                     if next((x for x in claims if x.claim_id == c), None) is not None)
    decision = StrategicDecision(
        decision_id=f"dec_{uuid4().hex}", objective_id=objective.objective_id,
        research_question_id=question.question_id,
        decision_state=(
            DecisionState.DRAFT if final_selection else DecisionState.BLOCKED),
        selected_option_id=selected.option_id if final_selection else "",
        options=tuple(options),
        rationale=(f"The provisional selection prioritizes {selected.consumer_value} as the "
                   f"evidenced value most directly related to {objective.business_type.value} "
                   f"({objective.description}); the other options retain different evidenced "
                   "trial triggers and explicit trade-offs."),
        rationale_claim_ids=accepted, accepted_claim_ids=accepted,
        unresolved_unknowns=question.unknowns,
        contradictions=tuple(c.statement for c in claims if c.contradicting_evidence_ids),
        verification_result_id="", approval_state=ApprovalState.PENDING,
        selection_status=(
            DecisionSelectionStatus.FINAL
            if final_selection else DecisionSelectionStatus.PROVISIONAL),
        provisional_option_id="" if final_selection else selected.option_id)
    return tuple(claims), decision


def build_positioning_strategy(
    brand: Mapping[str, Any], *, objective: BusinessObjective,
    requested_scope: Mapping[str, Any] | None = None, swot_run_id: str = "",
) -> CanonicalStrategyBuild:
    """Build and verify the canonical JSON payload. No provider, persistence, or approval call."""
    scope = dict(requested_scope or {})
    scope.setdefault("geography", list(objective.geography))
    if objective.market and not scope.get("market"):
        scope["market"] = objective.market
    adapted = adapt_existing_intelligence(
        brand, originating_swot_run_id=swot_run_id or None)
    question = _research_question(objective, adapted, scope)
    try:
        assessment = evaluate_research_sufficiency(
            question, adapted.evidence, adapted.claims)
        expected_requirement_ids = {
            requirement.requirement_id
            for requirement in question.evidence_requirements
        }
        actual_requirement_ids = [
            item.requirement_id
            for item in assessment.requirement_results
        ] if isinstance(assessment, ResearchSufficiencyAssessment) else []
        computed_blocking_ids = tuple(
            item.requirement_id
            for item in assessment.requirement_results
            if item.blocking
        ) if isinstance(assessment, ResearchSufficiencyAssessment) else ()
        assessment_complete = (
            isinstance(assessment, ResearchSufficiencyAssessment)
            and assessment.question_id == question.question_id
            and len(actual_requirement_ids) == len(expected_requirement_ids)
            and set(actual_requirement_ids) == expected_requirement_ids
            and assessment.blocking_requirement_ids == computed_blocking_ids
            and all(
                item.policy_id == assessment.policy_id
                and item.policy_version == assessment.policy_version
                for item in assessment.requirement_results
            )
            and not (
                assessment.eligible_for_final_selection
                and (
                    assessment.execution_status
                    != AssessmentExecutionStatus.EXECUTED
                    or assessment.blocking_requirement_ids
                )
            )
        )
        if not assessment_complete:
            assessment = failed_sufficiency_assessment(
                question,
                reason=(
                    "The canonical research evaluator did not return one complete, internally "
                    "consistent result for every requirement; presence heuristics were not used."
                ),
            )
    except Exception as exc:
        assessment = failed_sufficiency_assessment(
            question,
            reason=(
                "The canonical research evaluator failed to execute: "
                f"{type(exc).__name__}: {exc}"
            ),
        )
    required_by_id = {
        requirement.requirement_id: requirement
        for requirement in question.evidence_requirements
    }
    unmet = tuple(
        item.requirement_id
        for item in assessment.requirement_results
        if required_by_id[item.requirement_id].required
        and item.current_status != ResearchRequirementVerdict.SATISFIED
    )
    question = replace(
        question,
        sufficiency_status=assessment.overall_status,
        unmet_requirement_ids=unmet,
        unknowns=assessment.unknowns,
        stop_reason=(
            "Final positioning selection is blocked by canonical research requirements."
            if assessment.blocking_requirement_ids
            else ""
        ),
    )
    claims, draft = _propose(objective, question, adapted, assessment)
    objective_dict = objective_to_dict(objective)
    verification = PositioningDecisionVerifier().verify(
        objective=objective_dict, research_question=question, evidence=adapted.evidence,
        claims=claims, decision=draft, research_assessment=assessment)
    state = (
        DecisionState.BLOCKED
        if not assessment.eligible_for_final_selection
        or assessment.execution_status != AssessmentExecutionStatus.EXECUTED
        else DecisionState.VERIFIED
        if verification.status in (
            VerifierStatus.PASS, VerifierStatus.PASS_WITH_DISCLOSURE)
        else DecisionState.NEEDS_REPAIR
    )
    decision = replace(draft, decision_state=state,
                       verification_result_id=verification.verification_result_id)
    canonical = {
        "schema_version": 1,
        "research_question": to_dict(question),
        "research_sufficiency_assessment": to_dict(assessment),
        "evidence": to_dict(adapted.evidence),
        "claims": to_dict(claims),
        "decision": to_dict(decision),
        "verification": to_dict(verification),
    }
    requested_config = {
        "schema_version": 1, "decision_type": "POSITIONING",
        "objective": objective_dict, "requested_scope": scope,
    }
    evidence_geographies = sorted({g for e in adapted.evidence
                                   if e.availability == Availability.AVAILABLE
                                   for g in e.geography if str(g).strip()})
    evidence_markets = sorted({e.market for e in adapted.evidence
                               if e.availability == Availability.AVAILABLE and e.market})
    effective_config = {
        "objective_id": objective.objective_id,
        "effective_scope": {"geography": evidence_geographies, "markets": evidence_markets},
        "source_artifacts": list(adapted.source_artifacts),
        "input_evidence_ids": [e.evidence_id for e in adapted.evidence
                               if e.availability == Availability.AVAILABLE],
        "input_claim_ids": [c.claim_id for c in adapted.claims],
        "research_policy": {
            "policy_id": assessment.policy_id,
            "policy_version": assessment.policy_version,
        },
        "swot_run_id": swot_run_id or None,
    }
    deps = ((swot_run_id, "uses_swot"),) if swot_run_id else ()
    return CanonicalStrategyBuild(
        output={"canonical_strategy": canonical}, requested_config=requested_config,
        effective_config=effective_config, dependencies=deps)
