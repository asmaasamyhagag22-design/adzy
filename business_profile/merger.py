"""Profile merger.

Combines a rules-only BusinessProfile with a ValidatedPayload from the
LLM validator. The output is the final, full BusinessProfile with
recomputed quality and updated extraction_meta.

Per-field merge policy (explicit, deterministic):

| Field | Source winner when both present |
|---|---|
| name              | rules (schema.org > og:site_name > title > h1). LLM never overrides. |
| tagline           | LLM only (rules can't synthesize one) |
| description       | LLM only (rules can't synthesize one) |
| category          | rules if schema.org-sourced; otherwise LLM |
| offerings         | LLM only |
| pricing_visible   | rules only (boolean from currency regex) |
| pricing_posture   | LLM only |
| audience_type     | LLM only |
| audience_signals  | LLM only |
| value_propositions| LLM only |
| tone_of_voice     | LLM only |
| trust_signals     | LLM only |
| service_areas     | LLM only |
| locations         | rules (schema.org). LLM doesn't extract these. |
| hours             | rules (schema.org). LLM doesn't extract these. |
| contact_channels  | rules (pure manifest passthrough). LLM doesn't touch. |
| existing_ctas     | rules (from manifest.links.cta_candidates) |
| social_presence   | rules (from manifest.links.social) |
| visual            | rules (pure manifest passthrough) |
| languages         | rules (from manifest.languages) |

The merger is the last gate. If you want to change priority, change
the policy here — not anywhere upstream.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from .llm.caller import Usage
from .llm.extractor import LLMExtractionResult
from .llm.validator import (
    ValidatedIdentity,
    ValidatedListItem,
    ValidatedOfferings,
    ValidatedPayload,
    ValidatedPositioning,
    ValidatedScalarField,
    ValidatedTrust,
    ValidationDiagnostics,
)
from .quality import compute_quality
from .schemas import (
    BusinessCategory,
    BusinessProfile,
    Confidence,
    EvidenceItem,
    EvidencedField,
    Offering,
    SourceType,
)


# ---------------------------------------------------------------------
# Helpers for promoting validated payloads into EvidencedField shapes
# ---------------------------------------------------------------------

def _scalar_to_evidenced_field(scalar: ValidatedScalarField) -> EvidencedField:
    """Convert a validator ValidatedScalarField into the canonical EvidencedField."""
    if scalar.value is None or not scalar.evidence:
        return EvidencedField.missing()
    source_type = SourceType.INFERRED if scalar.inferred else SourceType.EXTRACTED
    return EvidencedField(
        value=scalar.value,
        evidence=list(scalar.evidence),
        confidence=scalar.confidence,
        source_type=source_type,
    )


def _list_item_to_evidenced_field(item: ValidatedListItem,
                                    inferred: bool = True) -> EvidencedField:
    if not item.evidence:
        return EvidencedField.missing()
    return EvidencedField(
        value=item.value,
        evidence=list(item.evidence),
        confidence=item.confidence,
        source_type=SourceType.INFERRED if inferred else SourceType.EXTRACTED,
    )


# ---------------------------------------------------------------------
# Per-field merge decisions
# ---------------------------------------------------------------------

def _category_was_schema_org(field: EvidencedField) -> bool:
    """True if the rules-only category came from schema.org (high trust)."""
    if field.value is None or not field.evidence:
        return False
    return any(ev.extractor.startswith("schema_org") for ev in field.evidence)


def _merge_scalar(
    rules_field: EvidencedField,
    llm_scalar: Optional[ValidatedScalarField],
    rules_wins_if_present: bool = True,
    rules_wins_only_if_extracted: bool = False,
) -> EvidencedField:
    """Generic scalar merge.

    rules_wins_if_present: when True, any rules value beats LLM.
    rules_wins_only_if_extracted: when True, rules value only beats LLM
        when source_type==EXTRACTED. Used for `category` so LLM can fill
        in when rules-only got an h1-derived weak signal (which we don't
        currently produce for category, but the option is here).
    """
    rules_present = rules_field.value is not None and rules_field.evidence
    if rules_present:
        if rules_wins_if_present:
            return rules_field
        if rules_wins_only_if_extracted and rules_field.source_type == SourceType.EXTRACTED:
            return rules_field

    if llm_scalar is None:
        return rules_field  # LLM had nothing to offer

    return _scalar_to_evidenced_field(llm_scalar)


def _merge_identity_axes(p, *, market, model) -> None:
    """Write the two split axes (process.md §8.11) onto the profile, in place.

    `market_category` (WHAT is sold) is free text → an EvidencedField. `business_model` (HOW it
    sells) is the BusinessModel enum. Each is written only when the LLM actually supplied a value,
    so a missing axis never overwrites what is already there (rules or a prior run). Accepts the
    validator's ValidatedScalarField OR any object with `.value`, so both production and tests fit.
    """
    if market is not None and getattr(market, "value", None):
        try:
            p.market_category = _scalar_to_evidenced_field(market)
        except Exception:  # noqa: BLE001 — a duck-typed test field: keep the raw value
            p.market_category = EvidencedField(value=str(getattr(market, "value")))
    if model is not None and getattr(model, "value", None):
        p.business_model = getattr(model, "value")


def _merge_category(
    rules_field: EvidencedField,
    llm_scalar: Optional[ValidatedScalarField],
) -> EvidencedField:
    """Category merge policy.

    v0.2-b1 fix: schema.org wins ONLY when its category is more specific
    than OTHER. A schema.org Organization that maps to OTHER should NOT
    block a better LLM-inferred category like 'restaurant' or 'education'.
    """
    rules_is_schema_org = _category_was_schema_org(rules_field)
    rules_is_other = (
        rules_field.value is not None
        and getattr(rules_field.value, "value", None) == "other"
    )

    # Strong rules signal: schema.org AND not OTHER → rules win.
    if rules_is_schema_org and not rules_is_other:
        return rules_field

    # Otherwise, prefer LLM if it has a value.
    if llm_scalar is None or llm_scalar.value is None:
        # No LLM input — keep what rules had (might be None or weak OTHER).
        return rules_field
    return _scalar_to_evidenced_field(llm_scalar)


# ---------------------------------------------------------------------
# Public merge entry point
# ---------------------------------------------------------------------

def merge_profile(
    rules_profile: BusinessProfile,
    payload: ValidatedPayload,
    llm_result: Optional[LLMExtractionResult] = None,
    *,
    expected_llm: bool = True,
    scrape_diagnostics: Optional[Any] = None,
) -> BusinessProfile:
    """Merge a rules-only profile with the validated LLM payload.

    The returned profile:
    - Has every field policy applied (rules vs LLM as documented above)
    - Has ExtractionMeta updated with LLM usage and diagnostics summary
    - Has ProfileQuality recomputed from final field state
    - Has offerings_extraction_note set when offerings is empty,
      explaining why (v0.2-b1)
    - Has llm_silent_failure set when expected_llm=True but no
      LLM calls actually succeeded (v0.2-b1)
    - Is deep-copied from the input (does not mutate rules_profile)

    expected_llm: True when the pipeline was supposed to run the LLM
    (i.e., not in skip_llm mode). Used to distinguish "LLM was skipped
    on purpose" from "LLM was requested but produced 0 successful calls".
    """
    p = deepcopy(rules_profile)

    # ---- Identity ----
    if payload.identity is not None:
        p.tagline = _merge_scalar(p.tagline, payload.identity.tagline,
                                   rules_wins_if_present=False)
        p.description = _merge_scalar(p.description, payload.identity.description,
                                       rules_wins_if_present=False)
        p.category = _merge_category(p.category, payload.identity.category)
        # The two split axes (process.md §8.11). Both are LLM-only for now (rules don't yet derive
        # them), so a present value is written and an absent one leaves the field as it was.
        _merge_identity_axes(p,
                             market=getattr(payload.identity, "market_category", None),
                             model=getattr(payload.identity, "business_model", None))
        # name: rules ALWAYS wins. We don't even consult the LLM.

    # ---- Offerings ----
    if payload.offerings is not None:
        llm_offerings = _convert_validated_offerings(payload.offerings)
        # Preserve rule-derived offerings when the LLM/validator returns zero
        # items. This matters for restaurants where menu/about text contains
        # broad cuisine signals but the LLM fails to produce offerings.
        if llm_offerings:
            p.offerings = llm_offerings
        p.pricing_posture = _merge_scalar(
            p.pricing_posture, payload.offerings.pricing_posture,
            rules_wins_if_present=False,
        )
        # pricing_visible: rules-only field, unchanged.

    # ---- Positioning ----
    if payload.positioning is not None:
        p.audience_type = _merge_scalar(
            p.audience_type, payload.positioning.audience_type,
            rules_wins_if_present=False,
        )
        p.tone_of_voice = _merge_scalar(
            p.tone_of_voice, payload.positioning.tone_of_voice,
            rules_wins_if_present=False,
        )
        p.audience_signals = [
            _list_item_to_evidenced_field(it, inferred=True)
            for it in payload.positioning.audience_signals
        ]
        p.value_propositions = [
            _list_item_to_evidenced_field(it, inferred=True)
            for it in payload.positioning.value_propositions
        ]
        p.other_unique_insights = [
            _list_item_to_evidenced_field(it, inferred=True)
            for it in payload.positioning.other_unique_insights
        ]

    # ---- Trust ----
    if payload.trust is not None:
        p.trust_signals = [
            _list_item_to_evidenced_field(it, inferred=True)
            for it in payload.trust.trust_signals
        ]
        p.service_areas = [
            _list_item_to_evidenced_field(it, inferred=True)
            for it in payload.trust.service_areas
        ]

    # ---- ExtractionMeta ----
    p.extraction_meta = _build_meta(
        rules_profile.extraction_meta, llm_result, payload.diagnostics,
    )

    # ---- v0.2-b1: silent LLM failure ----
    # If the pipeline expected LLM output but no group call succeeded,
    # flag it. Distinguishes "skip_llm=true" (intentional) from
    # "skip_llm=false but everything failed silently".
    if expected_llm and llm_result is not None:
        p.llm_silent_failure = (
            llm_result.total_calls == 0 and llm_result.total_failed > 0
        ) or (
            # All 4 calls "succeeded" but the validator dropped EVERYTHING
            # AND offerings stayed empty — likely a bad prompt rendering
            # or an LLM that returned empty structured outputs.
            llm_result.total_calls > 0
            and payload.identity is None
            and payload.offerings is None
            and payload.positioning is None
            and payload.trust is None
        )

    # ---- v0.2-b1: offerings_extraction_note ----
    # When offerings list is empty, set a short snake_case code explaining why.
    # Frontend can render a human-readable message from this code.
    if len(p.offerings) == 0:
        p.offerings_extraction_note = _compute_offerings_note(
            payload=payload,
            llm_result=llm_result,
            expected_llm=expected_llm,
        )
    else:
        p.offerings_extraction_note = None

    # ---- Quality (final pass) ----
    # Quality reads p.llm_silent_failure when computing poster_blockers,
    # so it must run AFTER the flag is set.
    p.quality = compute_quality(p)

    # ---- Stage F-readiness: SWOT / Strategy / Campaign gates ----
    # Pure function over the final profile. Scrape diagnostics are
    # optional; when the scraper provides them, gates tighten.
    from .readiness import compute_readiness
    p.readiness = compute_readiness(p, scrape_diagnostics=scrape_diagnostics)

    return p


def _compute_offerings_note(
    *,
    payload: ValidatedPayload,
    llm_result: Optional[LLMExtractionResult],
    expected_llm: bool,
) -> str:
    """Pick the short reason code explaining empty offerings.

    Returns one of:
      "llm_skipped"                       — pipeline was rules-only
      "llm_group_failed"                  — offerings call errored
      "llm_returned_zero_offerings"       — LLM returned empty list
      "all_candidates_rejected_by_validator" — LLM returned items, validator dropped all
      "llm_silent_failure"                — LLM produced nothing useful
      "unknown"                           — fallthrough; shouldn't happen
    """
    # Case: LLM was intentionally skipped.
    if not expected_llm:
        return "llm_skipped"

    # Case: LLM result is missing entirely.
    if llm_result is None:
        return "llm_skipped"

    # Case: the offerings group call itself failed (network, parse, etc.)
    if payload.offerings is None:
        return "llm_group_failed"

    diag = payload.diagnostics
    seen = diag.candidates_seen.get("offerings", 0)
    dropped = diag.items_dropped.get("offerings", 0)

    if seen == 0:
        return "llm_returned_zero_offerings"

    # Seen > 0 but list is empty in the validated payload → all rejected.
    if dropped >= seen:
        return "all_candidates_rejected_by_validator"

    # Shouldn't reach here: seen > dropped but offerings is empty.
    return "unknown"


def _convert_validated_offerings(vo: ValidatedOfferings) -> list[Offering]:
    """Promote each ValidatedOffering into a schema Offering."""
    out: list[Offering] = []
    for o in vo.offerings:
        out.append(Offering(
            name=o.name,
            short_description=o.short_description,
            price_text=o.price_text,
            page_url=(o.evidence[0].page_url if o.evidence else None),
            evidence=list(o.evidence),
            confidence=o.confidence,
        ))
    return out


def _build_meta(
    base_meta,
    llm_result: Optional[LLMExtractionResult],
    diagnostics: ValidationDiagnostics,
):
    """Update ExtractionMeta with LLM usage + diagnostics summary."""
    meta = deepcopy(base_meta)
    meta.extracted_at = datetime.now(timezone.utc)
    if llm_result is not None:
        meta.llm_calls = llm_result.total_calls
        meta.llm_tokens_input = llm_result.total_input_tokens
        meta.llm_tokens_output = llm_result.total_output_tokens
        meta.llm_cost_usd = llm_result.total_cost_usd
        meta.llm_model = llm_result.model
    # Diagnostics: write a one-line summary into rule_extractors_used (legacy),
    # AND a structured summary into validation_diagnostics (PR-validator-diag-persist).
    # The structured form lets `python -c "...meta['validation_diagnostics']"`
    # answer post-hoc questions about what the validator caught.
    summary = (
        f"validator: rejections={diagnostics.total_rejections}, "
        f"fields_dropped={diagnostics.fields_dropped}, "
        f"items_dropped={dict(diagnostics.items_dropped)}"
    )
    meta.rule_extractors_used = list(meta.rule_extractors_used) + ["merger:" + summary]

    # Structured diagnostics. Per-group rejection counts (NOT the full
    # records — those stay in the validator's in-memory log), plus
    # short summaries of rejected quotes truncated to 80 chars.
    rejections_by_group = {
        group: len(records)
        for group, records in diagnostics.rejections_by_group.items()
    }
    rejected_quotes = {
        group: [
            (rec.quote[:80] if rec.quote else f"<no quote: {rec.code}>")
            for rec in records
        ]
        for group, records in diagnostics.rejections_by_group.items()
        if records
    }
    meta.validation_diagnostics = {
        "total_rejections": diagnostics.total_rejections,
        "rejections_by_group": rejections_by_group,
        "fields_dropped": list(diagnostics.fields_dropped),
        "items_dropped": dict(diagnostics.items_dropped),
        "candidates_seen": dict(diagnostics.candidates_seen),
        "rejected_quotes": rejected_quotes,
        "blacklist_rejections": {
            group: list(claims)
            for group, claims in diagnostics.blacklist_rejections.items()
            if claims
        },
    }
    return meta