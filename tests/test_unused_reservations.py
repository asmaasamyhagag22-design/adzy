"""Actual PostgreSQL accounting actions; synthetic calls only, no providers."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID, uuid4
import json
import pytest
from sqlalchemy import select, func
from db.models import Run, UsageRecord, ReservationAdjustment, Job
from db.poster_operations import PosterOperationError, PosterOperations
from poster.strategy_contract import CreativeContractCompiler
from test_poster_operation_boundary import ready
from test_app_review_graph import application
from test_review_workflow import database


def operation(ready, monkeypatch, *, state='received', terminal=True):
    service,p,data=ready[2:5];ops=PosterOperations(p);key='unused_'+uuid4().hex
    monkeypatch.setenv('ADZY_POSTER_RESERVATION_USD','0.6')
    monkeypatch.setenv('ADZY_POSTER_CALL_ESTIMATES_JSON',json.dumps({'extract:test':'0.2'}))
    compile_contract=lambda:CreativeContractCompiler().compile(p.canonical_strategy_context(
        domain_key=data['domain'],user_id=data['actor'],run_id=data['subject']))
    request={'domain_key':data['domain'],'purpose':'semantic_shadow'}
    run,_,_=ops.admit(data['actor'],key,request,compile_contract)
    call=ops.start_call(run,'extract','test')
    if state!='sending':ops.finish_call(call,state)
    if terminal:ops.finish(run,'failed')
    return ops,data,key,run,call,compile_contract,request


def test_closed_unused_support_released_but_no_usage_or_settlement_invented(ready,monkeypatch):
    ops,data,key,run,call,*_=operation(ready,monkeypatch)
    first=ops.release_unused_reservation(data['actor'],key,reason='Support never began; operation terminal')
    assert Decimal(first['unused_reservation_released_usd'])==Decimal('.4')
    assert Decimal(first['retained_attempted_exposure_usd'])==Decimal('.2')
    status=ops.status(data['actor'],key,recover=False)
    assert status['known_usage_usd'] is None and status['settled_usd'] is None
    assert status['estimated_usd']=='0.200000000' and len(status['calls'])==1
    with ops.factory() as uow:
        c=uow.session.get(UsageRecord,UUID(call));assert c.cost_usd is None
        row=uow.session.get(Run,UUID(run));row.operation_state='running';uow.commit()
    # Durable close still blocks an accidental state revival, not just the current status string.
    with pytest.raises(PosterOperationError):ops.start_call(run,'extract','test')


@pytest.mark.parametrize('state,terminal,code',[
    ('sending',False,'RESERVATION_OPERATION_NOT_CLOSED'),
    ('unknown',True,'RESERVATION_OPERATION_NOT_CLOSED'),
    ('received',False,'RESERVATION_OPERATION_NOT_CLOSED'),
])
def test_pending_or_ambiguous_and_open_operations_retain_entire_hold(ready,monkeypatch,state,terminal,code):
    ops,data,key,run,*_=operation(ready,monkeypatch,state=state,terminal=terminal)
    with pytest.raises(PosterOperationError) as e:ops.release_unused_reservation(data['actor'],key,reason='test')
    assert e.value.detail['code']==code
    assert ops.status(data['actor'],key,recover=False)['reserved_usd']=='0.600000000'


def test_terminal_with_ambiguous_record_or_pending_job_cannot_release(ready,monkeypatch):
    ops,data,key,run,call,*_=operation(ready,monkeypatch)
    with ops.factory() as uow:
        c=uow.session.get(UsageRecord,UUID(call));c.state='unknown';uow.commit()
    with pytest.raises(PosterOperationError,match='RESERVATION_DISPATCH_EXPOSURE_UNRESOLVED'):
        ops.release_unused_reservation(data['actor'],key,reason='test')
    with ops.factory() as uow:
        c=uow.session.get(UsageRecord,UUID(call));c.state='received'
        uow.session.add(Job(run_id=UUID(run),owner_user_id=UUID(data['actor']),access_mode='user',status='queued'))
        uow.commit()
    with pytest.raises(PosterOperationError,match='RESERVATION_WORKER_OR_CONTINUATION_PENDING'):
        ops.release_unused_reservation(data['actor'],key,reason='test')


def test_known_cost_above_estimate_is_retained(ready,monkeypatch):
    ops,data,key,run,call,*_=operation(ready,monkeypatch)
    with ops.factory() as uow:
        uow.session.get(UsageRecord,UUID(call)).cost_usd=Decimal('.35');uow.commit()
    adjustment=ops.release_unused_reservation(data['actor'],key,reason='Keep observed cost')
    assert Decimal(adjustment['after_usd'])==Decimal('.35')
    assert Decimal(adjustment['unused_reservation_released_usd'])==Decimal('.25')
    assert ops.status(data['actor'],key,recover=False)['settled_usd'] is None


def test_concurrent_and_repeated_adjustment_only_releases_once(ready,monkeypatch):
    ops,data,key,run,*_=operation(ready,monkeypatch)
    def adjust(_):return ops.release_unused_reservation(data['actor'],key,reason='Concurrent same action')
    with ThreadPoolExecutor(3) as pool:rows=list(pool.map(adjust,range(3)))
    assert len({r['adjustment_id'] for r in rows})==1
    assert sum(not r['idempotent_replay'] for r in rows)==1
    assert adjust(0)['idempotent_replay']
    with ops.factory() as uow:
        assert uow.session.scalar(select(func.count()).select_from(ReservationAdjustment).where(ReservationAdjustment.run_id==UUID(run)))==1
    assert ops.status(data['actor'],key,recover=False)['reserved_usd']=='0.200000000'


def test_adjustment_serializes_with_concurrent_admission_under_same_owner_cap(ready,monkeypatch):
    ops,data,key,run,call,compile_contract,request=operation(ready,monkeypatch)
    monkeypatch.setenv('ADZY_POSTER_BUDGET_USD','0.8')
    def adjust():return ops.release_unused_reservation(data['actor'],key,reason='Closed support')
    def admit():
        try:return ops.admit(data['actor'],'new_'+uuid4().hex,request,compile_contract)[0]
        except PosterOperationError as e:return e.detail['code']
    with ThreadPoolExecutor(3) as pool:
        futures=[pool.submit(adjust),pool.submit(admit),pool.submit(admit)]
        results=[f.result() for f in futures]
    assert sum(isinstance(r,str) and r!='POSTER_BUDGET_EXHAUSTED' for r in results)<=1
    with ops.factory() as uow:
        held=uow.session.scalar(select(func.sum(Run.reserved_usd)).where(Run.created_by_user_id==UUID(data['actor']),Run.operation_key.is_not(None)))
        assert held<=Decimal('.8')


def test_owner_locked_request_limit_counts_attempts_and_never_resets(ready,monkeypatch):
    ops,data,key,run,call,*_=operation(ready,monkeypatch,terminal=False)
    with ops.factory() as uow:
        row=uow.session.get(Run,UUID(run))
        row.requested_config={**row.requested_config,'request':{**row.requested_config['request'],
            'service_request_authorization':{'cumulative_limit':3,'historical_metadata_requests':1,'new_dispatch_limit':2}}}
        uow.commit()
    second=ops.start_call(run,'extract','test');ops.finish_call(second,'rejected')
    with pytest.raises(PosterOperationError,match='CUMULATIVE_SERVICE_REQUEST_LIMIT_EXHAUSTED'):
        ops.start_call(run,'extract','test')
    with ops.factory() as uow:
        assert uow.session.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.run_id==UUID(run)))==2
