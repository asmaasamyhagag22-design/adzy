"""Evidence validator.

Takes raw LLMExtractionResult + the EvidencePack used to produce it,
and returns a ValidatedPayload — same data shape minus any evidence
that doesn't survive validation.

Validation rules (applied to every LLMEvidenceRef):
1. block_id must be a non-empty string.
2. block_id must exist in pack.block_ids (otherwise: hallucinated).
3. quote must be a non-empty string.
4. quote (after normalization) must appear in the cited block's text
   (after normalization). If the quote is >200 chars, we truncate and
   try a substring match — the prompt told the LLM to keep quotes short.

Field-level rules applied after item-level validation:
- Scalar fields lose their value if all their evidence is invalid.
- Enum fields' string values are matched against the target enum;
  bad values become null.
- List items (offerings, audience_signals, etc.) are dropped
  individually when they have no valid evidence left.

The validator NEVER fabricates evidence. The only thing it adds to
an EvidenceItem that wasn't in the LLMEvidenceRef is `page_url`,
which it looks up from the pack — that field is *structural*, not
content; the LLM has no business knowing or inventing it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..schemas import (
    AudienceType,
    BusinessCategory,
    BusinessModel,
    Confidence,
    EvidenceItem,
    LLMEvidenceRef,
    PricingPosture,
    ToneOfVoice,
)
from .evidence_pack import EvidencePack
from .extractor import GroupResult, LLMExtractionResult
from .prompts import UNSUBSTANTIATED_CLAIM_TOKENS
from .responses import (
    IdentityResponse,
    OfferingItem,
    OfferingsResponse,
    PositioningResponse,
    StringListItem,
    TrustResponse,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Validation primitives
# ---------------------------------------------------------------------

MAX_QUOTE_LEN = 200
_WS_RE = re.compile(r"\s+")
# Unify quote glyphs for matching. The evidence pack shows the LLM a TRANSFORMED block
# (`as_llm_text` replaces `"`->`'` and newlines->spaces so its `"..."` wrapper stays
# unambiguous), so a verbatim quote of a mid-text double-quote arrives as `'` and would
# never match the original `"`. Folding all quote glyphs to one char (here + on the block)
# closes that false-reject without loosening the substring check.
_QUOTE_RE = re.compile("[\"“”«»‘’`´]")


def _normalize(text: str) -> str:
    """Whitespace-collapse + lowercase + quote-glyph fold, for substring matching only.

    Returns "" for None or empty input.
    """
    if not text:
        return ""
    return _QUOTE_RE.sub("'", _WS_RE.sub(" ", text).strip().lower())


# A recovered quote (cited with a wrong block_id) must be at least this many normalized
# chars, so a stop-word ("the", "and") can't be laundered into a coincidental block match.
_MIN_RECOVER_QUOTE_LEN = 6


@dataclass
class _BlockLookup:
    """Cached lookups from the pack."""
    text_by_id: dict[str, str]
    page_url_by_id: dict[str, str]
    normalized_text_by_id: dict[str, str]


def _recover_by_quote(quote: str, lookup: "_BlockLookup"):
    """Find a block that VERBATIM contains `quote` — the LLM cited a wrong/absent block_id
    for a REAL quote. Returns (block_id, page_url, clean_quote) or None. Same substring
    strictness as the per-block check, searched across ALL blocks, with a length floor."""
    stripped = quote.strip(" \t\n\r\"'“”‘’«».,;:!?(){}[]<>—–-")
    for cand in (quote, stripped):
        cand = (cand or "")[:MAX_QUOTE_LEN]
        nq = _normalize(cand)
        if len(nq) < _MIN_RECOVER_QUOTE_LEN:
            continue
        for bid, nblock in lookup.normalized_text_by_id.items():
            if nq in nblock:
                return bid, lookup.page_url_by_id[bid], cand
    return None


def _build_lookup(pack: EvidencePack) -> _BlockLookup:
    text_by_id: dict[str, str] = {}
    page_url_by_id: dict[str, str] = {}
    normalized_text_by_id: dict[str, str] = {}
    for b in pack.blocks:
        text_by_id[b.block_id] = b.text
        page_url_by_id[b.block_id] = b.page_url
        normalized_text_by_id[b.block_id] = _normalize(b.text)
    return _BlockLookup(text_by_id, page_url_by_id, normalized_text_by_id)


# ---------------------------------------------------------------------
# Rejection codes (for diagnostics)
# ---------------------------------------------------------------------

class RejectionCode:
    NULL_BLOCK_ID = "null_block_id"
    EMPTY_BLOCK_ID = "empty_block_id"
    HALLUCINATED_BLOCK_ID = "hallucinated_block_id"
    NULL_QUOTE = "null_quote"
    EMPTY_QUOTE = "empty_quote"
    QUOTE_NOT_IN_BLOCK = "quote_not_in_block"
    ENUM_COERCION_MISS = "enum_coercion_miss"
    CLAIM_NOT_SUPPORTED = "claim_not_supported"  # P-70: value's hard claim not backed by the cited quote


@dataclass
class RejectionRecord:
    code: str
    block_id: Optional[str]
    quote: Optional[str]
    note: str = ""


# ---------------------------------------------------------------------
# Per-item validation
# ---------------------------------------------------------------------

def _validate_evidence_item(
    ref: LLMEvidenceRef,
    lookup: _BlockLookup,
    extractor_tag: str = "llm",
) -> tuple[Optional[EvidenceItem], Optional[RejectionRecord]]:
    """Validate one LLMEvidenceRef.

    Returns (EvidenceItem, None) on success, or (None, RejectionRecord)
    on failure. We never return both.
    """
    # block_id null/empty -> HARD reject (nothing to recover from).
    if ref.block_id is None:
        return None, RejectionRecord(
            code=RejectionCode.NULL_BLOCK_ID, block_id=None, quote=ref.quote)
    if not isinstance(ref.block_id, str) or not ref.block_id.strip():
        return None, RejectionRecord(
            code=RejectionCode.EMPTY_BLOCK_ID, block_id=ref.block_id, quote=ref.quote)

    # quote null/empty -> HARD reject (nothing to match/recover).
    if ref.quote is None:
        return None, RejectionRecord(
            code=RejectionCode.NULL_QUOTE, block_id=ref.block_id, quote=None)
    if not isinstance(ref.quote, str) or not ref.quote.strip():
        return None, RejectionRecord(
            code=RejectionCode.EMPTY_QUOTE, block_id=ref.block_id, quote=ref.quote)

    # Truncate long quotes — the prompt rule was <=200 chars.
    candidate = ref.quote[:MAX_QUOTE_LEN]
    stripped = candidate.strip(" \t\n\r\"'“”‘’«».,;:!?(){}[]<>—–-")

    # FAST PATH: the CITED block exists and verbatim-contains the quote (exact, or after a
    # punctuation strip — the LLM sometimes wraps the quote in quotes).
    if ref.block_id in lookup.normalized_text_by_id:
        norm_block = lookup.normalized_text_by_id[ref.block_id]
        if _normalize(candidate) in norm_block:
            return EvidenceItem(block_id=ref.block_id,
                                page_url=lookup.page_url_by_id[ref.block_id],
                                quote=candidate[:MAX_QUOTE_LEN], extractor=extractor_tag), None
        if stripped and stripped != candidate and _normalize(stripped) in norm_block:
            return EvidenceItem(block_id=ref.block_id,
                                page_url=lookup.page_url_by_id[ref.block_id],
                                quote=stripped[:MAX_QUOTE_LEN], extractor=extractor_tag), None

    # RECOVERY: the LLM cited a WRONG/absent block_id (or the wrong block) for a REAL quote.
    # A wrong block_id used to DISCARD the evidence outright even though the quote is verbatim
    # in another block — throwing away real, citable evidence. Search ALL blocks and accept
    # with the CORRECTED block_id/page_url. Same substring strictness as the per-block check,
    # plus a length floor so a stop-word can't be laundered into a coincidental match.
    rec = _recover_by_quote(candidate, lookup)
    if rec is not None:
        bid, page_url, clean_quote = rec
        return EvidenceItem(block_id=bid, page_url=page_url,
                            quote=clean_quote, extractor=extractor_tag), None

    # Unrecoverable -> reject with the most specific diagnostic code.
    if ref.block_id not in lookup.normalized_text_by_id:
        return None, RejectionRecord(
            code=RejectionCode.HALLUCINATED_BLOCK_ID, block_id=ref.block_id, quote=ref.quote,
            note="block_id not in evidence pack and quote not found in any block")
    return None, RejectionRecord(
        code=RejectionCode.QUOTE_NOT_IN_BLOCK, block_id=ref.block_id, quote=ref.quote[:200],
        note="quote not found in block text after normalization")


# P-70: hard, FALSIFIABLE claim kinds. A value carrying one of these must have it SUPPORTED by the
# cited evidence — not just accompanied by an authentic-but-unrelated quote. Soft marketing
# superlatives (best/leading/largest) are deliberately EXCLUDED: they are commonly synthesised into
# a description and enforcing them here would drop legitimate paraphrase. We target the fabrication
# archetype: a number/year, a #1/first/only ranking, an award/certification/guarantee, a free offer.
_HARD_CLAIM_KINDS = frozenset({"number", "first", "only", "award", "certification",
                               "guarantee", "free"})


def _price_currency_marks(text: str) -> set[str]:
    """Currency marks attached to prices, using the scraper's D-8-neutral price grammar."""
    from grounding.ledger import normalize
    from scraper.catalog import _PRICE_RE

    marks: set[str] = set()
    for match in _PRICE_RE.finditer(text or ""):
        # The shared price regex has already established that this span is currency+number.
        # Removing number punctuation leaves only the ISO code, symbol, or currency word.
        mark = re.sub(r"[\d\s,.\u066b\u066c]+", "", normalize(match.group(0)))
        if mark:
            marks.add(mark)
    return marks


