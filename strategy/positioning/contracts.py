"""Frozen Gate 2 positioning contracts.

The contracts are deliberately plain immutable values.  They are serialized into one immutable
``runs.output`` JSON document; they are not a second persistence model and they perform no I/O.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class TextEnum(str, Enum):
    """A JSON-safe enum whose string representation is its contract value."""

    def __str__(self) -> str:
        return self.value


class ObjectiveType(TextEnum):
    QUALIFIED_LEADS_GROWTH = "QUALIFIED_LEADS_GROWTH"
    REVENUE_GROWTH = "REVENUE_GROWTH"
    NEW_MARKET_ENTRY = "NEW_MARKET_ENTRY"
    TRIAL_GROWTH = "TRIAL_GROWTH"
    REPEAT_PURCHASE_GROWTH = "REPEAT_PURCHASE_GROWTH"
    AWARENESS_GROWTH = "AWARENESS_GROWTH"
    OTHER = "OTHER"


class ValueStatus(TextEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    NOT_SET = "NOT_SET"


class SufficiencyStatus(TextEnum):
    NOT_ASSESSED = "NOT_ASSESSED"
    COLLECT_MORE = "COLLECT_MORE"
    ASK_OWNER = "ASK_OWNER"
    PARTIALLY_SUFFICIENT = "PARTIALLY_SUFFICIENT"
    SUFFICIENT = "SUFFICIENT"
    CANNOT_PROCEED = "CANNOT_PROCEED"


class Availability(TextEnum):
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"
    NOT_COLLECTED = "NOT_COLLECTED"
    COLLECTION_FAILED = "COLLECTION_FAILED"
    NO_EVIDENCE_FOUND = "NO_EVIDENCE_FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EpistemicType(TextEnum):
    SOURCE_ASSERTION = "SOURCE_ASSERTION"
    DIRECT_OBSERVATION = "DIRECT_OBSERVATION"
    HUMAN_ASSERTION = "HUMAN_ASSERTION"
    INFERENCE = "INFERENCE"
    HYPOTHESIS = "HYPOTHESIS"


class VerificationStatus(TextEnum):
    NOT_ASSESSED = "NOT_ASSESSED"
    SUPPORTED = "SUPPORTED"
    CORROBORATED = "CORROBORATED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_TESTED = "NOT_TESTED"


class EvidenceStrength(TextEnum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


class ResearchCriticality(TextEnum):
    BLOCKING = "BLOCKING"
    DECISION_LIMITING = "DECISION_LIMITING"
    EXPLORATORY = "EXPLORATORY"


class ResolutionAction(TextEnum):
    USE_EXISTING_LOCAL_EVIDENCE = "USE_EXISTING_LOCAL_EVIDENCE"
    ASK_OWNER = "ASK_OWNER"
    COLLECT_SPECIFIC = "COLLECT_SPECIFIC"
    PROCEED_WITH_DISCLOSURE = "PROCEED_WITH_DISCLOSURE"
    DEFER_DECISION = "DEFER_DECISION"


class ResearchRequirementVerdict(TextEnum):
    SATISFIED = "SATISFIED"
    UNMET = "UNMET"
    UNKNOWN = "UNKNOWN"
    UNVERIFIED = "UNVERIFIED"


class AssessmentExecutionStatus(TextEnum):
    EXECUTED = "EXECUTED"
    UNVERIFIED = "UNVERIFIED"


class ResearchReasonCode(TextEnum):
    QUALIFYING_LOCAL_EVIDENCE = "QUALIFYING_LOCAL_EVIDENCE"
    NO_QUALIFYING_EVIDENCE = "NO_QUALIFYING_EVIDENCE"
    OWNER_INPUT_REQUIRED = "OWNER_INPUT_REQUIRED"
    EPISTEMIC_STRENGTH_TOO_WEAK = "EPISTEMIC_STRENGTH_TOO_WEAK"
    VERIFICATION_STATUS_INELIGIBLE = "VERIFICATION_STATUS_INELIGIBLE"
    EVIDENCE_STRENGTH_TOO_WEAK = "EVIDENCE_STRENGTH_TOO_WEAK"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    INDEPENDENCE_NOT_ESTABLISHED = "INDEPENDENCE_NOT_ESTABLISHED"
    VALUE_PROMISE_REUSED_AS_PROOF = "VALUE_PROMISE_REUSED_AS_PROOF"
    EVALUATOR_UNVERIFIED = "EVALUATOR_UNVERIFIED"
    UNKNOWN_POLICY = "UNKNOWN_POLICY"


class IndependenceStatus(TextEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    ESTABLISHED = "ESTABLISHED"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


class ResolutionStopKind(TextEnum):
    EVIDENCE_THRESHOLD_MET = "EVIDENCE_THRESHOLD_MET"
    OWNER_ANSWER_RECORDED = "OWNER_ANSWER_RECORDED"
    DECISION_DEFERRED = "DECISION_DEFERRED"
    DISCLOSURE_RECORDED = "DISCLOSURE_RECORDED"
    EVALUATOR_RESTORED = "EVALUATOR_RESTORED"


class OptionStatus(TextEnum):
    PROVISIONAL = "PROVISIONAL"
    SELECTED = "SELECTED"
    ALTERNATIVE = "ALTERNATIVE"


class DecisionSelectionStatus(TextEnum):
    FINAL = "FINAL"
    PROVISIONAL = "PROVISIONAL"
    NONE = "NONE"


class DecisionState(TextEnum):
    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    NEEDS_REPAIR = "NEEDS_REPAIR"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"


class ApprovalState(TextEnum):
    NOT_SUBMITTED = "NOT_SUBMITTED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class VerifierStatus(TextEnum):
    PASS = "PASS"
    PASS_WITH_DISCLOSURE = "PASS_WITH_DISCLOSURE"
    NEEDS_REPAIR = "NEEDS_REPAIR"
    FAIL = "FAIL"


class RubricStatus(TextEnum):
    MEETS = "MEETS"
    PARTIAL = "PARTIAL"
    DOES_NOT_MEET = "DOES_NOT_MEET"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"


class DefectCode(TextEnum):
    UNSUPPORTED_PREMISE = "UNSUPPORTED_PREMISE"
    MISSING_CUSTOMER_EVIDENCE = "MISSING_CUSTOMER_EVIDENCE"
    MISSING_COMPETITOR_EVIDENCE = "MISSING_COMPETITOR_EVIDENCE"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    OBJECTIVE_MISMATCH = "OBJECTIVE_MISMATCH"
    POSITIONING_NOT_DIFFERENTIATED = "POSITIONING_NOT_DIFFERENTIATED"
    POSITIONING_ALTERNATIVES_NOT_DISTINCT = "POSITIONING_ALTERNATIVES_NOT_DISTINCT"
    WEAK_REASON_TO_BELIEVE = "WEAK_REASON_TO_BELIEVE"
    NO_REAL_TRADEOFF = "NO_REAL_TRADEOFF"
    INSUFFICIENT_RESEARCH = "INSUFFICIENT_RESEARCH"
    UNKNOWN_REQUIRED_OWNER_INPUT = "UNKNOWN_REQUIRED_OWNER_INPUT"


@dataclass(frozen=True, slots=True)
class KnownValue:
    status: ValueStatus = ValueStatus.UNKNOWN
    value: Any = None

    def __post_init__(self) -> None:
        if self.status == ValueStatus.KNOWN and self.value in (None, "", []):
            raise ValueError("KNOWN values require a value")
        if self.status != ValueStatus.KNOWN and self.value is not None:
            raise ValueError("UNKNOWN/NOT_SET values cannot carry an invented value")


@dataclass(frozen=True, slots=True)
class BusinessObjective:
    objective_id: str
    origin: str
    business_type: ObjectiveType
    description: str
    metric: KnownValue = field(default_factory=KnownValue)
    baseline: KnownValue = field(default_factory=KnownValue)
    target: KnownValue = field(default_factory=KnownValue)
    time_horizon: KnownValue = field(default_factory=KnownValue)
    geography: tuple[str, ...] = ()
    market: str = ""
    constraints: tuple[str, ...] = ()
    marketing_role: str = "UNSPECIFIED"
    behavioral_objective: KnownValue = field(default_factory=KnownValue)
    media_optimization: KnownValue = field(
        default_factory=lambda: KnownValue(ValueStatus.NOT_SET, None))
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.objective_id.strip():
            raise ValueError("objective_id is required")
        if not self.description.strip():
            raise ValueError("an explicit objective description is required")


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    requirement_id: str
    requirement_type: str
    required: bool = True
    status: Availability = Availability.UNKNOWN
    decision_dependency: str = "Final positioning selection"
    criticality: ResearchCriticality = ResearchCriticality.BLOCKING
    accepted_source_classes: tuple[str, ...] = ()
    required_claim_roles: tuple[str, ...] = ()
    acceptable_epistemic_types: tuple[EpistemicType, ...] = ()
    acceptable_verification_statuses: tuple[VerificationStatus, ...] = ()
    minimum_evidence_strength: EvidenceStrength = EvidenceStrength.UNKNOWN
    subject_ref: str = ""
    scope: Mapping[str, Any] = field(default_factory=dict)
    minimum_independent_origins: int = 0
    allow_disclosure: bool = False
    owner_input_only: bool = False


@dataclass(frozen=True, slots=True)
class ResearchQuestion:
    question_id: str
    objective_id: str
    question: str
    scope: Mapping[str, Any]
    evidence_requirements: tuple[EvidenceRequirement, ...]
    sufficiency_status: SufficiencyStatus
    unmet_requirement_ids: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    stop_reason: str = ""
    decision_type: str = "POSITIONING"
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    availability: Availability
    source_class: str
    source_ref: str
    source_url: str
    subject_ref: str
    captured_at: str | None
    observed_at: str | None
    observed_at_status: ValueStatus
    market: str
    geography: tuple[str, ...]
    channel: str
    time_scope: str
    raw_value: Any
    excerpt: str
    freshness: str
    relevance: str
    reliability: str
    limitations: tuple[str, ...] = ()
    originating_run_id: str | None = None
    collector: str = ""
    derived_from_evidence_ids: tuple[str, ...] = ()
    # Independence is explicit provenance. A URL difference is never promoted into independence.
    origin_group: str | None = None
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    statement: str
    epistemic_type: EpistemicType
    scope: Mapping[str, Any]
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...] = ()
    verification_status: VerificationStatus = VerificationStatus.NOT_ASSESSED
    evidence_strength: EvidenceStrength = EvidenceStrength.UNKNOWN
    limitations: tuple[str, ...] = ()
    supersedes_claim_id: str | None = None
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class ResolutionStopCondition:
    kind: ResolutionStopKind
    description: str


@dataclass(frozen=True, slots=True)
class ResearchResolutionItem:
    requirement_id: str
    decision_dependency: str
    criticality: ResearchCriticality
    current_status: ResearchRequirementVerdict
    blocking: bool
    reason_code: ResearchReasonCode
    reason_detail: str
    evidence_considered_ids: tuple[str, ...]
    qualifying_evidence_ids: tuple[str, ...]
    required_evidence_class: tuple[str, ...]
    required_epistemic_strength: EvidenceStrength
    subject_ref: str
    scope: Mapping[str, Any]
    independence_status: IndependenceStatus
    independent_origin_count: int
    resolution_action: ResolutionAction
    action_details: str
    stop_condition: ResolutionStopCondition
    unknowns: tuple[str, ...]
    policy_id: str
    policy_version: str
    limitations: tuple[str, ...] = ()
    limitation_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchSufficiencyAssessment:
    assessment_id: str
    question_id: str
    execution_status: AssessmentExecutionStatus
    overall_status: SufficiencyStatus
    eligible_for_final_selection: bool
    requirement_results: tuple[ResearchResolutionItem, ...]
    blocking_requirement_ids: tuple[str, ...]
    unknowns: tuple[str, ...]
    limitations: tuple[str, ...]
    policy_id: str
    policy_version: str
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class PositioningOption:
    option_id: str
    positioning_statement: str
    target: str
    frame_of_reference: str
    differentiated_value: str
    reason_to_believe: str
    supporting_claim_ids: tuple[str, ...]
    hypothesis_claim_ids: tuple[str, ...]
    missing_evidence_requirement_ids: tuple[str, ...]
    trade_offs: tuple[str, ...]
    why_it_may_be_rejected: str
    # Creative semantics are explicit and additive. ``differentiated_value`` remains the approved
    # strategic contrast; it is not automatically customer-facing copy.
    consumer_value: str = ""
    consumer_value_claim_ids: tuple[str, ...] = ()
    differentiation_claim_ids: tuple[str, ...] = ()
    reason_to_believe_claim_ids: tuple[str, ...] = ()
    internal_supporting_claim_ids: tuple[str, ...] = ()
    option_status: OptionStatus = OptionStatus.PROVISIONAL


@dataclass(frozen=True, slots=True)
class StrategicDecision:
    decision_id: str
    objective_id: str
    research_question_id: str
    decision_state: DecisionState
    selected_option_id: str
    options: tuple[PositioningOption, ...]
    rationale: str
    rationale_claim_ids: tuple[str, ...]
    accepted_claim_ids: tuple[str, ...]
    unresolved_unknowns: tuple[str, ...]
    contradictions: tuple[str, ...]
    verification_result_id: str
    approval_state: ApprovalState = ApprovalState.PENDING
    decision_type: str = "POSITIONING"
    schema_version: int = 1
    selection_status: DecisionSelectionStatus = DecisionSelectionStatus.PROVISIONAL
    provisional_option_id: str = ""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verification_result_id: str
    status: VerifierStatus
    eligible_for_approval: bool
    defects: tuple[Mapping[str, Any], ...]
    rubric: Mapping[str, RubricStatus]
    checked_at: str
    schema_version: int = 1


def to_dict(value: Any) -> Any:
    """Recursively convert a contract to JSON primitives without changing UNKNOWNs."""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return to_dict(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): to_dict(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_dict(v) for v in value]
    return value


def objective_to_dict(objective: BusinessObjective) -> dict[str, Any]:
    """The frozen public JSON shape; internal dataclass fields stay convenient and typed."""
    return {
        "schema_version": objective.schema_version,
        "objective_id": objective.objective_id,
        "origin": objective.origin,
        "business": {
            "type": objective.business_type.value,
            "description": objective.description,
            "metric": to_dict(objective.metric),
            "baseline": to_dict(objective.baseline),
            "target": to_dict(objective.target),
            "time_horizon": to_dict(objective.time_horizon),
            "geography": list(objective.geography),
            "market": objective.market,
            "constraints": list(objective.constraints),
        },
        "marketing_role": objective.marketing_role,
        "behavioral_objective": to_dict(objective.behavioral_objective),
        "media_optimization": to_dict(objective.media_optimization),
    }
