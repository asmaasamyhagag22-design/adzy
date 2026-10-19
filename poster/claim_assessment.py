"""Provider-free final-copy assessment. Exact evidence reuse only; semantics unqualified.

Whole fields are conservative proposition candidates, NOT a qualified semantic extractor.
No scraped content is executed and no model output can create evidence.
"""
from __future__ import annotations
import hashlib
import json
from typing import Literal
from grounding.ledger import EvidenceLedger, LedgerEntry
from business_profile.llm.validator import _strict_number_context_supported, _price_currency_marks
from pydantic import BaseModel, ConfigDict, Field

POLICY = 'adzy.poster.copy-evidence@1.1'
METHOD = 'exact-approved-quotation@1'
TEXT_FIELDS = ('business_name', 'headline', 'subheadline', 'cta_text', 'cta_url',
               'contact_line', 'logo_text', 'pricing_note')
LIST_FIELDS = ('offerings', 'warnings', 'social')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def inventory(brief):
    """All authored text in PosterBrief, including ancillary text and link labels."""
    source = brief if isinstance(brief, dict) else (brief.model_dump() if hasattr(brief, 'model_dump') else vars(brief))
    result = {key: str(source.get(key) or '') for key in TEXT_FIELDS}
    for key in LIST_FIELDS:
        for i, value in enumerate(source.get(key) or []):
            if hasattr(value, 'model_dump'): value = value.model_dump()
            if isinstance(value, dict):
                for sub, text in value.items(): result[f'{key}[{i}].{sub}'] = str(text or '')
            else: result[f'{key}[{i}]'] = str(value or '')
    for key, value in source.items():
        if any(key.startswith(name + "[") for name in LIST_FIELDS): result[key] = str(value or "")
    return result


