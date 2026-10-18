"""Canonical strategy-to-poster contract, compiler, drift verifier, and repair scopes.

This module is deliberately provider-free.  It receives one persisted strategy context, verifies
its authority, and compiles the small immutable input that the existing poster pipeline consumes.
No LLM is allowed to reinterpret the selected positioning here.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field


class CreativeContractError(ValueError):
    def __init__(self, code: str, message: str, *, safe_alternative: str = "",
                 preflight_status: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.safe_alternative = safe_alternative
        self.preflight_status = preflight_status


class CreativeRequestStrategyConflict(CreativeContractError):
    def __init__(self, message: str, *, safe_alternative: str = "") -> None:
        super().__init__("CREATIVE_REQUEST_STRATEGY_CONFLICT", message,
                         safe_alternative=safe_alternative)


class AllowedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    statement: str
    strategic_role: str = ""
    epistemic_type: str = ""
    verification_status: str = ""
    evidence_strength: str = ""
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class HypothesisField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str
    epistemic_type: Literal["HYPOTHESIS"] = "HYPOTHESIS"


class CreativePreflightReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    field: str
    detail: str


class CreativePreflight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["READY", "NEEDS_REPAIR", "STRATEGY_NOT_CREATIVE_READY"]
    reasons: list[CreativePreflightReason] = Field(default_factory=list)


class CreativeRequest(BaseModel):
    """Only execution choices may be supplied; attempted strategy overrides are explicit.

    The three ``requested_*`` fields exist so an API can return a typed conflict instead of
    quietly ignoring an override hidden in free text.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_focus: str = ""
    offer_focus: str = ""
    occasion: str = ""
    format: str = "poster_1080x1350"
    style_preference: str = ""
    cta_preference: str = ""
    creative_angle: str = ""
    language: str = "auto"
    locale: str = ""
    requested_target: str = ""
    requested_positioning: str = ""
    requested_factual_claims: list[str] = Field(default_factory=list)
    creative_hypothesis: Optional[HypothesisField] = None
    expected_response: Optional[HypothesisField] = None


class MessageSourceSupport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["verbatim-profile-source@1"] = "verbatim-profile-source@1"
    support_kind: Literal["SOURCE_ASSERTION"] = "SOURCE_ASSERTION"
    claim_evidence_ids: dict[str, list[str]]
    proof_entailment: Literal["NOT_ASSESSED"] = "NOT_ASSESSED"


class CreativeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1, 2] = 2
    message_source_support: Optional[MessageSourceSupport] = None
    creative_contract_id: str
    strategy_version_id: str
    brand_id: str
    domain_reference: str
    profile_snapshot_id: str
    objective_id: str
    decision_id: str
    decision_type: str

    target: str
    frame_of_reference: str
    positioning_statement: str
    consumer_value: str
    differentiated_value: str
    reason_to_believe: str
    supporting_claim_ids: list[str]
    consumer_value_claim_ids: list[str]
    differentiation_claim_ids: list[str]
    reason_to_believe_claim_ids: list[str]
    internal_supporting_claim_ids: list[str]
    approved_unknowns: list[str]

    primary_message: str
    proof_points: list[AllowedClaim]
    differentiation_evidence: list[AllowedClaim]
    internal_supporting_evidence: list[AllowedClaim]
    allowed_claim_ids: list[str]
    allowed_claims: list[AllowedClaim]
    prohibited_or_unverified_claims: list[str]
    message_boundaries: list[str]

    copy_evidence: list[dict[str, Any]] = Field(default_factory=list)
    copy_metadata_evidence: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    asset_type: Literal["POSTER"] = "POSTER"
    channel_context: str = ""
    language: str
    locale: str = ""
    cta_direction: str
    product_offer_focus: str = ""
    brand_visual_constraints: list[str] = Field(default_factory=list)
    brand_tone_constraints: list[str] = Field(default_factory=list)
    internal_disclosures: list[str] = Field(default_factory=list)
    consumer_facing_disclosures: list[str] = Field(default_factory=list)
    creative_hypothesis: Optional[HypothesisField] = None
    expected_response: Optional[HypothesisField] = None
    preflight: CreativePreflight


class StrategyPosterBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    creative_contract_id: str
    strategy_version_id: str
    objective_context: str
    target: str
    positioning: str
    primary_message: str
    proof_points: list[AllowedClaim]
    cta: str
    product_offer_focus: str = ""
    visual_direction_constraints: list[str] = Field(default_factory=list)
    brand_tone_constraints: list[str] = Field(default_factory=list)
    language: str
    locale: str = ""
    required_elements: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    consumer_facing_disclosures: list[str] = Field(default_factory=list)


class PosterStrategyDefect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal[
        "TARGET_DRIFT", "POSITIONING_DRIFT", "MESSAGE_DRIFT",
        "UNSUPPORTED_POSTER_CLAIM", "PROOF_MESSAGE_MISMATCH", "CTA_CONFLICT",
        "PRODUCT_CONTEXT_CONFLICT", "LOCALE_CONFLICT",
    ]
    field: str
    detail: str
    repair_scope: str


class PosterStrategyVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    checked: bool = True
    overall_pass: bool
    defects: list[PosterStrategyDefect] = Field(default_factory=list)
    claim_ids_used: list[str] = Field(default_factory=list)


_REPAIR_SCOPE = {
    "UNSUPPORTED_POSTER_CLAIM": "COPY_ONLY",
    "MESSAGE_DRIFT": "MESSAGE_AND_COPY",
    "CTA_CONFLICT": "CTA_ONLY",
    "LOCALE_CONFLICT": "COPY_LOCALIZATION",
    "TARGET_DRIFT": "CONCEPT_AND_COPY",
    "POSITIONING_DRIFT": "CONCEPT_AND_COPY",
    "PROOF_MESSAGE_MISMATCH": "COPY_ONLY",
    "PRODUCT_CONTEXT_CONFLICT": "BRIEF_AND_CONCEPT",
    "VISUAL_QA_FAILURE": "EXISTING_VISUAL_REPAIR",
}


def repair_scope_for(code: str) -> str:
    return _REPAIR_SCOPE.get(str(code or ""), "REJECT")


