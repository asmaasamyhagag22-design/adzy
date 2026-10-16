"""Opt-in checks against diagnostics/product_slice/server.py and its isolated real PostgreSQL.
Only this synthetic, loopback server is supported; never point these tests at a real deployment.
"""
import importlib.util
import os
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('PRODUCT_SLICE_HTTP') != '1', reason='requires owned synthetic product_slice server')
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def client():
    spec=importlib.util.spec_from_file_location('product_http',ROOT/'docs/upgrade_audit/diagnostics/product_slice/http_client.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod,mod.login()


def test_browser_journey_persisted_exact_subjects_and_selected_accepted_attempt(client):
    c,t=client;p=c.proof('studio_example_test',t)
    assert p['swot_approved'] and p['strategy_approval_state']=='approved'
    approvals={(a['kind'],a['subject'],a['decision']) for a in p['approvals']}
    assert ('swot',p['swot_run_id'],'approved') in approvals
    assert ('strategy',p['strategy_version_id'],'approved') in approvals
    accepted=[r for r in p['runs'] if r['kind']=='poster' and r['status']=='succeeded']
    assert accepted and all(r['selected_attempt_id'] for r in accepted)
    attempts={a['id']:a for a in p['attempts']}
    assert all(attempts[r['selected_attempt_id']]['qa_passed'] is True for r in accepted)
    assert all(r['selected_attempt_id'] is None for r in p['runs'] if r['kind']=='poster' and r['status']=='failed')
    status,history=c.request('GET','/api/history',token=t);assert status==200
    items=history['items'] if 'items' in history else history.get('history',[])
    assert any(i.get('strategy_version_id')==p['strategy_version_id'] for i in items)


def test_foreign_owner_read_write_and_history_do_not_reveal_or_mutate(client):
    import uuid
    c,t=client;before=c.proof('studio_example_test',t)
    status,b=c.request('POST','/api/auth/signup',{'email':uuid.uuid4().hex+'@example.test','password':'SyntheticStrangerOnly42','full_name':'Synthetic stranger'})
    assert status==200;other=b['access_token']
    for method,path,payload in [
        ('GET','/api/brands/studio_example_test',None),
        ('GET','/api/intel/studio_example_test',None),
        ('POST','/api/swot/approve',{'domain_key':'studio_example_test','swot_run_id':before['swot_run_id']}),
        ('POST','/api/strategy/decide',{'domain_key':'studio_example_test','strategy_run_id':before['strategy_version_id'],'decision':'approved'}),
        ('POST','/api/poster/contract',{'domain_key':'studio_example_test','strategy_version_id':before['strategy_version_id']}),
        ('POST','/api/generate',{'domain_key':'studio_example_test','strategy_version_id':before['strategy_version_id']})]:
        assert c.request(method,path,payload,other)[0]==404
    assert c.request('GET','/api/brands/absent_example_test',token=other)[0]==404
    status,history=c.request('GET','/api/history',token=other);assert status==200
    assert history.get('items',history.get('history',[]))==[]
    after=c.proof('studio_example_test',t)
    assert before['approvals']==after['approvals'] and before['runs']==after['runs']
    assert before['image_provider_calls']==after['image_provider_calls']


def test_wrong_subject_is_private_validation_does_not_mutate(client):
    import uuid
    c,t=client;before=c.proof('studio_example_test',t)
    for subject,expected in [(None,422),('malformed',422),(str(uuid.uuid4()),404),
            (before['strategy_version_id'],404)]:
        assert c.request('POST','/api/swot/approve',{'domain_key':'studio_example_test','swot_run_id':subject},t)[0]==expected
    after=c.proof('studio_example_test',t);assert before['approvals']==after['approvals']


def test_get_refreshes_do_not_trigger_model_or_generation_work(client):
    c,t=client;before=c.proof('studio_example_test',t)
    for path in ['/api/brands','/api/brands/studio_example_test','/api/intel/studio_example_test','/api/history']:
        assert c.request('GET',path,token=t)[0]==200
    after=c.proof('studio_example_test',t)
    assert before['model_calls']==after['model_calls'] and before['image_provider_calls']==after['image_provider_calls']
    assert before['runs']==after['runs']

@pytest.mark.parametrize('mode,expected_status', [('fail','QA_FAIL'),('unverified','QA_UNVERIFIED'),('error',None)])
def test_failed_external_results_never_select_attempt_or_publish(client,mode,expected_status):
    import json
    c,t=client
    before=c.proof('studio_example_test',t)
    assert not any(r['status']=='running' for r in before['runs'] if r['kind']=='poster'), 'finish browser generation before changing fake mode'
    _,history_before=c.request('GET','/api/history',token=t)
    mode_file=Path('/tmp/adzy-product-mode.json')
    try:
        mode_file.write_text(json.dumps({'poster':mode}))
        status,body=c.request('POST','/api/generate',{'domain_key':'studio_example_test',
            'strategy_version_id':before['strategy_version_id'],'content_type':'brand_poster',
            'style':'strategy_grounded','cta':'','language':'en'},t)
    finally:
        mode_file.write_text(json.dumps({'poster':'pass'}))
    assert status==502
    detail=body['detail'];assert detail['code']=='CANONICAL_POSTER_GENERATION_FAILED'
    if expected_status:
        assert detail['qa_status']==expected_status and detail['accepted'] is False
        assert detail['reason_codes'] and 'retry' in detail['safe_alternative']
    else:
        assert 'SYNTHETIC_TEST' not in detail['message']
    after=c.proof('studio_example_test',t)
    run=next(r for r in after['runs'] if r['id']==detail['generation_run_id'])
    assert run['status']=='failed' and run['selected_attempt_id'] is None
    assert len(after['runs'])==len(before['runs'])+1
    _,history_after=c.request('GET','/api/history',token=t)
    assert history_before==history_after
