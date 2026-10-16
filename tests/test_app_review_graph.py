"""Real application auth/routes -> graph -> PostgreSQL; fictional evidence only."""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID
import json
import sqlite3
import pytest
from test_review_workflow import database, approvals, decision, sdk_telemetry
from workflows.demo import connect, seed
from workflows.runtime import ReviewWorkflow, setup
from workflows.services import ReviewBoundaryError


@pytest.fixture
def application(database,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from api.persistence import reset_persistence
    from api.user_auth import hash_password
    from db.models import User
    service,provider = connect(database)
    data = seed(service,provider)
    with service._uow_factory() as uow:
        row = uow.session.get(User,UUID(data['actor']))
        row.hashed_password = hash_password('SyntheticPassword42')
        email = row.email
        uow.commit()
    monkeypatch.setenv('PERSISTENCE_BACKEND','postgres')
    monkeypatch.setenv('BASEERA_DATABASE_URL',database['dsn'])
    monkeypatch.setenv('ADZY_REVIEW_STATE_DIR',str(tmp_path/'checkpoints'))
    reset_persistence()
    from api.main import app
    with TestClient(app) as client:
        login = client.post('/api/auth/login',json={'email':email,'password':'SyntheticPassword42'})
        assert login.status_code==200,login.text
        headers = {'Authorization':'Bearer '+login.json()['access_token']}
        yield client,headers,service,provider,data,app
    reset_persistence()


def begin(client,headers,data):
    return client.post('/api/review/start',headers=headers,json={'domain_key':data['domain'],'subject_id':data['subject']})


def test_application_real_auth_pause_restore_concurrent_resume(application):
    client,h,service,p,data,app = application
    assert begin(client,{},data).status_code==401
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _:begin(client,h,data),range(2)))
    assert all(r.status_code==200 for r in responses),[r.text for r in responses]
    state = responses[0].json()
    assert all(r.json()['workflow_id']==state['workflow_id'] for r in responses)
    assert state['status']=='NEEDS_REVIEW'
    assert not {'pending_nodes','profile_id','version','evidence_hash'} & state.keys()
    assert client.get('/api/review/'+state['workflow_id'],headers=h).json()==state
    path = '/api/review/'+state['workflow_id']+'/resume'
    wrong = {**decision(state),'subject_hash':'0'*64}
    assert client.post(path,headers=h,json=wrong).status_code==409
    assert not approvals(service,data)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _:client.post(path,headers=h,json=decision(state)),range(2)))
    assert all(r.status_code==200 and r.json()['status']=='READY_FOR_CREATIVE' for r in results),[r.text for r in results]
    assert len(approvals(service,data))==1
    contract = results[0].json()['creative_contract']
    assert contract['strategy_version_id']==data['subject']
    assert begin(client,h,data).json()['creative_contract']==contract
    assert client.post(path,headers=h,json=decision(state,'rejected')).status_code==409


def test_application_foreign_owner_hidden_and_rejected(application):
    client,h,service,p,data,app = application
    state = begin(client,h,data).json()
    signup = client.post('/api/auth/signup',json={'email':'foreign@example.invalid','password':'SyntheticPassword42'})
    assert signup.status_code==200,signup.text
    foreign = {'Authorization':'Bearer '+signup.json()['access_token']}
    assert begin(client,foreign,data).status_code==404
    assert client.get('/api/review/'+state['workflow_id'],headers=foreign).status_code==404
    path = '/api/review/'+state['workflow_id']+'/resume'
    assert client.post(path,headers=foreign,json=decision(state)).status_code==404
    r = client.post(path,headers=h,json=decision(state,'rejected'))
    assert r.json()['status']=='REJECTED' and r.json()['creative_contract'] is None
    assert len(approvals(service,data))==1


def test_application_stale_and_insufficient(application):
    from db.services import LiveRunData
    client,h,service,p,data,app = application
    state = begin(client,h,data).json()
    run = service.run_for_user(run_id=UUID(data['subject']),user_id=UUID(data['actor']))
    service.create_live_run(LiveRunData(brand_id=run.brand_id,user_id=UUID(data['actor']),kind='strategy',status='succeeded',input_profile_id=run.input_profile_id,requested_config=run.requested_config,effective_config=run.effective_config,output=run.output,output_schema_version=1))
    r = client.post('/api/review/'+state['workflow_id']+'/resume',headers=h,json=decision(state))
    assert r.status_code==409 and r.json()['detail']['code']=='STALE_SUBJECT'
    assert not approvals(service,data)
    thin = seed(service,p,insufficient=True)
    from api.user_auth import issue_token
    # Real persisted second user; signed app token resolves through the normal auth dependency.
    r = begin(client,{'Authorization':'Bearer '+issue_token(thin['actor'])},thin)
    assert r.status_code==200 and r.json()['status']=='BLOCKED' and r.json()['creative_contract'] is None
    assert r.json()['required_actions']