def _strict_number_context_supported(text: str, entries: list) -> bool:
    """Require extracted numeric facts to match both unit class and visible subject."""
    from grounding.ledger import (
        _STRONG_CLASSES,
        _digit_classes,
        _subject_stems_after,
        extract_claims,
    )

    for claim in extract_claims(text):
        if claim.kind != "number":
            continue
        claim_classes = _digit_classes(claim.text, claim.token)
        claim_strong = claim_classes.intersection(_STRONG_CLASSES)
        claim_subjects = _subject_stems_after(claim.text, claim.token)
        supported = False
        for entry in entries:
            evidence_classes = _digit_classes(entry.norm, claim.token)
            if not evidence_classes:
                continue
            evidence_strong = evidence_classes.intersection(_STRONG_CLASSES)
            # Symmetric unit check. The general Ledger intentionally permits a bare count to
            # resolve against a priced occurrence of the same digit; extracted profile values
            # cannot, because they become evidence for later stages.
            if claim_strong or evidence_strong:
                if not claim_strong or not evidence_strong \
                        or claim_strong.isdisjoint(evidence_strong):
                    continue
            elif claim_subjects:
                evidence_subjects = _subject_stems_after(entry.norm, claim.token)
                if not evidence_subjects or claim_subjects.isdisjoint(evidence_subjects):
                    continue
            supported = True
            break
        if not supported:
            return False
    return True


