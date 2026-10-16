"""Targeted, deterministic verifier for the first positioning decision slice."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Iterable, Mapping
from uuid import uuid4

from .contracts import (
    AssessmentExecutionStatus, Availability, Claim, DecisionSelectionStatus, DefectCode,
    EpistemicType, EvidenceRecord, OptionStatus, PositioningOption, ResearchQuestion,
    ResearchSufficiencyAssessment, RubricStatus, StrategicDecision, VerificationResult,
    VerifierStatus,
)
from .distinctness import duplicate_positioning_pairs


_RUBRIC_KEYS = (
    "objective_relevance", "customer_relevance", "differentiation",
    "reason_to_believe_credibility", "coherence", "real_trade_off", "feasibility",
)


def _normal(values: Iterable[str]) -> set[str]:
    return {" ".join(str(v).lower().split()) for v in values if str(v).strip()}


def _terms(text: str) -> set[str]:
    stop = {"with", "from", "that", "this", "their", "brand", "business", "service",
            "services", "customer", "customers", "market", "offers", "people"}
    return {w for w in re.findall(r"\w+", str(text or "").lower())
            if len(w) >= 4 and w not in stop}


def _claim_is_covered(claim: Claim, evidence_by_id: Mapping[str, EvidenceRecord]) -> bool:
    """Require every factual claim token to occur in its actual excerpts.

    The adapter emits atomic source fragments, so exact lexical coverage is intentionally strict.
    This is a deterministic coverage gate, not a semantic-similarity score.
    """
    if claim.epistemic_type == EpistemicType.HYPOTHESIS:
        return True
    excerpts = " ".join(
        evidence_by_id[eid].excerpt for eid in claim.supporting_evidence_ids
        if eid in evidence_by_id and evidence_by_id[eid].availability == Availability.AVAILABLE)
    claim_terms = _terms(claim.statement)
    excerpt_terms = _terms(excerpts)
    if claim_terms:
        return claim_terms <= excerpt_terms
    return " ".join(claim.statement.lower().split()) in " ".join(excerpts.lower().split())


_PREVALENCE_MARKERS = {
    "all", "always", "common", "commonly", "frequent", "frequently", "many", "most",
    "typically", "widespread", "العملاء", "الجميع", "دائما", "دائمًا", "شائع", "معظم",
}


def _contains_high_risk_premise(text: str) -> bool:
    normalized = str(text or "").lower()
    stems = (
        "absorp", "bioavail", "clinical", "collagen", "cure", "efficacy", "health",
        "medical", "supplement", "treat", "امتصاص", "علاج", "صحت", "مكمل", "فعالة",
        "فعّالة", "كفاءة",
    )
    return any(stem in normalized for stem in stems)


class PositioningDecisionVerifier:
    """Checks shape, lineage, evidence scope, and the frozen strategic-quality rubric."""

    def verify(
        self, *, objective: Mapping, research_question: ResearchQuestion,
        evidence: tuple[EvidenceRecord, ...], claims: tuple[Claim, ...],
        decision: StrategicDecision,
        research_assessment: ResearchSufficiencyAssessment | None = None,
    ) -> VerificationResult:
        defects: list[dict] = []

        def defect(code: DefectCode, detail: str, repair_target: str, **inspection) -> None:
            if not any(d["code"] == code.value and d["detail"] == detail for d in defects):
                defects.append({"code": code.value, "detail": detail,
                                "repair_target": repair_target, **inspection})

        if not str(objective.get("objective_id") or "").strip():
            defect(DefectCode.UNKNOWN_REQUIRED_OWNER_INPUT, "Business objective is missing.", "objective")
        objective_id = str(objective.get("objective_id") or "")
        if (decision.objective_id != objective_id
                or research_question.objective_id != objective_id
                or decision.research_question_id != research_question.question_id):
            defect(DefectCode.OBJECTIVE_MISMATCH,
                   "Decision, research question, and explicit objective references do not match.",
                   "decision.objective_id")
        if len(decision.options) < 2:
            defect(DefectCode.INSUFFICIENT_RESEARCH, "At least two options are required.", "decision.options")
        by_option = {o.option_id: o for o in decision.options}
        assessment_blocks = bool(
            research_assessment is not None
            and not research_assessment.eligible_for_final_selection
        )
        if research_assessment is not None:
            if research_assessment.execution_status != AssessmentExecutionStatus.EXECUTED:
                defect(
                    DefectCode.INSUFFICIENT_RESEARCH,
                    "Canonical research evaluation is UNVERIFIED; final selection is ineligible.",
                    "research_sufficiency_assessment",
                )
            if research_assessment.blocking_requirement_ids:
                defect(
                    DefectCode.INSUFFICIENT_RESEARCH,
                    "Blocking canonical research gaps remain unresolved.",
                    "research_sufficiency_assessment.blocking_requirement_ids",
                    requirement_ids=list(research_assessment.blocking_requirement_ids),
                )
            if research_assessment.eligible_for_final_selection:
                if decision.selection_status != DecisionSelectionStatus.FINAL:
                    defect(
                        DefectCode.INSUFFICIENT_RESEARCH,
                        "An eligible final decision must explicitly declare FINAL selection status.",
                        "decision.selection_status",
                    )
            else:
                if decision.selected_option_id:
                    defect(
                        DefectCode.UNSUPPORTED_PREMISE,
                        "A blocked assessment cannot carry a final selected_option_id.",
                        "decision.selected_option_id",
                    )
                if any(option.option_status != OptionStatus.PROVISIONAL
                       for option in decision.options):
                    defect(
                        DefectCode.UNSUPPORTED_PREMISE,
                        "Every option must remain PROVISIONAL while a blocking gap exists.",
                        "decision.options.option_status",
                    )
        for comparison in duplicate_positioning_pairs(decision.options):
            compared = [comparison.left_option_id, comparison.right_option_id]
            defect(
                DefectCode.POSITIONING_ALTERNATIVES_NOT_DISTINCT,
                (f"{comparison.right_option_id} materially duplicates "
                 f"{comparison.left_option_id}: {comparison.reason}"),
                "decision.options",
                compared_option_ids=compared,
                fields_considered_equivalent=list(comparison.equivalent_fields),
                reason=comparison.reason,
            )
        selected = by_option.get(decision.selected_option_id)
        if selected is None and assessment_blocks:
            selected = by_option.get(decision.provisional_option_id)
        if selected is None and not assessment_blocks:
            defect(DefectCode.UNSUPPORTED_PREMISE, "selected_option_id does not name an option.",
                   "decision.selected_option_id")
        if not decision.rationale.strip():
            defect(DefectCode.UNSUPPORTED_PREMISE, "Decision rationale is missing.", "decision.rationale")

        claim_by_id = {c.claim_id: c for c in claims}
        evidence_by_id = {e.evidence_id: e for e in evidence}
        referenced_claims: set[str] = set(decision.rationale_claim_ids) | set(decision.accepted_claim_ids)
        for option in decision.options:
            referenced_claims.update(option.supporting_claim_ids)
            referenced_claims.update(option.hypothesis_claim_ids)
            if not option.target.strip():
                defect(DefectCode.MISSING_CUSTOMER_EVIDENCE, f"{option.option_id}: target missing.",
                       f"options.{option.option_id}.target")
            if not option.frame_of_reference.strip():
                defect(DefectCode.UNSUPPORTED_PREMISE, f"{option.option_id}: frame of reference missing.",
                       f"options.{option.option_id}.frame_of_reference")
            if not option.differentiated_value.strip():
                defect(DefectCode.POSITIONING_NOT_DIFFERENTIATED,
                       f"{option.option_id}: differentiated value missing.",
                       f"options.{option.option_id}.differentiated_value")
            if not option.reason_to_believe.strip():
                defect(DefectCode.WEAK_REASON_TO_BELIEVE,
                       f"{option.option_id}: reason to believe missing.",
                       f"options.{option.option_id}.reason_to_believe")
            if not option.trade_offs or not any(str(v).strip() for v in option.trade_offs):
                defect(DefectCode.NO_REAL_TRADEOFF, f"{option.option_id}: trade-off missing.",
                       f"options.{option.option_id}.trade_offs")
            if not option.why_it_may_be_rejected.strip():
                defect(DefectCode.NO_REAL_TRADEOFF,
                       f"{option.option_id}: rejected-alternative reason missing.",
                       f"options.{option.option_id}.why_it_may_be_rejected")
            if not option.supporting_claim_ids:
                defect(DefectCode.UNSUPPORTED_PREMISE,
                       f"{option.option_id}: factual premises have no Claim reference.",
                       f"options.{option.option_id}.supporting_claim_ids")

        for claim_id in sorted(referenced_claims):
            claim = claim_by_id.get(claim_id)
            if claim is None:
                defect(DefectCode.UNSUPPORTED_PREMISE, f"Broken Claim reference: {claim_id}.",
                       "claims")
                continue
            if claim.epistemic_type != EpistemicType.HYPOTHESIS and not claim.supporting_evidence_ids:
                defect(DefectCode.UNSUPPORTED_PREMISE, f"Claim {claim_id} has no evidence.", "claims")
            for evidence_id in claim.supporting_evidence_ids + claim.contradicting_evidence_ids:
                if evidence_id not in evidence_by_id:
                    defect(DefectCode.UNSUPPORTED_PREMISE,
                           f"Broken Evidence reference: {evidence_id} from {claim_id}.", "evidence")
            if claim.epistemic_type == EpistemicType.DIRECT_OBSERVATION:
                derived = [evidence_by_id[eid] for eid in claim.supporting_evidence_ids
                           if eid in evidence_by_id
                           and evidence_by_id[eid].source_class in {
                               "DERIVED_SWOT", "TREND_SIGNAL", "OWNER_INPUT"}]
                if derived:
                    defect(DefectCode.UNSUPPORTED_PREMISE,
                           f"Claim {claim_id} labels derived/asserted evidence as direct observation.",
                           "claims.epistemic_type")
            if claim.epistemic_type == EpistemicType.HYPOTHESIS and claim_id in decision.accepted_claim_ids:
                defect(DefectCode.UNSUPPORTED_PREMISE,
                       f"Hypothesis {claim_id} cannot be an accepted factual claim.", "decision")
            if claim.contradicting_evidence_ids:
                defect(DefectCode.CONTRADICTORY_EVIDENCE,
                       f"Claim {claim_id} has contradicting evidence.", "decision.contradictions")
            if (claim.verification_status.value in {"SUPPORTED", "CORROBORATED"}
                    and not _claim_is_covered(claim, evidence_by_id)):
                defect(DefectCode.UNSUPPORTED_PREMISE,
                       f"Claim {claim_id} is marked supported but its excerpts do not cover the "
                       "whole claim.", "claims.verification_status")

        available = [e for e in evidence if e.availability == Availability.AVAILABLE]
        source_classes = {e.source_class for e in available}
        if "CUSTOMER_VOICE" not in source_classes:
            defect(DefectCode.MISSING_CUSTOMER_EVIDENCE,
                   "No available customer/VOC evidence supports the positioning.", "research")
        if "COMPETITOR_ANALYSIS" not in source_classes:
            defect(DefectCode.MISSING_COMPETITOR_EVIDENCE,
                   "No available competitor evidence supports differentiation.", "research")

        selected_claims = [claim_by_id[c] for c in (selected.supporting_claim_ids if selected else ())
                           if c in claim_by_id]
        selected_evidence = [evidence_by_id[eid] for c in selected_claims
                             for eid in c.supporting_evidence_ids if eid in evidence_by_id]
        if selected and not any(e.source_class == "COMPETITOR_ANALYSIS" for e in selected_evidence):
            defect(DefectCode.POSITIONING_NOT_DIFFERENTIATED,
                   "Selected differentiation does not trace to competitor evidence.",
                   f"options.{selected.option_id}.supporting_claim_ids")
        if selected and not any(e.source_class == "CUSTOMER_VOICE" for e in selected_evidence):
            defect(DefectCode.MISSING_CUSTOMER_EVIDENCE,
                   "Selected customer premise does not trace to customer/VOC evidence.",
                   f"options.{selected.option_id}.supporting_claim_ids")
        if selected:
            if research_assessment is not None:
                proof_ids = set(selected.reason_to_believe_claim_ids)
                value_ids = set(selected.consumer_value_claim_ids)
                if proof_ids & value_ids:
                    defect(
                        DefectCode.WEAK_REASON_TO_BELIEVE,
                        "Consumer value Claim ids cannot be reused as reason-to-believe proof.",
                        f"options.{selected.option_id}.reason_to_believe_claim_ids",
                    )
                if not proof_ids or any(
                    claim_id not in claim_by_id
                    or str(claim_by_id[claim_id].scope.get("role") or "")
                    != "REASON_TO_BELIEVE"
                    for claim_id in proof_ids
                ):
                    defect(
                        DefectCode.WEAK_REASON_TO_BELIEVE,
                        "The selected reason to believe must trace to an explicit RTB Claim.",
                        f"options.{selected.option_id}.reason_to_believe_claim_ids",
                    )
            customer_claims = [c for c in selected_claims if any(
                evidence_by_id.get(eid) is not None
                and evidence_by_id[eid].source_class == "CUSTOMER_VOICE"
                for eid in c.supporting_evidence_ids)]
            decision_terms = _terms(" ".join((
                selected.target, selected.differentiated_value)))
            if customer_claims and not any(
                    decision_terms & _terms(c.statement) for c in customer_claims):
                defect(DefectCode.MISSING_CUSTOMER_EVIDENCE,
                        "Available customer evidence is not linked to the selected customer/value premise.",
                        f"options.{selected.option_id}.supporting_claim_ids")
            for customer_claim in customer_claims:
                customer_evidence = [evidence_by_id[eid]
                                     for eid in customer_claim.supporting_evidence_ids
                                     if eid in evidence_by_id
                                     and evidence_by_id[eid].source_class == "CUSTOMER_VOICE"]
                if (len(customer_evidence) <= 1
                        and _terms(customer_claim.statement) & _PREVALENCE_MARKERS):
                    defect(DefectCode.MISSING_CUSTOMER_EVIDENCE,
                           "A single customer signal cannot support a prevalence claim.",
                           f"claims.{customer_claim.claim_id}")

            competitor_claims = [c for c in selected_claims if any(
                evidence_by_id.get(eid) is not None
                and evidence_by_id[eid].source_class == "COMPETITOR_ANALYSIS"
                for eid in c.supporting_evidence_ids)]
            comparison_terms = _terms(selected.differentiated_value)
            if competitor_claims and not any(
                    comparison_terms & _terms(c.statement) for c in competitor_claims):
                defect(DefectCode.POSITIONING_NOT_DIFFERENTIATED,
                       "Competitor evidence is present but not linked to the selected difference.",
                       f"options.{selected.option_id}.differentiated_value")

            if _contains_high_risk_premise(" ".join((
                    selected.differentiated_value, selected.reason_to_believe))):
                factual_evidence = [e for e in selected_evidence
                                    if e.source_class not in {"CUSTOMER_VOICE",
                                                              "COMPETITOR_ANALYSIS"}]
                if factual_evidence and all(e.source_class in {
                        "BRAND_OWNED", "OWNER_INPUT", "DERIVED_SWOT"}
                        for e in factual_evidence):
                    defect(DefectCode.WEAK_REASON_TO_BELIEVE,
                           "A high-risk outcome/product premise relies only on self/derived claims.",
                           f"options.{selected.option_id}.reason_to_believe")

        requested_geo = _normal((research_question.scope.get("geography") or []))
        if requested_geo:
            for ev in selected_evidence:
                actual = _normal(ev.geography)
                if actual and actual.isdisjoint(requested_geo):
                    defect(DefectCode.SCOPE_MISMATCH,
                           f"Evidence {ev.evidence_id} geography is outside requested scope.",
                           "research.scope")
        for ev in selected_evidence:
            if str(ev.freshness).upper() == "STALE" and not ev.limitations:
                defect(DefectCode.STALE_EVIDENCE,
                       f"Stale evidence {ev.evidence_id} has no disclosure.", "evidence.limitations")

        if research_question.unmet_requirement_ids:
            defect(DefectCode.INSUFFICIENT_RESEARCH,
                   "Required research evidence remains unmet.", "research_question")
        if research_question.sufficiency_status.value == "ASK_OWNER":
            defect(DefectCode.UNKNOWN_REQUIRED_OWNER_INPUT,
                   "A required owner answer is still unknown.", "objective")

        codes = {d["code"] for d in defects}
        rubric = {key: RubricStatus.NOT_ASSESSABLE for key in _RUBRIC_KEYS}
        objective_hypotheses = [claim_by_id[c] for c in (
            selected.hypothesis_claim_ids if selected else ()) if c in claim_by_id]
        rubric["objective_relevance"] = (
            RubricStatus.DOES_NOT_MEET if DefectCode.OBJECTIVE_MISMATCH.value in codes
            else RubricStatus.PARTIAL if any(
                str(c.scope.get("objective_id") or "") == objective_id
                for c in objective_hypotheses)
            else RubricStatus.NOT_ASSESSABLE)
        narrow_customer_support = bool(selected_claims) and any(
            c.evidence_strength.value == "WEAK"
            and any(evidence_by_id.get(eid) is not None
                    and evidence_by_id[eid].source_class == "CUSTOMER_VOICE"
                    for eid in c.supporting_evidence_ids)
            for c in selected_claims)
        rubric["customer_relevance"] = (
            RubricStatus.DOES_NOT_MEET if DefectCode.MISSING_CUSTOMER_EVIDENCE.value in codes
            else RubricStatus.PARTIAL if narrow_customer_support else RubricStatus.MEETS)
        strong_comparison = any(
            c.evidence_strength.value == "STRONG"
            and any(evidence_by_id.get(eid) is not None
                    and evidence_by_id[eid].source_class == "COMPETITOR_ANALYSIS"
                    for eid in c.supporting_evidence_ids)
            for c in selected_claims)
        rubric["differentiation"] = (
            RubricStatus.DOES_NOT_MEET if codes & {
                DefectCode.MISSING_COMPETITOR_EVIDENCE.value,
                DefectCode.POSITIONING_NOT_DIFFERENTIATED.value,
                DefectCode.POSITIONING_ALTERNATIVES_NOT_DISTINCT.value}
            else RubricStatus.MEETS if strong_comparison else RubricStatus.PARTIAL)
        proof_claims = [c for c in selected_claims
                        if str(c.scope.get("role") or "") == "REASON_TO_BELIEVE"]
        proof_evidence = [evidence_by_id[eid] for c in proof_claims
                          for eid in c.supporting_evidence_ids if eid in evidence_by_id]
        proof_independent = bool(proof_evidence) and any(
            e.source_class not in {"BRAND_OWNED", "OWNER_INPUT", "DERIVED_SWOT"}
            for e in proof_evidence)
        rubric["reason_to_believe_credibility"] = (
            RubricStatus.DOES_NOT_MEET if DefectCode.WEAK_REASON_TO_BELIEVE.value in codes
            else RubricStatus.MEETS if proof_independent else RubricStatus.PARTIAL)
        rubric["coherence"] = RubricStatus.PARTIAL if defects else RubricStatus.MEETS
        rubric["real_trade_off"] = (
            RubricStatus.DOES_NOT_MEET if DefectCode.NO_REAL_TRADEOFF.value in codes
            else RubricStatus.MEETS)
        rubric["feasibility"] = RubricStatus.NOT_ASSESSABLE

        hard_fail = (
            research_assessment is not None
            and research_assessment.execution_status
            != AssessmentExecutionStatus.EXECUTED
        ) or any(
            d["code"] == DefectCode.UNSUPPORTED_PREMISE.value
            and ("Broken" in d["detail"] or "missing" in d["detail"].lower())
            for d in defects
        )
        if hard_fail:
            status = VerifierStatus.FAIL
        elif defects:
            status = VerifierStatus.NEEDS_REPAIR
        elif research_question.unknowns:
            status = VerifierStatus.PASS_WITH_DISCLOSURE
        else:
            status = VerifierStatus.PASS
        eligible = status in (
            VerifierStatus.PASS, VerifierStatus.PASS_WITH_DISCLOSURE
        )
        if research_assessment is not None:
            eligible = (
                eligible
                and research_assessment.execution_status
                == AssessmentExecutionStatus.EXECUTED
                and research_assessment.eligible_for_final_selection
                and decision.selection_status == DecisionSelectionStatus.FINAL
            )
        return VerificationResult(
            verification_result_id=f"vr_{uuid4().hex}", status=status,
            eligible_for_approval=eligible,
            defects=tuple(defects), rubric=rubric,
            checked_at=datetime.now(timezone.utc).isoformat())
