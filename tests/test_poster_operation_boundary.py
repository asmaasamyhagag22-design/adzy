"""Guarded disposable PostgreSQL + real authenticated HTTP path + fake SDK HTTP transport."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select
from test_review_workflow import database, decision
from test_app_review_graph import application, begin
from poster_boundary_support import install


@pytest.fixture
def ready(application,monkeypatch,tmp_path):
    client,h,service,p,data,app=application
    state=begin(client,h,data).json()
    response=client.post('/api/review/'+state['workflow_id']+'/resume',headers=h,json=decision(state))
    assert response.json()['status']=='READY_FOR_CREATIVE',response.text
    monkeypatch.setenv('BASEERA_ENABLE_PAID_IMAGES','1')
    monkeypatch.setenv('ADZY_POSTER_RESERVATION_USD','0.6')
    monkeypatch.setenv('ADZY_POSTER_BUDGET_USD','10')
    monkeypatch.setenv('ADZY_POSTER_CALL_ESTIMATES_JSON',json.dumps({'generate_content:gemini-2.5-flash-image':'0.2','generate_content:gemini-2.5-pro':'0.1'}))
    monkeypatch.setenv('BASEERA_BLOB_BACKEND','local')
    monkeypatch.setenv('BASEERA_NO_SPEND','0')  # fake transport only; external DNS/socket firewall stays on
    return client,h,service,p,data,app,tmp_path


def body(data,key=None):
    return {'domain_key':data['domain'],'strategy_version_id':data['subject'],'operation_key':key or uuid4().hex,'language':'en'}


def submit(ready,request):
    c,h,*_=ready
    r=c.post('/api/generate',headers=h,json=request)
    assert r.status_code==200,r.text
    return r.json()


def status(ready,key):
    c,h,*_=ready
    return c.get('/api/poster/operations/'+key,headers=h)


def test_duplicate_acceptance_and_hash_conflict(ready,monkeypatch):
    c,h,service,p,data,app,tmp=ready
    counter,transports,sdk=install(monkeypatch,tmp)
    request=body(data)
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(lambda _:c.post('/api/generate',headers=h,json=request),range(2)))
    assert all(r.status_code==200 for r in result),[r.text for r in result]
    operation=status(ready,request['operation_key']).json()
    assert operation['state']=='accepted' and operation['result']['accepted'] is True
    assert len(transports)==1 and len(counter.read_text().splitlines())==1
    assert len({r.json()['generation_run_id'] for r in result})==1
    assert submit(ready,request)['generation_run_id']==operation['generation_run_id']
    assert c.post('/api/generate',headers=h,json={**request,'headline':'different'}).status_code==409
    assert operation['settled_usd'] is None and operation['known_usage_usd'] is None
    assert operation['calls'][0]['provider_reference']=='synthetic-response'
    assert operation['calls'][0]['usage']['thoughts_token_count']==3
    assert len(p.gallery_history(user_id=data['actor']))==1
    sdk.close()


def test_atomic_point_six_reservations(ready,monkeypatch):
    c,h,service,p,data,app,tmp=ready
    monkeypatch.setenv('ADZY_POSTER_BUDGET_USD','1.0')
    counter,transports,sdk=install(monkeypatch,tmp)
    # Different brands: the Brand lock alone cannot serialize this owner budget.
    from workflows.demo import seed
    monkeypatch.setattr(service,'create_user',lambda **kw:service.user_by_id(UUID(data['actor'])))
    second=seed(service,p)
    service.decide_current_strategy(brand_id=p._brand_row(domain_key=second['domain'],user_id=data['actor']).id,
        user_id=UUID(data['actor']),run_id=UUID(second['subject']),decision='approved')
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda request:c.post('/api/generate',headers=h,json=request),[body(data),body(second)]))
    assert sorted(r.status_code for r in results)==[200,409],[r.text for r in results]
    assert len(transports)==1
    sdk.close()


@pytest.mark.parametrize('mode,expected,dispatches',[('timeout','unknown',1),('503','unknown',1),('429','failed',3)])
def test_unknown_is_never_resent_sdk_retries_cannot_escape(ready,monkeypatch,mode,expected,dispatches):
    c,h,service,p,data,app,tmp=ready
    counter,transports,sdk=install(monkeypatch,tmp,mode)
    request=body(data);op=submit(ready,request)
    assert len(transports)==dispatches
    assert op['state']==expected,op
    assert submit(ready,request)['state']==expected
    assert status(ready,request['operation_key']).json()['state']==expected
    assert len(transports)==dispatches
    if expected=='unknown': assert op['reserved_usd']=='0.600000000' and op['settled_usd'] is None
    sdk.close()


def test_multiple_billable_stages_and_budget(ready,monkeypatch):
    c,h,service,p,data,app,tmp=ready
    counter,transports,sdk=install(monkeypatch,tmp,'multistage')
    op=submit(ready,body(data))
    assert op['state']=='accepted' and len(op['calls'])==2
    assert op['estimated_usd']=='0.300000000' and len(transports)==2
    assert op['calls'][1]['stage']=='generate_content/poster_vision_qa'
    sdk.close()


def test_missing_call_price_is_not_free(ready,monkeypatch):
    c,h,service,p,data,app,tmp=ready
    counter,transports,sdk=install(monkeypatch,tmp)
    monkeypatch.setenv('ADZY_POSTER_CALL_ESTIMATES_JSON','{}')
    op=submit(ready,body(data))
    assert op['state']=='failed' and len(transports)==0
    sdk.close()


def test_wrong_owner_no_disclosure_or_mutation_and_approval_gate(ready,monkeypatch):
    c,h,service,p,data,app,tmp=ready
    _,transports,sdk=install(monkeypatch,tmp)
    request=body(data);op=submit(ready,request)
    signup=c.post('/api/auth/signup',json={'email':uuid4().hex+'@example.invalid','password':'SyntheticPassword42'})
    foreign={'Authorization':'Bearer '+signup.json()['access_token']}
    assert c.get('/api/poster/operations/'+request['operation_key'],headers=foreign).status_code==404
    assert c.get('/api/poster/operations/latest',params={'domain_key':data['domain']},headers=foreign).status_code==404
    assert c.post('/api/generate',headers=foreign,json=request).status_code==404
    assert status(ready,request['operation_key']).json()==op and len(transports)==1
    service.decide_current_strategy(brand_id=p._brand_row(domain_key=data['domain'],user_id=data['actor']).id,user_id=UUID(data['actor']),run_id=UUID(data['subject']),decision='rejected')
    assert c.post('/api/generate',headers=h,json=body(data)).status_code==409
    assert len(transports)==1
    sdk.close()


def test_refresh_distinguishes_latest_failure_and_gallery_success(ready,monkeypatch):
    c,h,service,p,data,app,tmp=ready
    _,_,sdk=install(monkeypatch,tmp)
    first=submit(ready,body(data));sdk.close()
    _,_,sdk=install(monkeypatch,tmp,'qa_fail')
    failed=submit(ready,body(data))
    assert failed['state']=='failed'
    from api.persistence import reset_persistence
    reset_persistence()
    latest=c.get('/api/poster/operations/latest',headers=h,params={'domain_key':data['domain']}).json()['operation']
    assert latest==failed and latest['generation_run_id']!=first['generation_run_id']
    payload=c.get('/api/poster/operations/latest',headers=h,params={'domain_key':data['domain']}).json()
    assert payload['last_accepted']['generation_run_id']==first['generation_run_id']
    assert any(r['generation_run_id']==first['generation_run_id'] for r in p.gallery_history(user_id=data['actor']))
    sdk.close()


@pytest.mark.parametrize('crash,exit_code,expected,accepted',[('before_send',71,'failed',0),('dispatch_intent',74,'unknown',0),('external_acceptance',72,'unknown',1),('local_result',73,'accepted',1)])
def test_process_death_http_recovery_never_replays(ready,monkeypatch,crash,exit_code,expected,accepted):
    c,h,service,p,data,app,tmp=ready
    request=body(data)
    config=tmp/'crash.json'
    config.write_text(json.dumps({'request':request,'headers':h,'directory':str(tmp),'crash':crash}))
    code='''
import sys,json,os
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from workflows.demo import isolate
isolate()
os.environ['BASEERA_ENABLE_PAID_IMAGES']='1'
os.environ['BASEERA_NO_SPEND']='0'
from pytest import MonkeyPatch
from poster_boundary_support import install
cfg=json.loads(Path(sys.argv[1]).read_text())
mp=MonkeyPatch();install(mp,cfg['directory'],crash=cfg['crash'])
from api.main import app
from fastapi.testclient import TestClient
with TestClient(app) as client:
    r=client.post('/api/generate',headers=cfg['headers'],json=cfg['request'])
    print(r.status_code,r.text,flush=True)
'''
    process=subprocess.run([sys.executable,'-c',code,str(config)],env=dict(os.environ),capture_output=True,text=True,timeout=60)
    assert process.returncode==exit_code,process.stdout+process.stderr
    op=status(ready,request['operation_key']).json()
    assert op['state']==expected,op
    count=tmp/'acceptances.jsonl'
    assert (len(count.read_text().splitlines()) if count.exists() else 0)==accepted
    recovered=submit(ready,request)
    assert recovered['generation_run_id']==op['generation_run_id'] and recovered['state']==expected
    assert (len(count.read_text().splitlines()) if count.exists() else 0)==accepted


def test_verified_receipt_settles_unknown_without_resend(ready,monkeypatch):
    c,h,service,p,data,app,tmp=ready
    from db.poster_operations import PosterOperations
    operations=PosterOperations(p)
    _,transports,sdk=install(monkeypatch,tmp,'timeout')
    request=body(data);unknown=submit(ready,request)
    costs={call['id']:'0.7' for call in unknown['calls']}
    with pytest.raises(ValueError):
        operations.reconcile_receipt(data['actor'],request['operation_key'],receipt_reference='local deadline',final_costs=costs,terminal_confirmed=False)
    assert status(ready,request['operation_key']).json()['state']=='unknown'
    operations.reconcile_receipt(data['actor'],request['operation_key'],receipt_reference='SYNTHETIC_FINAL_PROVIDER_RECEIPT',final_costs=costs,terminal_confirmed=True)
    # A delayed recovery observation cannot reopen a reconciled terminal operation.
    operations.finish(unknown['generation_run_id'],'unknown')
    settled=status(ready,request['operation_key']).json()
    assert settled['state']=='failed' and settled['settled_usd']=='0.700000000' and settled['reserved_usd']=='0E-9'
    assert len(transports)==1
    monkeypatch.setenv('ADZY_POSTER_BUDGET_USD','1.0')
    assert c.post('/api/generate',headers=h,json=body(data)).status_code==409
    sdk.close()


def test_actual_pipeline_ambiguity_stops_all_fallbacks(ready,monkeypatch):
    c,h,service,p,data,app,tmp=ready
    from api import webapp_images
    from business_profile.llm.caller import GeminiCaller
    import business_profile.llm as llm
    real_render=webapp_images._render_real
    _,transports,sdk=install(monkeypatch,tmp,'timeout')
    monkeypatch.setattr(webapp_images,'_render_real',real_render)
    caller=GeminiCaller(model='gemini-2.5-pro',max_retries=3);caller._client=sdk
    monkeypatch.setattr(llm,'default_caller',lambda **kw:caller)
    monkeypatch.chdir(tmp)
    request=body(data)
    op=submit(ready,request)
    assert op['state']=='unknown' and len(transports)==1,op
    assert op['calls'][0]['model']=='gemini-2.5-pro'
    assert status(ready,request['operation_key']).json()['state']=='unknown'
    assert submit(ready,request)['state']=='unknown' and len(transports)==1
    assert p.gallery_history(user_id=data['actor'])==[]
    sdk.close()


def test_head_schema_matches_updated_models(ready):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from db.base import Base
    with ready[2]._uow_factory() as uow:
        diff=compare_metadata(MigrationContext.configure(uow.session.connection(),opts={'compare_type':True}),Base.metadata)
        assert diff==[],diff