def _value_hard_claims_supported(
    value: object,
    accepted: list,
    *,
    strict_extracted_value: bool = False,
) -> bool:
    """True unless the value carries an UNSOURCED hard claim (P-70). Reuses the Evidence Ledger's
    claim resolution against the accepted evidence quotes. Pure paraphrase / puffery -> True.
    Degrades to True (no block) whenever grounding is unavailable or there is no evidence to check
    against — the strict require_evidence gate already handles the no-evidence case."""
    text = value if isinstance(value, str) else str(getattr(value, "value", value) or "")
    if not text.strip():
        return True
    try:
        from grounding.ledger import EvidenceLedger, LedgerEntry
    except Exception:  # noqa: BLE001
        return True
    entries = [LedgerEntry(raw=(getattr(it, "quote", "") or ""), source_type="profile_evidence")
               for it in accepted if getattr(it, "quote", None)]
    if not entries:
        return True
    # The Ledger distinguishes price from percentage/duration, but deliberately treats all
    # currencies as one strong class. That is correct for generic copy gating and insufficient
    # for extracted profile values: EUR 500 is not supported by a real USD 500 quote. Keep the
    # scalar contract unchanged; offering/list callers opt into exact marks because those values
    # later become evidence themselves.
    if strict_extracted_value:
        claimed_marks = _price_currency_marks(text)
        if claimed_marks:
            evidence_marks = set().union(*(
                _price_currency_marks(entry.raw) for entry in entries
            ))
            if not claimed_marks.issubset(evidence_marks):
                return False
        if not _strict_number_context_supported(text, entries):
            return False
    try:
        verdicts = EvidenceLedger(entries).audit_text(text)
    except Exception:  # noqa: BLE001
        return True
    # Fail only when a HARD-kind claim is unsourced; soft superlatives never trigger a drop.
    return all(v.sourced or getattr(v.claim, "kind", "") not in _HARD_CLAIM_KINDS for v in verdicts)


