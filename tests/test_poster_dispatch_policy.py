"""Operation-bound dispatch policy: real disposable PostgreSQL, fake SDK HTTP only."""
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
import httpx
import pytest
from test_poster_operation_boundary import ready, body
from test_review_workflow import database
from test_app_review_graph import application
from poster.paid_boundary import paid_scope, paid_call, StopPaidOperation
from db.poster_operations import PosterOperations, fingerprint, PosterOperationError


def admit(ready, monkeypatch, *, policy=True):
    c,h,service,p,data,app,tmp = ready
    request = body(data)
    key = request.pop('operation_key')
    from api.routes.generate import GenerateRequest, _compile_contract
    request = GenerateRequest(**request).model_dump(exclude={'operation_key'})
    routes = [{'provider':'vertex','project':'synthetic-project','location':'us-central1','model':'gemini-2.5-pro',
               'stage':'generate_content/poster_design','estimate_usd':'0.01','max_output_tokens':4096}]
    routes.append({'provider':'vertex','project':'synthetic-project','location':'global','model':'gemini-2.5-flash-image','stage':'generate_content','estimate_usd':'0.055910400','max_output_tokens':1536})
    value = {'plan_id':'SYNTHETIC-validation-1','expires_on':'2099-01-01','max_dispatches':12,
             'request_hashes':{'en':fingerprint(request)},'routes':routes}
    if policy: monkeypatch.setenv('ADZY_POSTER_DISPATCH_POLICY_JSON',json.dumps(value))
    monkeypatch.setenv('ADZY_POSTER_CALL_ESTIMATES_JSON',json.dumps({'generate_content:gemini-2.5-pro':'0.01'}))
    ops = PosterOperations(p)
    def compile_current():
        return _compile_contract(domain_key=data['domain'],strategy_version_id=data['subject'],user_id=data['actor'],request={'language':'en'})[0]
    run,_,_ = ops.admit(data['actor'],key,request,compile_current)
    return ops,run,key,request,compile_current,value


def sdk(mode='ok', location='us-central1'):
    from google import genai
    from google.genai import types
    sent=[]
    def transport(req):
        sent.append(json.loads(req.content))
        if mode=='timeout': raise httpx.ReadTimeout('SYNTHETIC')
        if mode=='429': return httpx.Response(429,json={'error':{'code':429,'message':'SYNTHETIC'}})
        return httpx.Response(200,json={'candidates':[{'content':{'role':'model','parts':[{'text':'ok'}]},'finishReason':'STOP'}]})
    from google.auth.credentials import Credentials
    class SyntheticCredentials(Credentials):
        def refresh(self, request): self.token='SYNTHETIC'
    client=genai.Client(vertexai=True,project='synthetic-project',location=location,credentials=SyntheticCredentials(),http_options=types.HttpOptions(httpx_client=httpx.Client(transport=httpx.MockTransport(transport)),retry_options=types.HttpRetryOptions(attempts=5)))
    return client,sent


def send(ops,run,client,stage='poster_design',model='gemini-2.5-pro',contents='synthetic'):
    from google.genai import types
    with paid_scope(ops,run):
        return paid_call(client.models.generate_content,_stage=stage,model=model,contents=contents,config=types.GenerateContentConfig())


def test_before_policy_existing_boundary_allows_thirteen(ready,monkeypatch):
    ops,run,*_ = admit(ready,monkeypatch,policy=False)
    client,sent=sdk()
    for _ in range(13): send(ops,run,client)
    assert len(sent)==13
    client.close()


def test_twelve_permitted_thirteen_blocked_after_recovery_and_env_change(ready,monkeypatch):
    ops,run,*_ = admit(ready,monkeypatch)
    client,sent=sdk()
    for _ in range(12): send(ops,run,client)
    assert len(sent)==12
    assert sent[-1]['generationConfig']['maxOutputTokens']==4096
    assert sent[-1]['generationConfig']['thinkingConfig'].get('thinkingBudget', sent[-1]['generationConfig']['thinkingConfig'].get('thinking_budget'))==1024
    monkeypatch.delenv('ADZY_POSTER_DISPATCH_POLICY_JSON')
    with pytest.raises(StopPaidOperation): send(PosterOperations(ready[3]),run,client)
    assert len(sent)==12
    client.close()


def test_concurrent_last_slot_never_overadmits(ready,monkeypatch):
    ops,run,*_ = admit(ready,monkeypatch)
    client,sent=sdk()
    for _ in range(11): send(ops,run,client)
    def race(_):
        try: send(PosterOperations(ready[3]),run,client); return True
        except StopPaidOperation: return False
    with ThreadPoolExecutor(4) as pool: results=list(pool.map(race,range(4)))
    assert sum(results)==1 and len(sent)==12
    client.close()


@pytest.mark.parametrize('stage,model,contents',[('unlisted','gemini-2.5-pro','synthetic'),('poster_design','unpriced','synthetic'),('poster_design','gemini-2.5-pro','x'*24001)])
def test_unlisted_or_unbounded_never_reaches_transport(ready,monkeypatch,stage,model,contents):
    ops,run,*_ = admit(ready,monkeypatch)
    client,sent=sdk()
    with pytest.raises(StopPaidOperation): send(ops,run,client,stage,model,contents)
    assert sent==[]
    client.close()


