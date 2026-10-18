"""Public frozen case; provider-free behavior and adversarial support regressions."""
import json
from copy import deepcopy
from pathlib import Path
import pytest
from poster.strategy_contract import (CreativeContractCompiler, CreativeContractError,
    PosterStrategyVerifier, claim_ids_for_text)
from test_gate2c_canonical_poster import _concept, _brief

FIXTURE = Path(__file__).resolve().parents[1] / 'docs/experiments/poster_compiler_source_support_20261018/context.json'


def context():
    return json.loads(FIXTURE.read_text())


def selected(c):
    return c['output']['canonical_strategy']['decision']['options'][0]


def test_current_failure_was_lexical_disjointness_and_output_keeps_reviewed_meaning():
    c = context(); original = deepcopy(c)
    value = selected(c)['consumer_value']; reason = selected(c)['reason_to_believe']
    # Original algorithm's containment/overlap necessarily rejected these two strings.
    assert not set(value.split()) & set(reason.split())
    result = CreativeContractCompiler().compile(c)
    assert c == original
    assert result.schema_version == 2
    assert result.primary_message == value == 'الخصوصية مُدمجة'
    assert result.reason_to_believe == reason == 'فنحن نحظر مُعظم المتعقّبات تلقائيًا.'
    assert result.message_source_support.proof_entailment == 'NOT_ASSESSED'
    assert result.message_source_support.support_kind == 'SOURCE_ASSERTION'
    assert set(result.message_source_support.claim_evidence_ids) == set(
        result.consumer_value_claim_ids + result.reason_to_believe_claim_ids)
    assert result.copy_evidence == c['output']['canonical_strategy']['evidence']
    assert all(claim.limitations for claim in result.allowed_claims)
    assert PosterStrategyVerifier().verify(contract=result, concept=_concept(result),
        brief=_brief(result), language='ar').overall_pass


@pytest.mark.parametrize('promise,proof', [
    ('Fast specialist onboarding', 'Published delivery process'),
    ('Fast specialist onboarding', 'Specialist onboarding process is published'),
    ('We block all trackers automatically', 'We block most trackers automatically'),
    ('We do not block trackers', 'We block trackers'),
    ('Product A blocks trackers', 'Product B blocks trackers'),
    ('We block trackers', 'We block trackers when enabled'),
])
def test_approval_citation_and_overlap_do_not_prove_a_different_promise(promise, proof):
    c = context(); s = selected(c); canonical = c['output']['canonical_strategy']
    s['consumer_value'] = promise; s['reason_to_believe'] = proof
    for claim in canonical['claims']:
        if claim['claim_id'] in s['consumer_value_claim_ids']: claim['statement'] = promise
        if claim['claim_id'] in s['reason_to_believe_claim_ids']: claim['statement'] = proof
    # Citation and approved/SUPPORTED metadata remain, but original quotes do not support new text.
    with pytest.raises(CreativeContractError, match='independently anchored'):
        CreativeContractCompiler().compile(c)


@pytest.mark.parametrize('changed', [
    'فنحن نحظر كل المتعقّبات تلقائيًا.',
    'فنحن لا نحظر مُعظم المتعقّبات تلقائيًا.',
    'متصفح آخر يحظر مُعظم المتعقّبات تلقائيًا.',
    'فنحن نحظر مُعظم المتعقّبات.',
])
def test_rendered_proof_cannot_widen_or_change_exact_approved_assertion(changed):
    result = CreativeContractCompiler().compile(context())
    assert not claim_ids_for_text(changed, result)
    check = PosterStrategyVerifier().verify(contract=result, concept=_concept(result),
        brief=_brief(result, offerings=[changed]), language='ar')
    assert not check.overall_pass
    assert 'PROOF_MESSAGE_MISMATCH' in {d.code for d in check.defects}


@pytest.mark.parametrize('mutation', ['quote', 'profile', 'subject', 'type', 'contradiction', 'ids'])
def test_metadata_does_not_override_source_integrity(mutation):
    c = context(); canonical = c['output']['canonical_strategy']
    cid = selected(c)['consumer_value_claim_ids'][0]
    claim = next(v for v in canonical['claims'] if v['claim_id'] == cid)
    evidence = next(v for v in canonical['evidence'] if v['evidence_id'] in claim['supporting_evidence_ids'])
    if mutation == 'quote': evidence['excerpt'] = 'Other promise'
    if mutation == 'profile': c['profile']['value_propositions'][0]['evidence'][0]['quote'] = 'Other promise'
    if mutation == 'subject': evidence['subject_ref'] = 'another_brand'
    if mutation == 'type': claim['epistemic_type'] = 'INFERENCE'
    if mutation == 'contradiction': claim['contradicting_evidence_ids'] = ['ev_other']
    if mutation == 'ids': claim['supporting_evidence_ids'] = ['ev_missing']
    with pytest.raises(CreativeContractError): CreativeContractCompiler().compile(c)


def test_old_approval_cannot_override_currentness():
    c = context(); c['is_current'] = False
    with pytest.raises(CreativeContractError) as exc: CreativeContractCompiler().compile(c)
    assert exc.value.code == 'STRATEGY_VERSION_STALE'