class Proposition(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    proposition_id: str
    location: str
    text: str
    start: int = 0
    end: int
    kind: Literal['FACTUAL_CANDIDATE', 'NONFACTUAL_INVITATION', 'IDENTITY_CONTACT_METADATA'] = 'FACTUAL_CANDIDATE'
    extraction_method: str = 'whole-field-candidate@1'


class SupportJudgment(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    proposition_id: str
    verdict: Literal['SUPPORTED', 'CONTRADICTED', 'INSUFFICIENT_EVIDENCE', 'UNAVAILABLE']
    reason: str
    evidence: list[dict] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    considered_evidence: list[dict] = Field(default_factory=list)


class CopyAssessment(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    policy_version: str = POLICY
    method: str = METHOD
    semantic_qualification: str = 'UNQUALIFIED'
    model_version: str | None = None
    prompt_version: str | None = None
    copy_hash: str
    evidence_hash: str
    evidence_snapshot_id: str
    contract_id: str
    strategy_version_id: str
    attempt_id: str
    artifact_sha256: str
    assessed_fields: dict[str, str]
    extraction_status: str = 'WHOLE_FIELD_CANDIDATES'
    coverage_status: str = 'PARTIAL'
    execution_status: str = 'COMPLETED'
    propositions: list[Proposition]
    judgments: list[SupportJudgment]
    status: Literal['SUPPORTED', 'UNRESOLVED']
    authored_copy_eligible: bool
    pixel_text_coverage: str = 'NOT_ASSESSED'
    human_acceptance: str = 'NOT_RECORDED'


def evidence_snapshot(contract):
    return {'profile_snapshot_id': contract.profile_snapshot_id,
            'strategy_version_id': contract.strategy_version_id,
            'claims': [v.model_dump(mode='json') for v in contract.allowed_claims],
            'evidence': contract.copy_evidence,
            'metadata_evidence': contract.copy_metadata_evidence}


def assess_copy(*, brief, contract, attempt_id='', payload=b''):
    fields = inventory(brief)
    evidence = {v.get('evidence_id'): v for v in contract.copy_evidence}
    propositions, judgments = [], []
    for location, text in fields.items():
        if not text.strip(): continue
        ident = 'p_' + digest([location, text])[:20]
        invitation = location == 'cta_text' and text == contract.cta_direction and text.casefold().strip() in {'visit website', 'learn more', 'contact us', 'view menu', 'order now', 'اعرف المزيد', 'تواصل معنا', 'اطلب الآن'}
        metadata_refs = [ref for ref in contract.copy_metadata_evidence.get(location, [])
                         if ref.get('value') == text == ref.get('quote') and ref.get('page_url')]
        propositions.append(Proposition(proposition_id=ident, location=location, text=text, end=len(text), kind='IDENTITY_CONTACT_METADATA' if metadata_refs else 'NONFACTUAL_INVITATION' if invitation else 'FACTUAL_CANDIDATE'))
        refs = []
        # Exact FULL statement and FULL quotation only. No substring/token/number laundering.
        for claim in contract.allowed_claims:
            if (claim.claim_id in contract.internal_supporting_claim_ids or claim.statement != text
                    or claim.verification_status not in ('SUPPORTED', 'CORROBORATED')): continue
            for eid in claim.supporting_evidence_ids:
                row = evidence.get(eid, {})
                if (row.get('excerpt') == text and row.get('source_url')
                        and row.get('availability') == 'AVAILABLE'
                        and row.get('source_class') == 'BRAND_OWNED'
                        and row.get('subject_ref') == contract.domain_reference
                        and all(v == 'Brand-owned source; not independent validation.' for v in row.get('limitations', []))
                        and all(v == 'Brand-owned source; not independent validation.' for v in claim.limitations)):
                    refs.append({'evidence_id': eid, 'claim_id': claim.claim_id,
                                 'quote': row['excerpt'], 'page_url': row['source_url'],
                                 'scope': 'Brand-owned source assertion; independent truth not established.'})
        if metadata_refs:
            refs = [{'quote': ref['quote'], 'page_url': ref['page_url'],
                     'profile_field': ref['profile_field'], 'scope': 'Exact sourced identity/contact metadata; not a marketing-promise verification.'}
                    for ref in metadata_refs]
        # Numeric/literal cross-check reuses the existing ledger and strict currency validator.
        if refs:
            entries = [LedgerEntry(raw=r['quote'], source_url=r['page_url'], source_type='profile_evidence') for r in refs]
            ledger = EvidenceLedger(entries)
            if (not _strict_number_context_supported(text, entries) or
                    not _price_currency_marks(text).issubset(set().union(*[_price_currency_marks(e.raw) for e in entries])) or
                    any(not v.sourced for v in ledger.audit_text(text) if v.claim.kind == 'number')):
                refs = []
        if metadata_refs and refs:
            verdict, reason = 'SUPPORTED', 'Exact sourced identity/contact metadata match; no marketing-promise judgment.'
        elif refs:
            verdict, reason = 'SUPPORTED', 'Exact approved statement equals a complete brand-owned quotation.'
        elif invitation:
            verdict, reason = 'UNAVAILABLE', 'Approved CTA retained as an instruction; semantic factual content not assessed.'
        else:
            verdict, reason = ('INSUFFICIENT_EVIDENCE' if evidence else 'UNAVAILABLE'), 'No exact scoped quotation. Semantic support requires qualification/review.'
        judgments.append(SupportJudgment(proposition_id=ident, verdict=verdict, reason=reason,
            evidence=refs, considered_evidence=[] if refs or invitation else [
                {'evidence_id': r.get('evidence_id'), 'quote': r.get('excerpt'), 'page_url': r.get('source_url'),
                 'scope': 'Considered, not established support.'}
                for r in evidence.values() if r.get('source_class') == 'BRAND_OWNED'
                and r.get('subject_ref') == contract.domain_reference and r.get('source_url')][:8],
            actions=[] if refs or invitation else ['REQUEST_EVIDENCE_OR_REVIEW', 'REMOVE', 'ABSTAIN']))
    # No empty-output PASS. Pure invitations are explicitly outside the factual denominator.
    factual_ids = {p.proposition_id for p in propositions if p.kind == 'FACTUAL_CANDIDATE'}
    required_ids = {p.proposition_id for p in propositions if p.kind != 'NONFACTUAL_INVITATION'}
    eligible = bool(factual_ids) and all(v.verdict == 'SUPPORTED' for v in judgments if v.proposition_id in required_ids)
    return CopyAssessment(copy_hash=digest(fields), evidence_hash=digest(evidence_snapshot(contract)),
        evidence_snapshot_id=contract.profile_snapshot_id, contract_id=contract.creative_contract_id,
        strategy_version_id=contract.strategy_version_id, attempt_id=attempt_id,
        artifact_sha256=hashlib.sha256(payload).hexdigest(), assessed_fields=fields,
        propositions=propositions, judgments=judgments,
        coverage_status='COMPLETE_FACTUAL_LITERAL_REUSE_WITH_INVITATION_EXCLUSION' if eligible else 'PARTIAL',
        status='SUPPORTED' if eligible else 'UNRESOLVED', authored_copy_eligible=eligible)


def validate_assessment(value, *, brief, contract, attempt_id, payload):
    """Recompute authority rather than trusting caller flags, parsed models, or stale hashes."""
    current = assess_copy(brief=brief, contract=contract, attempt_id=attempt_id, payload=payload)
    try: stored = CopyAssessment.model_validate(value)
    except Exception: return False
    return stored == current and current.authored_copy_eligible


def project_factual_acceptance(entry, *, payload):
    """New canonical writes only. Historical reads are never rewritten."""
    from poster.strategy_contract import CreativeContract
    result = dict(entry)
    try:
        contract = CreativeContract.model_validate(result.get('creative_contract'))
        eligible = all(result.get(k, '') == (result.get('authored_copy') or {}).get(v, '')
                       for k, v in [('headline', 'headline'), ('subtext', 'subheadline'), ('cta', 'cta_text')])
        eligible = eligible and validate_assessment(result.get('factual_assessment'),
            brief=result.get('authored_copy') or {}, contract=contract,
            attempt_id=str(result.get('generation_attempt_id') or ''), payload=payload)
    except Exception:
        eligible = False
    result['factual_eligible'] = eligible
    for flag in ('accepted', 'publishable', 'deliverable'):
        result[flag] = result.get(flag) is True and eligible
    result['draft'] = not result['accepted']
    return result


def repair_copy_once(*, concept, brief, contract):
    """One existing deterministic repair; unsupported ancillary fields remain unresolved."""
    from poster.strategy_contract import strategy_repair_concept
    before = assess_copy(brief=brief, contract=contract)
    failed = {j.proposition_id for j in before.judgments if j.verdict != 'SUPPORTED'}
    locations = {p.location for p in before.propositions
                 if p.proposition_id in failed and p.kind == 'FACTUAL_CANDIDATE'}
    if not locations & {'headline', 'subheadline'}:
        return concept, brief, None
    repaired = strategy_repair_concept(concept, contract)
    updates = {}
    if 'headline' in locations: updates['headline'] = repaired.headline
    if 'subheadline' in locations: updates['subheadline'] = repaired.subheadline
    changed = brief.model_copy(update=updates)
    after = assess_copy(brief=changed, contract=contract)
    return repaired, changed, {'repair': 'COPY_ONLY_EVIDENCE_REUSE',
        'before_copy_hash': before.copy_hash, 'after_copy_hash': after.copy_hash,
        'status': after.status, 'changed_fields': sorted(updates),
        'strategy_version_id': contract.strategy_version_id,
        'creative_contract_id': contract.creative_contract_id}

# Shadow semantic candidate: shares the literal assessment, evidence snapshot, and field inventory.
# No caller of project_factual_acceptance consults this candidate.
from typing import Annotated
from pydantic import StrictInt
from poster.copy_semantic_prompts import (
    EXTRACTION_PROMPT, SUPPORT_PROMPT, EXTRACTION_VERSION, SUPPORT_VERSION,
)
SHADOW_VERSION = 'adzy.copy-semantic-shadow@2'
EXTRACTION_SCHEMA_VERSION = 'adzy.atomic-copy-source-quotes@1'
ANCHOR_METHOD = 'adzy.copy-source-anchor@1'
SOURCE_SEGMENT_CHARACTERS = 2000
SHADOW_MODEL = 'gemini-3.5-flash-lite'
MAX_PROMPT_BYTES = 32768
MAX_PROPOSITIONS = 20


class ShadowModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class CopySpan(ShadowModel):
    field: str = Field(min_length=1, max_length=100)
    start: Annotated[StrictInt, Field(ge=0)]
    end: Annotated[StrictInt, Field(ge=1)]
    text: str = Field(min_length=1, max_length=2000)


class ClaimQualifiers(ShadowModel):
    quantity: str = Field(max_length=250)
    location: str = Field(max_length=250)
    conditions: str = Field(max_length=500)
    timeframe: str = Field(max_length=250)
    comparison: str = Field(max_length=250)
    negation: str = Field(max_length=250)


class AtomicCopyProposition(ShadowModel):
    proposition_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    span: CopySpan
    subject: str = Field(min_length=1, max_length=250)
    asserted_fact: str = Field(min_length=1, max_length=1000)
    qualifiers: ClaimQualifiers


class CoverageSpan(ShadowModel):
    span: CopySpan
    kind: Literal['FACTUAL', 'NONFACTUAL', 'METADATA', 'UNKNOWN']
    proposition_ids: list[str] = Field(max_length=MAX_PROPOSITIONS)
    explanation: str = Field(min_length=1, max_length=300)


class AtomicCopyExtraction(ShadowModel):
    execution_status: Literal['COMPLETE', 'INCOMPLETE']
    propositions: list[AtomicCopyProposition] = Field(max_length=MAX_PROPOSITIONS)
    coverage: list[CoverageSpan] = Field(max_length=120)


class SourceQuoteAnchor(ShadowModel):
    field: str = Field(min_length=1, max_length=100)
    source_ref: str = Field(pattern=r'^src_[a-f0-9]{32}$')
    source_quote: str = Field(min_length=1, max_length=2000)


class SourceQuotedProposition(ShadowModel):
    proposition_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    source: SourceQuoteAnchor
    subject: str = Field(min_length=1, max_length=250)
    asserted_fact: str = Field(min_length=1, max_length=1000)
    qualifiers: ClaimQualifiers


class SourceQuotedCoverage(ShadowModel):
    source: SourceQuoteAnchor
    kind: Literal['FACTUAL', 'NONFACTUAL', 'METADATA', 'UNKNOWN']
    proposition_ids: list[str] = Field(max_length=MAX_PROPOSITIONS)
    explanation: str = Field(min_length=1, max_length=300)


class SourceQuotedExtraction(ShadowModel):
    copy_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    execution_status: Literal['COMPLETE', 'INCOMPLETE']
    propositions: list[SourceQuotedProposition] = Field(max_length=MAX_PROPOSITIONS)
    coverage: list[SourceQuotedCoverage] = Field(max_length=120)


def _source_segments(fields):
    """Fixed code-point chunks of exact input; offsets stay local and never enter A's schema."""
    revision = digest(fields)
    segments = []
    for field, text in sorted(fields.items()):
        for start in range(0, len(text), SOURCE_SEGMENT_CHARACTERS):
            part = text[start:start + SOURCE_SEGMENT_CHARACTERS]
            ref = 'src_' + digest({'copy_hash': revision, 'field': field, 'start': start, 'text': part})[:32]
            segments.append({'field': field, 'source_ref': ref, 'source_text': part, 'start': start})
    return segments


def copy_source_catalog(fields):
    return [{key: value for key, value in segment.items() if key != 'start'}
            for segment in _source_segments(fields)]


def resolve_source_quotes(extraction, fields):
    """Resolve literal quotes only. No normalization, fuzzy search, clamping or proposition search."""
    extraction = SourceQuotedExtraction.model_validate(extraction)
    if extraction.copy_hash != digest(fields): raise ValueError('ANCHOR_COPY_HASH_MISMATCH')
    sources = {s['source_ref']: s for s in _source_segments(fields)}
    def resolve(anchor):
        if anchor.field not in fields: raise ValueError('ANCHOR_UNKNOWN_FIELD')
        source = sources.get(anchor.source_ref)
        if source is None: raise ValueError('ANCHOR_UNKNOWN_SOURCE_REF')
        if source['field'] != anchor.field: raise ValueError('ANCHOR_FIELD_REFERENCE_MISMATCH')
        quote = anchor.source_quote
        at = source['source_text'].find(quote)
        if at < 0: raise ValueError('ANCHOR_QUOTE_ABSENT')
        # Include overlapping occurrences: e.g. "aaa" occurs twice inside "aaaa".
        if source['source_text'].find(quote, at + 1) >= 0: raise ValueError('ANCHOR_QUOTE_AMBIGUOUS')
        start = source['start'] + at
        end = start + len(quote)
        if fields[anchor.field][start:end] != quote: raise ValueError('ANCHOR_SOURCE_SLICE_MISMATCH')
        span = CopySpan(field=anchor.field, start=start, end=end, text=quote)
        _validate_span(span, fields)
        return span
    return AtomicCopyExtraction(execution_status=extraction.execution_status,
        propositions=[AtomicCopyProposition(**p.model_dump(exclude={'source'}), span=resolve(p.source))
                      for p in extraction.propositions],
        coverage=[CoverageSpan(**row.model_dump(exclude={'source'}), span=resolve(row.source))
                  for row in extraction.coverage])


class EvidenceCitation(ShadowModel):
    evidence_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=4000)


class AtomicSupportJudgment(ShadowModel):
    proposition_id: str = Field(min_length=1, max_length=64)
    verdict: Literal['SUPPORTED', 'CONTRADICTED', 'INSUFFICIENT_EVIDENCE', 'UNAVAILABLE']
    citations: list[EvidenceCitation] = Field(max_length=8)
    explanation: str = Field(min_length=1, max_length=600)
    same_entity: bool
    conditions_preserved: bool
    negation_preserved: bool
    strength_preserved: bool


class AtomicCopySupport(ShadowModel):
    execution_status: Literal['COMPLETE', 'INCOMPLETE']
    judgments: list[AtomicSupportJudgment] = Field(max_length=MAX_PROPOSITIONS)


class SemanticCopyShadow(ShadowModel):
    method: str = SHADOW_VERSION
    mode: Literal['SHADOW'] = 'SHADOW'
    qualification: Literal['UNQUALIFIED'] = 'UNQUALIFIED'
    may_change_acceptance: Literal[False] = False
    copy_hash: str
    evidence_hash: str
    contract_id: str
    strategy_version_id: str
    profile_snapshot_id: str
    source_attempt_id: str
    artifact_sha256: str
    dispatch_run_id: str
    requested_model: str
    prompt_versions: dict[str, str]
    prompt_hashes: dict[str, str]
    baseline: dict
    extraction: AtomicCopyExtraction | None = None
    provider_extraction: SourceQuotedExtraction | None = None
    extraction_schema_version: str | None = None
    anchor_method: str | None = None
    source_catalog_hash: str = ''
    anchor_status: Literal['UNRESOLVED', 'INVALID', 'RESOLVED'] = 'UNRESOLVED'
    declared_coverage_status: Literal['UNRESOLVED', 'SPAN_DECLARATIONS_VALIDATED', 'NONFACTUAL_UNQUALIFIED'] = 'UNRESOLVED'
    atomic_extraction_quality: Literal['NOT_INDEPENDENTLY_REVIEWED'] = 'NOT_INDEPENDENTLY_REVIEWED'
    support: AtomicCopySupport | None = None
    execution_status: Literal['COMPLETED', 'UNVERIFIED', 'DISPATCH_BLOCKED', 'OUTCOME_UNKNOWN']
    coverage_status: str = 'UNVERIFIED'
    candidate_would_support_authored_copy: bool = False
    complete_poster_eligibility: Literal['NOT_ESTABLISHED'] = 'NOT_ESTABLISHED'
    reason_codes: list[str] = Field(default_factory=list)
    calls: list[dict] = Field(default_factory=list, description='Caller stage requests, not dispatch authority. Actual sends are existing operation UsageRecords.')
    failure_diagnostics: list[dict] = Field(default_factory=list)
    elapsed_ms: int


def _validate_span(span, fields):
    if span.field not in fields or span.start >= span.end or span.end > len(fields[span.field]) or fields[span.field][span.start:span.end] != span.text:
        raise ValueError('INVALID_COPY_SPAN')


def validate_atomic_extraction(extraction, fields, contract):
    """Coverage declarations are validated, not trusted as proof of semantic completeness."""
    from grounding.ledger import extract_claims
    if extraction.execution_status != 'COMPLETE':
        raise ValueError('EMPTY_OR_INCOMPLETE_EXTRACTION')
    ids = [p.proposition_id for p in extraction.propositions]
    if len(ids) != len(set(ids)):
        raise ValueError('DUPLICATE_PROPOSITIONS')
    by_id = {p.proposition_id: p for p in extraction.propositions}
    assigned = set()
    covered = {key: set() for key, text in fields.items() if text.strip()}
    for prop in extraction.propositions:
        _validate_span(prop.span, fields)
    for row in extraction.coverage:
        span = row.span
        _validate_span(span, fields)
        if row.kind == 'UNKNOWN': raise ValueError('INCOMPLETE_COVERAGE')
        if row.kind == 'FACTUAL':
            if not row.proposition_ids: raise ValueError('MISSING_PROPOSITION_REFERENCE')
            for ident in row.proposition_ids:
                prop = by_id.get(ident)
                if prop is None or prop.span.field != span.field or not (prop.span.start < span.end and span.start < prop.span.end):
                    raise ValueError('INVALID_PROPOSITION_REFERENCE')
                assigned.add(ident)
        else:
            if row.proposition_ids: raise ValueError('NONFACTUAL_PROPOSITION_REFERENCE')
            if row.kind == 'METADATA':
                if not any(r.get('value') == span.text == r.get('quote') and r.get('page_url')
                           for r in contract.copy_metadata_evidence.get(span.field, [])):
                    raise ValueError('UNSOURCED_METADATA_EXCLUSION')
            elif extract_claims(span.text):
                raise ValueError('LITERAL_FACT_EXCLUDED_FROM_EXTRACTION')
        if span.field in covered: covered[span.field].update(range(span.start, span.end))
    if assigned != set(ids): raise ValueError('UNASSIGNED_PROPOSITIONS')
    if any(any(not char.isspace() and i not in covered[key] for i, char in enumerate(fields[key])) for key in covered):
        raise ValueError('UNCOVERED_COPY_CHARACTERS')
    if not extraction.propositions:
        raise ValueError('NO_FACTUAL_PROPOSITIONS_DECLARED_UNQUALIFIED')


def validate_atomic_support(support, extraction, contract):
    if support.execution_status != 'COMPLETE': raise ValueError('INCOMPLETE_SUPPORT')
    props = {p.proposition_id: p for p in extraction.propositions}
    ids = [j.proposition_id for j in support.judgments]
    if len(ids) != len(set(ids)) or set(ids) != set(props): raise ValueError('JUDGMENT_SET_MISMATCH')
    evidence = {r.get('evidence_id'): r for r in contract.copy_evidence}
    allowed_evidence = {eid for c in contract.allowed_claims
                        if c.claim_id not in contract.internal_supporting_claim_ids for eid in c.supporting_evidence_ids}
    for judgment in support.judgments:
        refs = []
        for citation in judgment.citations:
            row = evidence.get(citation.evidence_id)
            if not row or row.get('excerpt') != citation.quote or not row.get('source_url') or row.get('availability') != 'AVAILABLE':
                raise ValueError('INVALID_EVIDENCE_REFERENCE')
            refs.append(row)
        if judgment.verdict in ('SUPPORTED', 'CONTRADICTED') and not refs:
            raise ValueError('MISSING_EVIDENCE_REFERENCE')
        if judgment.verdict == 'SUPPORTED':
            if not all((judgment.same_entity, judgment.conditions_preserved, judgment.negation_preserved, judgment.strength_preserved)):
                raise ValueError('SUPPORT_SCOPE_NOT_PRESERVED')
            if any(r.get('evidence_id') not in allowed_evidence or r.get('subject_ref') != contract.domain_reference
                   or r.get('source_class') != 'BRAND_OWNED' for r in refs):
                raise ValueError('SUPPORT_EVIDENCE_SCOPE_MISMATCH')
            text = props[judgment.proposition_id].span.text
            entries = [LedgerEntry(raw=r['excerpt'], source_url=r['source_url'], source_type='profile_evidence') for r in refs]
            if (not _strict_number_context_supported(text, entries)
                    or not _price_currency_marks(text).issubset(set().union(*[_price_currency_marks(e.raw) for e in entries]))):
                raise ValueError('NUMERIC_OR_CURRENCY_SUPPORT_MISMATCH')


def _shadow_prompt(system, data, schema):
    user = json.dumps(data, ensure_ascii=False, sort_keys=True)
    size = len((system + user + json.dumps(schema.model_json_schema())).encode())
    if size > MAX_PROMPT_BYTES: raise ValueError('PROMPT_TOO_LARGE_NO_TRUNCATION')
    return user


def provider_schema_projection(model):
    """Provider grammar only; every response still passes the unchanged strict local model.

    Reduce documented constrained-grammar complexity: omit lengths/patterns/numeric/array
    bounds, titles and extra-property keywords. Keep names, types, enums, required fields,
    nested object/array structure, and exact property order. No semantic gate is relaxed.
    """
    schema=model.model_json_schema()
    definitions=schema.get('$defs',{})
    def project(node,depth=0):
        if depth>20: raise ValueError('PROVIDER_SCHEMA_RECURSION_LIMIT')
        if '$ref' in node:
            return project(definitions[node['$ref'].removeprefix('#/$defs/')],depth+1)
        value={key:node[key] for key in ('type','enum','required','nullable') if key in node}
        if 'const' in node: value['enum']=[node['const']]
        if 'properties' in node:
            value['properties']={key:project(child,depth+1) for key,child in node['properties'].items()}
            value['propertyOrdering']=list(value['properties'])
        if 'items' in node: value['items']=project(node['items'],depth+1)
        if 'anyOf' in node: value['anyOf']=[project(child,depth+1) for child in node['anyOf']]
        return value
    return project(schema)


class ProtectedSemanticCaller:
    """Existing GeminiCaller, no application/SDK retries, no tools, bounded output/timeout.

    Explicit Vertex client avoids GeminiCaller auto-switching to a different API-key surface.
    No client construction or dispatch occurs until __call__ after protected scope checks.
    """
    model = SHADOW_MODEL

    def __init__(self, *, project, client=None):
        self.project = project
        self.client = client

    def __call__(self, system, user, schema, *, group_name):
        from poster.paid_boundary import require_paid_scope
        from business_profile.llm.caller import GeminiCaller
        scope = require_paid_scope()
        scope.call_id = None
        scope.response_received = False
        caller = GeminiCaller(model=self.model, project=self.project, location='global',
            max_retries=0, max_output_tokens=8192 if group_name == 'poster_copy_extract' else 4096,
            thinking_level='MINIMAL', strict_completion=True, temperature=None, timeout_s=60,
            pricing_per_million=(0.30, 2.50), provider_response_schema=provider_schema_projection(schema))
        if self.client is None:
            from google import genai
            from google.genai import types
            self.client = genai.Client(vertexai=True, project=self.project, location='global',
                http_options=types.HttpOptions(timeout=60000, retry_options=types.HttpRetryOptions(attempts=1)))
        caller._client = self.client
        return caller(system, user, schema, group_name=group_name)


def assess_semantic_shadow(*, brief, contract, caller, dispatch_run_id,
                           source_attempt_id='', payload=b''):
    """Explicit caller only; never invoked automatically by generation/acceptance or graph nodes."""
    import time
    from dataclasses import asdict
    from poster.paid_boundary import require_paid_scope, StopPaidOperation
    started = time.monotonic()
    fields = inventory(brief)
    catalog = copy_source_catalog(fields)
    baseline = assess_copy(brief=brief, contract=contract, attempt_id=source_attempt_id, payload=payload)
    extraction = provider_extraction = support = None
    anchor_status, declared_coverage = 'UNRESOLVED', 'UNRESOLVED'
    calls, reasons, diagnostics = [], [], []
    execution, coverage, would_support = 'UNVERIFIED', 'UNVERIFIED', False
    scope = require_paid_scope(dispatch_run_id)  # No unprotected GeminiCaller fallback permitted.
    request_hashes = {}
    failure_stage = 'extraction_request_preparation'
    try:
        if caller.model != SHADOW_MODEL: raise ValueError('UNFROZEN_MODEL')
        a_data = {'copy_hash': digest(fields), 'copy_sources': catalog, 'deterministic_metadata': contract.copy_metadata_evidence,
                  'entity': contract.domain_reference, 'product_context': contract.product_offer_focus}
        user = _shadow_prompt(EXTRACTION_PROMPT, a_data, SourceQuotedExtraction)
        request_hashes['extraction_request'] = digest(user)
        calls.append({'stage':'extraction','usage':None,'caller_returned':False})
        failure_stage = 'extraction'
        provider_result, usage = caller(EXTRACTION_PROMPT, user, SourceQuotedExtraction, group_name='poster_copy_extract')
        calls[-1].update(usage={k:v for k,v in asdict(usage).items() if k != 'cost_usd'} if usage else None, caller_returned=True)
        failure_stage = 'extraction_output_validation'
        if not usage or str(usage.finish_reason).upper() != 'STOP': raise ValueError('INCOMPLETE_EXTRACTOR_RECEIPT')
        if not usage.usage_available or not usage.resolved_model_version:
            raise ValueError('INCOMPLETE_EXTRACTOR_RECEIPT')
        provider_extraction = SourceQuotedExtraction.model_validate(provider_result)
        anchor_status = 'INVALID'
        extraction = resolve_source_quotes(provider_extraction, fields)
        anchor_status = 'RESOLVED'
        validate_atomic_extraction(extraction, fields, contract)
        declared_coverage = 'SPAN_DECLARATIONS_VALIDATED'
        failure_stage = 'support_request_preparation'
        user = _shadow_prompt(SUPPORT_PROMPT, {'copy_fields':fields,
            'entity':contract.domain_reference, 'product_context':contract.product_offer_focus,
            'extraction':extraction.model_dump(), 'evidence_snapshot':evidence_snapshot(contract)}, AtomicCopySupport)
        request_hashes['support_request'] = digest(user)
        calls.append({'stage':'support','usage':None,'caller_returned':False})
        failure_stage = 'support'
        support, usage = caller(SUPPORT_PROMPT, user, AtomicCopySupport, group_name='poster_copy_support')
        calls[-1].update(usage={k:v for k,v in asdict(usage).items() if k != 'cost_usd'} if usage else None, caller_returned=True)
        failure_stage = 'support_output_validation'
        if not usage or str(usage.finish_reason).upper() != 'STOP': raise ValueError('INCOMPLETE_SUPPORT_RECEIPT')
        support = AtomicCopySupport.model_validate(support)
        validate_atomic_support(support, extraction, contract)
        execution = 'COMPLETED'
        coverage = 'MODEL_DECLARED_COVERAGE_UNQUALIFIED'
        reasons.append('SEMANTIC_COVERAGE_NOT_INDEPENDENTLY_VERIFIED')
        would_support = all(j.verdict == 'SUPPORTED' for j in support.judgments)
        versions = {(c.get('usage') or {}).get('resolved_model_version') for c in calls} - {None}
        if len(versions) > 1:
            execution, coverage, would_support = 'UNVERIFIED', 'UNVERIFIED', False
            reasons.append('MODEL_VERSION_CHANGED_DURING_ASSESSMENT')
    except StopPaidOperation as exc:
        execution = 'OUTCOME_UNKNOWN' if exc.unknown else 'DISPATCH_BLOCKED'
        reasons.append(exc.code)
        if exc.diagnostic:
            diagnostics.append(exc.diagnostic)
    except Exception as exc:
        from poster.paid_boundary import retain_failure
        diagnostics.append(retain_failure(exc, failure_stage, received=failure_stage.endswith('_output_validation')))
        # Never persist exception strings containing prompts/SDK payloads/private evidence.
        known = {'ANCHOR_COPY_HASH_MISMATCH','ANCHOR_UNKNOWN_FIELD','ANCHOR_UNKNOWN_SOURCE_REF',
            'ANCHOR_FIELD_REFERENCE_MISMATCH','ANCHOR_QUOTE_ABSENT','ANCHOR_QUOTE_AMBIGUOUS',
            'ANCHOR_SOURCE_SLICE_MISMATCH','INVALID_COPY_SPAN','DUPLICATE_PROPOSITIONS','EMPTY_OR_INCOMPLETE_EXTRACTION',
            'INCOMPLETE_COVERAGE','MISSING_PROPOSITION_REFERENCE','INVALID_PROPOSITION_REFERENCE',
            'NONFACTUAL_PROPOSITION_REFERENCE','UNSOURCED_METADATA_EXCLUSION','LITERAL_FACT_EXCLUDED_FROM_EXTRACTION',
            'UNASSIGNED_PROPOSITIONS','UNCOVERED_COPY_CHARACTERS','INCOMPLETE_SUPPORT','JUDGMENT_SET_MISMATCH',
            'INVALID_EVIDENCE_REFERENCE','MISSING_EVIDENCE_REFERENCE','SUPPORT_SCOPE_NOT_PRESERVED',
            'SUPPORT_EVIDENCE_SCOPE_MISMATCH','NUMERIC_OR_CURRENCY_SUPPORT_MISMATCH',
            'PROMPT_TOO_LARGE_NO_TRUNCATION','UNFROZEN_MODEL','INCOMPLETE_EXTRACTOR_RECEIPT','INCOMPLETE_SUPPORT_RECEIPT',
            'NO_FACTUAL_PROPOSITIONS_DECLARED_UNQUALIFIED'}
        reasons.append(str(exc) if type(exc) is ValueError and str(exc) in known else 'INVALID_OR_UNAVAILABLE_MODEL_OUTPUT')
        if reasons[-1] == 'NO_FACTUAL_PROPOSITIONS_DECLARED_UNQUALIFIED':
            coverage = 'MODEL_DECLARED_NONFACTUAL_UNQUALIFIED'
            declared_coverage = 'NONFACTUAL_UNQUALIFIED'
    return SemanticCopyShadow(copy_hash=baseline.copy_hash, evidence_hash=baseline.evidence_hash,
        contract_id=contract.creative_contract_id, strategy_version_id=contract.strategy_version_id,
        profile_snapshot_id=contract.profile_snapshot_id, source_attempt_id=source_attempt_id,
        artifact_sha256=hashlib.sha256(payload).hexdigest(), dispatch_run_id=scope.run_id,
        requested_model=caller.model, prompt_versions={'extraction':EXTRACTION_VERSION,'support':SUPPORT_VERSION},
        prompt_hashes={'extraction':digest(EXTRACTION_PROMPT),'support':digest(SUPPORT_PROMPT), **request_hashes},
        baseline=baseline.model_dump(mode='json'), extraction=extraction, provider_extraction=provider_extraction, support=support,
        extraction_schema_version=EXTRACTION_SCHEMA_VERSION, anchor_method=ANCHOR_METHOD,
        source_catalog_hash=digest(catalog), anchor_status=anchor_status, declared_coverage_status=declared_coverage,
        execution_status=execution, coverage_status=coverage, candidate_would_support_authored_copy=would_support,
        reason_codes=reasons, calls=calls, failure_diagnostics=diagnostics,
        elapsed_ms=int((time.monotonic()-started)*1000))
