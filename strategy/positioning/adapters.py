"""Adapters from already-collected brand intelligence into Gate 2 evidence and claims."""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import re
from typing import Any, Iterable, Mapping

from .contracts import (
    Availability, Claim, EpistemicType, EvidenceRecord, EvidenceStrength,
    ValueStatus, VerificationStatus,
)


@dataclass(frozen=True, slots=True)
class AdaptedIntelligence:
    evidence: tuple[EvidenceRecord, ...]
    claims: tuple[Claim, ...]
    source_artifacts: tuple[str, ...]


def _id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(str(p or "") for p in parts)
    return f"{prefix}_{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _value(raw: Any) -> Any:
    return raw.get("value") if isinstance(raw, Mapping) and "value" in raw else raw


def _text(raw: Any) -> str:
    value = _value(raw)
    if isinstance(value, Mapping):
        return str(value.get("value") or value.get("name") or "").strip()
    return str(value or "").strip()


def _profile_geography(profile: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for loc in profile.get("locations") or []:
        if not isinstance(loc, Mapping):
            continue
        for key in ("country", "city", "region"):
            val = _text(loc.get(key))
            if val and val not in out:
                out.append(val)
    for area in profile.get("service_areas") or []:
        val = _text(area)
        if val and val not in out:
            out.append(val)
    return tuple(out)


def _evidence_items(raw: Any) -> list[Mapping[str, Any]]:
    if isinstance(raw, Mapping):
        return [e for e in (raw.get("evidence") or []) if isinstance(e, Mapping)]
    return []


def _normal_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _directly_supported_claim(value: str, item: Mapping[str, Any]) -> tuple[str, bool]:
    """Keep a profile interpretation only when the quote contains it verbatim.

    Otherwise the quote itself is the safe atomic claim.  This deliberately avoids a fuzzy
    similarity threshold: a broad profile sentence cannot borrow support from one matching
    fragment, while an exact/contained source phrase remains usable.
    """
    excerpt = str(item.get("quote") or item.get("excerpt") or "").strip()
    if not excerpt:
        return value, True
    normal_value = _normal_text(value)
    normal_excerpt = _normal_text(excerpt)
    if normal_value and normal_value in normal_excerpt:
        return value, True
    return excerpt, False


def _is_compound_claim(value: str) -> bool:
    """Detect multiple material clauses without semantic similarity scoring."""
    text = " ".join(str(value or "").split())
    if len([part for part in re.split(r"(?<=[.!?])\s+|;", text) if part.strip()]) > 1:
        return True
    for conjunction in (" and ", " but ", " while ", " along with ", " as well as "):
        if conjunction in text.lower():
            left, right = text.lower().split(conjunction, 1)
            left_terms = re.findall(r"\w+", left)
            right_terms = re.findall(r"\w+", right)
            if len(left_terms) >= 2 and len(right_terms) >= 2:
                return True
    comma_parts = [part for part in text.split(",") if len(re.findall(r"\w+", part)) >= 2]
    return len(comma_parts) >= 2


_CUSTOMER_SEMANTIC_MARKERS = (
    "customer voice", "customer review", "customer comment", "social comment",
    "testimonial", "complaint theme", "praise theme", "review theme",
    "saved social customer signal", "تعليق عميل", "شكوى عميل", "رأي عميل",
)
_BRAND_AUTHORED_MARKERS = (
    "brand-authored", "brand authored", "brand social post", "brand-owned social post",
    "social copy", "منشور البراند", "منشور العلامة",
)
_TREND_SEMANTIC_MARKERS = (
    "trend signal", "market trend", "industry trend", "news signal", "seasonal trend",
    "اتجاه السوق", "ترند",
)


def _semantic_blob(row: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in ("evidence", "source_theme", "source_type", "origin", "collector", "kind"):
        raw = row.get(key)
        if isinstance(raw, (list, tuple)):
            values.extend(str(v) for v in raw)
        else:
            values.append(str(raw or ""))
    return _normal_text(" ".join(values))


def _swot_semantics(row: Mapping[str, Any], *, comparative: bool) -> tuple[str, str]:
    """Classify SWOT evidence by its stated origin, never by URL transport."""
    if comparative:
        return "COMPETITOR_ANALYSIS", "COMPETITIVE_DIFFERENTIATION"
    semantics = _semantic_blob(row)
    brand_authored = any(marker in semantics for marker in _BRAND_AUTHORED_MARKERS)
    if not brand_authored and any(marker in semantics for marker in _CUSTOMER_SEMANTIC_MARKERS):
        return "CUSTOMER_VOICE", "CUSTOMER_RELEVANCE"
    if any(marker in semantics for marker in _TREND_SEMANTIC_MARKERS):
        return "TREND_SIGNAL", "STRATEGIC_CONTEXT"
    return "DERIVED_SWOT", "STRATEGIC_CONTEXT"


def _customer_voice_kind(row: Mapping[str, Any]) -> str:
    semantics = _semantic_blob(row)
    if "complaint" in semantics or "شكوى" in semantics:
        return "complaint"
    if "praise" in semantics or "مدح" in semantics:
        return "praise"
    if "testimonial" in semantics:
        return "testimonial"
    return "comment"


def adapt_existing_intelligence(
    brand: Mapping[str, Any], *, originating_swot_run_id: str | None = None,
) -> AdaptedIntelligence:
    """Translate existing artifacts without recollecting, upgrading, or fabricating scope/time."""
    profile = brand.get("_profile") if isinstance(brand.get("_profile"), Mapping) else {}
    full = brand.get("_full_result") if isinstance(brand.get("_full_result"), Mapping) else {}
    captured_at = str(full.get("generated_at") or "").strip() or None
    geography = _profile_geography(profile)
    market = _text(profile.get("market_category"))
    evidence: list[EvidenceRecord] = []
    claims: list[Claim] = []
    artifacts: set[str] = {"profile_snapshot"} if profile else set()

    def add(
        *, value: str, source_class: str, source_ref: str, role: str,
        item: Mapping[str, Any] | None = None,
        raw_value: Any | None = None,
        epistemic: EpistemicType = EpistemicType.SOURCE_ASSERTION,
        strength: EvidenceStrength = EvidenceStrength.MODERATE,
        verification: VerificationStatus = VerificationStatus.SUPPORTED,
        scope_details: Mapping[str, Any] | None = None,
        limitations: Iterable[str] = (), run_id: str | None = None,
    ) -> None:
        if not value:
            return
        item = item or {}
        url = str(item.get("page_url") or item.get("url") or "").strip()
        excerpt = str(item.get("quote") or item.get("excerpt") or value).strip()
        ev_id = _id("ev", source_class, source_ref, url, excerpt)
        claim_id = _id("cl", role, value, ev_id)
        evidence.append(EvidenceRecord(
            evidence_id=ev_id, availability=Availability.AVAILABLE,
            source_class=source_class, source_ref=source_ref, source_url=url,
            subject_ref=str(brand.get("domain_key") or profile.get("source_url") or ""),
            captured_at=captured_at, observed_at=None, observed_at_status=ValueStatus.UNKNOWN,
            market=market, geography=geography, channel=str(item.get("extractor") or ""),
            time_scope="", raw_value=value if raw_value is None else raw_value,
            excerpt=excerpt, freshness="UNKNOWN",
            relevance="RELEVANT", reliability="SOURCE_DEPENDENT",
            limitations=tuple(limitations), originating_run_id=run_id,
            collector=str(item.get("extractor") or "existing_artifact_adapter"),
        ))
        claim_scope = {"role": role, "market": market, "geography": list(geography)}
        claim_scope.update(scope_details or {})
        claims.append(Claim(
            claim_id=claim_id, statement=value, epistemic_type=epistemic,
            scope=claim_scope,
            supporting_evidence_ids=(ev_id,), verification_status=verification,
            evidence_strength=strength, limitations=tuple(limitations),
        ))

    # Brand-owned fields remain source assertions. Profile fields marked inferred remain inference.
    field_roles = {
        "tagline": "BRAND_CAPABILITY", "description": "BRAND_CAPABILITY",
        "category": "FRAME_OF_REFERENCE", "market_category": "FRAME_OF_REFERENCE",
        "value_propositions": "BRAND_CAPABILITY", "other_unique_insights": "BRAND_CAPABILITY",
        "trust_signals": "REASON_TO_BELIEVE", "audience_signals": "CUSTOMER_RELEVANCE",
    }
    for field_name, role in field_roles.items():
        raw_values = profile.get(field_name)
        rows = raw_values if isinstance(raw_values, list) else [raw_values]
        for ordinal, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            value = _text(row)
            items = _evidence_items(row) or [{}]
            epistemic = (EpistemicType.INFERENCE if str(row.get("source_type") or "").lower()
                          == "inferred" else EpistemicType.SOURCE_ASSERTION)
            for item_ordinal, item in enumerate(items):
                claim_value, complete = _directly_supported_claim(value, item)
                limitations = ["Brand-owned source; not independent validation."]
                verification = VerificationStatus.SUPPORTED
                if not complete:
                    if epistemic == EpistemicType.INFERENCE and not _is_compound_claim(value):
                        claim_value = value
                        verification = VerificationStatus.PARTIALLY_SUPPORTED
                        limitations.append(
                            "Atomic profile inference is linked to the quote but is not a verbatim "
                            "source assertion.")
                    else:
                        limitations.append(
                            "Broader profile wording was reduced to this directly quoted atomic "
                            "fragment.")
                add(value=claim_value, raw_value=value, source_class="BRAND_OWNED",
                    source_ref=f"profile.{field_name}[{ordinal}].evidence[{item_ordinal}]",
                    role=role, item=item,
                    scope_details={"profile_field": field_name},
                    epistemic=epistemic,
                    verification=verification,
                    limitations=limitations)

    # Structural counts are observations of the snapshot, not assertions that the counts are
    # strategically good. They are kept separate from brand language and are never comparative.
    for field_name in ("offerings", "social_presence", "locations"):
        rows = profile.get(field_name) or []
        if isinstance(rows, list) and rows:
            add(value=f"Snapshot contains {len(rows)} {field_name} rows.",
                source_class="STRUCTURAL_OBSERVATION", source_ref=f"profile.{field_name}",
                role="STRUCTURAL_CONTEXT", epistemic=EpistemicType.DIRECT_OBSERVATION,
                strength=EvidenceStrength.MODERATE,
                limitations=("Count describes this snapshot only; no market comparison is implied.",))

    # Owner statements retain their distinct human-assertion type.
    owner_inputs = brand.get("owner_inputs") if isinstance(brand.get("owner_inputs"), Mapping) else {}
    if owner_inputs:
        artifacts.add("owner_inputs")
    for key, raw in owner_inputs.items():
        rows = raw if isinstance(raw, list) else [raw]
        for ordinal, row in enumerate(rows):
            value = _text(row)
            add(value=value, source_class="OWNER_INPUT", source_ref=f"owner_inputs.{key}[{ordinal}]",
                role="OWNER_CONTEXT", epistemic=EpistemicType.HUMAN_ASSERTION,
                limitations=("Owner assertion; external verification not performed.",))

    # The stored pain-point rows are extracted audience comments. Other own-social rows are the
    # brand speaking and therefore do not become customer voice merely because Instagram hosted it.
    own_intel = full.get("marketing_intel") if isinstance(full.get("marketing_intel"), Mapping) else {}
    for ordinal, row in enumerate(own_intel.get("pain_points") or []):
        if not isinstance(row, Mapping):
            continue
        value = _text(row)
        for item_ordinal, item in enumerate(_evidence_items(row) or [{}]):
            excerpt = str(item.get("quote") or item.get("excerpt") or "").strip()
            exact = bool(value and _normal_text(value) in _normal_text(excerpt))
            add(value=value or excerpt, raw_value=value, source_class="CUSTOMER_VOICE",
                source_ref=(f"marketing_intel.pain_points[{ordinal}]"
                            f".evidence[{item_ordinal}]"), role="CUSTOMER_RELEVANCE",
                item=item,
                limitations=("Single observed customer signal; occurrence is established, "
                             "but prevalence is not.",
                             "Normalized/translated collector interpretation; the original "
                             "comment remains in the evidence excerpt."),
                epistemic=EpistemicType.INFERENCE,
                verification=(VerificationStatus.SUPPORTED if exact
                              else VerificationStatus.PARTIALLY_SUPPORTED),
                scope_details={"customer_voice_kind": "pain_point",
                               "customer_subject": "own_brand"},
                strength=EvidenceStrength.WEAK)
            artifacts.add("marketing_intel")

    # Competitor intel is still a source assertion about that competitor, not a verified market
    # fact. A separately validated SWOT comparison below is the stronger comparative input.
    competitor_intel = full.get("competitor_intel") if isinstance(full.get("competitor_intel"), Mapping) else {}
    for peer, block in competitor_intel.items():
        if not isinstance(block, Mapping):
            continue
        for list_name in ("offers", "usps", "pain_points", "message_angles"):
            for ordinal, row in enumerate(block.get(list_name) or []):
                if not isinstance(row, Mapping):
                    continue
                value = _text(row)
                for item_ordinal, item in enumerate(_evidence_items(row) or [{}]):
                    claim_value, _complete = _directly_supported_claim(value, item)
                    add(value=claim_value, raw_value=value,
                        source_class="COMPETITOR_ANALYSIS",
                        source_ref=(f"competitor_intel.{peer}.{list_name}[{ordinal}]"
                                    f".evidence[{item_ordinal}]"),
                        role="COMPETITIVE_DIFFERENTIATION", item=item,
                        limitations=("Competitor/source assertion; comparative conclusion not implied.",))
                    artifacts.add("competitor_intel")

    raw_swot = brand.get("_swot_raw") if isinstance(brand.get("_swot_raw"), Mapping) else {}
    if raw_swot:
        artifacts.add("approved_swot")
    for quadrant in ("strengths", "weaknesses", "opportunities", "threats"):
        for ordinal, row in enumerate(raw_swot.get(quadrant) or []):
            if not isinstance(row, Mapping):
                continue
            citations = [str(v) for v in (row.get("citation") or [])]
            strength_raw = str(row.get("claim_strength") or "")
            peer_scope = _normal_text(" ".join(citations) + " " + _semantic_blob(row))
            is_peer_scoped = any(token in peer_scope for token in (
                "peer", "competitor", "منافس"))
            is_comparative = is_peer_scoped or (
                strength_raw == "validated" and any(
                    token in " ".join(citations).lower()
                    for token in ("peer", "average", "places")))
            source_class, role = _swot_semantics(row, comparative=is_comparative)
            url = next((c for c in citations if c.startswith(("http://", "https://"))), "")
            customer_voice = source_class == "CUSTOMER_VOICE"
            swot_value = str(row.get("text") or "").strip()
            swot_excerpt = str(
                row.get("text") if customer_voice else row.get("evidence") or row.get("text")
                or "").strip()
            swot_exact = bool(swot_value and _normal_text(swot_value) in _normal_text(swot_excerpt))
            limitations = (
                ("Single observed customer signal; occurrence is established, but prevalence is not.",)
                if customer_voice else
                (("Aggregate comparison from approved SWOT; individual peer identities are not "
                  "serialized in this artifact.",) if is_comparative else
                 ("Derived SWOT interpretation.",))
            )
            add(value=swot_value, source_class=source_class,
                source_ref=f"swot.{quadrant}[{ordinal}]:{' | '.join(citations)}", role=role,
                item={"page_url": url,
                      "quote": swot_excerpt},
                epistemic=(EpistemicType.SOURCE_ASSERTION if customer_voice
                           else EpistemicType.INFERENCE),
                strength=(EvidenceStrength.STRONG
                          if is_comparative and strength_raw == "validated"
                          else EvidenceStrength.MODERATE if is_comparative
                          else EvidenceStrength.WEAK),
                verification=(VerificationStatus.SUPPORTED if swot_exact
                              else VerificationStatus.PARTIALLY_SUPPORTED),
                scope_details=({"customer_voice_kind": _customer_voice_kind(row),
                                "customer_subject": "own_brand"}
                               if customer_voice else None),
                limitations=limitations,
                run_id=originating_swot_run_id)

    # Distinct absence records make “unknown”, “not collected”, and “nothing found” impossible to
    # collapse. These records never support a claim.
    present_classes = {ev.source_class for ev in evidence}
    for source_class, availability in (
        ("CUSTOMER_VOICE", Availability.NOT_COLLECTED),
        ("COMPETITOR_ANALYSIS", Availability.NOT_COLLECTED),
    ):
        if source_class not in present_classes:
            evidence.append(EvidenceRecord(
                evidence_id=_id("ev", source_class, availability.value),
                availability=availability, source_class=source_class,
                source_ref=f"expected:{source_class.lower()}", source_url="",
                subject_ref=str(brand.get("domain_key") or ""), captured_at=None,
                observed_at=None, observed_at_status=ValueStatus.UNKNOWN, market=market,
                geography=(), channel="", time_scope="", raw_value=None, excerpt="",
                freshness="UNKNOWN", relevance="UNKNOWN", reliability="UNKNOWN",
                limitations=("This source class was not available in the supplied artifacts.",),
                collector="existing_artifact_adapter"))

    # Contradictory brand-owned numeric assertions are not averaged or silently selected. This
    # catches the concrete stored shape “44 branches” vs “45 branches” while avoiding arbitrary
    # semantic sentiment matching. Both original assertions and both evidence chains remain.
    evidence_by_id = {e.evidence_id: e for e in evidence}
    numeric_units: dict[str, tuple[str, str]] = {}
    contradictions: dict[str, set[str]] = {}
    unit_re = re.compile(r"\b([0-9][0-9,]*)\s+(branches?|locations?|years?|stores?|offices?)\b", re.I)
    for claim in claims:
        if claim.epistemic_type not in (EpistemicType.SOURCE_ASSERTION, EpistemicType.INFERENCE):
            continue
        if not any(evidence_by_id.get(eid) is not None
                   and evidence_by_id[eid].source_class == "BRAND_OWNED"
                   for eid in claim.supporting_evidence_ids):
            continue
        match = unit_re.search(claim.statement)
        if not match:
            continue
        number, unit = match.group(1).replace(",", ""), match.group(2).lower().rstrip("s")
        previous = numeric_units.get(unit)
        if previous is not None and previous[0] != number:
            previous_claim = next(c for c in claims if c.claim_id == previous[1])
            contradictions.setdefault(claim.claim_id, set()).update(previous_claim.supporting_evidence_ids)
            contradictions.setdefault(previous_claim.claim_id, set()).update(claim.supporting_evidence_ids)
        else:
            numeric_units[unit] = (number, claim.claim_id)
    if contradictions:
        claims = [replace(c, contradicting_evidence_ids=tuple(sorted(
            set(c.contradicting_evidence_ids) | contradictions.get(c.claim_id, set()))))
            for c in claims]

    return AdaptedIntelligence(tuple(evidence), tuple(claims), tuple(sorted(artifacts)))
