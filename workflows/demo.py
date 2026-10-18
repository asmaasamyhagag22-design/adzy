"""Credential-free synthetic demo. Docker PostgreSQL app records, SQLite checkpoints.
No private .env, providers, model outputs or media are used.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from uuid import uuid4


def isolate():
    import config_env
    config_env._loaded = True
    config_env.load_env = lambda *a, **k: False
    os.environ['ADZY_LANGFUSE_ENABLED'] = '0'
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    os.environ['BASEERA_ENABLE_PAID_IMAGES'] = '0'
    os.environ['BASEERA_NO_SPEND'] = '1'
    original_dns = socket.getaddrinfo
    def local_dns(host, *args, **kwargs):
        if host not in ('127.0.0.1','::1','localhost'):
            raise RuntimeError('DEMO_EXTERNAL_DNS_BLOCKED')
        return original_dns(host,*args,**kwargs)
    socket.getaddrinfo = local_dns
    original = socket.socket.connect
    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
            raise RuntimeError('DEMO_EXTERNAL_NETWORK_BLOCKED')
        return original(sock,address)
    socket.socket.connect = local_only


def command(args):
    return subprocess.run(args,check=True,capture_output=True,text=True).stdout.strip()


def connect(manifest):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from db.services import PersistenceService
    from db.uow import unit_of_work_factory
    from api.persistence import PostgresPersistence
    engine = create_engine(manifest['dsn'])
    service = PersistenceService(unit_of_work_factory(sessionmaker(engine,expire_on_commit=False)))
    return service,PostgresPersistence(service)


def seed(service,provider, *, insufficient=False, brand_source=None, owner=None):
    from db.services import ProfileData, ScrapeSuccess, LiveRunData
    from strategy.positioning.objective import objective_from_mapping
    from strategy.positioning.service import build_positioning_strategy
    from .synthetic import brand, objective
    domain = f'synthetic-{uuid4().hex}.test'
    source = (brand_source or brand)(domain)
    if insufficient:
        source['_profile']['trust_signals'] = []
        source['_profile']['audience_signals'] = []
    user = owner or service.create_user(email=f'{uuid4().hex}@synthetic.invalid',hashed_password='SYNTHETIC_DISABLED_LOGIN')
    job = service.create_user_job(owner_user_id=user.id,requested_domain_key=domain)
    service.claim_job(job_id=job.id,worker_id=str(job.id),lease_seconds=600)
    committed = service.commit_scrape_success(ScrapeSuccess(domain_key=domain,user_id=user.id,job_id=job.id,
        worker_id=str(job.id),profile=ProfileData(payload=source['_profile'],schema_version=1),
        display_name='SYNTHETIC Studio',source_url='https://studio.example.test'))
    swot = service.create_live_run(LiveRunData(brand_id=committed.brand.id,user_id=user.id,kind='swot',
        status='succeeded',input_profile_id=committed.brand.current_profile_id,
        output={'has_swot':True,'swot':{'strengths':[],'weaknesses':[],'opportunities':[],'threats':[]}},output_schema_version=1))
    service.approve_current_swot(brand_id=committed.brand.id,user_id=user.id,expected_run_id=swot.id)
    build = build_positioning_strategy(source,objective=objective_from_mapping(objective()),swot_run_id=str(swot.id))
    job = service.create_user_job(owner_user_id=user.id,requested_domain_key=domain)
    service.claim_job(job_id=job.id,worker_id=str(job.id),lease_seconds=600)
    provider.finish_canonical_strategy_job(job_id=str(job.id),domain_key=domain,user_id=str(user.id),build=build)
    run = service.current_successful_run(brand_id=committed.brand.id,kind='strategy',user_id=user.id)
    return {'actor':str(user.id),'domain':domain,'subject':str(run.id)}


def init(directory):
    if directory.exists(): raise ValueError('Use a new demo directory')
    directory.mkdir(parents=True)
    name = 'adzy-review-demo-' + uuid4().hex[:12]
    command(['docker','run','--pull=never','--detach','--rm','--name',name,
             '--label','adzy.review-demo=true','--publish','127.0.0.1::5432',
             '--env','POSTGRES_HOST_AUTH_METHOD=trust','--env','POSTGRES_DB=baseera_pytest_foundation','postgres:16-alpine'])
    try:
        for _ in range(60):
            p = subprocess.run(['docker','exec',name,'pg_isready','-U','postgres'],capture_output=True)
            if p.returncode == 0: break
            time.sleep(.25)
        else: raise RuntimeError('PostgreSQL startup failed')
        port = command(['docker','port',name,'5432']).rsplit(':',1)[1]
        manifest = {'container':name,'dsn':f'postgresql+psycopg://postgres@127.0.0.1:{port}/baseera_pytest_foundation',
                    'synthetic':True}
        from db.test_database_safety import require_disposable_test_database
        require_disposable_test_database(manifest['dsn'],environ={
            'BASEERA_DESTRUCTIVE_TEST_DATABASE':'baseera_pytest_foundation'})
        from alembic import command as alembic_command
        from alembic.config import Config
        from sqlalchemy import create_engine
        root = Path(__file__).resolve().parents[1]
        os.environ['BASEERA_DATABASE_URL'] = manifest['dsn']
        cfg = Config(str(root/'alembic.ini'))
        cfg.set_main_option('script_location',str(root/'db/migrations'))
        with create_engine(manifest['dsn']).begin() as conn:
            cfg.attributes['connection'] = conn
            alembic_command.upgrade(cfg,'head')
        service,provider = connect(manifest)
        manifest.update(seed(service,provider))
        from .runtime import setup
        setup(directory)
        (directory/'demo.json').write_text(json.dumps(manifest,indent=2))
        return {'synthetic':True,'initialized':True}
    except BaseException:
        command(['docker','stop',name])
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['init','start','status','resume','stale','cleanup','diagram'])
    parser.add_argument('--directory',default='.review-demo')
    parser.add_argument('--decision',choices=['approved','rejected'])
    args = parser.parse_args()
    isolate()
    directory = Path(args.directory)
    if args.action == 'init': result = init(directory)
    else:
        manifest = json.loads((directory/'demo.json').read_text())
        if args.action == 'cleanup':
            name = manifest['container']
            if not name.startswith('adzy-review-demo-') or command(['docker','inspect','--format','{{ index .Config.Labels "adzy.review-demo" }}',name])!='true':
                raise ValueError('Not an ADZY demo resource')
            command(['docker','stop',name])
            # Delete only enumerated files created by this demo, never arbitrary directory contents.
            for file in ('demo.json','execution.sqlite','bindings.sqlite','checkpoints.sqlite','checkpoints.sqlite-wal','checkpoints.sqlite-shm'):
                (directory/file).unlink(missing_ok=True)
            if not any(directory.iterdir()): directory.rmdir()
            result = {'cleaned':True}
        else:
            service,provider = connect(manifest)
            from .runtime import ReviewWorkflow
            workflow = ReviewWorkflow(directory,provider,actor_id=manifest['actor'])
            if args.action=='start':
                if manifest.get('workflow'): raise ValueError('Demo already started')
                result = workflow.start(domain_key=manifest['domain'],subject_id=manifest['subject'])
                manifest['workflow'] = result['workflow_id']
                (directory/'demo.json').write_text(json.dumps(manifest,indent=2))
            elif args.action=='diagram':
                with workflow._execution(manifest['workflow']) as (graph,config,services):
                    print(graph.get_graph().draw_mermaid())
                return
            elif args.action=='stale':
                from db.services import LiveRunData
                from uuid import UUID
                run = service.run_for_user(run_id=UUID(manifest['subject']),user_id=UUID(manifest['actor']))
                service.create_live_run(LiveRunData(brand_id=run.brand_id,user_id=UUID(manifest['actor']),
                    kind='strategy',status='succeeded',input_profile_id=run.input_profile_id,
                    requested_config=run.requested_config,effective_config=run.effective_config,output=run.output,output_schema_version=1))
                result = workflow.status(manifest['workflow'])
            elif args.action=='status': result = workflow.status(manifest['workflow'])
            else:
                if not args.decision: parser.error('resume requires --decision')
                state = workflow.status(manifest['workflow'])
                result = workflow.resume(manifest['workflow'],dict(subject_id=state['subject_id'],
                    subject_hash=state['subject_hash'],decision=args.decision))
    print(json.dumps({'synthetic':True, **result},indent=2))

if __name__=='__main__': main()
