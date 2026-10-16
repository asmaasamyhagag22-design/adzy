"""Explicit source-only candidate; never reads env files, databases, runtime or customer media."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

PACKAGES = ('ads_intel','advisor','api','brand','business_profile','campaign','competitor',
    'creative','dashboard','db','decision_engine','grounding','instagram_intelligence',
    'launch_bundle','marketing_intel','media_plan','media_plan_v2','ops','policy_linter',
    'poster','reel','reviews','scraper','scripts','social_intel','strategy','telemetry','trends','workflows')
EXTENSIONS = {'.py','.yaml','.yml','.toml','.sql','.mako'}
DOCS = ('.gitignore','README.md','docs/validation/review-foundation.md','docs/validation/app-review-integration.md','docs/getting-started.md',
    'alembic.ini','requirements.txt','requirements-lock.txt','requirements-workflow.in',
    'requirements-workflow.txt','requirements-app.in','requirements-app.txt',
    'tests/conftest.py','tests/test_review_workflow.py','tests/test_app_review_graph.py',
    'tools/prepare_app_publication.py','docs/upgrade_audit/PROGRESS.md',
    'docs/upgrade_audit/PROGRESS.md')


def prepare(destination):
    root = Path(__file__).resolve().parents[1]
    target = Path(destination).resolve()
    if target.exists(): raise ValueError('Candidate directory must be new')
    target.mkdir(parents=True)
    paths = {root/name for name in ('config_env.py','spend_tripwire.py','vertex_spend.py','reel.py') if (root/name).is_file()}
    # Preserve existing subsystem sources, including the unmodified media subsystem.
    for package in PACKAGES:
        paths.update(p for p in (root/package).rglob('*') if p.is_file() and p.suffix in EXTENSIONS and '__pycache__' not in p.parts)
    paths.update(p for p in (root/'frontend').rglob('*') if p.is_file() and p.suffix in {'.html','.js','.css','.svg'} and 'node_modules' not in p.parts)
    paths.update((root/'frontend/assets'/n) for n in ['logo.png'])
    paths.update(root/p for p in DOCS if (root/p).is_file())
    paths.update(p for p in (root/'tests').rglob('*') if p.is_file() and p.suffix in {'.py','.cjs'} and '__pycache__' not in p.parts)
    public_architecture = ('app_review.md','app_publication.md','review_workflow.md','review_graph.mmd',
        'app_browser_evidence.json','app_candidate_evidence.json','app_validation.txt',
        'foundation_demo_evidence.json','foundation_sdk_observations.json')
    paths.update(root/'docs/architecture'/name for name in public_architecture if (root/'docs/architecture'/name).is_file())
    paths.update(p for p in (root/'docs/images').glob('*') if p.suffix in {'.png','.jpg'})
    paths.add(root/'config/runtime.production.toml')
    paths.add(root/'docs/legacy/README.md')
    for folder in ('docs/results','examples/volume-cafe'):
        paths.update(p for p in (root/folder).glob('*') if p.is_file() and p.suffix in {'.md','.json','.png'})
    findings = []
    patterns = {'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'google_key':r'AIza[0-9A-Za-z_-]{30,}',
        'provider_token':r'\b(?:sk-proj-|sk-ant-|ghp_)[A-Za-z0-9_-]{20,}',
        'aws_access_key':r'\bAKIA[A-Z0-9]{16}\b'}
    rows = []
    for source in sorted(paths):
        rel = source.relative_to(root)
        raw = source.read_bytes()
        if source.suffix not in {'.png'}:
            text = raw.decode('utf-8',errors='replace')
            for rule,pattern in patterns.items():
                for m in re.finditer(pattern,text):
                    findings.append({'path':str(rel),'line':text.count('\n',0,m.start())+1,'rule':rule})
        out = target/rel; out.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,out)
        rows.append({'path':str(rel),'sha256':hashlib.sha256(raw).hexdigest()})
    manifest = {'scope':'application source, curated archived results and focused review validation; no live provider certification',
        'files':rows,'secret_scan':{'patterns':list(patterns),'findings':findings},
        'exclusions':['.env*','database_export','outputs','scrapes','runs','registry','private root JSON/media',
                      'frontend/assets/showcase (customer-derived imagery; rights not assessed)',
                      'historical audit artifacts','venvs','checkpoints','Git metadata']}
    manifest_path = target/'docs/publication/manifest.json'
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'candidate':str(target),'files':len(rows),'secret_findings':findings}))
    if findings: raise SystemExit('Candidate contains credential findings; do not publish')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination')
    prepare(parser.parse_args().destination)