def test_failed_first_start_is_honest_not_replayed(database,tmp_path,monkeypatch):
    import workflows.runtime as runtime
    service,p = connect(database); data = seed(service,p)
    setup(tmp_path)
    workflow = ReviewWorkflow(tmp_path,p,actor_id=data['actor'])
    original = runtime.build_graph
    def fail(*a,**kw): raise RuntimeError('PRIVATE_FAILURE')
    monkeypatch.setattr(runtime,'build_graph',fail)
    with pytest.raises(RuntimeError): workflow.start(domain_key=data['domain'],subject_id=data['subject'],reuse=True)
    monkeypatch.setattr(runtime,'build_graph',original)
    result = workflow.start(domain_key=data['domain'],subject_id=data['subject'],reuse=True)
    assert result['status']=='FAILED' and result['reasons']==['START_FAILED']
    assert not approvals(service,data)
    with pytest.raises(ReviewBoundaryError,match='START_FAILED'):
        workflow.resume(result['workflow_id'],{'subject_id':data['subject'],'subject_hash':'0'*64,'decision':'approved'})


def test_application_sdk_capture_and_export_failure(application):
    from api.routes.review import get_review_telemetry
    from workflows.telemetry import Telemetry
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    client,h,service,p,data,app = application
    exporter = InMemorySpanExporter(); telemetry = sdk_telemetry(exporter)
    app.dependency_overrides[get_review_telemetry] = lambda:Telemetry(telemetry.client)
    try:
        state = begin(client,h,data).json()
        r = client.post('/api/review/'+state['workflow_id']+'/resume',headers=h,json=decision(state))
        assert r.json()['status']=='READY_FOR_CREATIVE'
        spans = exporter.get_finished_spans()
        assert len(spans)>=10
        assert len({s.context.trace_id for s in spans})==1
        raw = str([dict(s.attributes) for s in spans])
        assert data['actor'] not in raw and data['subject'] not in raw and data['domain'] not in raw
        assert 'studio.example.test' not in raw and 'creative_contract' not in raw
        assert not any('usage' in k or 'cost_details' in k for s in spans for k in s.attributes)
    finally:
        app.dependency_overrides.clear()
        telemetry.client.shutdown()


def test_application_start_failure_and_strict_inputs(application,monkeypatch):
    import workflows.runtime as runtime
    client,h,service,p,data,app = application
    original = runtime.build_graph
    def fail(*a,**kw): raise RuntimeError('PRIVATE_INTERNAL_PATH_OR_SECRET')
    monkeypatch.setattr(runtime,'build_graph',fail)
    r = begin(client,h,data)
    assert r.status_code==503 and 'PRIVATE_' not in r.text
    monkeypatch.setattr(runtime,'build_graph',original)
    r = begin(client,h,data)
    assert r.status_code==200 and r.json()['status']=='FAILED'
    assert r.json()['reasons']==['START_FAILED']
    assert not approvals(service,data)
    path='/api/review/'+r.json()['workflow_id']+'/resume'
    body={'subject_id':data['subject'],'subject_hash':'0'*64,'decision':'approved'}
    assert client.post(path,headers=h,json=body).status_code==409
    assert client.post(path,headers=h,json={**body,'owner_id':data['actor']}).status_code==422
    assert client.post(path,headers=h,json={**body,'decision':'approve_latest'}).status_code==422
    assert client.post('/api/review/start',headers=h,json={'domain_key':data['domain'],'subject_id':data['subject'],'directory':'/tmp/attacker'}).status_code==422


def test_application_export_failure_isolated(application):
    from opentelemetry.sdk.trace.export import SpanExporter
    from api.routes.review import get_review_telemetry
    from workflows.telemetry import Telemetry
    class Failing(SpanExporter):
        def export(self,spans): raise RuntimeError('PRIVATE_EXPORT_FAILURE')
        def shutdown(self): pass
    client,h,service,p,data,app = application
    telemetry=sdk_telemetry(Failing())
    app.dependency_overrides[get_review_telemetry]=lambda:Telemetry(telemetry.client)
    try:
        state=begin(client,h,data).json()
        r=client.post('/api/review/'+state['workflow_id']+'/resume',headers=h,json=decision(state))
        assert r.status_code==200 and r.json()['status']=='READY_FOR_CREATIVE'
        assert len(approvals(service,data))==1
    finally:
        app.dependency_overrides.clear()
        telemetry.client.shutdown()
