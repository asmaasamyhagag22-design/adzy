"""Pure research-sufficiency evaluation over evidence already present in a Strategy Run."""
from __future__ import annotations

from hashlib import sha256
from typing import Iterable, Mapping, Sequence

from .contracts import (
    AssessmentExecutionStatus,
    Availability,
    Claim,
    EpistemicType,
    EvidenceRecord,
    EvidenceRequirement,
    EvidenceStrength,
    IndependenceStatus,
    ResearchCriticality,
    ResearchQuestion,
    ResearchReasonCode,
    ResearchRequirementVerdict,
    ResearchResolutionItem,
    ResearchSufficiencyAssessment,
    ResolutionAction,
    ResolutionStopCondition,
    ResolutionStopKind,
    SufficiencyStatus,
    VerificationStatus,
)


RESEARCH_POLICY_ID = "baseera.positioning.research_sufficiency"
RESEARCH_POLICY_VERSION = "1.0.0"

_STRENGTH_RANK = {
    EvidenceStrength.UNKNOWN: 0,
    EvidenceStrength.WEAK: 1,
    EvidenceStrength.MODERATE: 2,
    EvidenceStrength.STRONG: 3,
}


def _assessment_id(
    question: ResearchQuestion, policy_id: str, policy_version: str, marker: str,
) -> str:
    requirements = "|".join(r.requirement_id for r in question.evidence_requirements)
    material = f"{question.question_id}|{requirements}|{policy_id}|{policy_version}|{marker}"
    return f"rsa_{sha256(material.encode('utf-8')).hexdigest()[:16]}"


def _blocking(requirement: EvidenceRequirement) -> bool:
    return (
        requirement.required
        and requirement.criticality == ResearchCriticality.BLOCKING
    )


def _failed_item(
    requirement: EvidenceRequirement,
    *,
    policy_id: str,
    policy_version: str,
    reason_code: ResearchReasonCode,
    detail: str,
) -> ResearchResolutionItem:
    return ResearchResolutionItem(
        requirement_id=requirement.requirement_id,
        decision_dependency=requirement.decision_dependency,
        criticality=requirement.criticality,
        current_status=ResearchRequirementVerdict.UNVERIFIED,
        blocking=_blocking(requirement),
        reason_code=reason_code,
        reason_detail=detail,
        evidence_considered_ids=(),
        qualifying_evidence_ids=(),
        required_evidence_class=requirement.accepted_source_classes,
        required_epistemic_strength=requirement.minimum_evidence_strength,
        subject_ref=requirement.subject_ref,
        scope=dict(requirement.scope),
        independence_status=IndependenceStatus.UNKNOWN,
        independent_origin_count=0,
        resolution_action=ResolutionAction.DEFER_DECISION,
        action_details=(
            "Do not make a final selection until the canonical evaluator executes under "
            "a recognized policy."
        ),
        stop_condition=ResolutionStopCondition(
            ResolutionStopKind.EVALUATOR_RESTORED,
            "The canonical evaluator executes successfully with this Run's complete local inputs.",
        ),
        unknowns=(detail,),
        policy_id=policy_id,
        policy_version=policy_version,
        limitations=("Research sufficiency was not verified; evidence presence is not a fallback.",),
        limitation_codes=("EVALUATOR_UNVERIFIED",),
    )


def failed_sufficiency_assessment(
    question: ResearchQuestion,
    *,
    reason: str,
    policy_id: str = RESEARCH_POLICY_ID,
    policy_version: str = RESEARCH_POLICY_VERSION,
    reason_code: ResearchReasonCode = ResearchReasonCode.EVALUATOR_UNVERIFIED,
) -> ResearchSufficiencyAssessment:
    """Create a serialized fail-closed result instead of falling back to presence checks."""
    items = tuple(
        _failed_item(
            requirement,
            policy_id=policy_id,
            policy_version=policy_version,
            reason_code=reason_code,
            detail=reason,
        )
        for requirement in question.evidence_requirements
    )
    blocking_ids = tuple(
        item.requirement_id for item in items if item.blocking
    )
    return ResearchSufficiencyAssessment(
        assessment_id=_assessment_id(
            question, policy_id, policy_version, f"UNVERIFIED:{reason}"
        ),
        question_id=question.question_id,
        execution_status=AssessmentExecutionStatus.UNVERIFIED,
        overall_status=SufficiencyStatus.CANNOT_PROCEED,
        eligible_for_final_selection=False,
        requirement_results=items,
        blocking_requirement_ids=blocking_ids,
        unknowns=(reason,),
        limitations=(
            "Evaluator result is UNVERIFIED; final selection and approval are ineligible.",
        ),
        policy_id=policy_id,
        policy_version=policy_version,
    )