def _plain(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("value")
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return re.sub(r"[^\w\u0600-\u06ff]+", " ", _plain(value).casefold()).strip()


def _numbers(value: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", value or ""))


_INTERNAL_MESSAGE_MARKERS = re.compile(
    r"\b(review volume|peer average|sample size|n\s*=|evidence strength|claim[_ -]?id|"
    r"snapshot contains|baseline|kpi|uplift|verification status|customer signal|"
    r"one observation|not prevalence)\b|"
    r"(حجم المراجعات|متوسط المنافسين|حجم العينة|دليل داخلي|إشارة عميل|ملاحظة واحدة)",
    re.IGNORECASE,
)


def _consumer_value_problem(value: str) -> str:
    text = _plain(value)
    if not text:
        return "The selected positioning does not declare an explicit consumer_value."
    if _INTERNAL_MESSAGE_MARKERS.search(text):
        return "consumer_value contains an internal metric, diagnostic, or evidence qualifier."
    return ""


def _semantic_supports(message: str, proof: str) -> bool:
    """Only identity is decidable here. Lexical similarity is not entailment."""
    return bool(_plain(message) and _plain(message) == _plain(proof))


def _source_support(claim: AllowedClaim, canonical: Mapping[str, Any],
                    profile: Mapping[str, Any], domain: str) -> list[str]:
    """Resolve a verbatim source assertion through its frozen profile evidence path.

    This proves SOURCE_ASSERTION support only, never the truth of the source or an
    entailment between the selected value and its strategic reason to believe.
    """
    raw = next((v for v in canonical.get("claims", [])
                if v.get("claim_id") == claim.claim_id), {})
    if (claim.epistemic_type != "SOURCE_ASSERTION"
            or claim.verification_status not in {"SUPPORTED", "CORROBORATED"}
            or raw.get("contradicting_evidence_ids")):
        return []
    found = []
    for e in canonical.get("evidence", []):
        if (e.get("evidence_id") not in claim.supporting_evidence_ids
                or e.get("availability") != "AVAILABLE"
                or e.get("source_class") != "BRAND_OWNED"
                or e.get("subject_ref") != domain
                or e.get("excerpt") != claim.statement
                or e.get("raw_value") != claim.statement
                or not str(e.get("source_url", "")).startswith(("https://", "http://"))):
            continue
        # Follow an existing serialized path; neither a citation nor mutable status
        # metadata alone can manufacture support for a different statement.
        path = re.fullmatch(r"profile\.([a-zA-Z_]+)\[(\d+)\]\.evidence\[(\d+)\]", e.get("source_ref", ""))
        if not path:
            continue
        try:
            item = profile[path[1]][int(path[2])]
            ref = item["evidence"][int(path[3])]
            if (item.get("value") == claim.statement and ref.get("quote") == claim.statement
                    and ref.get("page_url") == e["source_url"]):
                found.append(e["evidence_id"])
        except (KeyError, IndexError, TypeError):
            continue
    return found


def _stable_id(payload: Mapping[str, Any]) -> str:
    frozen = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "cc_" + uuid.uuid5(uuid.NAMESPACE_URL, frozen).hex


def _claim_matches(text: str, claim: AllowedClaim) -> bool:
    # Preserve quantifiers, entity, negation and conditions; no subset/overlap support.
    return bool(_plain(text) and _plain(text) == claim.statement)


def claim_ids_for_text(text: str, contract: CreativeContract) -> list[str]:
    return [claim.claim_id for claim in contract.allowed_claims if _claim_matches(text, claim)]


def _profile_ctas(profile: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for item in profile.get("existing_ctas") or []:
        value = item.get("value") if isinstance(item, Mapping) else item
        text = _plain(value)
        if text and text.casefold() not in {v.casefold() for v in out}:
            out.append(text)
    return out


def _objective_text(context: Mapping[str, Any], canonical: Mapping[str, Any]) -> str:
    requested = (context.get("requested_config")
                 if isinstance(context.get("requested_config"), Mapping) else {})
    objective = (requested.get("objective")
                 if isinstance(requested.get("objective"), Mapping) else {})
    if not objective:
        objective = (canonical.get("objective")
                     if isinstance(canonical.get("objective"), Mapping) else {})
    business = objective.get("business") if isinstance(objective.get("business"), Mapping) else {}
    behavioral = (objective.get("behavioral_objective")
                  if isinstance(objective.get("behavioral_objective"), Mapping) else {})
    return " ".join(filter(None, (
        _plain(business.get("description")),
        _plain(behavioral.get("value")),
    )))


def _shares_objective_action(cta: str, objective_text: str) -> bool:
    cta_terms = [v for v in _norm(cta).split() if len(v) >= 4]
    objective_terms = [v for v in _norm(objective_text).split() if len(v) >= 4]
    return any(
        left == right or left.startswith(right) or right.startswith(left)
        for left in cta_terms for right in objective_terms
    )


def _default_cta(ctas: list[str], context: Mapping[str, Any],
                 canonical: Mapping[str, Any]) -> str:
    """Use the first evidenced CTA that shares an action with the explicit objective."""
    objective_text = _objective_text(context, canonical)
    if objective_text:
        matched = next((cta for cta in ctas
                        if _shares_objective_action(cta, objective_text)), "")
        if matched:
            return matched
    return ctas[0] if ctas else "Visit website"


def _profile_offerings(profile: Mapping[str, Any]) -> list[str]:
    return [_plain(item.get("name") if isinstance(item, Mapping) else item)
            for item in (profile.get("offerings") or [])
            if _plain(item.get("name") if isinstance(item, Mapping) else item)]


def _default_language(profile: Mapping[str, Any]) -> str:
    try:
        from poster.locale import content_language_of
        found = str(content_language_of(dict(profile)) or "").lower()[:2]
        if found in ("ar", "en"):
            return found
    except Exception:
        pass
    languages = [str(v).lower()[:2] for v in (profile.get("languages") or [])]
    return languages[0] if languages and languages[0] in ("ar", "en") else "en"


def _default_locale(profile: Mapping[str, Any]) -> str:
    try:
        from poster.locale import country_of
        return str(country_of(dict(profile)) or "")
    except Exception:
        return ""


def _visual_constraints(profile: Mapping[str, Any]) -> list[str]:
    visual = profile.get("visual") if isinstance(profile.get("visual"), Mapping) else {}
    colors = [str(v) for v in (visual.get("palette_hex") or []) if v]
    if not colors:
        colors = [str(v) for v in (profile.get("brand_colors") or []) if v]
    out = ["Use the evidenced brand palette: " + ", ".join(colors[:5])] if colors else []
    if visual.get("logo_url") or visual.get("primary_logo") or profile.get("logo_url"):
        out.append("Use the real brand mark; never redraw or mutate it.")
    return out


def _copy_metadata(profile):
    """Copy identity/contact only with exact source quotation; never manufacture provenance."""
    result = {}
    name = profile.get('name') or {}
    if isinstance(name, Mapping) and isinstance(name.get('value'), str):
        refs = [dict(ref) for ref in name.get('evidence', []) if isinstance(ref, Mapping)
                and ref.get('quote') == name['value'] and str(ref.get('page_url', '')).startswith(('https://', 'http://'))]
        if refs:
            for field in ('business_name', 'logo_text'):
                result[field] = [{'value': name['value'], **ref, 'profile_field': 'name'} for ref in refs]
    contact = profile.get('contact_channels') or {}
    if isinstance(contact, Mapping):
        values = list(contact.get('emails') or []) + list(contact.get('whatsapp_numbers') or [])
        for phone in contact.get('phones') or []:
            if isinstance(phone, Mapping): values.extend(v for v in (phone.get('e164'), phone.get('raw')) if v)
        for value in values:
            if not isinstance(value, str): continue
            for ref in contact.get('evidence') or []:
                if (isinstance(ref, Mapping) and ref.get('quote') == value
                        and str(ref.get('page_url', '')).startswith(('https://', 'http://'))):
                    result.setdefault('contact_line', []).append({'value': value, **dict(ref), 'profile_field': 'contact_channels'})
    return result


class CreativeContractCompiler:
    """Compile the strategic core from one exact, approved, current StrategyVersion."""

    def compile(self, context: Mapping[str, Any], *,
                request: CreativeRequest | Mapping[str, Any] | None = None,
                current_only: bool = True) -> CreativeContract:
        req = request if isinstance(request, CreativeRequest) else CreativeRequest(**(request or {}))
        run_id = _plain(context.get("strategy_version_id"))
        if not run_id:
            raise CreativeContractError("STRATEGY_VERSION_MISSING",
                                        "An exact StrategyVersion is required for a canonical poster.")
        if _plain(context.get("run_kind")) != "strategy" or _plain(context.get("run_status")) != "succeeded":
            raise CreativeContractError("STRATEGY_VERSION_INVALID",
                                        "The requested run is not a successful strategy run.")
        brand_id = _plain(context.get("brand_id"))
        if brand_id != _plain(context.get("run_brand_id")):
            raise CreativeContractError("STRATEGY_WRONG_BRAND",
                                        "The StrategyVersion belongs to a different brand.")
        if current_only and not bool(context.get("is_current")):
            raise CreativeContractError("STRATEGY_VERSION_STALE",
                                        "The StrategyVersion is no longer the current strategy.")
        if _plain(context.get("approval_state")).lower() != "approved":
            raise CreativeContractError("STRATEGY_NOT_APPROVED",
                                        "The exact StrategyVersion has no current approved decision.")

        profile = context.get("profile") if isinstance(context.get("profile"), Mapping) else {}
        profile_id = _plain(context.get("profile_snapshot_id"))
        if not profile_id or profile_id != _plain(context.get("run_input_profile_id")):
            raise CreativeContractError("PROFILE_SNAPSHOT_MISMATCH",
                                        "The StrategyVersion does not resolve to its exact ProfileSnapshot.")

        output = context.get("output") if isinstance(context.get("output"), Mapping) else {}
        canonical = output.get("canonical_strategy") if isinstance(output.get("canonical_strategy"), Mapping) else {}
        decision = canonical.get("decision") if isinstance(canonical.get("decision"), Mapping) else {}
        verification = canonical.get("verification") if isinstance(canonical.get("verification"), Mapping) else {}
        if not decision or not verification or not bool(verification.get("eligible_for_approval")):
            raise CreativeContractError("STRATEGY_NOT_ELIGIBLE",
                                        "The persisted strategy verifier did not make this run eligible.")

        selected_id = _plain(decision.get("selected_option_id"))
        options = [v for v in (decision.get("options") or []) if isinstance(v, Mapping)]
        selected = next((v for v in options if _plain(v.get("option_id")) == selected_id), None)
        if selected is None:
            raise CreativeContractError("STRATEGY_SELECTION_MISSING",
                                        "The approved decision has no selected positioning option.")

        supporting_ids = [str(v) for v in (selected.get("supporting_claim_ids") or []) if v]
        claim_map = {str(v.get("claim_id")): v for v in (canonical.get("claims") or [])
                     if isinstance(v, Mapping) and v.get("claim_id")}
        missing = [cid for cid in supporting_ids if cid not in claim_map]
        if missing:
            raise CreativeContractError("STRATEGY_CLAIM_MISSING",
                                        "The selected option references claims absent from the StrategyVersion.")
        allowed = [AllowedClaim(
            claim_id=cid,
            statement=_plain(claim_map[cid].get("statement")),
            strategic_role=_plain(
                (claim_map[cid].get("scope") or {}).get("role")
                if isinstance(claim_map[cid].get("scope"), Mapping) else ""),
            epistemic_type=_plain(claim_map[cid].get("epistemic_type")),
            verification_status=_plain(claim_map[cid].get("verification_status")),
            evidence_strength=_plain(claim_map[cid].get("evidence_strength")),
            supporting_evidence_ids=[str(v) for v in
                                     (claim_map[cid].get("supporting_evidence_ids") or []) if v],
            limitations=[_plain(v) for v in (claim_map[cid].get("limitations") or []) if _plain(v)],
        ) for cid in supporting_ids]

        consumer_value = _plain(selected.get("consumer_value"))
        consumer_problem = _consumer_value_problem(consumer_value)
        if consumer_problem:
            raise CreativeContractError(
                "STRATEGY_NOT_CREATIVE_READY", consumer_problem,
                safe_alternative=(
                    "Create and approve a new StrategyVersion whose selected PositioningOption "
                    "declares a customer-facing consumer_value separately from differentiation evidence."),
                preflight_status="STRATEGY_NOT_CREATIVE_READY")

        semantic_id_fields = {
            "consumer_value_claim_ids": [str(v) for v in
                                         (selected.get("consumer_value_claim_ids") or []) if v],
            "differentiation_claim_ids": [str(v) for v in
                                          (selected.get("differentiation_claim_ids") or []) if v],
            "reason_to_believe_claim_ids": [str(v) for v in
                                            (selected.get("reason_to_believe_claim_ids") or []) if v],
            "internal_supporting_claim_ids": [str(v) for v in
                                              (selected.get("internal_supporting_claim_ids") or []) if v],
        }
        semantic_missing = sorted({cid for ids in semantic_id_fields.values() for cid in ids
                                   if cid not in claim_map})
        if semantic_missing:
            raise CreativeContractError(
                "STRATEGY_CLAIM_MISSING",
                "Creative semantics reference claims absent from the StrategyVersion: "
                + ", ".join(semantic_missing), preflight_status="NEEDS_REPAIR")
        consumer_ids = semantic_id_fields["consumer_value_claim_ids"]
        if not consumer_ids or not any(
                _claim_matches(consumer_value, claim) for claim in allowed
                if claim.claim_id in consumer_ids):
            raise CreativeContractError(
                "CONSUMER_VALUE_CLAIM_MISMATCH",
                "consumer_value does not resolve to its declared approved Claim IDs.",
                safe_alternative="Repair the StrategyVersion; do not rewrite the value in poster code.",
                preflight_status="NEEDS_REPAIR")

        target = _plain(selected.get("target"))
        positioning = _plain(selected.get("positioning_statement"))
        differentiated = _plain(selected.get("differentiated_value"))
        reason = _plain(selected.get("reason_to_believe"))
        reason_ids = semantic_id_fields["reason_to_believe_claim_ids"]
        reason_claims = [claim for claim in allowed if claim.claim_id in reason_ids]
        # A strategic reason is not automatically an entailment proof of the value.
        # Both may instead have independent verbatim source-assertion support. Keep
        # that limited epistemic meaning explicit, without rewriting approved copy.
        consumer_claims = [c for c in allowed if c.claim_id in consumer_ids]
        source_bindings = {c.claim_id: _source_support(
            c, canonical, profile, _plain(context.get("domain_reference")))
            for c in consumer_claims + reason_claims}
        if (len(consumer_claims) != len(consumer_ids)
                or not all(source_bindings.get(cid) for cid in consumer_ids)
                or (reason and (not reason_claims
                    or not any(_claim_matches(reason, c) for c in reason_claims)
                    or not all(source_bindings.get(cid) for cid in reason_ids)))):
            raise CreativeContractError(
                "PROOF_MESSAGE_MISMATCH",
                "Value and reason require exact, independently anchored source assertions; "
                "lexical overlap and approval do not establish support.",
                safe_alternative="Repair and re-approve unsupported assertions or qualify an entailment policy.",
                preflight_status="NEEDS_REPAIR")
        if req.requested_target and _norm(req.requested_target) != _norm(target):
            raise CreativeRequestStrategyConflict(
                "The creative request attempts to replace the approved target.",
                safe_alternative=f"Keep the approved target: {target}")
        if req.requested_positioning and _norm(req.requested_positioning) != _norm(positioning):
            raise CreativeRequestStrategyConflict(
                "The creative request attempts to replace the approved positioning.",
                safe_alternative="Keep the approved positioning and vary only the execution.")
        for requested_claim in req.requested_factual_claims:
            if not any(_claim_matches(requested_claim, claim) for claim in allowed):
                raise CreativeRequestStrategyConflict(
                    f"Requested factual claim is outside the approved allowlist: {requested_claim}",
                    safe_alternative="Use one of the approved proof points.")

        offerings = _profile_offerings(profile)
        focus = _plain(req.product_focus or req.offer_focus)
        if focus and _norm(focus) not in {_norm(v) for v in offerings}:
            raise CreativeRequestStrategyConflict(
                "The requested product/offer is not present in the exact ProfileSnapshot.",
                safe_alternative=(f"Choose an evidenced offering such as {offerings[0]}."
                                  if offerings else "Generate a brand-level poster without a product claim."))

        ctas = _profile_ctas(profile)
        cta = _plain(req.cta_preference) or _default_cta(ctas, context, canonical)
        if req.cta_preference and _norm(req.cta_preference) not in {_norm(v) for v in ctas}:
            raise CreativeRequestStrategyConflict(
                "The requested CTA is not an evidenced action for this brand.",
                safe_alternative=(f"Use the evidenced CTA: {ctas[0]}" if ctas else "Use Visit website."))

        all_allowed_text = " ".join(v.statement for v in allowed)
        if req.creative_angle:
            unsupported_numbers = _numbers(req.creative_angle) - _numbers(all_allowed_text)
            if unsupported_numbers:
                raise CreativeRequestStrategyConflict(
                    "The creative angle introduces unsupported numbers: "
                    + ", ".join(sorted(unsupported_numbers)),
                    safe_alternative="Use non-factual creative language or an allowed claim.")

        # Customer value is the message. Differentiation evidence and diagnostic context remain
        # inspectable, but cannot become visible copy through a fallback.
        primary_message = consumer_value
        proof_points = [claim for claim in reason_claims
                        if _norm(claim.statement) != _norm(consumer_value)]
        differentiation_evidence = [
            claim for claim in allowed
            if claim.claim_id in semantic_id_fields["differentiation_claim_ids"]]
        internal_evidence = [
            claim for claim in allowed
            if claim.claim_id in semantic_id_fields["internal_supporting_claim_ids"]]

        unknowns = [_plain(v) for v in (decision.get("unresolved_unknowns") or []) if _plain(v)]
        internal = list(unknowns)
        prohibited = [
            "No unsupported numbers, rankings, superiority, leadership, discounts, urgency, or uplift.",
            "No factual statement outside allowed_claim_ids, except non-factual creative language.",
        ]
        for claim in allowed:
            internal.extend(f"{claim.claim_id}: {v}" for v in claim.limitations)
            if any("prevalence" in v.casefold() for v in claim.limitations):
                prohibited.append(
                    f"{claim.claim_id} is one observed signal; never generalize it into customer prevalence.")
        if unknowns:
            prohibited.append("Do not invent an objective metric, baseline, target, horizon, KPI, or uplift.")

        language = req.language.lower()[:2] if req.language.lower()[:2] in ("ar", "en") \
            else _default_language(profile)
        locale = _plain(req.locale) or _default_locale(profile)
        tone = _plain(profile.get("tone_of_voice"))
        request_payload = req.model_dump(mode="json")
        identity = {
            "strategy_version_id": run_id,
            "brand_id": brand_id,
            "profile_snapshot_id": profile_id,
            "decision_id": _plain(decision.get("decision_id")),
            "selected_option_id": selected_id,
            "request": request_payload,
            "compiler_policy": "verbatim-profile-source@1",
        }
        return CreativeContract(
            message_source_support=MessageSourceSupport(claim_evidence_ids=source_bindings),
            creative_contract_id=_stable_id(identity),
            strategy_version_id=run_id,
            brand_id=brand_id,
            domain_reference=_plain(context.get("domain_reference")),
            profile_snapshot_id=profile_id,
            objective_id=_plain(decision.get("objective_id")),
            decision_id=_plain(decision.get("decision_id")),
            decision_type=_plain(decision.get("decision_type")),
            target=target,
            frame_of_reference=_plain(selected.get("frame_of_reference")),
            positioning_statement=positioning,
            consumer_value=consumer_value,
            differentiated_value=differentiated,
            reason_to_believe=reason,
            supporting_claim_ids=supporting_ids,
            consumer_value_claim_ids=consumer_ids,
            differentiation_claim_ids=semantic_id_fields["differentiation_claim_ids"],
            reason_to_believe_claim_ids=reason_ids,
            internal_supporting_claim_ids=semantic_id_fields["internal_supporting_claim_ids"],
            approved_unknowns=unknowns,
            primary_message=primary_message,
            proof_points=proof_points,
            differentiation_evidence=differentiation_evidence,
            internal_supporting_evidence=internal_evidence,
            allowed_claim_ids=supporting_ids,
            allowed_claims=allowed,
            copy_metadata_evidence=_copy_metadata(profile),
            copy_evidence=[dict(v) for v in (canonical.get("evidence") or []) if isinstance(v, Mapping)],
            prohibited_or_unverified_claims=list(dict.fromkeys(prohibited)),
            message_boundaries=[
                "The target, positioning, differentiated value, and proof are frozen.",
                "Product, occasion, style, format, CTA, and creative angle may vary only within evidence.",
                "Internal disclosures constrain copy; they are not automatically consumer fine print.",
            ],
            channel_context=_plain(context.get("channel_context")),
            language=language,
            locale=locale,
            cta_direction=cta,
            product_offer_focus=focus,
            brand_visual_constraints=_visual_constraints(profile),
            brand_tone_constraints=([f"Use the evidenced brand tone: {tone}"] if tone else []),
            internal_disclosures=list(dict.fromkeys(internal)),
            consumer_facing_disclosures=[],
            creative_hypothesis=req.creative_hypothesis,
            expected_response=req.expected_response,
            preflight=CreativePreflight(status="READY", reasons=[]),
        )

    @staticmethod
    def poster_brief(contract: CreativeContract) -> StrategyPosterBrief:
        required = ["brand mark", "one primary message", "CTA"]
        if contract.proof_points:
            required.append("approved proof")
        if contract.product_offer_focus:
            required.append("selected product/offer")
        return StrategyPosterBrief(
            creative_contract_id=contract.creative_contract_id,
            strategy_version_id=contract.strategy_version_id,
            objective_context=contract.objective_id,
            target=contract.target,
            positioning=contract.positioning_statement,
            primary_message=contract.primary_message,
            proof_points=contract.proof_points,
            cta=contract.cta_direction,
            product_offer_focus=contract.product_offer_focus,
            visual_direction_constraints=contract.brand_visual_constraints,
            brand_tone_constraints=contract.brand_tone_constraints,
            language=contract.language,
            locale=contract.locale,
            required_elements=required,
            forbidden_claims=contract.prohibited_or_unverified_claims,
            consumer_facing_disclosures=contract.consumer_facing_disclosures,
        )


class PosterStrategyVerifier:
    """Deterministic authority for strategy drift and factual allowlist violations."""

    _FACT_MARKERS = re.compile(
        r"\d|\b(best|first|leading|largest|fastest|guaranteed|discount|%|average|reviews?|"
        r"customers?|everyone|many|most)\b|"
        r"(الأفضل|الأول|الأكبر|الأسرع|مضمون|خصم|متوسط|مراجعة|العملاء|كل الناس|أغلب)",
        re.IGNORECASE)

    def verify(self, *, contract: CreativeContract, concept: Any,
               brief: Any, product_context: str = "", language: str = "") -> PosterStrategyVerification:
        defects: list[PosterStrategyDefect] = []

        def add(code: str, field: str, detail: str) -> None:
            defects.append(PosterStrategyDefect(
                code=code, field=field, detail=detail, repair_scope=repair_scope_for(code)))

        if _norm(getattr(concept, "audience", "")) != _norm(contract.target):
            add("TARGET_DRIFT", "concept.audience", "Concept audience differs from the approved target.")
        if _norm(getattr(concept, "core_benefit", "")) != _norm(contract.consumer_value):
            add("POSITIONING_DRIFT", "concept.core_benefit",
                "Concept benefit differs from the approved consumer value.")
        if _norm(getattr(concept, "single_message", "")) != _norm(contract.primary_message):
            add("MESSAGE_DRIFT", "concept.single_message",
                "Concept message differs from the compiled primary message.")

        proof_texts = [str(v) for v in (getattr(brief, "offerings", None) or []) if v]
        used: list[str] = []
        for proof in proof_texts:
            ids = claim_ids_for_text(proof, contract)
            proof_ids = [cid for cid in ids if cid in contract.reason_to_believe_claim_ids]
            if (not proof_ids or not contract.message_source_support
                    or not all(contract.message_source_support.claim_evidence_ids.get(cid)
                               for cid in proof_ids)):
                add("PROOF_MESSAGE_MISMATCH", "brief.offerings",
                    f"Visible proof is not approved support for the consumer message: {proof}")
            used.extend(proof_ids)

        copy_fields = {
            "headline": str(getattr(brief, "headline", "") or ""),
            "subheadline": str(getattr(brief, "subheadline", "") or ""),
            "cta": str(getattr(brief, "cta_text", "") or ""),
        }
        for field, text in copy_fields.items():
            ids = claim_ids_for_text(text, contract)
            used.extend(ids)
            if ids and set(ids).issubset(set(contract.internal_supporting_claim_ids)):
                add("UNSUPPORTED_POSTER_CLAIM", field,
                    f"Internal strategy evidence cannot become consumer-facing copy: {text}")
            if self._FACT_MARKERS.search(text) and not ids:
                add("UNSUPPORTED_POSTER_CLAIM", field,
                    f"Factual poster statement is outside allowed_claim_ids: {text}")

        if _norm(copy_fields["cta"]) != _norm(contract.cta_direction):
            add("CTA_CONFLICT", "brief.cta_text", "CTA differs from the compiled direction.")
        expected_product = _norm(contract.product_offer_focus)
        actual_product = _norm(product_context)
        if expected_product != actual_product:
            add("PRODUCT_CONTEXT_CONFLICT", "product_context",
                "Poster product/offer context differs from the CreativeContract.")
        actual_language = str(language or getattr(concept, "language", "") or "").lower()[:2]
        if actual_language and actual_language != contract.language:
            add("LOCALE_CONFLICT", "language", "Poster copy language differs from the contract.")

        return PosterStrategyVerification(
            overall_pass=not defects,
            defects=defects,
            claim_ids_used=list(dict.fromkeys(contract.consumer_value_claim_ids + used)),
        )


def strategy_repair_concept(concept: Any, contract: CreativeContract) -> Any:
    """Copy-only deterministic repair; never changes contract or StrategyVersion."""
    proof = [v.statement for v in contract.proof_points]
    update = {
        "audience": contract.target,
        "single_message": contract.primary_message,
        "core_benefit": contract.consumer_value,
        "proof_points": proof,
        "headline": contract.primary_message,
        "subheadline": (proof[0] if proof else ""),
        "cta": contract.cta_direction,
        "language": contract.language,
    }
    if hasattr(concept, "model_copy"):
        return concept.model_copy(update=update)
    for key, value in update.items():
        setattr(concept, key, value)
    return concept


def constrain_concept_to_contract(concept: Any, contract: CreativeContract) -> Any:
    """Freeze strategy fields while leaving headline/tone/visual/placement as creative choices."""
    proof = [v.statement for v in contract.proof_points]
    update = {
        "audience": contract.target,
        "single_message": contract.primary_message,
        "core_benefit": contract.consumer_value,
        "proof_points": proof,
        # One exact approved proof/message line anchors any creative headline.
        "subheadline": contract.primary_message,
        "cta": contract.cta_direction,
        "language": contract.language,
    }
    if hasattr(concept, "model_copy"):
        return concept.model_copy(update=update)
    for key, value in update.items():
        setattr(concept, key, value)
    return concept


__all__ = [
    "AllowedClaim", "CreativeContract", "CreativeContractCompiler", "CreativeContractError",
    "CreativePreflight", "CreativePreflightReason", "CreativeRequest",
    "CreativeRequestStrategyConflict", "HypothesisField",
    "PosterStrategyDefect", "PosterStrategyVerification", "PosterStrategyVerifier",
    "StrategyPosterBrief", "claim_ids_for_text", "constrain_concept_to_contract",
    "repair_scope_for", "strategy_repair_concept",
]
