"""Execute only an explicitly authorized, frozen local portfolio plan through ordinary HTTP routes."""
import argparse
import hashlib
import json
from pathlib import Path


def authorized_plan(directory, authorization):
    from db.poster_operations import fingerprint
    plan=json.loads((directory/'live-plan.json').read_text())
    auth=json.loads(Path(authorization).read_text())
    if (auth.get('plan_sha256')!=fingerprint(plan) or auth.get('owner_id')!=plan['owner_id']
            or auth.get('reservation_usd')!='2.000000000' or auth.get('max_dispatches')!=12
            or auth.get('decision')!='APPROVE_EXACT_SWOT_STRATEGY_AND_LIVE_POSTER'
            or not auth.get('owner_statement')):
        raise ValueError('A matching explicit human authorization is required; this helper never creates it')
    from poster.dispatch_policy import DispatchPolicy
    policy=DispatchPolicy.model_validate(plan['policy'])
    for route in policy.routes: policy.route(route.stage,route.model)
    return plan


def live_environment(directory, authorization):
    import os
    plan=authorized_plan(directory,authorization)
    from .demo import connect
    m=json.loads((directory/'portfolio.json').read_text());_,p=connect(m)
    c=p.canonical_strategy_context(domain_key=plan['domain_key'],user_id=plan['owner_id'],run_id=plan['strategy_run_id'])
    from db.poster_operations import fingerprint
    if not c or not c['is_current'] or c['profile_snapshot_id']!=plan['profile_snapshot_id'] or fingerprint(c['output'])!=plan['strategy_output_sha256']:
        raise ValueError('Frozen case drift: re-review before any paid execution')
    project=plan['policy']['routes'][0]['project']
    os.environ.pop('GEMINI_API_KEY',None);os.environ.pop('GOOGLE_API_KEY',None)
    os.environ.update(GOOGLE_CLOUD_PROJECT=project,GOOGLE_CLOUD_LOCATION='global',GEMINI_MODEL='gemini-3.5-flash-lite',
        ONESHOT_IMAGE_MODEL='gemini-2.5-flash-image',BASEERA_ENABLE_PAID_IMAGES='1',BASEERA_NO_SPEND='0',
        ADZY_POSTER_RESERVATION_USD=plan['proposed_reservation_usd'],ADZY_POSTER_BUDGET_USD=plan['proposed_owner_cap_usd'],
        ADZY_POSTER_DISPATCH_POLICY_JSON=json.dumps(plan['policy']))


def execute(directory,authorization,origin):
    import httpx
    from db.poster_operations import fingerprint
    plan=authorized_plan(directory,authorization)
    if origin not in ('http://127.0.0.1:8010','http://localhost:8010'):
        raise ValueError('Portfolio execution is loopback-only on port8010')
    m=json.loads((directory/'portfolio.json').read_text())
    with httpx.Client(base_url=origin,timeout=900) as client:
        def call(method,path,**kwargs):
            r=client.request(method,path,**kwargs);r.raise_for_status();return r.json()
        token=call('POST','/api/auth/login',json={'email':m['email'],'password':m['password']})['access_token']
        client.headers['Authorization']='Bearer '+token
        brand=call('GET','/api/brands/'+plan['domain_key'])
        candidate=brand['strategy_candidate']
        # Same projection and immutable subject the UI reviews; never accept a substituted version.
        if (brand['swot_run_id']!=plan['swot_run_id'] or candidate['run_id']!=plan['strategy_run_id']
                or fingerprint(brand['swot'])!=plan['swot_content_sha256']):
            raise ValueError('Frozen approval subjects changed')
        if not brand.get('swot_approved'):
            call('POST','/api/swot/approve',json={'domain_key':plan['domain_key'],'swot_run_id':plan['swot_run_id']})
        review=call('POST','/api/review/start',json={'domain_key':plan['domain_key'],'subject_id':plan['strategy_run_id']})
        if review['subject_hash']!=plan['strategy_output_sha256']:
            raise ValueError('Review material changed')
        if review['status']=='NEEDS_REVIEW':
            review=call('POST','/api/review/'+review['workflow_id']+'/resume',json={
                'subject_id':review['subject_id'],'subject_hash':review['subject_hash'],'decision':'approved'})
        (directory/'review-result.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
        if review['status']!='READY_FOR_CREATIVE':
            raise ValueError('Owner-authorized review blocked: '+', '.join(review.get('reasons',[])))
        operation=call('POST','/api/generate',json=plan['request'])
        (directory/'live-operation.json').write_text(json.dumps(operation,ensure_ascii=False,indent=2)+'\n')
        if operation['state']!='accepted':
            print(json.dumps({'state':operation['state'],'operation_key':plan['request']['operation_key'],
                'action':'STOP; inspect existing operation, never rotate the key or resend unknown'}));return
        run=operation['generation_run_id']
        delivery=call('GET',f'/api/poster/runs/{run}/delivery')
        if not delivery['final_eligible']:raise ValueError('Exact server-side delivery gate refused final export')
        response=client.get(f'/api/poster/runs/{run}/download',params={'mode':'final',
            'attempt_id':delivery['generation_attempt_id'],'checksum':delivery['checksum']})
        response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest()!=delivery['checksum']:raise ValueError('Download checksum mismatch')
        (directory/'final-poster.png').write_bytes(response.content)
        (directory/'delivery.json').write_text(json.dumps(delivery,indent=2)+'\n')
        print(json.dumps({'state':'accepted','download_checksum_verified':True,'calls':len(operation['calls'])}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',default='.adzy-portfolio/state');parser.add_argument('--authorization',required=True)
    parser.add_argument('--origin',default='http://127.0.0.1:8010')
    args=parser.parse_args();execute(Path(args.directory).resolve(),args.authorization,args.origin)

if __name__=='__main__':main()