def _validate_evidence_list(
    refs: list[LLMEvidenceRef],
    lookup: _BlockLookup,
    extractor_tag: str,
) -> tuple[list[EvidenceItem], list[RejectionRecord]]:
    accepted: list[EvidenceItem] = []
    rejected: list[RejectionRecord] = []
    for ref in refs:
        item, rej = _validate_evidence_item(ref, lookup, extractor_tag)
        if item is not None:
            accepted.append(item)
        elif rej is not None:
            rejected.append(rej)
    return accepted, rejected


# ---------------------------------------------------------------------
# Enum coercion
# ---------------------------------------------------------------------

def _coerce_enum(value: Optional[str], enum_cls):
    """Map a string to an enum member. Returns None on miss."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    # Try direct match first
    for member in enum_cls:
        if member.value == v:
            return member
    # Case-insensitive fallback
    v_low = v.lower()
    for member in enum_cls:
        if member.value.lower() == v_low:
            return member
    return None


# ---------------------------------------------------------------------
# Validated payload shapes
# ---------------------------------------------------------------------

@dataclass
class ValidatedScalarField:
    """A validated scalar field's pieces, ready for the merger."""
    value: object  # final value (str | enum member | None)
    evidence: list[EvidenceItem]
    confidence: Confidence
    inferred: bool  # True if the LLM's signal was inferential


@dataclass
class ValidatedListItem:
    """A validated list item with its surviving evidence."""
    value: object  # str, OfferingItem-like dict, etc.
    evidence: list[EvidenceItem]
    confidence: Confidence


@dataclass
class ValidatedIdentity:
    tagline: ValidatedScalarField
    description: ValidatedScalarField
    category: ValidatedScalarField  # value is BusinessCategory or None
    # process.md §8.11 — the two axes `category` conflated. Defaulted so older call sites and
    # tests that build a ValidatedIdentity without them still construct.
    market_category: ValidatedScalarField = field(
        default_factory=lambda: ValidatedScalarField(
            value=None, evidence=[], confidence=Confidence.NONE, inferred=True))
    business_model: ValidatedScalarField = field(
        default_factory=lambda: ValidatedScalarField(
            value=None, evidence=[], confidence=Confidence.NONE, inferred=True))


@dataclass
class ValidatedOffering:
    name: str
    short_description: Optional[str]
    price_text: Optional[str]
    evidence: list[EvidenceItem]
    confidence: Confidence


@dataclass
class ValidatedOfferings:
    offerings: list[ValidatedOffering]
    pricing_posture: ValidatedScalarField  # value is PricingPosture or None


@dataclass
class ValidatedPositioning:
    audience_type: ValidatedScalarField  # AudienceType or None
    audience_signals: list[ValidatedListItem]  # values are str
    value_propositions: list[ValidatedListItem]  # values are str
    tone_of_voice: ValidatedScalarField  # ToneOfVoice or None
    other_unique_insights: list[ValidatedListItem] = field(default_factory=list)


@dataclass
class ValidatedTrust:
    trust_signals: list[ValidatedListItem]
    service_areas: list[ValidatedListItem]


