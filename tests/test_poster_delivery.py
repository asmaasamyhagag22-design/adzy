"""Exact persisted revision delivery through real auth/PostgreSQL; fake providers only."""
import hashlib
from uuid import UUID, uuid4
import pytest
from test_review_workflow import database, newer
from test_app_review_graph import application
from test_poster_operation_boundary import ready, body, submit
from poster_boundary_support import install


def delivery(ready, op, mode='final', checksum=None, headers=None):
    c,h,*_=ready
    run=op['generation_run_id']
    check=c.get(f'/api/poster/runs/{run}/delivery',headers=h)
    digest=checksum or (check.json().get('checksum') if check.status_code==200 else '0'*64)
    return c.get(f'/api/poster/runs/{run}/download',headers=headers or h,
                 params={'mode':mode,'attempt_id':op['generation_attempt_id'],'checksum':digest})


def test_final_download_exact_bytes_refresh_and_foreign_owner(ready,monkeypatch):
    c,h,service,p,data,app,tmp=ready
    _,_,sdk=install(monkeypatch,tmp)
    op=submit(ready,body(data)); run=op['generation_run_id']
    check=c.get(f'/api/poster/runs/{run}/delivery',headers=h)
    assert check.status_code==200,check.text
    assert check.json()['final_eligible'] is True
    download=delivery(ready,op)
    assert download.status_code==200,download.text
    assert hashlib.sha256(download.content).hexdigest()==check.json()['checksum']
    assert download.content==(tmp/'candidate-0.png').read_bytes()
    assert download.headers['x-adzy-delivery']=='final'
    assert delivery(ready,op,checksum='0'*64).status_code==409
    restored=c.get('/api/poster/operations/latest',params={'domain_key':data['domain']},headers=h).json()
    assert restored['operation']['generation_attempt_id']==op['generation_attempt_id']
    foreign=c.post('/api/auth/signup',json={'email':uuid4().hex+'@example.invalid','password':'SyntheticPassword42'}).json()
    assert delivery(ready,op,headers={'Authorization':'Bearer '+foreign['access_token']}).status_code==404
    sdk.close()


@pytest.mark.parametrize('mode',['factual_draft','pass'])
def test_draft_and_changed_or_stale_revision_never_deliver_final(ready,monkeypatch,mode):
    c,h,service,p,data,app,tmp=ready
    _,_,sdk=install(monkeypatch,tmp)
    if mode=='factual_draft':
        from api import webapp_images
        original=webapp_images._render_real
        def unsupported(*args,**kwargs):
            rendered=original(*args,**kwargs)
            rendered.result.brief.headline='We never close'
            return rendered
        monkeypatch.setattr(webapp_images,'_render_real',unsupported)
    op=submit(ready,body(data))
    if mode=='factual_draft':
        assert op['state']=='draft'
        assert delivery(ready,op).status_code==409
        draft=delivery(ready,op,'draft');assert draft.status_code==200
        assert draft.headers['x-adzy-delivery']=='draft'
        assert 'draft-' in draft.headers['content-disposition']
    else:
        assert delivery(ready,op).status_code==200
        newer(service,data)
        assert delivery(ready,op).status_code==409
        assert delivery(ready,op,'draft').status_code==200
    sdk.close()


@pytest.mark.parametrize('change',['bytes','copy','evidence','selected','required_qa','unknown_with_asset','unknown_without_asset'])
def test_delivery_rechecks_persisted_artifact_and_assessments(ready,monkeypatch,change):
    from db.models import Run,Attempt
    from api import gallery_store
    c,h,service,p,data,app,tmp=ready
    _,_,sdk=install(monkeypatch,tmp)
    op=submit(ready,body(data));run_id=UUID(op['generation_run_id'])
    row=service.poster_gallery_result(user_id=UUID(data['actor']),run_id=run_id)
    if change=='bytes':
        monkeypatch.setattr(gallery_store,'load',lambda _:b'changed image bytes')
    else:
        with service._uow_factory() as uow:
            run=uow.session.get(Run,run_id)
            if change=='copy':run.output={**run.output,'authored_copy':{**run.output['authored_copy'],'headline':'We never close'}}
            if change=='evidence':run.effective_config={**run.effective_config,'creative_contract':{**run.effective_config['creative_contract'],'copy_evidence':[]}}
            if change=='selected':run.selected_attempt_id=None
            if change=='required_qa':
                attempt=uow.session.get(Attempt,UUID(op['generation_attempt_id']))
                attempt.qa={**attempt.qa,'visual':{}}
            if change.startswith('unknown'):
                run.operation_state='unknown'
                if change=='unknown_without_asset':run.selected_attempt_id=None
            uow.commit()
    response=delivery(ready,op)
    assert response.status_code==(200 if change=='unknown_with_asset' else 409),response.text
    # Delivery reads never rewrite historical acceptance.
    assert service.poster_gallery_result(user_id=UUID(data['actor']),run_id=run_id)==row
    sdk.close()


def test_visual_rejection_has_no_downloadable_artifact(ready,monkeypatch):
    _,_,sdk=install(monkeypatch,ready[-1],'qa_fail')
    op=submit(ready,body(ready[4]))
    assert op['state']=='failed'
    assert delivery(ready,op).status_code==404
    assert delivery(ready,op,'draft').status_code==404
    sdk.close()


def test_delivery_and_status_reads_do_not_consume_generation_quota(ready,monkeypatch):
    from api import auth
    c,h,service,p,data,app,tmp=ready
    _,_,sdk=install(monkeypatch,tmp)
    request=body(data);op=submit(ready,request)
    # Exercise actual throttle dependency; pytest ordinarily bypasses it.
    monkeypatch.delenv('PYTEST_CURRENT_TEST',raising=False)
    from collections import deque
    import time
    monkeypatch.setitem(auth._hits,'spend:'+data['actor'],deque([time.monotonic()]*auth._SPEND_RATE_MAX))
    assert delivery(ready,op).status_code==200
    assert c.get('/api/poster/operations/'+request['operation_key'],headers=h).status_code==200
    assert c.get('/api/poster/operations/latest',params={'domain_key':data['domain']},headers=h).status_code==200
    assert c.post('/api/generate',headers=h,json=body(data)).status_code==429
    sdk.close()
