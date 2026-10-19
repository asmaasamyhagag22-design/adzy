"""The operational helper refuses incomplete/mismatched owner decisions before HTTP or models."""
import json
import pytest
from db.poster_operations import fingerprint
from workflows.portfolio_live import authorized_plan


def test_preparation_is_not_authorization(tmp_path):
    plan={'owner_id':'local-demo-owner','financial_authorized':False}
    (tmp_path/'live-plan.json').write_text(json.dumps(plan))
    auth=tmp_path/'authorization.json';auth.write_text('{}')
    with pytest.raises(ValueError,match='explicit human authorization'):
        authorized_plan(tmp_path,auth)
    auth.write_text(json.dumps({'owner_id':'local-demo-owner','plan_sha256':fingerprint(plan),
        'reservation_usd':'2.000000000','max_dispatches':12,'decision':'APPROVE_EXACT_SWOT_STRATEGY_AND_LIVE_POSTER',
        'owner_statement':''}))
    with pytest.raises(ValueError,match='explicit human authorization'):
        authorized_plan(tmp_path,auth)


# Same authenticated HTTP/SQL services as the application, with fictional external responses only.
from test_poster_operation_boundary import ready,body
from test_app_review_graph import application
from test_review_workflow import database


def test_authorized_helper_uses_canonical_review_generation_and_exact_download(ready,monkeypatch,tmp_path):
    import hashlib
    import httpx
    from poster_boundary_support import install
    from api.routes.generate import GenerateRequest
    from workflows.portfolio_live import execute
    client,headers,service,p,data,app,_=ready
    counter,sent,sdk=install(monkeypatch,tmp_path)
    brand=p.visible_brand(domain_key=data['domain'],user_id=data['actor'])
    context=p.canonical_strategy_context(domain_key=data['domain'],user_id=data['actor'],run_id=data['subject'])
    request=GenerateRequest(**body(data)).model_dump()
    policy={'plan_id':'SYNTHETIC-portfolio-helper','expires_on':'2099-01-01','max_dispatches':12,'request_hashes':{},
        'routes':[{'provider':'developer','project':'','location':'','stage':'generate_content','model':'gemini-2.5-flash-image','estimate_usd':'0.2','max_output_tokens':1536}]}
    plan={'owner_id':data['actor'],'domain_key':data['domain'],'swot_run_id':brand['swot_run_id'],
        'swot_content_sha256':fingerprint(brand['swot']),'strategy_run_id':data['subject'],
        'strategy_output_sha256':fingerprint(context['output']),'request':request,'policy':policy}
    email=service.user_by_id(__import__('uuid').UUID(data['actor'])).email
    (tmp_path/'portfolio.json').write_text(json.dumps({'email':email,'password':'SyntheticPassword42'}))
    (tmp_path/'live-plan.json').write_text(json.dumps(plan))
    auth=tmp_path/'authorization.json';auth.write_text(json.dumps({'plan_sha256':fingerprint(plan),'owner_id':data['actor'],
        'reservation_usd':'2.000000000','max_dispatches':12,'decision':'APPROVE_EXACT_SWOT_STRATEGY_AND_LIVE_POSTER',
        'owner_statement':'SYNTHETIC_TEST simulated authorization; not a human decision'}))
    requested_paths=[]
    class LocalHTTP:
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def request(self,method,path,**kwargs):
            requested_paths.append((method,path))
            return client.request(method,path,headers=self.headers,**kwargs)
        def get(self,path,**kwargs):return self.request('GET',path,**kwargs)
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:LocalHTTP())
    execute(tmp_path,auth,'http://127.0.0.1:8010')
    delivery=json.loads((tmp_path/'delivery.json').read_text())
    assert hashlib.sha256((tmp_path/'final-poster.png').read_bytes()).hexdigest()==delivery['checksum']
    assert len(sent)==1
    assert not any(path.endswith(('/approve', '/resume')) for _,path in requested_paths)
    sdk.close()