@dataclass
class ValidationDiagnostics:
    """Per-group rejection counts and records, for debugging."""
    rejections_by_group: dict[str, list[RejectionRecord]] = field(
        default_factory=lambda: {"identity": [], "offerings": [],
                                  "positioning": [], "trust": []}
    )
    fields_dropped: list[str] = field(default_factory=list)
    items_dropped: dict[str, int] = field(default_factory=dict)
    # --- v0.2-b1 ---
    # How many candidate items each group emitted (the denominator
    # for items_dropped). Useful for distinguishing "LLM returned nothing"
    # from "LLM returned 10 items, all rejected by validator."
    candidates_seen: dict[str, int] = field(default_factory=dict)
    # Items rejected specifically because they contained a blacklisted
    # claim token (halal, organic, ISO, etc.) that wasn't in the cited
    # quote. Separate counter so we can surface this distinctly in
    # diagnostics. Subset of items_dropped.
    blacklist_rejections: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total_rejections(self) -> int:
        return sum(len(v) for v in self.rejections_by_group.values())

    @property
    def total_items_dropped(self) -> int:
        return sum(self.items_dropped.values())

    @property
    def total_blacklist_rejections(self) -> int:
        return sum(len(v) for v in self.blacklist_rejections.values())


@dataclass
class ValidatedPayload:
    """Everything from the LLM, after validation. Ready for the merger."""
    identity: Optional[ValidatedIdentity]
    offerings: Optional[ValidatedOfferings]
    positioning: Optional[ValidatedPositioning]
    trust: Optional[ValidatedTrust]
    diagnostics: ValidationDiagnostics


# ---------------------------------------------------------------------
# Group validators
# ---------------------------------------------------------------------

