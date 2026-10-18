"""Real graph + PostgreSQL application services + local SDK export capture. No providers."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import UUID, uuid4
import pytest

from workflows.contracts import Decision
from workflows.demo import init, connect, seed, isolate, command
from workflows.runtime import ReviewWorkflow, setup
from workflows.services import ReviewBoundaryError
from workflows.telemetry import Telemetry, mask


@pytest.fixture(scope='module')
def database(tmp_path_factory):
    isolate()
    directory = tmp_path_factory.mktemp('review')/'demo'
    init(directory)
    manifest = json.loads((directory/'demo.json').read_text())
    yield manifest
    command(['docker','stop',manifest['container']])


@pytest.fixture
def review(database,tmp_path):
    service,provider = connect(database)
    data = seed(service,provider)
    setup(tmp_path)
    workflow = ReviewWorkflow(tmp_path,provider,actor_id=data['actor'])
    state = workflow.start(domain_key=data['domain'],subject_id=data['subject'])
    assert state['status']=='NEEDS_REVIEW', state
    assert state['pending_nodes']==['human_review']
    return service,provider,data,workflow,state


def decision(state,choice='approved'):
    return {'subject_id':state['subject_id'],'subject_hash':state['subject_hash'],'decision':choice}


def approvals(service,data):
    from db.models import Approval
    from sqlalchemy import select
    with service._uow_factory() as uow:
        rows = list(uow.session.scalars(select(Approval).where(Approval.subject_run_id==UUID(data['subject']))))
        uow.session.expunge_all()
        return rows


def test_real_tools_pause_and_exact_approval_idempotency(review):
    service,provider,data,workflow,state = review
    assert not approvals(service,data)
    result = workflow.resume(state['workflow_id'],decision(state))
    assert result['status']=='READY_FOR_CREATIVE',result
    assert result['contract_id'] and len(result['contract_hash'])==64
    assert {e['name'] for e in workflow.telemetry.events if 'name' in e} >= {
        'get_review_subject','inspect_research_readiness','inspect_creative_contract_requirements'}
    rows = approvals(service,data)
    assert len(rows)==1 and str(rows[0].subject_run_id)==data['subject']
    assert str(rows[0].decided_by_user_id)==data['actor']
    assert workflow.resume(state['workflow_id'],decision(state))['contract_id']==result['contract_id']
    assert len(approvals(service,data))==1


def test_rejection_is_terminal(review):
    service,provider,data,workflow,state = review
    result = workflow.resume(state['workflow_id'],decision(state,'rejected'))
    assert result['status']=='REJECTED' and not result.get('contract_id')
    assert approvals(service,data)[0].decision=='rejected'
    with pytest.raises(ReviewBoundaryError,match='DECISION_CHANGED'):
        workflow.resume(state['workflow_id'],decision(state))


def test_insufficient_routes_to_actions(database,tmp_path):
    service,provider = connect(database)
    data = seed(service,provider,insufficient=True)
    setup(tmp_path)
    result = ReviewWorkflow(tmp_path,provider,actor_id=data['actor']).start(domain_key=data['domain'],subject_id=data['subject'])
    assert result['status']=='BLOCKED' and result['required_actions'] and not result['pending_nodes']
    assert not approvals(service,data)


def newer(service,data):
    from db.services import LiveRunData
    run = service.run_for_user(run_id=UUID(data['subject']),user_id=UUID(data['actor']))
    return service.create_live_run(LiveRunData(brand_id=run.brand_id,user_id=UUID(data['actor']),
        kind='strategy',status='succeeded',input_profile_id=run.input_profile_id,
        requested_config=run.requested_config,effective_config=run.effective_config,output=run.output,output_schema_version=1))


def test_stale_never_approves_latest(review):
    service,provider,data,workflow,state = review
    new = newer(service,data)
    result = workflow.resume(state['workflow_id'],decision(state))
    assert result['status']=='BLOCKED' and result['reasons']==['STALE_SUBJECT']
    assert not approvals(service,data)
    assert service.decision_for_current_strategy(brand_id=new.brand_id,user_id=UUID(data['actor'])) is None


def test_owner_isolation_for_all_entrypoints(review):
    service,provider,data,workflow,state = review
    other = service.create_user(email=f'{uuid4().hex}@synthetic.invalid',hashed_password='DISABLED')
    foreign = ReviewWorkflow(workflow.directory,provider,actor_id=str(other.id))
    for action in [lambda:foreign.status(state['workflow_id']),
                   lambda:foreign.resume(state['workflow_id'],decision(state)),
                   lambda:foreign.start(domain_key=data['domain'],subject_id=data['subject'])]:
        with pytest.raises(ReviewBoundaryError,match='NOT_VISIBLE'): action()
    assert not approvals(service,data)


def test_wrong_review_hash_never_writes(review):
    service,provider,data,workflow,state = review
    bad = {**decision(state),'subject_hash':'0'*64}
    with pytest.raises(ReviewBoundaryError,match='REVIEW_SUBJECT_MISMATCH'):
        workflow.resume(state['workflow_id'],bad)
    assert not approvals(service,data)


def test_approval_commit_before_checkpoint_recovery(review,database,tmp_path):
    service,provider,data,workflow,state = review
    payload = {**database,**data,'directory':str(workflow.directory),'state':state}
    path = tmp_path/'crash-input.json';path.write_text(json.dumps(payload))
    script = '''
import json,sys,os
from workflows.demo import isolate,connect
isolate()
m=json.load(open(sys.argv[1])); _,p=connect(m)
from workflows.runtime import ReviewWorkflow
w=ReviewWorkflow(m['directory'],p,actor_id=m['actor'],after_approval=lambda:os._exit(73))
s=m['state']; w.resume(s['workflow_id'],dict(subject_id=s['subject_id'],subject_hash=s['subject_hash'],decision='approved'))
'''
    crashed = subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True,text=True,timeout=60)
    assert crashed.returncode==73,crashed.stderr
    assert len(approvals(service,data))==1
    # Reconstruct graph and service, with a pending apply_decision node (not a second interrupt).
    recovered = ReviewWorkflow(workflow.directory,provider,actor_id=data['actor'])
    assert recovered.status(state['workflow_id'])['pending_nodes']==['apply_decision']
    result = recovered.resume(state['workflow_id'],decision(state))
    assert result['status']=='READY_FOR_CREATIVE',result
    assert len(approvals(service,data))==1


def test_cross_process_resume_and_no_external_network(review,database,tmp_path):
    service,provider,data,workflow,state = review
    payload = {**database,**data,'directory':str(workflow.directory),'state':state}
    path = tmp_path/'input.json';path.write_text(json.dumps(payload))
    script = '''
import json,sys,socket
from workflows.demo import isolate,connect
isolate()
m=json.load(open(sys.argv[1])); _,p=connect(m)
from workflows.runtime import ReviewWorkflow
w=ReviewWorkflow(m['directory'],p,actor_id=m['actor'])
s=m['state']; r=w.resume(s['workflow_id'],dict(subject_id=s['subject_id'],subject_hash=s['subject_hash'],decision='approved'))
print(json.dumps(r))
'''
    result = subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='READY_FOR_CREATIVE'
    assert len(approvals(service,data))==1


def sdk_telemetry(exporter):
    from workflows.telemetry import create_client
    client = create_client(public_key='pk-local-'+uuid4().hex,secret_key='sk-synthetic',base_url='http://127.0.0.1:9',
        span_exporter=exporter)
    telemetry = Telemetry(client)
    telemetry.correlate('synthetic-workflow')
    return telemetry


def test_real_sdk_observations_redaction_and_latency(review):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    t = sdk_telemetry(exporter)
    service,provider,data,w,state = review
    w.telemetry = t
    state = w.start(domain_key=data['domain'],subject_id=data['subject'])
    assert state['status']=='NEEDS_REVIEW'
    assert w.resume(state['workflow_id'],decision(state))['status']=='READY_FOR_CREATIVE'
    # Attack the SDK directly: proves mask applies to SDK-managed inputs/outputs, not a custom logger.
    with t.client.start_as_current_observation(name='redaction_probe', input={'document':'PRIVATE_EMAIL secret key image bytes'}) as span:
        span.update(output={'password':'PRIVATE_PASSWORD'},metadata={'pii':'PRIVATE_PII'})
    t.flush()
    spans = exporter.get_finished_spans()
    assert len(spans)>=5
    serialized = str([(s.name,dict(s.attributes)) for s in spans])
    assert 'PRIVATE_' not in serialized and 'studio.example.test' not in serialized
    assert 'langfuse' in str([s.instrumentation_scope.name for s in spans]).lower()
    assert all(s.end_time>=s.start_time for s in spans)
    graph_spans = [s for s in spans if s.name!='redaction_probe']
    assert len({s.context.trace_id for s in graph_spans})==1
    assert not any('usage' in key or 'cost_details' in key for s in spans for key in s.attributes)


def test_exporter_failure_cannot_change_result(review):
    from opentelemetry.sdk.trace.export import SpanExporter
    class Failing(SpanExporter):
        def export(self,spans): raise RuntimeError('synthetic export failure')
        def shutdown(self): pass
    service,provider,data,w,state = review
    w.telemetry = sdk_telemetry(Failing())
    assert w.resume(state['workflow_id'],decision(state))['status']=='READY_FOR_CREATIVE'
    assert len(approvals(service,data))==1


def test_diagram_is_actual_compiled_graph(review):
    service,provider,data,w,state = review
    with w._execution(state['workflow_id']) as (graph,config,services):
        diagram = graph.get_graph().draw_mermaid()
    saved = Path('docs/architecture/review_graph.mmd').read_text()
    assert diagram.rstrip()==saved.rstrip()


def test_already_approved_routes_without_another_review(review):
    service,provider,data,w,state = review
    w.resume(state['workflow_id'],decision(state))
    second = w.start(domain_key=data['domain'],subject_id=data['subject'])
    assert second['status']=='READY_FOR_CREATIVE' and not second['pending_nodes']
    with pytest.raises(ReviewBoundaryError,match='DECISION_CHANGED'):
        w.resume(second['workflow_id'],decision(second,'rejected'))
    assert len(approvals(service,data))==1


def test_status_rechecks_authoritative_rejection(review):
    service,provider,data,w,state = review
    w.resume(state['workflow_id'],decision(state))
    provider.decide_strategy(domain_key=data['domain'],user_id=data['actor'],run_id=data['subject'],decision='rejected')
    result = w.status(state['workflow_id'])
    assert result['status']=='BLOCKED' and not result['contract_id']
    result = w.resume(state['workflow_id'],decision(state))
    assert result['status']=='BLOCKED' and not result['contract_id']


def test_tools_reject_caller_identity_and_fail_closed(review):
    from pydantic import ValidationError
    from workflows.tools import inspection_tools
    from workflows.services import CampaignReviewServices
    service,provider,data,w,state = review
    services = CampaignReviewServices(provider,actor_id=data['actor'],domain_key=data['domain'],subject_id=data['subject'])
    tools = inspection_tools(services,Telemetry())
    with pytest.raises(ValidationError):
        tools['get_review_subject'].invoke({'actor_id':str(uuid4())})
    services.subject_id = str(uuid4())
    r = tools['get_review_subject'].invoke({})
    assert r=={'status':'ERROR','data':{},'error':'NOT_VISIBLE'}


def test_default_execution_has_no_external_attempts(review,monkeypatch):
    service,provider,data,w,state = review
    attempts = []
    original = socket.socket.connect
    def guard(sock,address):
        if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
            attempts.append(address[0])
            raise AssertionError('external call attempted')
        return original(sock,address)
    monkeypatch.setattr(socket.socket,'connect',guard)
    assert w.telemetry.client is None
    assert w.resume(state['workflow_id'],decision(state))['status']=='READY_FOR_CREATIVE'
    assert attempts==[]


def test_sdk_exception_redacted_before_export_and_segment_correlation():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    t = sdk_telemetry(exporter)
    with pytest.raises(ValueError):
        with t.observation('synthetic_failure'):
            raise ValueError('PRIVATE_EXCEPTION_CONTAINS_PII')
    # A later execution segment uses the same deterministic trace ID.
    t.correlate('synthetic-workflow')
    with t.observation('synthetic_resume'):
        t.outcome('READY_FOR_CREATIVE')
    t.flush()
    spans = exporter.get_finished_spans()
    assert len(spans)==2 and len({s.context.trace_id for s in spans})==1
    assert 'PRIVATE_' not in str([dict(s.attributes) for s in spans])
    assert 'ERROR' in str(dict(spans[0].attributes))


def test_explicit_start_recovers_same_undecided_subject_after_swot_review(review,tmp_path):
    from sqlalchemy import select,delete
    from db.models import Approval,Run
    service,p,data,_,_=review
    brand=p._brand_row(domain_key=data['domain'],user_id=data['actor'])
    swot=service.current_successful_run(brand_id=brand.id,kind='swot',user_id=UUID(data['actor']))
    # Remove only this disposable fixture's simulated SWOT approval to reproduce initial preparation.
    with service._uow_factory() as uow:
        uow.session.execute(delete(Approval).where(Approval.subject_run_id==swot.id));uow.commit()
    directory=tmp_path/'pending-swot';setup(directory)
    workflow=ReviewWorkflow(directory,p,actor_id=data['actor'])
    first=workflow.start(domain_key=data['domain'],subject_id=data['subject'],reuse=True)
    assert first['status']=='BLOCKED' and first['reasons']==['SWOT_NOT_APPROVED']
    assert workflow.start(domain_key=data['domain'],subject_id=data['subject'],reuse=True)['status']=='BLOCKED'
    assert not approvals(service,data)
    service.approve_current_swot(brand_id=brand.id,user_id=UUID(data['actor']),expected_run_id=swot.id)
    second=workflow.start(domain_key=data['domain'],subject_id=data['subject'],reuse=True)
    assert second['workflow_id']==first['workflow_id']
    assert second['status']=='NEEDS_REVIEW' and second['pending_nodes']==['human_review']
    assert not approvals(service,data)  # no automatic strategy decision during repair
    assert workflow.start(domain_key=data['domain'],subject_id=data['subject'],reuse=True)['workflow_id']==first['workflow_id']


def test_compiler_recovery_keeps_checkpoint_exact_decision_and_never_reapproves(review, monkeypatch):
    from workflows.services import CampaignReviewServices
    from poster.strategy_contract import CreativeContractError
    service, provider, data, w, state = review
    original = CampaignReviewServices.compile
    def blocked(self, state):
        raise CreativeContractError('PROOF_MESSAGE_MISMATCH', 'SYNTHETIC compiler block')
    monkeypatch.setattr(CampaignReviewServices, 'compile', blocked)
    failed = w.resume(state['workflow_id'], decision(state))
    assert failed['status'] == 'BLOCKED' and failed['reasons'] == ['PROOF_MESSAGE_MISMATCH']
    before = [(r.id, r.decided_at) for r in approvals(service, data)]
    monkeypatch.setattr(CampaignReviewServices, 'compile', original)
    monkeypatch.setattr(CampaignReviewServices, 'decide', lambda *a: pytest.fail('approval replay'))
    recovered = w.start(domain_key=data['domain'], subject_id=data['subject'], reuse=True)
    assert recovered['status'] == 'READY_FOR_CREATIVE'
    assert recovered['workflow_id'] == state['workflow_id']
    assert recovered['subject_hash'] == state['subject_hash']
    assert recovered['decision'] == failed['decision']
    assert [(r.id, r.decided_at) for r in approvals(service, data)] == before


def test_compiler_recovery_cannot_refresh_changed_material_hashes(review, monkeypatch):
    from workflows.services import CampaignReviewServices
    from poster.strategy_contract import CreativeContractError
    from db.models import Run
    service, provider, data, w, state = review
    original = CampaignReviewServices.compile
    monkeypatch.setattr(CampaignReviewServices, 'compile', lambda *a: (_ for _ in ()).throw(
        CreativeContractError('PROOF_MESSAGE_MISMATCH', 'SYNTHETIC compiler block')))
    w.resume(state['workflow_id'], decision(state))
    monkeypatch.setattr(CampaignReviewServices, 'compile', original)
    with service._uow_factory() as uow:
        run = uow.session.get(Run, UUID(data['subject']))
        run.output = {**run.output, 'modified_metadata': 'cannot become fresh approval'}
        uow.commit()
    recovered = w.start(domain_key=data['domain'], subject_id=data['subject'], reuse=True)
    assert recovered['status'] == 'BLOCKED' and recovered['reasons'] == ['STALE_SUBJECT']
    assert recovered['subject_hash'] == state['subject_hash']
    assert len(approvals(service, data)) == 1


@pytest.mark.parametrize('final_server_available', [True, False])
def test_demo_never_migrates_against_temporary_unix_only_postgres(tmp_path, monkeypatch, final_server_available):
    """Docker's init server accepts Unix probes before the published TCP server starts."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    import workflows.demo as demo
    import workflows.runtime as runtime
    import sqlalchemy
    from alembic import command as migrations
    state = {'tcp_probes': 0, 'tcp_ready': False, 'migrations': 0, 'stopped': False}

    def docker_command(args):
        if args[:2] == ['docker', 'port']:
            return '127.0.0.1:23765'
        if args[:2] == ['docker', 'stop']:
            state['stopped'] = True
        return 'SYNTHETIC_CONTAINER'

    def readiness(args, **kwargs):
        # The temporary bootstrap server already accepts Unix-socket connections.
        if '-h' not in args:
            return SimpleNamespace(returncode=0)
        state['tcp_probes'] += 1
        state['tcp_ready'] = final_server_available and state['tcp_probes'] >= 3
        return SimpleNamespace(returncode=0 if state['tcp_ready'] else 1)

    class Engine:
        def begin(self):
            assert state['tcp_ready'], 'migrations started while only the temporary server was ready'
            return nullcontext(object())

    def migrate(*args):
        assert state['tcp_ready']
        state['migrations'] += 1

    monkeypatch.setenv('BASEERA_DATABASE_URL', '')
    monkeypatch.setattr(demo, 'command', docker_command)
    monkeypatch.setattr(demo.subprocess, 'run', readiness)
    monkeypatch.setattr(demo.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(sqlalchemy, 'create_engine', lambda *args, **kwargs: Engine())
    monkeypatch.setattr(migrations, 'upgrade', migrate)
    monkeypatch.setattr(demo, 'connect', lambda manifest: (object(), object()))
    monkeypatch.setattr(demo, 'seed', lambda *args: {})
    monkeypatch.setattr(runtime, 'setup', lambda directory: None)
    if final_server_available:
        assert demo.init(tmp_path/'demo')['initialized'] is True
        assert state['tcp_probes'] == 3 and state['migrations'] == 1
    else:
        with pytest.raises(RuntimeError, match='PostgreSQL startup failed'):
            demo.init(tmp_path/'demo')
        assert state['migrations'] == 0 and state['stopped']
