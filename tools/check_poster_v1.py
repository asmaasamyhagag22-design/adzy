"""Release checks in fresh processes; external providers blocked, disposable PostgreSQL only."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

CHECKS = ('test_compiler_source_support.py','test_gate2c_canonical_poster.py','test_poster_oneshot_best_attempt.py','test_poster_economic_repair.py','test_poster_qa_brand_roles.py','test_poster_candidate_persistence.py','test_poster_design_controls.py','test_poster_acceptance_red_boundary.py','test_vision_qa_hard_gate_matrix.py','test_poster_dispatch_policy.py','test_poster_rtl_alignment.py','test_portfolio_authorization.py','test_review_workflow.py','test_app_review_graph.py','test_poster_operation_boundary.py',
          'test_poster_delivery.py','test_poster_acceptance_truth.py','test_poster_claim_assessment.py',
          'test_unused_reservations.py','test_webapp_spend_gate.py',
          'test_route_persistence_classification_2gb.py','test_security_headers.py')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',default=shutil.which('node'))
    parser.add_argument('--evidence-dir',default=None)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    if not args.node: parser.error('Node.js required; supply --node /absolute/path/to/node')
    evidence=Path(args.evidence_dir).resolve() if args.evidence_dir else None
    if evidence: evidence.mkdir(parents=True,exist_ok=True)
    commands=[(name,[sys.executable,'-m','workflows.verify','-q','tests/'+name]) for name in CHECKS]
    commands.append(('frontend',[args.node,'--test','tests/poster_operations_frontend.test.cjs','tests/wp0_swot_frontend.test.cjs']))
    failed=[]
    env={**os.environ,'BASEERA_DATABASE_URL':'','BASEERA_TEST_DATABASE_URL':''}
    for name,cmd in commands:
        result=subprocess.run(cmd,cwd=root,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        if evidence: (evidence/(name+'.txt')).write_text(result.stdout)
        print(name+': '+('PASS' if result.returncode==0 else 'FAIL'),flush=True)
        print(result.stdout[-1600:],flush=True)
        if result.returncode:failed.append(name)
    if failed: raise SystemExit('Failed checks: '+', '.join(failed))

if __name__=='__main__':main()