@pytest.mark.parametrize('mode',['429','timeout'])
def test_failure_consumes_slot_and_stops_even_legacy_exception_fallback(ready,monkeypatch,mode):
    ops,run,key,*_ = admit(ready,monkeypatch)
    client,sent=sdk(mode)
    with pytest.raises(StopPaidOperation):
        try: send(ops,run,client)
        except Exception: send(ops,run,client)  # must be unreachable, including SDK retries
    with pytest.raises(StopPaidOperation): send(PosterOperations(ready[3]),run,client)
    assert len(sent)==1
    result=ops.status(ready[4]['actor'],key,recover=False)
    assert len(result['calls'])==1 and result['calls'][0]['state'] in ('rejected','unknown')
    client.close()


def test_one_operation_per_language_under_concurrent_admission(ready,monkeypatch):
    ops,run,key,request,compile_current,_ = admit(ready,monkeypatch)
    def race(n):
        try: ops.admit(ready[4]['actor'],'another-operation-key-'+str(n),request,compile_current); return True
        except PosterOperationError as exc:
            assert exc.detail['code']=='POSTER_CASE_ALREADY_ADMITTED'
            return False
    with ThreadPoolExecutor(3) as pool: assert not any(pool.map(race,range(3)))


def test_provider_change_and_missing_reservation_block_before_transport(ready,monkeypatch):
    from uuid import UUID
    from db.models import Run
    ops,run,*_ = admit(ready,monkeypatch)
    client,sent=sdk()
    client.models._api_client.location='global'
    with pytest.raises(StopPaidOperation): send(ops,run,client)
    assert not sent
    client.models._api_client.location='us-central1'
    with ready[2]._uow_factory() as uow:
        row=uow.session.get(Run,UUID(run)); row.reserved_usd=0; uow.commit()
    with pytest.raises(StopPaidOperation): send(ops,run,client)
    assert not sent
    client.close()


def test_validation_disables_only_optional_upscale(ready,monkeypatch):
    from poster.paid_boundary import validation_render_options
    ops,run,*_ = admit(ready,monkeypatch)
    assert validation_render_options()=={}
    with paid_scope(ops,run): assert validation_render_options()=={'upscale':False}
    assert validation_render_options()=={}


def test_actual_image_sdk_wire_bounds_and_unlisted_fallback(ready,monkeypatch):
    from google.genai import types
    ops,run,*_ = admit(ready,monkeypatch)
    client,sent=sdk(location='global')
    with paid_scope(ops,run):
        paid_call(client.models.generate_content,model='gemini-2.5-flash-image',contents='synthetic scene',
                  config=types.GenerateContentConfig(response_modalities=['IMAGE','TEXT']))
    assert len(sent)==1
    assert sent[0]['generationConfig']['maxOutputTokens']==1536
    assert sent[0]['generationConfig']['candidateCount']==1
    with pytest.raises(StopPaidOperation): send(ops,run,client,stage='',model='retired-preview')
    assert len(sent)==1
    client.close()


def test_selected_text_route_projects_grammar_and_sends_documented_thinking_level(ready,monkeypatch):
    from uuid import UUID
    from db.models import Run
    from google.genai import types
    from pydantic import BaseModel, Field
    ops,run,*_ = admit(ready,monkeypatch)
    with ready[2]._uow_factory() as uow:
        row=uow.session.get(Run,UUID(run))
        policy=dict(row.effective_config['dispatch_policy'])
        policy['routes']=[{**policy['routes'][0],'model':'gemini-3.5-flash-lite','thinking_level':'MINIMAL','project_schema':True,'max_image_dimension':3072,'max_image_bytes':7_000_000}]
        row.effective_config={**row.effective_config,'dispatch_policy':policy};uow.commit()
    class Answer(BaseModel):
        text: str = Field(max_length=20)
    client,sent=sdk()
    with paid_scope(ops,run):
        paid_call(client.models.generate_content,_stage='poster_design',model='gemini-3.5-flash-lite',contents='synthetic',
            config=types.GenerateContentConfig(response_schema=Answer,response_mime_type='application/json',temperature=0))
    cfg=sent[0]['generationConfig']
    thinking=cfg['thinkingConfig']
    assert thinking.get('thinkingLevel',thinking.get('thinking_level'))=='MINIMAL'
    schema=cfg['responseSchema']
    assert schema.get('propertyOrdering',schema.get('property_ordering'))==['text']
    assert 'maxLength' not in schema['properties']['text']
    assert 'temperature' not in cfg
    # Canonical HTML posters are2160×2700: send the original pixels to QA without resizing.
    import io
    from PIL import Image
    image=io.BytesIO();Image.new('RGB',(2160,2700),'white').save(image,format='PNG')
    with paid_scope(ops,run):
        paid_call(client.models.generate_content,_stage='poster_design',model='gemini-3.5-flash-lite',
            contents=[types.Part.from_bytes(data=image.getvalue(),mime_type='image/png'),'synthetic'],
            config=types.GenerateContentConfig(response_schema=Answer,response_mime_type='application/json'))
    assert len(sent)==2
    client.close()
