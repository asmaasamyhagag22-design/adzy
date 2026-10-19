"""Real HTTP/PostgreSQL/local Chromium; zero model dispatch; synthetic source assertions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, func
from test_review_workflow import database, newer
from test_app_review_graph import application
from test_poster_operation_boundary import ready, body, submit
from db.models import Run, Attempt, UsageRecord, GalleryEntry


@pytest.fixture
def original(ready, monkeypatch):
    from workflows.synthetic_poster import install
    from api import webapp_images
    install('accepted')
    # All model entry points are tripwires; the demo compositor is local.
    from business_profile.llm import caller
    monkeypatch.setattr(caller.GeminiCaller,'__call__',lambda *a,**k:pytest.fail('PROVIDER_DISPATCH'))
    op=submit(ready,body(ready[4]));assert op['state']=='accepted',op
    yield op


def path(op,action='copy-editor'):
    return '/api/poster/runs/'+op['generation_run_id']+'/'+action


def ctx(ready,op):
    c,h,*_=ready;r=c.get(path(op),headers=h);assert r.status_code==200,r.text;return r.json()


def request(context,**copy):
    return {'copy':{**context['copy'],**copy},'expected_base_hash':context['base_hash'],
        'expected_head_run_id':context['head_run_id'],'idempotency_key':uuid4().hex}


def save(ready,op,req):
    return ready[0].post(path(op,'copy-revisions'),headers=ready[1],json=req)


def test_independent_revision_same_background_no_calls_refresh_and_delivery(ready,original):
    c,h,service,p,data,app,tmp=ready
    before=p.poster_gallery_result(user_id=data['actor'],run_id=original['generation_run_id'])
    context=ctx(ready,original);assert context['available']
    req=request(context,headline='We never close')
    r=save(ready,original,req);assert r.status_code==200,r.text
    saved=r.json();out=saved['result'];assert out['generation_run_id']!=original['generation_run_id']
    assert out['factual_assessment']['status']=='UNRESOLVED'
    assert not out['strategy_verification']['overall_pass']
    assert out['accepted'] is False and saved['checks']['visual_review']=='NOT_CHECKED'
    assert saved['lineage']['background_sha256']==before['editor_recipe']['background']['sha256']
    assert p.poster_gallery_result(user_id=data['actor'],run_id=original['generation_run_id'])==before
    assert p.poster_gallery_result(user_id=data['actor'],domain_key=data['domain'])['generation_run_id']==original['generation_run_id']
    restored=c.get(path(out),headers=h).json();assert restored['copy']['headline']=='We never close'
    assert restored['checks']==saved['checks']
    d=saved['delivery'];params={'attempt_id':d['generation_attempt_id'],'checksum':d['checksum']}
    assert c.get(path(out,'download'),headers=h,params={**params,'mode':'final'}).status_code==409
    download=c.get(path(out,'download'),headers=h,params={**params,'mode':'draft'})
    assert download.status_code==200 and hashlib.sha256(download.content).hexdigest()==d['checksum']
    with service._uow_factory() as uow: assert uow.session.scalar(select(func.count()).select_from(UsageRecord))==0


def test_response_loss_duplicates_stale_base_concurrency_and_explicit_branch(ready,original):
    context=ctx(ready,original);req=request(context)
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(lambda _:save(ready,original,req),range(2)))
    assert all(r.status_code==200 for r in results),[r.text for r in results]
    assert len({r.json()['run_id'] for r in results})==1
    assert save(ready,original,req).json()['run_id']==results[0].json()['run_id']
    assert save(ready,original,{**req,'copy':{**req['copy'],'headline':'Changed'}}).status_code==409
    assert save(ready,original,request(context)).json()['detail']['code']=='REVISION_CONFLICT'
    fresh=ctx(ready,original)
    assert save(ready,original,request(fresh)).json()['detail']['code']=='EXPLICIT_BRANCH_REQUIRED'
    branch=request(fresh);branch['branch']=True
    assert save(ready,original,branch).status_code==200
    child=results[0].json();bad=request(child);bad['expected_base_hash']='0'*64
    assert save(ready,child['result'],bad).json()['detail']['code']=='BASE_REVISION_CHANGED'


@pytest.mark.parametrize('text',['Guided specialist onboarding','We serve 99 teams','We onboard all teams','We onboard most teams','Guided OtherCompany onboarding'])
def test_exact_wording_and_numeric_quantifier_entity_guards(ready,original,text):
    c,h,*_=ready
    r=c.post(path(original,'copy-preview'),headers=h,json={**ctx(ready,original)['copy'],'headline':text})
    assert r.status_code==200,r.text
    a=r.json()['factual_assessment'];assert a['authored_copy_eligible'] is (text=='Guided specialist onboarding')
    assert r.json()['final_eligible'] is False


def test_ownership_substitution_stale_strategy_and_missing_background(ready,original,monkeypatch):
    c,h,service,p,data,*_=ready
    other=c.post('/api/auth/signup',json={'email':uuid4().hex+'@example.invalid','password':'SyntheticPassword42'}).json()
    foreign={'Authorization':'Bearer '+other['access_token']}
    for suffix in ('copy-editor','copy-preview','copy-revisions'):
        method=c.get if suffix=='copy-editor' else c.post
        kw={} if suffix=='copy-editor' else {'json':ctx(ready,original)['copy'] if suffix=='copy-preview' else request(ctx(ready,original))}
        assert method(path(original,suffix),headers=foreign,**kw).status_code==404
    req=request(ctx(ready,original));req['asset_id']=str(uuid4())
    assert save(ready,original,req).status_code==422
    from api import gallery_store
    load=gallery_store.load;bg=p.poster_gallery_result(user_id=data['actor'],run_id=original['generation_run_id'])['editor_recipe']['background']['storage_key']
    monkeypatch.setattr(gallery_store,'load',lambda key:None if key==bg else load(key))
    assert ctx(ready,original)['available'] is False
    assert save(ready,original,request(ctx(ready,original))).status_code==409
    monkeypatch.setattr(gallery_store,'load',load)
    newer(service,data)
    assert ctx(ready,original)['available'] is False
    assert save(ready,original,request(ctx(ready,original))).status_code==409


def test_render_failure_and_length_are_explicit_without_revision(ready,original,monkeypatch):
    import db.copy_editor as editor
    context=ctx(ready,original)
    assert save(ready,original,request(context,headline='a'*161)).status_code==422
    def fail(*a):raise RuntimeError('synthetic compositor error')
    monkeypatch.setattr(editor,'render',fail)
    r=save(ready,original,request(context));assert r.status_code==422
    assert r.json()['detail']['code']=='LOCAL_RENDER_FAILED'
    assert len(ctx(ready,original)['revisions'])==1


def test_exact_source_supported_but_outside_message_and_cta_promise(ready,original):
    from poster.copy_editor import evaluate
    from poster.strategy_contract import CreativeContract, AllowedClaim
    from poster.schemas import PosterBrief
    item=ready[3].poster_gallery_result(user_id=ready[4]['actor'],run_id=original['generation_run_id'])
    contract=CreativeContract.model_validate(item['creative_contract'])
    claim=contract.allowed_claims[0].model_copy(update={'claim_id':'outside','statement':'We open at 9', 'supporting_evidence_ids':['outside_e']})
    evidence={**contract.copy_evidence[0],'evidence_id':'outside_e','excerpt':'We open at 9'}
    changed=contract.model_copy(update={'allowed_claims':[*contract.allowed_claims,claim], 'copy_evidence':[*contract.copy_evidence,evidence]})
    brief=PosterBrief.model_validate({**item['editor_recipe']['brief'],'headline':'We open at 9'})
    a,s=evaluate(brief,changed)
    headline=next(p for p in a['propositions'] if p['location']=='headline')
    assert next(j for j in a['judgments'] if j['proposition_id']==headline['proposition_id'])['verdict']=='SUPPORTED'
    assert not s['overall_pass']
    a,s=evaluate(brief.model_copy(update={'headline':'Guided specialist onboarding','cta_text':'Guaranteed results'}),contract)
    assert not a['authored_copy_eligible'] and not s['overall_pass']


def test_xss_quotes_arabic_diacritics_emoji_and_overflow(ready,original):
    from poster.copy_editor import offline_html
    from poster.schemas import PosterBrief,PosterDesignSpec
    from playwright.sync_api import sync_playwright
    from pathlib import Path
    item=ready[3].poster_gallery_result(user_id=ready[4]['actor'],run_id=original['generation_run_id'])
    recipe=item['editor_recipe'];text='<script>window.pwned=1</script> \" مُعظَم Firefox 🙂 \u2067نص\u2069'
    from api import gallery_store
    background=Path(ready[-1])/'safe-background.png';background.write_bytes(gallery_store.load(recipe['background']['storage_key']))
    brief=PosterBrief.model_validate({**recipe['brief'],'headline':text})
    html=offline_html(brief,background,PosterDesignSpec.model_validate(recipe['spec']),recipe['inputs'])
    with sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page();requests=[]
        page.route('**/*',lambda route:(requests.append(route.request.url),route.abort())[-1])
        page.set_content(html)
        assert page.evaluate('typeof window.pwned')=='undefined'
        assert page.locator('script').count()==0
        assert 'مُعظَم' in page.locator('body').inner_text()
        assert not requests
        browser.close()
    preview=ready[0].post(path(original,'copy-preview'),headers=ready[1],json={**ctx(ready,original)['copy'],'headline':'W'*160})
    assert preview.status_code==200 and preview.json()['local_layout']['defects']


def test_fake_complete_checks_use_existing_final_delivery_checksum(ready,original):
    # Explicit fake reviewer fixtures, never a user-supplied QA route or live quality claim.
    from test_poster_acceptance_truth import _verdict
    from api.persistence import _truthful_poster_entry
    from api import gallery_store
    saved=save(ready,original,request(ctx(ready,original))).json();out=saved['result']
    c,h,service,p,data,*_=ready
    with service._uow_factory() as uow:
        run=uow.session.get(Run,UUID(out['generation_run_id']))
        attempt=uow.session.get(Attempt,UUID(out['generation_attempt_id']))
        row=uow.session.scalar(select(GalleryEntry).where(GalleryEntry.metadata_json['generation_run_id'].astext==str(run.id)))
        payload=gallery_store.load(uow.session.get(__import__('db.models',fromlist=['Asset']).Asset,attempt.asset_id).storage_key)
        verdict=_verdict(payload=payload,attempt_id=str(attempt.id))
        revised,decision=_truthful_poster_entry({**run.output,'visual_qa':verdict.model_dump(mode='json')},payload=payload)
        assert revised['factual_eligible'] and revised['strategy_verification']['overall_pass']
        assert not revised['editor_checks']['local_layout']['defects']
        run.output=revised;run.selected_attempt_id=attempt.id;row.metadata_json=revised
        attempt.qa={**attempt.qa,'visual':revised['visual_qa'],'acceptance':decision.model_dump(mode='json')};attempt.qa_passed=True
        uow.commit()
    d=c.get(path(out,'delivery'),headers=h).json();assert d['final_eligible'],d
    final=c.get(path(out,'download'),headers=h,params={'mode':'final','attempt_id':d['generation_attempt_id'],'checksum':d['checksum']})
    assert final.status_code==200 and hashlib.sha256(final.content).hexdigest()==d['checksum']


def test_quantifier_number_entity_change_against_supported_quotation(ready,original):
    from poster.claim_assessment import assess_copy
    from poster.strategy_contract import CreativeContract
    item=ready[3].poster_gallery_result(user_id=ready[4]['actor'],run_id=original['generation_run_id'])
    contract=CreativeContract.model_validate(item['creative_contract'])
    quote='Studio helps most teams in 9 regions'
    claim=contract.allowed_claims[0].model_copy(update={'claim_id':'guard','statement':quote,'supporting_evidence_ids':['guard_e']})
    evidence={**contract.copy_evidence[0],'evidence_id':'guard_e','excerpt':quote}
    contract=contract.model_copy(update={'allowed_claims':[claim],'copy_evidence':[evidence]})
    for text,expected in [(quote,True),(quote.replace('most','all'),False),(quote.replace('9','10'),False),(quote.replace('Studio','OtherCompany'),False)]:
        a=assess_copy(brief={**item['authored_copy'],'headline':text},contract=contract)
        assert a.authored_copy_eligible is expected


def test_foreign_parent_substitution_and_stale_profile_fail_closed(ready,original):
    from db.models import Brand
    c,h,service,p,data,*_=ready
    saved=save(ready,original,request(ctx(ready,original))).json();out=saved['result']
    with service._uow_factory() as uow:
        run=uow.session.get(Run,UUID(out['generation_run_id']))
        row=uow.session.scalar(select(GalleryEntry).where(GalleryEntry.metadata_json['generation_run_id'].astext==str(run.id)))
        snapshot=deepcopy(row.metadata_json)
        revised={**snapshot,'copy_revision':{**snapshot['copy_revision'],'parent_run_id':str(uuid4())}}
        row.metadata_json=revised;run.output=revised;uow.commit()
    assert c.get(path(out),headers=h).status_code==404
    assert c.get(path(out,'delivery'),headers=h).status_code==404
    with service._uow_factory() as uow:
        run=uow.session.get(Run,UUID(out['generation_run_id']));row=uow.session.scalar(select(GalleryEntry).where(GalleryEntry.metadata_json['generation_run_id'].astext==str(run.id)))
        row.metadata_json=snapshot;run.output=snapshot
        brand=uow.session.get(Brand,run.brand_id,with_for_update=True)
        profile=uow.profiles.by_id(brand.current_profile_id)
        replacement=uow.profiles.create_version_for_locked_brand(brand_id=brand.id,profile=profile.profile,schema_version=1)
        brand.current_profile_id=replacement.id;uow.commit()
    assert not ctx(ready,out)['available']
    assert save(ready,out,request(ctx(ready,out))).status_code==409
    assert c.get(path(out,'delivery'),headers=h).json()['final_eligible'] is False


def test_application_cannot_accept_client_qa_or_grant_acceptance(ready,original):
    c,h,service,p,data,*_=ready
    context=ctx(ready,original)
    forged={'checked':True,'overall_pass':True,'mandatory_checks_completed':['all']}
    for key,value in [('visual_qa',forged),('accepted',True),('qa_passed',True),('editor_checks',{'visual_review':'PASS'}),('selected_attempt_id',str(uuid4()))]:
        response=save(ready,original,{**request(context),key:value})
        assert response.status_code==422,response.text
    invalid_copy={**request(context),'copy':{**context['copy'],'visual_qa':forged}}
    assert save(ready,original,invalid_copy).status_code==422
    response=c.post(path(original,'copy-preview'),headers=h,json={**context['copy'],'visual_qa':forged})
    assert response.status_code==422
    saved=save(ready,original,request(context)).json()
    with service._uow_factory() as uow:
        run=uow.session.get(Run,UUID(saved['run_id']))
        attempt=uow.session.get(Attempt,UUID(saved['delivery']['generation_attempt_id']))
        assert run.selected_attempt_id is None
        assert attempt.qa_passed is False and attempt.qa['visual']['checked'] is False
        assert attempt.qa['visual']['attempt_identity']['attempt_id']==str(attempt.id)
        assert attempt.qa['visual']['attempt_identity']['content_sha256']==saved['delivery']['checksum']
    assert not saved['delivery']['final_eligible']
    assert len(ctx(ready,original)['revisions'])==2
    assert c.post(path(saved['result'],'visual-review'),headers=h,json=forged).status_code==404