def _validate_scalar(
    refs: list[LLMEvidenceRef],
    value: object,
    confidence: Confidence,
    lookup: _BlockLookup,
    diag_bucket: list[RejectionRecord],
    *,
    inferred: bool = False,
    require_evidence: bool = True,
) -> ValidatedScalarField:
    """Validate a scalar field's evidence and decide whether to keep value.

    If value is None or all evidence is invalid, the field is missing.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return ValidatedScalarField(value=None, evidence=[], confidence=Confidence.NONE,
                            inferred=False)

    accepted, rejected = _validate_evidence_list(refs, lookup, "llm")
    diag_bucket.extend(rejected)

    if require_evidence and not accepted:
        return ValidatedScalarField(value=None, evidence=[], confidence=Confidence.NONE,
                            inferred=False)

    # P-70: the quote is authentic, but does it SUPPORT the value? A hallucinated hard claim
    # ("Egypt's #1 pharmacy", "serving since 1965") attached to a real but unrelated quote used to
    # pass. Require the value's hard claims to resolve against the cited evidence; drop otherwise.
    if accepted and not _value_hard_claims_supported(value, accepted):
        diag_bucket.append(RejectionRecord(
            code=RejectionCode.CLAIM_NOT_SUPPORTED, block_id=None, quote=str(value)[:200],
            note="value carries a hard claim (number/ranking/credential) not supported by the cited evidence"))
        return ValidatedScalarField(value=None, evidence=[], confidence=Confidence.NONE,
                            inferred=False)

    return ValidatedScalarField(value=value, evidence=accepted, confidence=confidence,
                        inferred=inferred)


def _validate_identity(
    resp: IdentityResponse, lookup: _BlockLookup,
    diag: ValidationDiagnostics,
) -> ValidatedIdentity:
    bucket = diag.rejections_by_group["identity"]

    # tagline: direct quote from a block; inferred=False
    tagline = _validate_scalar(
        resp.tagline_evidence, resp.tagline_value,
        resp.tagline_confidence, lookup, bucket,
        inferred=False,
    )

    # description: synthesized from multiple blocks; inferred=True
    description = _validate_scalar(
        resp.description_evidence, resp.description_value,
        resp.description_confidence, lookup, bucket,
        inferred=True,
    )

    # category: enum-coerced; LLM-inferred but high-confidence on schema match.
    # FIX-2a: two silent-drop paths closed. (1) An enum-coercion miss now RECORDS the raw LLM
    # string — 'LLM said null' vs 'non-enum string' vs 'no evidence' are distinguishable in
    # diagnostics instead of an untraceable fields_dropped entry. (2) category is a whole-pack
    # CLASSIFICATION (inferred=True, enum-constrained), so a valid member must not die for
    # missing/invalid block refs -> require_evidence=False; evidence is still validated and
    # attached when present. tagline/description keep the strict gate.
    coerced_cat = _coerce_enum(resp.category_value, BusinessCategory)
    if coerced_cat is None and resp.category_value and str(resp.category_value).strip():
        bucket.append(RejectionRecord(
            code=RejectionCode.ENUM_COERCION_MISS, block_id=None,
            quote=str(resp.category_value)[:200],
            note="category_value is not a BusinessCategory member (raw LLM string preserved)"))
    category = _validate_scalar(
        resp.category_evidence, coerced_cat,
        resp.category_confidence, lookup, bucket,
        inferred=True,
        require_evidence=False,
    )

    if tagline.value is None:
        diag.fields_dropped.append("tagline")
    if description.value is None:
        diag.fields_dropped.append("description")
    if category.value is None:
        diag.fields_dropped.append("category")

    # market_category — free text (WHAT is sold). No enum coercion; evidence validated when present.
    market_category = _validate_scalar(
        resp.market_category_evidence, resp.market_category_value,
        resp.market_category_confidence, lookup, bucket,
        inferred=True, require_evidence=False)
    # business_model — closed enum (HOW it sells). A non-member string is dropped, never crashes.
    coerced_model = _coerce_enum(resp.business_model_value, BusinessModel)
    business_model = _validate_scalar(
        resp.business_model_evidence, coerced_model,
        resp.business_model_confidence, lookup, bucket,
        inferred=True, require_evidence=False)

    return ValidatedIdentity(tagline=tagline, description=description,
                              category=category, market_category=market_category,
                              business_model=business_model)


# ---------------------------------------------------------------------
# Unsubstantiated-claim blacklist (v0.2-b1)
# ---------------------------------------------------------------------

def _contains_blacklisted_claim(
    text: str, evidence_items: list[EvidenceItem],
) -> Optional[str]:
    """Check if `text` (a candidate offering name/description or list
    value) contains a blacklisted claim token NOT supported by any
    cited evidence quote. Returns the offending token, or None.

    Rule:
    - If the candidate text contains any blacklist token T, AND
    - No cited evidence quote contains T (case-insensitive),
    - THEN reject.
    """
    text_lc = (text or "").lower()
    quotes_lc = " ||| ".join(
        (e.quote or "").lower() for e in evidence_items if e.quote
    )
    for token in UNSUBSTANTIATED_CLAIM_TOKENS:
        # WORD-BOUNDARY match, not raw substring. The substring test flagged "iso" inside
        # poison / comparison / revision / decision (false-rejecting a real offering) AND let
        # an "ISO"/"organic" claim LAUNDER through a quote that merely contained the substring
        # (e.g. "comparison" grounds "iso"). \b anchors the token as a whole word/phrase; the
        # hyphens/spaces of multi-word tokens ("gluten-free", "fda approved") match literally
        # between the boundaries.
        pat = r"\b" + re.escape(token) + r"\b"
        if re.search(pat, text_lc) and not re.search(pat, quotes_lc):
            return token
    return None


def _validate_offerings(
    resp: OfferingsResponse, lookup: _BlockLookup,
    diag: ValidationDiagnostics,
) -> ValidatedOfferings:
    bucket = diag.rejections_by_group["offerings"]
    diag.candidates_seen["offerings"] = len(resp.offerings)
    blacklist_hits: list[str] = []

    # Each offering item gets validated; drop items with no valid evidence
    kept_offerings: list[ValidatedOffering] = []
    dropped = 0
    for item in resp.offerings:
        if not item.name or not item.name.strip():
            dropped += 1
            continue
        accepted, rejected = _validate_evidence_list(item.evidence, lookup, "llm")
        bucket.extend(rejected)
        if not accepted:
            dropped += 1
            continue
        # v0.2-b1: universal blacklist — reject offerings whose name or
        # short_description mentions an unsupported claim (halal, organic,
        # ISO, etc.) when the cited quote doesn't literally contain it.
        candidate_text = " ".join(filter(None, [
            item.name, item.short_description or "",
        ]))
        offending = _contains_blacklisted_claim(candidate_text, accepted)
        if offending:
            blacklist_hits.append(
                f"{item.name!r}: '{offending}' not in cited quotes"
            )
            dropped += 1
            continue
        unsupported_field = None
        for field_name, value in (
            ("name", item.name),
            ("short_description", item.short_description),
            ("price_text", item.price_text),
        ):
            if value and not _value_hard_claims_supported(
                value, accepted, strict_extracted_value=True,
            ):
                unsupported_field = field_name
                bucket.append(RejectionRecord(
                    code=RejectionCode.CLAIM_NOT_SUPPORTED,
                    block_id=None,
                    quote=str(value)[:200],
                    note=(f"offering {field_name} carries a hard claim not supported "
                          "by the cited evidence"),
                ))
                break
        if unsupported_field is not None:
            dropped += 1
            continue
        kept_offerings.append(ValidatedOffering(
            name=item.name.strip(),
            short_description=(item.short_description or None),
            price_text=(item.price_text or None),
            evidence=accepted,
            confidence=item.confidence,
        ))
    if dropped:
        diag.items_dropped["offerings"] = dropped
    if blacklist_hits:
        diag.blacklist_rejections["offerings"] = blacklist_hits

    # pricing_posture: enum-coerced scalar
    coerced_posture = _coerce_enum(resp.pricing_posture_value, PricingPosture)
    pricing_posture = _validate_scalar(
        resp.pricing_posture_evidence, coerced_posture,
        resp.pricing_posture_confidence, lookup, bucket,
        inferred=True,
    )
    if pricing_posture.value is None:
        diag.fields_dropped.append("pricing_posture")

    return ValidatedOfferings(offerings=kept_offerings,
                                pricing_posture=pricing_posture)


def _validate_string_list(
    items: list[StringListItem],
    lookup: _BlockLookup,
    diag_bucket: list[RejectionRecord],
    diag: ValidationDiagnostics,
    item_label: str,
) -> list[ValidatedListItem]:
    # Track candidates_seen by item type (trust_signals, value_propositions,
    # audience_signals, service_areas) so diagnostics show the denominator.
    diag.candidates_seen[item_label] = (
        diag.candidates_seen.get(item_label, 0) + len(items)
    )
    blacklist_hits: list[str] = []
    kept: list[ValidatedListItem] = []
    dropped = 0
    for item in items:
        if not item.value or not item.value.strip():
            dropped += 1
            continue
        accepted, rejected = _validate_evidence_list(item.evidence, lookup, "llm")
        diag_bucket.extend(rejected)
        if not accepted:
            dropped += 1
            continue
        # v0.2-b1: universal blacklist — reject list items mentioning
        # unsupported claims (e.g., a "value proposition" claiming "halal"
        # when no quote contains the word).
        offending = _contains_blacklisted_claim(item.value, accepted)
        if offending:
            blacklist_hits.append(
                f"{item.value!r}: '{offending}' not in cited quotes"
            )
            dropped += 1
            continue
        if not _value_hard_claims_supported(
            item.value, accepted, strict_extracted_value=True,
        ):
            diag_bucket.append(RejectionRecord(
                code=RejectionCode.CLAIM_NOT_SUPPORTED,
                block_id=None,
                quote=item.value[:200],
                note=f"{item_label} value carries a hard claim not supported by the cited evidence",
            ))
            dropped += 1
            continue
        kept.append(ValidatedListItem(
            value=item.value.strip(),
            evidence=accepted,
            confidence=item.confidence,
        ))
    if dropped:
        diag.items_dropped[item_label] = (
            diag.items_dropped.get(item_label, 0) + dropped
        )
    if blacklist_hits:
        diag.blacklist_rejections[item_label] = (
            diag.blacklist_rejections.get(item_label, []) + blacklist_hits
        )
    return kept


def _validate_positioning(
    resp: PositioningResponse, lookup: _BlockLookup,
    diag: ValidationDiagnostics,
) -> ValidatedPositioning:
    bucket = diag.rejections_by_group["positioning"]

    coerced_audience = _coerce_enum(resp.audience_type_value, AudienceType)
    audience_type = _validate_scalar(
        resp.audience_type_evidence, coerced_audience,
        resp.audience_type_confidence, lookup, bucket,
        inferred=True,
    )

    coerced_tone = _coerce_enum(resp.tone_of_voice_value, ToneOfVoice)
    tone_of_voice = _validate_scalar(
        resp.tone_of_voice_evidence, coerced_tone,
        resp.tone_of_voice_confidence, lookup, bucket,
        inferred=True,
    )

    audience_signals = _validate_string_list(
        resp.audience_signals, lookup, bucket, diag, "audience_signals",
    )
    value_propositions = _validate_string_list(
        resp.value_propositions, lookup, bucket, diag, "value_propositions",
    )
    other_unique_insights = _validate_string_list(
        resp.other_unique_insights, lookup, bucket, diag, "other_unique_insights",
    )

    if audience_type.value is None:
        diag.fields_dropped.append("audience_type")
    if tone_of_voice.value is None:
        diag.fields_dropped.append("tone_of_voice")

    return ValidatedPositioning(
        audience_type=audience_type,
        audience_signals=audience_signals,
        value_propositions=value_propositions,
        tone_of_voice=tone_of_voice,
        other_unique_insights=other_unique_insights,
    )


def _validate_trust(
    resp: TrustResponse, lookup: _BlockLookup,
    diag: ValidationDiagnostics,
) -> ValidatedTrust:
    bucket = diag.rejections_by_group["trust"]
    trust_signals = _validate_string_list(
        resp.trust_signals, lookup, bucket, diag, "trust_signals",
    )
    service_areas = _validate_string_list(
        resp.service_areas, lookup, bucket, diag, "service_areas",
    )
    return ValidatedTrust(trust_signals=trust_signals,
                           service_areas=service_areas)


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------

def validate_llm_extraction(
    result: LLMExtractionResult,
    pack: EvidencePack,
) -> ValidatedPayload:
    """Validate every piece of LLM output against the evidence pack.

    Returns a ValidatedPayload with:
    - field values dropped to None when evidence is invalid
    - list items dropped when evidence is invalid
    - every surviving EvidenceItem fully populated (block_id, page_url,
      quote, extractor='llm')
    - diagnostics listing every rejection for auditing
    """
    lookup = _build_lookup(pack)
    diag = ValidationDiagnostics()

    identity: Optional[ValidatedIdentity] = None
    offerings: Optional[ValidatedOfferings] = None
    positioning: Optional[ValidatedPositioning] = None
    trust: Optional[ValidatedTrust] = None

    if _is_group_ok(result.identity, IdentityResponse):
        identity = _validate_identity(result.identity.response, lookup, diag)  # type: ignore[arg-type]

    if _is_group_ok(result.offerings, OfferingsResponse):
        offerings = _validate_offerings(result.offerings.response, lookup, diag)  # type: ignore[arg-type]

    if _is_group_ok(result.positioning, PositioningResponse):
        positioning = _validate_positioning(result.positioning.response, lookup, diag)  # type: ignore[arg-type]

    if _is_group_ok(result.trust, TrustResponse):
        trust = _validate_trust(result.trust.response, lookup, diag)  # type: ignore[arg-type]

    logger.info(
        "Validator finished: rejections=%d, fields_dropped=%s, items_dropped=%s",
        diag.total_rejections, diag.fields_dropped, dict(diag.items_dropped),
    )
    return ValidatedPayload(
        identity=identity,
        offerings=offerings,
        positioning=positioning,
        trust=trust,
        diagnostics=diag,
    )


def _is_group_ok(gr: GroupResult, expected_type: type) -> bool:
    return gr.ok and isinstance(gr.response, expected_type)