def _normal(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _scope_values(scope: Mapping[str, object], key: str) -> tuple[str, ...]:
    raw = scope.get(key)
    if raw in (None, "", (), []):
        return ()
    values = raw if isinstance(raw, (list, tuple, set)) else (raw,)
    return tuple(_normal(value) for value in values if _normal(value))


def _scope_matches(
    evidence: EvidenceRecord, scope: Mapping[str, object],
) -> bool:
    required_geography = set(_scope_values(scope, "geography"))
    actual_geography = {_normal(value) for value in evidence.geography if _normal(value)}
    if required_geography and not (required_geography & actual_geography):
        return False
    for key, actual in (("market", evidence.market), ("channel", evidence.channel)):
        required = set(_scope_values(scope, key))
        if required and _normal(actual) not in required:
            return False
    return True


def _claim_evidence(
    claim: Claim, evidence_by_id: Mapping[str, EvidenceRecord],
) -> tuple[EvidenceRecord, ...]:
    return tuple(
        evidence_by_id[evidence_id]
        for evidence_id in claim.supporting_evidence_ids
        if evidence_id in evidence_by_id
    )


def _stop_condition(
    requirement: EvidenceRequirement, action: ResolutionAction,
) -> ResolutionStopCondition:
    if action == ResolutionAction.USE_EXISTING_LOCAL_EVIDENCE:
        return ResolutionStopCondition(
            ResolutionStopKind.EVIDENCE_THRESHOLD_MET,
            "The requirement is met by the listed qualifying local evidence.",
        )
    if action == ResolutionAction.ASK_OWNER:
        return ResolutionStopCondition(
            ResolutionStopKind.OWNER_ANSWER_RECORDED,
            "The owner answer is recorded as typed OWNER_INPUT evidence in a new Strategy Run.",
        )
    if action == ResolutionAction.PROCEED_WITH_DISCLOSURE:
        return ResolutionStopCondition(
            ResolutionStopKind.DISCLOSURE_RECORDED,
            "The non-blocking limitation is explicitly disclosed in the decision.",
        )
    if action == ResolutionAction.DEFER_DECISION:
        return ResolutionStopCondition(
            ResolutionStopKind.DECISION_DEFERRED,
            "The final decision remains deferred until the requirement is satisfied.",
        )
    return ResolutionStopCondition(
        ResolutionStopKind.EVIDENCE_THRESHOLD_MET,
        (
            f"Collect qualifying {', '.join(requirement.accepted_source_classes) or 'typed'} "
            f"evidence until {requirement.minimum_independent_origins} explicit independent "
            "origin(s) are established, where required."
        ),
    )


def _resolution_action(
    requirement: EvidenceRequirement,
    verdict: ResearchRequirementVerdict,
) -> ResolutionAction:
    if verdict == ResearchRequirementVerdict.SATISFIED:
        return ResolutionAction.USE_EXISTING_LOCAL_EVIDENCE
    if requirement.owner_input_only:
        return ResolutionAction.ASK_OWNER
    if not _blocking(requirement) and requirement.allow_disclosure:
        return ResolutionAction.PROCEED_WITH_DISCLOSURE
    if requirement.requirement_type in {
        "BRAND_CAPABILITY_PROOF",
        "REASON_TO_BELIEVE_PROOF",
    }:
        return ResolutionAction.DEFER_DECISION
    return ResolutionAction.COLLECT_SPECIFIC


def _evaluate_requirement(
    requirement: EvidenceRequirement,
    evidence: Sequence[EvidenceRecord],
    claims: Sequence[Claim],
    *,
    policy_id: str,
    policy_version: str,
) -> ResearchResolutionItem:
    evidence_by_id = {row.evidence_id: row for row in evidence}
    accepted_classes = set(requirement.accepted_source_classes)
    considered = tuple(
        row for row in evidence
        if not accepted_classes or row.source_class in accepted_classes
    )
    considered_ids = tuple(row.evidence_id for row in considered)
    required_roles = set(requirement.required_claim_roles)
    role_claims = tuple(
        claim for claim in claims
        if not required_roles or str(claim.scope.get("role") or "") in required_roles
    )
    value_statements = {
        _normal(claim.statement)
        for claim in claims
        if str(claim.scope.get("role") or "") == "BRAND_CAPABILITY"
    }

    candidate_evidence: list[EvidenceRecord] = []
    candidate_claims: list[Claim] = []
    failures: set[ResearchReasonCode] = set()
    limitation_codes: set[str] = set()
    limitations: list[str] = []

    for claim in role_claims:
        linked = _claim_evidence(claim, evidence_by_id)
        linked = tuple(
            row for row in linked
            if not accepted_classes or row.source_class in accepted_classes
        )
        if not linked:
            continue
        if (
            "REASON_TO_BELIEVE" in required_roles
            and _normal(claim.statement) in value_statements
        ):
            failures.add(ResearchReasonCode.VALUE_PROMISE_REUSED_AS_PROOF)
            continue
        if (
            requirement.acceptable_epistemic_types
            and claim.epistemic_type not in requirement.acceptable_epistemic_types
        ):
            failures.add(ResearchReasonCode.EPISTEMIC_STRENGTH_TOO_WEAK)
            continue
        if (
            requirement.acceptable_verification_statuses
            and claim.verification_status
            not in requirement.acceptable_verification_statuses
        ):
            failures.add(ResearchReasonCode.VERIFICATION_STATUS_INELIGIBLE)
            continue
        if (
            _STRENGTH_RANK[claim.evidence_strength]
            < _STRENGTH_RANK[requirement.minimum_evidence_strength]
        ):
            failures.add(ResearchReasonCode.EVIDENCE_STRENGTH_TOO_WEAK)
            continue

        claim_qualified = False
        for row in linked:
            if row.availability != Availability.AVAILABLE:
                continue
            if requirement.subject_ref and row.subject_ref != requirement.subject_ref:
                failures.add(ResearchReasonCode.SUBJECT_MISMATCH)
                continue
            if not _scope_matches(row, requirement.scope):
                failures.add(ResearchReasonCode.SCOPE_MISMATCH)
                limitation_codes.add("SCOPE_MISMATCH")
                limitations.append(
                    f"Evidence {row.evidence_id} is outside the required scope; "
                    "this is a limitation, not a contradiction."
                )
                continue
            candidate_evidence.append(row)
            claim_qualified = True
        if claim_qualified:
            candidate_claims.append(claim)

    unique_candidates = {
        row.evidence_id: row for row in candidate_evidence
    }
    candidate_evidence = list(unique_candidates.values())
    known_origins = {
        row.origin_group.strip()
        for row in candidate_evidence
        if row.origin_group and row.origin_group.strip()
    }
    unknown_origin_count = sum(
        not (row.origin_group and row.origin_group.strip())
        for row in candidate_evidence
    )
    origin_count = len(known_origins)
    minimum_origins = requirement.minimum_independent_origins
    if minimum_origins <= 0:
        independence = IndependenceStatus.NOT_REQUIRED
        independence_met = True
    elif origin_count >= minimum_origins:
        independence = IndependenceStatus.ESTABLISHED
        independence_met = True
    elif origin_count == 0 and unknown_origin_count:
        independence = IndependenceStatus.UNKNOWN
        independence_met = False
        failures.add(ResearchReasonCode.INDEPENDENCE_NOT_ESTABLISHED)
    else:
        independence = IndependenceStatus.INSUFFICIENT
        independence_met = False
        failures.add(ResearchReasonCode.INDEPENDENCE_NOT_ESTABLISHED)

    satisfied = bool(candidate_claims and candidate_evidence and independence_met)
    if satisfied:
        verdict = ResearchRequirementVerdict.SATISFIED
        reason_code = ResearchReasonCode.QUALIFYING_LOCAL_EVIDENCE
        reason_detail = (
            "Existing local evidence meets the requirement's explicit epistemic, scope, "
            "verification, strength, and independence policy."
        )
    else:
        verdict = (
            ResearchRequirementVerdict.UNKNOWN
            if considered
            and all(row.availability == Availability.UNKNOWN for row in considered)
            else ResearchRequirementVerdict.UNMET
        )
        priority = (
            ResearchReasonCode.SCOPE_MISMATCH,
            ResearchReasonCode.VALUE_PROMISE_REUSED_AS_PROOF,
            ResearchReasonCode.EPISTEMIC_STRENGTH_TOO_WEAK,
            ResearchReasonCode.VERIFICATION_STATUS_INELIGIBLE,
            ResearchReasonCode.EVIDENCE_STRENGTH_TOO_WEAK,
            ResearchReasonCode.SUBJECT_MISMATCH,
            ResearchReasonCode.INDEPENDENCE_NOT_ESTABLISHED,
        )
        reason_code = next(
            (code for code in priority if code in failures),
            ResearchReasonCode.OWNER_INPUT_REQUIRED
            if requirement.owner_input_only
            else ResearchReasonCode.NO_QUALIFYING_EVIDENCE,
        )
        reason_detail = {
            ResearchReasonCode.SCOPE_MISMATCH:
                "Available evidence does not match the required geography, market, or channel.",
            ResearchReasonCode.VALUE_PROMISE_REUSED_AS_PROOF:
                "A consumer value promise cannot be reused as its own reason to believe.",
            ResearchReasonCode.EPISTEMIC_STRENGTH_TOO_WEAK:
                "The claim's epistemic type is weaker than this requirement permits.",
            ResearchReasonCode.VERIFICATION_STATUS_INELIGIBLE:
                "The claim has not reached an accepted verification status.",
            ResearchReasonCode.EVIDENCE_STRENGTH_TOO_WEAK:
                "The claim's evidence strength is below the policy threshold.",
            ResearchReasonCode.SUBJECT_MISMATCH:
                "The evidence concerns a different subject.",
            ResearchReasonCode.INDEPENDENCE_NOT_ESTABLISHED:
                "The required number of explicit independent origins is not established.",
            ResearchReasonCode.OWNER_INPUT_REQUIRED:
                "This decision fact can only be supplied by the owner.",
            ResearchReasonCode.NO_QUALIFYING_EVIDENCE:
                "No supplied local evidence qualifies under the requirement policy.",
        }[reason_code]

    for row in candidate_evidence:
        for limitation in row.limitations:
            if limitation not in limitations:
                limitations.append(limitation)
    for claim in candidate_claims:
        for limitation in claim.limitations:
            if limitation not in limitations:
                limitations.append(limitation)

    action = _resolution_action(requirement, verdict)
    unknowns: list[str] = []
    if independence == IndependenceStatus.UNKNOWN:
        unknowns.append(
            "Origin independence is unknown and is not counted toward the threshold."
        )
    if verdict != ResearchRequirementVerdict.SATISFIED:
        unknowns.append(reason_detail)
    action_details = {
        ResolutionAction.USE_EXISTING_LOCAL_EVIDENCE:
            "Use only the listed qualifying evidence already serialized in this Strategy Run.",
        ResolutionAction.ASK_OWNER:
            "Ask the owner for the missing typed fact; do not infer it from brand materials.",
        ResolutionAction.COLLECT_SPECIFIC:
            "Collect only the specified evidence class and scope in a future authorized run.",
        ResolutionAction.PROCEED_WITH_DISCLOSURE:
            "Proceed only because the gap is non-blocking, and serialize the limitation.",
        ResolutionAction.DEFER_DECISION:
            "Keep the strategic decision deferred; do not serialize a final selection.",
    }[action]

    return ResearchResolutionItem(
        requirement_id=requirement.requirement_id,
        decision_dependency=requirement.decision_dependency,
        criticality=requirement.criticality,
        current_status=verdict,
        blocking=_blocking(requirement) and verdict != ResearchRequirementVerdict.SATISFIED,
        reason_code=reason_code,
        reason_detail=reason_detail,
        evidence_considered_ids=considered_ids,
        qualifying_evidence_ids=tuple(row.evidence_id for row in candidate_evidence),
        required_evidence_class=requirement.accepted_source_classes,
        required_epistemic_strength=requirement.minimum_evidence_strength,
        subject_ref=requirement.subject_ref,
        scope=dict(requirement.scope),
        independence_status=independence,
        independent_origin_count=origin_count,
        resolution_action=action,
        action_details=action_details,
        stop_condition=_stop_condition(requirement, action),
        unknowns=tuple(unknowns),
        policy_id=policy_id,
        policy_version=policy_version,
        limitations=tuple(limitations),
        limitation_codes=tuple(sorted(limitation_codes)),
    )


def evaluate_research_sufficiency(
    question: ResearchQuestion,
    evidence: Iterable[EvidenceRecord] | None,
    claims: Iterable[Claim] | None,
    *,
    policy_id: str = RESEARCH_POLICY_ID,
    policy_version: str = RESEARCH_POLICY_VERSION,
) -> ResearchSufficiencyAssessment:
    """Evaluate local evidence only; unknown policy and nonexecution are fail-closed."""
    if (
        policy_id != RESEARCH_POLICY_ID
        or policy_version != RESEARCH_POLICY_VERSION
    ):
        return failed_sufficiency_assessment(
            question,
            reason=(
                f"Unknown research policy {policy_id}@{policy_version}; "
                "no fallback policy was applied."
            ),
            policy_id=policy_id,
            policy_version=policy_version,
            reason_code=ResearchReasonCode.UNKNOWN_POLICY,
        )

    if evidence is None or claims is None:
        return failed_sufficiency_assessment(
            question,
            reason=(
                "Canonical research evaluator inputs are missing; "
                "evidence presence heuristics were not used."
            ),
        )
    evidence_rows = tuple(evidence)
    claim_rows = tuple(claims)
    items = tuple(
        _evaluate_requirement(
            requirement,
            evidence_rows,
            claim_rows,
            policy_id=policy_id,
            policy_version=policy_version,
        )
        for requirement in question.evidence_requirements
    )
    blocking_ids = tuple(
        item.requirement_id for item in items if item.blocking
    )
    eligible = not blocking_ids
    overall = (
        SufficiencyStatus.SUFFICIENT
        if eligible
        else SufficiencyStatus.CANNOT_PROCEED
    )
    unknowns = list(question.unknowns)
    limitations: list[str] = []
    for item in items:
        for unknown in item.unknowns:
            if unknown not in unknowns:
                unknowns.append(unknown)
        for limitation in item.limitations:
            if limitation not in limitations:
                limitations.append(limitation)
    marker = "|".join(
        f"{item.requirement_id}:{item.current_status.value}:{item.reason_code.value}:"
        f"{','.join(item.qualifying_evidence_ids)}"
        for item in items
    )
    return ResearchSufficiencyAssessment(
        assessment_id=_assessment_id(question, policy_id, policy_version, marker),
        question_id=question.question_id,
        execution_status=AssessmentExecutionStatus.EXECUTED,
        overall_status=overall,
        eligible_for_final_selection=eligible,
        requirement_results=items,
        blocking_requirement_ids=blocking_ids,
        unknowns=tuple(unknowns),
        limitations=tuple(limitations),
        policy_id=policy_id,
        policy_version=policy_version,
    )
