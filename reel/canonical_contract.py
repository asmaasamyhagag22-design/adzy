"""Provider-free canonical authority for Adzy Reel.

This module deliberately imports no Reel planner, renderer, provider, Poster brief, or database
model.  It turns one exact persisted StrategyVersion context into an immutable Reel contract and
authorizes consumer-facing semantic intents against that contract.
"""
from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Callable, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReelContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LockedReelField(str, Enum):
    OBJECTIVE = "objective"
    TARGET = "target"
    FRAME_OF_REFERENCE = "frame_of_reference"
    CONSUMER_VALUE = "consumer_value"
    PRIMARY_MESSAGE = "primary_message"
    ALLOWED_FACTUAL_CLAIMS = "allowed_factual_claims"
    CONSUMER_PROOF = "consumer_proof"
    CTA = "cta"
    PRODUCT_TRUTH = "product_truth"
    OFFER_TRUTH = "offer_truth"
    LOCALE = "locale"
    PROHIBITED_CLAIMS = "prohibited_claims"
    PROHIBITED_VISUAL_IMPLICATIONS = "prohibited_visual_implications"


class CreativeFreeReelField(str, Enum):
    HOOK_TREATMENT = "hook_treatment"
    NARRATIVE_DEVICE = "narrative_device"
    SCENE_STRUCTURE = "scene_structure"
    CAMERA = "camera"
    PACING = "pacing"
    TRANSITIONS = "transitions"
    ACTOR_CHOICE = "actor_or_no_actor"
    VISUAL_METAPHOR = "visual_metaphor"
    PERFORMANCE_STYLE = "performance_style"
    MUSIC_DIRECTION = "music_direction"
    SOUND_DESIGN = "sound_design"
    EDITING_RHYTHM = "editing_rhythm"
    COMPOSITION = "composition"


LOCKED_REEL_FIELDS = tuple(LockedReelField)
CREATIVE_FREE_REEL_FIELDS = tuple(CreativeFreeReelField)


class RenderableAllowedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    statement: str
    epistemic_type: str = ""
    verification_status: str = ""
    supporting_evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class InternalStrategicEvidence(BaseModel):
    """Inspectable strategy rationale which cannot be projected to a creative planner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    statement: str
    reason_for_strategy_use: str = ""
    consumer_renderable: Literal[False] = False


class RenderableReelAuthority(BaseModel):
    """The only contract projection a future creative planner is allowed to consume."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str
    contract_hash: str
    strategy_version_id: str
    objective_type: str
    objective_description: str
    target: str
    frame_of_reference: str
    consumer_value: str
    primary_message: str
    exact_cta: str
    allowed_claim_ids: tuple[str, ...]
    allowed_claims: tuple[RenderableAllowedClaim, ...]
    proof_points: tuple[RenderableAllowedClaim, ...]
    prohibited_claims: tuple[str, ...]
    prohibited_visual_implications: tuple[str, ...]
    product_or_offer_focus: str
    language: str
    locale: str
    brand_constraints: tuple[str, ...]
    locked_fields: tuple[LockedReelField, ...]
    creative_free_fields: tuple[CreativeFreeReelField, ...]
    disclosure_requirements: tuple[str, ...]


class ReelCreativeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str
    contract_version: Literal["1.0"] = "1.0"
    strategy_version_id: str
    selected_decision_id: str
    profile_snapshot_id: str
    objective_type: str
    objective_description: str
    target: str
    frame_of_reference: str
    consumer_value: str
    primary_message: str
    exact_cta: str
    allowed_claim_ids: tuple[str, ...]
    allowed_claims: tuple[RenderableAllowedClaim, ...]
    proof_points: tuple[RenderableAllowedClaim, ...]
    internal_supporting_evidence: tuple[InternalStrategicEvidence, ...]
    prohibited_claims: tuple[str, ...]
    prohibited_visual_implications: tuple[str, ...]
    product_or_offer_focus: str
    language: str
    locale: str
    brand_constraints: tuple[str, ...]
    locked_fields: tuple[LockedReelField, ...]
    creative_free_fields: tuple[CreativeFreeReelField, ...]
    disclosure_requirements: tuple[str, ...]
    contract_hash: str

    def planner_authority(self) -> RenderableReelAuthority:
        """Return a type that has no field capable of carrying internal strategy evidence."""
        return RenderableReelAuthority(**self.model_dump(
            include=set(RenderableReelAuthority.model_fields), mode="python"))


