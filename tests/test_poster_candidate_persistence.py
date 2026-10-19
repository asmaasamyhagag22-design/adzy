"""Canonical HTTP + SQL + fake SDK; rejected preview survives an admission stop and refresh."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4,UUID
from test_poster_operation_boundary import ready,body
from test_review_workflow import database
from test_app_review_graph import application


def test_rejected_qa_and_bytes_survive_budget_stop_refresh_and_foreign_owner(ready,monkeypatch,tmp_path):
    from poster_boundary_support import install
    from api import webapp_images
    from poster import oneshot
    from poster.paid_boundary import checkpoint_candidate
    from poster.schemas import PosterBrief,PosterDesignSpec
    from poster.vision_qa import _policy_from_verdict,decide_poster_acceptance
    counter,sent,sdk=install(monkeypatch,tmp_path,'qa_fail')
    original=webapp_images._render_real
    monkeypatch.setenv('ADZY_POSTER_CALL_ESTIMATES_JSON','{"generate_content:gemini-2.5-flash-image":"0.4"}')
    def render(brand,**kwargs):
        real=original(brand,**kwargs)
        path=tmp_path/'rejected.png';path.write_bytes(real.png)
        qa=real.result.qa.model_copy(update={'reason':'CTA clipped; https://private.example/path contact@example.test'})
        policy=_policy_from_verdict(qa)
        decision=decide_poster_acceptance(qa,attempt_identity=qa.attempt_identity,required_policy=policy)
        checkpoint_candidate(path,identity=qa.attempt_identity,policy=policy,qa=qa,acceptance=decision,
            brief=PosterBrief(business_name='Test',headline='Rejected candidate'),spec=PosterDesignSpec(),
            local_checks={'source':'LOCAL','defects':['LOCAL_CLIPPING']},background_path=path)
        # The second admission really fails before SDK send; the stored rejected candidate remains.
        oneshot.generate_oneshot_poster('SYNTHETIC',tmp_path/'never.png',client=sdk,model='gemini-2.5-flash-image')
        raise AssertionError('must stop')
    monkeypatch.setattr(webapp_images,'_render_real',render)
    client,h,service,p,data,*_=ready
    req=body(data)
    op=client.post('/api/generate',headers=h,json=req).json()
    assert op['state']=='failed' and len(sent)==1
    assert op['reason_code']=='POSTER_STAGE_BUDGET_EXHAUSTED'
    refreshed=client.get('/api/poster/operations/'+req['operation_key'],headers=h).json()
    candidate=refreshed['candidates'][0]
    assert candidate['visual_qa']['overall_pass'] is False
    assert 'CTA clipped' in candidate['visual_qa']['reason'] and 'private.example' not in candidate['visual_qa']['reason']
    assert candidate['visual_qa']['policy_version'] and candidate['identity']['content_sha256']==candidate['checksum']
    url='/api/poster/operations/'+req['operation_key']+'/candidates/'+candidate['identity']['candidate_id']+'/preview'
    image=client.get(url,headers=h)
    assert image.status_code==200 and image.content==(tmp_path/'rejected.png').read_bytes()
    assert client.get(url+'?as_json=true',headers=h).json()['accepted'] is False
    assert client.get('/api/poster/runs/'+op['generation_run_id']+'/download',params={'attempt_id':op['generation_attempt_id'],'checksum':candidate['checksum'],'mode':'final'},headers=h).status_code==404
    foreign=client.post('/api/auth/signup',json={'email':uuid4().hex+'@test.invalid','password':'SyntheticPassword42'}).json()
    assert client.get(url,headers={'Authorization':'Bearer '+foreign['access_token']}).status_code==404
    assert client.post('/api/generate',headers=h,json=req).json()['state']=='failed' and len(sent)==1
    sdk.close()
