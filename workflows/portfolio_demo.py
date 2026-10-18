"""Persistent local portfolio case. Real public evidence; no automatic human decisions or providers."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import UUID, uuid4
from .demo import command, connect


def configure(directory, manifest):
    import config_env
    config_env._loaded=True
    config_env.load_env=lambda *a,**k:False
    os.environ.update(PERSISTENCE_BACKEND='postgres',BASEERA_DATABASE_URL=manifest['dsn'],
        BASEERA_WEBAPP_DIR=str(directory/'application'),ADZY_REVIEW_STATE_DIR=str(directory/'review'),
        BASEERA_ENABLE_PAID_IMAGES='0',BASEERA_NO_SPEND='1',ADZY_LANGFUSE_ENABLED='0',
        LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false')


def init(directory):
    if directory.exists(): raise ValueError('Use a new portfolio state directory; existing state is never reset')
    directory.mkdir(parents=True)
    name='adzy-portfolio-'+uuid4().hex[:12]
    volume=name+'-data'
    command(['docker','volume','create','--label','adzy.portfolio=true',volume])
    command(['docker','run','--pull=never','--detach','--name',name,'--label','adzy.portfolio=true',
        '--publish','127.0.0.1::5432','--mount',f'type=volume,src={volume},dst=/var/lib/postgresql/data',
        '--env','POSTGRES_HOST_AUTH_METHOD=trust','--env','POSTGRES_DB=adzy_portfolio','postgres:16-alpine'])
    for _ in range(80):
        if subprocess.run(['docker','exec',name,'pg_isready','-U','postgres'],capture_output=True).returncode==0: break
        time.sleep(.25)
    else: raise RuntimeError('Portfolio PostgreSQL startup failed; container retained for diagnosis')
    port=command(['docker','port',name,'5432']).rsplit(':',1)[1]
    m={'container':name,'volume':volume,'dsn':f'postgresql+psycopg://postgres@127.0.0.1:{port}/adzy_portfolio',
       'scope':'LOCAL_PORTFOLIO_PUBLIC_EVIDENCE','email':'portfolio@adzy.local','password':'PortfolioDemo42'}
    (directory/'portfolio.json').write_text(json.dumps(m,indent=2)+'\n')
    (directory/'portfolio.json').chmod(0o600)
    configure(directory,m)
    from alembic import command as alembic_command
    from alembic.config import Config
    from sqlalchemy import create_engine
    root=Path(__file__).resolve().parents[1]
    cfg=Config(str(root/'alembic.ini'));cfg.set_main_option('script_location',str(root/'db/migrations'))
    with create_engine(m['dsn']).begin() as conn:
        cfg.attributes['connection']=conn;alembic_command.upgrade(cfg,'head')
    service,p=connect(m)
    from api.user_auth import hash_password
    from db.services import ProfileData,ScrapeSuccess,LiveRunData
    case=json.loads((root/'docs/demo/firefox/case.json').read_text())
    brand=case['brand'];domain=brand['domain_key']
    user=service.create_user(email=m['email'],hashed_password=hash_password(m['password']))
    job=service.create_user_job(owner_user_id=user.id,requested_domain_key=domain)
    service.claim_job(job_id=job.id,worker_id=str(job.id),lease_seconds=600)
    committed=service.commit_scrape_success(ScrapeSuccess(domain_key=domain,user_id=user.id,job_id=job.id,worker_id=str(job.id),
        profile=ProfileData(payload=brand['_profile'],primary_language='ar',language_source='public Arabic page'),
        display_name='Firefox · public-source portfolio demo',source_url=brand['_profile']['source_url'],
        run_output={'profile':brand['_profile'],'full_result':brand['_full_result'],'demo_provenance':'AI-curated public snapshot; human decisions pending'}))
    # Preserve the actual sourced intelligence through ordinary enrichment records.
    for kind,payload in [('competitors',{'marketing_intel':brand['_full_result']['marketing_intel'],'competitor_intel':brand['_full_result']['competitor_intel']})]:
        p.record_enrichment(domain_key=domain,user_id=str(user.id),kind=kind,patch=payload)
    swot={'strengths':[{'text':'الخصوصية مُدمجة','source_url':case['sources']['brand']['url']}],
          'weaknesses':[{'text':'إشارة مستخدم واحدة من ديسمبر2025 إلى ضبط الحماية عند تعطل فيديو؛ ليست قياسًا لعيب حالي أو انتشار المشكلة','source_url':case['sources']['voice']['url']}],
          'opportunities':[{'text':'فرضية AI: اختبار وضوح رسالة الحماية الافتراضية؛ الأثر التجاري غير مقاس','source_url':case['sources']['brand']['url']}],
          'threats':[{'text':'Microsoft Edge يعرض منع التعقب أيضًا؛ لا ادعاء حصرية Firefox','source_url':case['sources']['peer']['url']}]}
    swot_run=service.create_live_run(LiveRunData(brand_id=committed.brand.id,user_id=user.id,kind='swot',status='succeeded',
        input_profile_id=committed.brand.current_profile_id,output={'has_swot':True,'swot':swot,'ai_review':True,'human_approval':'PENDING'},output_schema_version=1))
    from strategy.positioning import build_positioning_strategy,objective_from_mapping
    # Precompute an inspectable proposal; save no approval. Graph refuses until SWOT is reviewed.
    build=build_positioning_strategy(brand,objective=objective_from_mapping(case['objective']),swot_run_id=str(swot_run.id))
    strategy=service.create_live_run(p._canonical_strategy_run_data(domain_key=domain,user_id=str(user.id),build=build))
    m.update(actor=str(user.id),domain=domain,profile_id=str(committed.brand.current_profile_id),
             swot_id=str(swot_run.id),strategy_id=str(strategy.id))
    (directory/'portfolio.json').write_text(json.dumps(m,indent=2)+'\n')
    (directory/'strategy.json').write_text(json.dumps(build.output,ensure_ascii=False,indent=2)+'\n')
    (directory/'swot.json').write_text(json.dumps(swot,ensure_ascii=False,indent=2)+'\n')
    return m


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['init','serve','status'])
    parser.add_argument('--directory',default='.adzy-portfolio/state')
    parser.add_argument('--port',type=int,default=8010)
    parser.add_argument('--authorization',help='Explicit matching owner authorization; absent means no paid calls')
    args=parser.parse_args();directory=Path(args.directory).resolve()
    if args.action=='init':
        m=init(directory);print(json.dumps({k:m[k] for k in ('email','password','domain','strategy_id')},indent=2));return
    m=json.loads((directory/'portfolio.json').read_text())
    if command(['docker','inspect','--format','{{ index .Config.Labels "adzy.portfolio" }}',m['container']])!='true':
        raise ValueError('Not an ADZY portfolio resource')
    if command(['docker','inspect','--format','{{.State.Running}}',m['container']])!='true':
        command(['docker','start',m['container']])
        for _ in range(80):
            if subprocess.run(['docker','exec',m['container'],'pg_isready','-U','postgres'],capture_output=True).returncode==0: break
            time.sleep(.25)
        else: raise RuntimeError('Portfolio database did not restart')
    port=command(['docker','port',m['container'],'5432']).rsplit(':',1)[1]
    m['dsn']=f'postgresql+psycopg://postgres@127.0.0.1:{port}/adzy_portfolio'
    (directory/'portfolio.json').write_text(json.dumps(m,indent=2)+'\n')
    configure(directory,m)
    if args.action=='status':
        service,p=connect(m)
        brand=p.visible_brand(domain_key=m['domain'],user_id=m['actor'])
        print(json.dumps({'domain':m['domain'],'swot_approved':brand['swot_approved'],'strategy':p.canonical_strategy_context(
            domain_key=m['domain'],user_id=m['actor'],run_id=m['strategy_id'])['approval_state']},indent=2));return
    if args.authorization:
        from .portfolio_live import live_environment
        live_environment(directory,args.authorization)
    import uvicorn
    from api.main import app
    uvicorn.run(app,host='127.0.0.1',port=args.port)

if __name__=='__main__':main()