def _plain(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("value")
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return re.sub(r"[^\w\u0600-\u06ff]+", " ", _plain(value).casefold()).strip()


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _semantic_match(left: str, right: str) -> bool:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    stop = {"with", "from", "that", "this", "your", "their", "the", "and", "for",
            "our", "into", "على", "من", "إلى", "في", "عن", "مع"}
    at = {v for v in a.split() if len(v) >= 3 and v not in stop}
    bt = {v for v in b.split() if len(v) >= 3 and v not in stop}
    return bool(at and bt and len(at & bt) / max(1, min(len(at), len(bt))) >= 0.65)


def _objective(context: Mapping[str, Any], decision: Mapping[str, Any]) -> tuple[str, str]:
    requested = context.get("requested_config")
    requested = requested if isinstance(requested, Mapping) else {}
    obj = requested.get("objective")
    obj = obj if isinstance(obj, Mapping) else {}
    business = obj.get("business")
    business = business if isinstance(business, Mapping) else {}
    behavioral = obj.get("behavioral_objective")
    behavioral = behavioral if isinstance(behavioral, Mapping) else {}
    objective_type = _plain(
        obj.get("objective_type") or obj.get("type") or business.get("type")
        or decision.get("objective_type") or decision.get("decision_type"))
    description = _plain(
        business.get("description") or obj.get("description") or behavioral.get("value")
        or decision.get("objective_description"))
    if not objective_type:
        objective_type = "UNSPECIFIED"
    if not description:
        raise ReelContractError(
            "CANONICAL_OBJECTIVE_MISSING",
            "The approved StrategyVersion does not contain an objective description.")
    return objective_type, description


def _profile_ctas(profile: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for item in profile.get("existing_ctas") or []:
        text = _plain(item)
        if text and _norm(text) not in {_norm(v) for v in values}:
            values.append(text)
    return values


def _exact_cta(context: Mapping[str, Any], canonical: Mapping[str, Any],
               decision: Mapping[str, Any], selected: Mapping[str, Any],
               objective_description: str, profile: Mapping[str, Any]) -> str:
    requested = context.get("requested_config")
    requested = requested if isinstance(requested, Mapping) else {}
    objective = requested.get("objective")
    objective = objective if isinstance(objective, Mapping) else {}
    explicit = _plain(
        selected.get("exact_cta") or decision.get("exact_cta") or canonical.get("exact_cta")
        or objective.get("exact_cta") or objective.get("cta"))
    if explicit:
        return explicit
    ctas = _profile_ctas(profile)
    objective_terms = {v for v in _norm(objective_description).split() if len(v) >= 4}
    for cta in ctas:
        cta_terms = {v for v in _norm(cta).split() if len(v) >= 4}
        if any(a == b or a.startswith(b) or b.startswith(a)
               for a in cta_terms for b in objective_terms):
            return cta
    if len(ctas) == 1:
        return ctas[0]
    raise ReelContractError(
        "CANONICAL_CTA_MISSING",
        "The approved StrategyVersion does not resolve to one exact advertising CTA.")


def _claim(raw: Mapping[str, Any]) -> RenderableAllowedClaim:
    return RenderableAllowedClaim(
        claim_id=_plain(raw.get("claim_id")),
        statement=_plain(raw.get("statement")),
        epistemic_type=_plain(raw.get("epistemic_type")),
        verification_status=_plain(raw.get("verification_status")),
        supporting_evidence_ids=tuple(str(v) for v in
                                      (raw.get("supporting_evidence_ids") or []) if v),
        limitations=tuple(_plain(v) for v in (raw.get("limitations") or []) if _plain(v)),
    )


def _profile_constraints(profile: Mapping[str, Any]) -> tuple[str, ...]:
    visual = profile.get("visual")
    visual = visual if isinstance(visual, Mapping) else {}
    colors = tuple(str(v) for v in (visual.get("palette_hex") or []) if v)
    values: list[str] = []
    if colors:
        values.append("Use only the evidenced brand palette: " + ", ".join(colors[:6]))
    if visual.get("logo_url") or visual.get("primary_logo") or profile.get("logo_url"):
        values.append("Preserve the real brand mark without redrawing or mutating it.")
    tone = _plain(profile.get("tone_of_voice"))
    if tone:
        values.append("Use the evidenced brand tone: " + tone)
    return tuple(values)


class ReelCreativeContractCompiler:
    """Compile one exact, current, approved StrategyVersion without a provider call."""

    def compile(self, context: Mapping[str, Any], *, current_only: bool = True
                ) -> ReelCreativeContract:
        strategy_id = _plain(context.get("strategy_version_id"))
        if not strategy_id:
            raise ReelContractError("STRATEGY_VERSION_MISSING",
                                    "An exact StrategyVersion is required for canonical Reel.")
        if _plain(context.get("run_kind")) != "strategy" \
                or _plain(context.get("run_status")) != "succeeded":
            raise ReelContractError("STRATEGY_VERSION_INVALID",
                                    "The requested version is not a successful strategy run.")
        if _plain(context.get("brand_id")) != _plain(context.get("run_brand_id")):
            raise ReelContractError("STRATEGY_WRONG_BRAND",
                                    "The StrategyVersion belongs to another brand.")
        if current_only and not bool(context.get("is_current")):
            raise ReelContractError("STRATEGY_VERSION_STALE",
                                    "The StrategyVersion is not the current strategy.")
        if _plain(context.get("approval_state")).casefold() != "approved":
            raise ReelContractError("STRATEGY_NOT_APPROVED",
                                    "The exact StrategyVersion is not approved.")
        profile_id = _plain(context.get("profile_snapshot_id"))
        if not profile_id or profile_id != _plain(context.get("run_input_profile_id")):
            raise ReelContractError("PROFILE_SNAPSHOT_MISMATCH",
                                    "The StrategyVersion does not resolve to its exact profile.")

        output = context.get("output")
        output = output if isinstance(output, Mapping) else {}
        canonical = output.get("canonical_strategy")
        canonical = canonical if isinstance(canonical, Mapping) else {}
        verification = canonical.get("verification")
        verification = verification if isinstance(verification, Mapping) else {}
        if not bool(verification.get("eligible_for_approval")):
            raise ReelContractError("STRATEGY_NOT_ELIGIBLE",
                                    "The StrategyVersion verifier did not make this version eligible.")
        decision = canonical.get("decision")
        decision = decision if isinstance(decision, Mapping) else {}
        selected_option_id = _plain(decision.get("selected_option_id"))
        selected = next((v for v in (decision.get("options") or [])
                         if isinstance(v, Mapping)
                         and _plain(v.get("option_id")) == selected_option_id), None)
        if not isinstance(selected, Mapping):
            raise ReelContractError("STRATEGY_SELECTION_MISSING",
                                    "The approved decision has no selected positioning option.")
        selected_decision_id = _plain(decision.get("decision_id"))
        if not selected_decision_id:
            raise ReelContractError("STRATEGY_DECISION_MISSING",
                                    "The approved strategy has no immutable decision id.")

        claims = {str(v.get("claim_id")): v for v in (canonical.get("claims") or [])
                  if isinstance(v, Mapping) and v.get("claim_id")}
        consumer_ids = tuple(dict.fromkeys(str(v) for v in
                             (selected.get("consumer_value_claim_ids") or []) if v))
        proof_ids = tuple(dict.fromkeys(str(v) for v in
                          (selected.get("reason_to_believe_claim_ids") or []) if v))
        internal_ids = tuple(dict.fromkeys(str(v) for v in (
            list(selected.get("internal_supporting_claim_ids") or [])
            + list(selected.get("differentiation_claim_ids") or [])) if v))
        referenced = set(consumer_ids) | set(proof_ids) | set(internal_ids)
        missing = sorted(referenced - set(claims))
        if missing:
            raise ReelContractError("STRATEGY_CLAIM_MISSING",
                                    "Strategy references missing claims: " + ", ".join(missing))

        target = _plain(selected.get("target"))
        frame = _plain(selected.get("frame_of_reference"))
        consumer_value = _plain(selected.get("consumer_value"))
        if not target or not frame or not consumer_value:
            raise ReelContractError(
                "STRATEGY_NOT_REEL_READY",
                "Target, frame of reference, and consumer value must all be explicit.")
        if not consumer_ids or not any(
                _semantic_match(consumer_value, _plain(claims[cid].get("statement")))
                for cid in consumer_ids):
            raise ReelContractError(
                "CONSUMER_VALUE_CLAIM_MISMATCH",
                "Consumer value is not bound to an approved consumer claim id.")

        # Consumer proof is a separate semantic role.  The primary message never becomes proof
        # merely because the same approved claim supports the value statement.
        reason = _plain(selected.get("reason_to_believe"))
        renderable_proof_ids = tuple(cid for cid in proof_ids
                                     if cid not in internal_ids
                                     and reason
                                     and not _semantic_match(
                                         consumer_value, _plain(claims[cid].get("statement"))))
        allowed_ids = tuple(dict.fromkeys((*consumer_ids, *renderable_proof_ids)))
        allowed_claims = tuple(_claim(claims[cid]) for cid in allowed_ids)
        proof_points = tuple(_claim(claims[cid]) for cid in renderable_proof_ids)

        internal: list[InternalStrategicEvidence] = [
            InternalStrategicEvidence(
                evidence_id=cid,
                statement=_plain(claims[cid].get("statement")),
                reason_for_strategy_use="Strategy-only supporting evidence",
            ) for cid in internal_ids
        ]
        for index, unknown in enumerate(decision.get("unresolved_unknowns") or []):
            statement = _plain(unknown)
            if statement:
                internal.append(InternalStrategicEvidence(
                    evidence_id=f"unknown_{index + 1}", statement=statement,
                    reason_for_strategy_use="Unresolved strategy limitation"))

        objective_type, objective_description = _objective(context, decision)
        profile = context.get("profile")
        profile = profile if isinstance(profile, Mapping) else {}
        exact_cta = _exact_cta(context, canonical, decision, selected,
                               objective_description, profile)
        prohibited = tuple(dict.fromkeys(filter(None, (
            "No factual claim outside allowed_claim_ids.",
            "No review, rating, competitor statistic, CTA count, internal strength, research "
            "observation, prevalence, ranking, guarantee, discount, urgency, or uplift unless "
            "it is an explicit approved consumer claim.",
            *(_plain(v) for v in (selected.get("prohibited_claims") or [])),
        ))))
        prohibited_visual = tuple(dict.fromkeys(filter(None, (
            "before/after transformation without an approved factual claim",
            "guaranteed result",
            "social success caused by the product",
            "physical transformation caused by the product",
            "performance superiority",
            *(_plain(v) for v in (selected.get("prohibited_visual_implications") or [])),
        ))))
        language = _plain(selected.get("language") or canonical.get("language")
                          or profile.get("primary_language"))
        if not language:
            languages = profile.get("languages") or []
            language = _plain(languages[0] if languages else "")
        locale = _plain(selected.get("locale") or canonical.get("locale")
                        or profile.get("locale") or profile.get("country"))

        material = {
            "contract_version": "1.0",
            "strategy_version_id": strategy_id,
            "selected_decision_id": selected_decision_id,
            "profile_snapshot_id": profile_id,
            "objective_type": objective_type,
            "objective_description": objective_description,
            "target": target,
            "frame_of_reference": frame,
            "consumer_value": consumer_value,
            "primary_message": consumer_value,
            "exact_cta": exact_cta,
            "allowed_claim_ids": allowed_ids,
            "allowed_claims": [v.model_dump(mode="json") for v in allowed_claims],
            "proof_points": [v.model_dump(mode="json") for v in proof_points],
            "internal_supporting_evidence": [v.model_dump(mode="json") for v in internal],
            "prohibited_claims": prohibited,
            "prohibited_visual_implications": prohibited_visual,
            "product_or_offer_focus": _plain(selected.get("product_or_offer_focus")),
            "language": language,
            "locale": locale,
            "brand_constraints": _profile_constraints(profile),
            "locked_fields": [v.value for v in LOCKED_REEL_FIELDS],
            "creative_free_fields": [v.value for v in CREATIVE_FREE_REEL_FIELDS],
            "disclosure_requirements": tuple(_plain(v) for v in
                                              (selected.get("disclosure_requirements") or [])
                                              if _plain(v)),
        }
        contract_hash = _hash(material)
        return ReelCreativeContract(
            contract_id="rcc_" + contract_hash[:32], contract_hash=contract_hash, **material)


class ClaimSurface(str, Enum):
    HOOK = "HOOK"
    VOICE_OVER = "VOICE_OVER"
    DIALOGUE = "DIALOGUE"
    CAPTION = "CAPTION"
    ON_SCREEN_COPY = "ON_SCREEN_COPY"
    PACKAGE_SIGN_COPY = "PACKAGE_SIGN_COPY"
    END_CARD = "END_CARD"
    SOCIAL_CAPTION = "SOCIAL_CAPTION"
    VISUAL_IMPLICATION = "VISUAL_IMPLICATION"


class ClaimType(str, Enum):
    MESSAGE = "MESSAGE"
    PROOF = "PROOF"
    CTA = "CTA"
    FACTUAL = "FACTUAL"
    EXPRESSIVE = "EXPRESSIVE"
    VISUAL = "VISUAL"


class ClaimVerificationStatus(str, Enum):
    NOT_VERIFIED = "NOT_VERIFIED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class ClaimAuthorizationState(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    EXPRESSIVE_NONFACTUAL = "EXPRESSIVE_NONFACTUAL"
    REQUIRES_REPAIR = "REQUIRES_REPAIR"
    BLOCKED = "BLOCKED"


class ClaimIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    surface: ClaimSurface
    text: str = ""
    semantic_intent: str = ""
    supporting_allowed_claim_ids: tuple[str, ...] = ()
    claim_type: ClaimType
    factual: bool
    expressive: bool
    exact_wording_required: bool
    verification_status: ClaimVerificationStatus

    @model_validator(mode="after")
    def has_semantics(self) -> "ClaimIntent":
        if not _plain(self.text or self.semantic_intent):
            raise ValueError("a claim intent must declare text or semantic_intent")
        if self.factual and self.expressive:
            raise ValueError("a claim intent cannot be both factual and expressive")
        return self


class ClaimAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ClaimAuthorizationState
    code: str
    detail: str
    authorized_claim_ids: tuple[str, ...] = ()


_FACTUAL_MARKERS = re.compile(
    r"\d|\b(best|first|leading|largest|fastest|guaranteed|reviews?|ratings?|average|"
    r"customers?|everyone|many|most|discount|superior)\b|"
    r"(الأفضل|الأول|الأكبر|الأسرع|مضمون|مراجعات|تقييم|متوسط|العملاء|أغلب|خصم)",
    re.IGNORECASE,
)


def authorize_claim(intent: ClaimIntent, contract: ReelCreativeContract, *,
                    supplemental_checker: Optional[Callable[[ClaimIntent], bool]] = None
                    ) -> ClaimAuthorization:
    """Authorize one consumer-facing semantic intent; every unexpected error blocks."""
    try:
        content = _plain(intent.text or intent.semantic_intent)
        if supplemental_checker is not None and not bool(supplemental_checker(intent)):
            return ClaimAuthorization(
                state=ClaimAuthorizationState.BLOCKED, code="SUPPLEMENTAL_CHECK_REJECTED",
                detail="The supporting evidence check rejected this intent.")

        for internal in contract.internal_supporting_evidence:
            if _semantic_match(content, internal.statement):
                return ClaimAuthorization(
                    state=ClaimAuthorizationState.BLOCKED,
                    code="INTERNAL_EVIDENCE_LEAK",
                    detail="Internal strategy evidence is not consumer-renderable.")

        for prohibited in contract.prohibited_claims:
            if _semantic_match(content, prohibited):
                return ClaimAuthorization(
                    state=ClaimAuthorizationState.BLOCKED,
                    code="PROHIBITED_CLAIM",
                    detail="The intent conflicts with a prohibited canonical claim.")

        if intent.claim_type == ClaimType.CTA or intent.surface == ClaimSurface.END_CARD:
            if intent.text != contract.exact_cta:
                return ClaimAuthorization(
                    state=ClaimAuthorizationState.REQUIRES_REPAIR, code="CTA_MISMATCH",
                    detail=f"CTA must be exactly: {contract.exact_cta}")
            return ClaimAuthorization(
                state=ClaimAuthorizationState.AUTHORIZED, code="EXACT_CTA_AUTHORIZED",
                detail="CTA exactly matches the canonical contract.")

        if intent.claim_type == ClaimType.PROOF and not contract.proof_points:
            return ClaimAuthorization(
                state=ClaimAuthorizationState.BLOCKED, code="NO_APPROVED_CONSUMER_PROOF",
                detail="The contract approves no separate consumer-facing proof.")

        if intent.surface == ClaimSurface.VISUAL_IMPLICATION:
            for prohibited in contract.prohibited_visual_implications:
                if _semantic_match(content, prohibited):
                    return ClaimAuthorization(
                        state=ClaimAuthorizationState.BLOCKED,
                        code="PROHIBITED_VISUAL_IMPLICATION",
                        detail="The visual implication is prohibited by the canonical contract.")

        requested_ids = tuple(dict.fromkeys(intent.supporting_allowed_claim_ids))
        unknown_ids = set(requested_ids) - set(contract.allowed_claim_ids)
        if unknown_ids:
            return ClaimAuthorization(
                state=ClaimAuthorizationState.BLOCKED, code="UNSUPPORTED_CLAIM",
                detail="Intent references claim ids outside the canonical allowlist.")

        looks_factual = bool(_FACTUAL_MARKERS.search(content))
        if intent.factual or looks_factual or intent.claim_type in {
                ClaimType.FACTUAL, ClaimType.PROOF, ClaimType.VISUAL}:
            if not requested_ids:
                return ClaimAuthorization(
                    state=ClaimAuthorizationState.BLOCKED, code="UNSUPPORTED_CLAIM",
                    detail="A factual or visual claim requires an approved claim id.")
            claim_map = {v.claim_id: v for v in contract.allowed_claims}
            if intent.surface != ClaimSurface.VISUAL_IMPLICATION and not any(
                    cid in claim_map and _semantic_match(content, claim_map[cid].statement)
                    for cid in requested_ids):
                return ClaimAuthorization(
                    state=ClaimAuthorizationState.REQUIRES_REPAIR,
                    code="CLAIM_WORDING_NOT_AUTHORIZED",
                    detail="The wording is not a faithful expression of its approved claim.")
            return ClaimAuthorization(
                state=ClaimAuthorizationState.AUTHORIZED, code="CLAIM_AUTHORIZED",
                detail="The intent is bound to the canonical claim allowlist.",
                authorized_claim_ids=requested_ids)

        if intent.expressive and not intent.factual:
            return ClaimAuthorization(
                state=ClaimAuthorizationState.EXPRESSIVE_NONFACTUAL,
                code="EXPRESSIVE_NONFACTUAL", detail="No factual assertion was detected.")
        return ClaimAuthorization(
            state=ClaimAuthorizationState.REQUIRES_REPAIR,
            code="CLAIM_CLASSIFICATION_REQUIRED",
            detail="The intent is neither an authorized claim nor declared expressive language.")
    except Exception:
        return ClaimAuthorization(
            state=ClaimAuthorizationState.BLOCKED,
            code="CLAIM_VERIFICATION_ERROR",
            detail="Claim verification failed closed.")


__all__ = [
    "ClaimAuthorization", "ClaimAuthorizationState", "ClaimIntent", "ClaimSurface",
    "ClaimType", "ClaimVerificationStatus", "CREATIVE_FREE_REEL_FIELDS",
    "CreativeFreeReelField", "InternalStrategicEvidence", "LOCKED_REEL_FIELDS",
    "LockedReelField", "ReelContractError", "ReelCreativeContract",
    "ReelCreativeContractCompiler", "RenderableAllowedClaim", "RenderableReelAuthority",
    "authorize_claim",
]
