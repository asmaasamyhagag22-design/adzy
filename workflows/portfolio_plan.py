"""Bind one existing public case to the existing poster admission; prepare only, no sends/decisions."""
import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID


def prepare(directory):
    from .portfolio_demo import configure
    from .demo import connect
    from api.routes.generate import GenerateRequest
    from db.poster_operations import fingerprint
    from db.models import Run,Approval,UsageRecord
    from sqlalchemy import select,func
    from dotenv import dotenv_values
    m=json.loads((directory/'portfolio.json').read_text());configure(directory,m)
    service,p=connect(m)
    context=p.canonical_strategy_context(domain_key=m['domain'],user_id=m['actor'],run_id=m['strategy_id'])
    if not context or not context['is_current'] or context['profile_snapshot_id']!=m['profile_id']:
        raise ValueError('Current case binding changed')
    canonical=context['output']['canonical_strategy']
    if not canonical['verification']['eligible_for_approval']:
        raise ValueError('Research/strategy requires repair; never manufacture eligibility')
    import os
    project=os.getenv('GOOGLE_CLOUD_PROJECT') or dotenv_values(Path(__file__).resolve().parents[1]/'.env').get('GOOGLE_CLOUD_PROJECT')
    if not project: raise ValueError('Configure GOOGLE_CLOUD_PROJECT in this project before binding a live plan')
    body=GenerateRequest(domain_key=m['domain'],strategy_version_id=m['strategy_id'],language='ar',content_type='brand_poster',style='strategy_grounded',
        operation_key='portfolio-firefox-ar-20261018-v1').model_dump()
    digest=fingerprint({k:v for k,v in body.items() if k!='operation_key'})
    routes=[]
    for stage in ['creative_dna','poster_concept_brief','poster_concept','poster_design','poster_vision_qa','copy_sense','social_caption']:
        routes.append(dict(provider='vertex',model='gemini-3.5-flash-lite',stage='generate_content/'+stage,
            location='global',project=project,estimate_usd='0.032460800',max_output_tokens=4096,
            thinking_level='MINIMAL',project_schema=True,max_image_dimension=3072,max_image_bytes=7_000_000,max_images=3))
    routes.append(dict(provider='vertex',model='gemini-2.5-flash-image',stage='generate_content',location='global',
        project=project,estimate_usd='0.055910400',max_output_tokens=1536))
    policy=dict(plan_id='adzy-portfolio-firefox-ar-20261018-v1',expires_on='2026-10-25',max_dispatches=12,
        request_hashes={'ar':digest},routes=routes)
    with service._uow_factory() as uow:
        charged=uow.session.scalar(select(func.coalesce(func.sum(func.coalesce(Run.reserved_usd,0)+func.coalesce(Run.settled_usd,0)),0)).where(Run.created_by_user_id==UUID(m['actor']),Run.operation_key.is_not(None)))
        calls=uow.session.scalar(select(func.count()).select_from(UsageRecord).join(Run).where(Run.created_by_user_id==UUID(m['actor'])))
        approvals=uow.session.scalar(select(func.count()).select_from(Approval).where(Approval.decided_by_user_id==UUID(m['actor'])))
    plan={'status':'AWAITING_COMBINED_OWNER_DECISION','owner_id':m['actor'],'domain_key':m['domain'],
        'profile_snapshot_id':m['profile_id'],'swot_run_id':m['swot_id'],'strategy_run_id':m['strategy_id'],
        'strategy_output_sha256':fingerprint(context['output']),'swot_content_sha256':fingerprint(json.loads((directory/'swot.json').read_text())),'request_hash':digest,'request':body,'policy':policy,
        'prior_owner_commitments_usd':str(charged),'prior_model_dispatches':calls,'canonical_human_decisions':approvals,
        'proposed_reservation_usd':'2.000000000','proposed_owner_cap_usd':str(charged+Decimal('2')),
        'max_dispatches':12,'max_operation_estimates_usd':'0.670924800','account_inference_access':'UNVERIFIED',
        'analysis_route':'Frozen public-source AI curation + deterministic positioning/research/verifier, zero paid analysis calls',
        'semantic_status':'SHADOW_UNQUALIFIED_NO_SEMANTIC_DISPATCHES','financial_authorized':False}
    for name,value in [('live-plan.json',plan),('request.json',body),('dispatch-policy.json',policy)]:
        (directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    return plan


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',default='.adzy-portfolio/state')
    plan=prepare(Path(parser.parse_args().directory).resolve())
    print(json.dumps({k:plan[k] for k in ('status','profile_snapshot_id','strategy_run_id','request_hash','prior_owner_commitments_usd','canonical_human_decisions','max_dispatches')}))

if __name__=='__main__':main()
