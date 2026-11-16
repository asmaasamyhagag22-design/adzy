"""Explicit source-only candidate; never reads env files, databases, runtime or customer media."""
import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil

PACKAGES = ('benchmark','info','ads_intel','advisor','api','brand','business_profile','campaign','competitor',
    'creative','dashboard','db','decision_engine','grounding','instagram_intelligence',
    'launch_bundle','marketing_intel','media_plan','media_plan_v2','ops','policy_linter',
    'poster','reel','reviews','scraper','scripts','social_intel','strategy','telemetry','trends','workflows')
EXTENSIONS = {'.ttf','.py','.yaml','.yml','.toml','.sql','.mako'}
DOCS = ('COPY_EDITOR_RESULT.md','.gitignore','README.md','THIRD_PARTY_ASSETS.md','POSTER_V1_PUBLICATION_REVIEW.md','FOUNDATION_RESULT.md','APP_GRAPH_INTEGRATION_RESULT.md',
    'alembic.ini','requirements.txt','requirements-lock.txt','requirements-workflow.in',
    'requirements-workflow.txt','requirements-app.in','requirements-app.txt',
    'tests/conftest.py','tests/test_review_workflow.py','tests/test_app_review_graph.py',
    'tools/prepare_app_publication.py','POSTER_V1_RELEASE_RESULT.md',
    'POSTER_BOUNDARY_RESULT.md','CLAIM_ASSESSMENT_RESULT.md','SEMANTIC_QUALIFICATION_RESULT.md',
    'tools/check_poster_v1.py','tools/verify_poster_v1_browser.py','pytest.ini','.github/workflows/poster-v1.yml','tests/test_poster_delivery.py','tests/test_poster_operation_boundary.py',
    'tests/poster_boundary_support.py','tests/test_poster_acceptance_truth.py',
    'tests/test_poster_dispatch_policy.py','tests/test_poster_rtl_alignment.py','tests/test_portfolio_authorization.py',
    'docs/demo/README.md',
    'tests/test_poster_claim_assessment.py','tests/test_unused_reservations.py',
    'tests/poster_operations_frontend.test.cjs','tests/wp0_swot_frontend.test.cjs',
    'grounding/poster_claim_contract_fixture.json','tests/test_webapp_spend_gate.py',
    'tests/test_route_persistence_classification_2gb.py','tests/test_security_headers.py')


CAMPAIGN_DOCS = {
    'docs/campaigns/transformation': (
        'ARCHITECTURE.md', 'OPERATIONS.md', 'REPORT.md', 'REVIEW-PACKET.md', 'RISKS.md',
        'browser-results.json', 'context-cases.json', 'context-manifest.json', 'pilot-drill.json',
        'protocol.md', 'publisher-diagnostics.json', 'replay-outcomes.json',
    ),
    'docs/images/campaign-architecture': ('diagram.html', 'overview.webp', 'overview-mobile.webp'),
    'docs/images/campaign-showcase': (
        'README.md', 'buffalo-formats.webp', 'campaign-review-mobile.webp',
        'campaign-review.webp', 'verification.json',
    ),
}
CAMPAIGN_SNAPSHOTS = (
    'docs/demo/campaign-snapshots/buffalo', 'docs/demo/campaign-snapshots/five-guys',
    'docs/demo/campaign-snapshots/nandos', 'docs/campaigns/lifecycle/snapshots/osm',
    'docs/campaigns/lifecycle/snapshots/python', 'docs/campaigns/editing/snapshots/debian',
    'docs/campaigns/editing/snapshots/nasa', 'docs/campaigns/editing/snapshots/sqlite',
)


class ImageTargets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        if tag not in {'img', 'source'}:
            return
        media = dict(attrs)
        if media.get('src'):
            self.targets.append(media['src'])
        for candidate in (media.get('srcset') or '').split(','):
            if fields := candidate.split():
                self.targets.append(fields[0])


def release_test_paths(root):
    """Only selected release checks and their local Python helper imports, not every old test."""
    from check_poster_v1 import CHECKS
    pending = [root/'tests'/name for name in CHECKS]
    pending += [root/'tests'/name for name in (
        'conftest.py','poster_operations_frontend.test.cjs','wp0_swot_frontend.test.cjs',
        'copy_editor_review_fixture.py','test_adzy_v2_research.py','test_adzy_v2_campaign.py')]
    found = set()
    while pending:
        path = pending.pop()
        if path in found: continue
        if not path.is_file(): raise ValueError('Missing release test dependency: '+str(path))
        found.add(path)
        if path.suffix != '.py': continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import): names = [v.name for v in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
                if node.module == 'tests': names += ['tests.'+v.name for v in node.names]
            for name in names:
                local = name.removeprefix('tests.').split('.')[0]
                candidate = root/'tests'/(local+'.py')
                if candidate.is_file() and candidate not in found: pending.append(candidate)
    return found


def source_candidate(path, root):
    rel = path.relative_to(root)
    if any((parent/'PUBLICATION_MANIFEST.json').is_file() for parent in path.parents if parent!=root and root in parent.parents):
        return False
    return (path.is_file() and not path.is_symlink() and
        not any(part.startswith('.') or part in {'node_modules','__pycache__','outputs','runs','scrapes','venv','artifacts','dist','build','scratch','exports'}
                for part in rel.parts))


def prepare(destination):
    root = Path(__file__).resolve().parents[1]
    target = Path(destination).resolve()
    if target.exists(): raise ValueError('Candidate directory must be new')
    target.mkdir(parents=True)
    paths = {root/name for name in ('config_env.py','vertex_spend.py','spend_tripwire.py','reel.py')}
    # Preserve existing subsystem sources, including the unmodified media subsystem.
    for package in PACKAGES:
        paths.update(p for p in (root/package).rglob('*') if source_candidate(p,root) and p.suffix in EXTENSIONS)
    paths.update(p for p in (root/'frontend').rglob('*') if source_candidate(p,root) and p.suffix in {'.html','.js','.css','.svg'})
    # Public showcase is limited to explicit project-authored synthetic copies.
    paths.update(p for p in (root/'frontend/assets/showcase').glob('*') if p.name.startswith(('01_synthetic_','02_synthetic_','03_synthetic_')) and p.suffix in {'.png','.jpg'})
    paths.add(root/'frontend/assets/showcase/PROVENANCE.json')
    paths.update(release_test_paths(root))
    paths.update(p for p in (root/'tools').glob('*.py'))
    paths.update(root/n for n in ('.env.example','docs/getting-started.md','docs/results/README.md','docs/results/provenance.json','docs/results/studio-fixture-poster.png','docs/results/studio-fixture-result.json','docs/results/copy-editor-draft.png','docs/results/copy-editor-draft.json','docs/licenses/Noto-OFL.txt','examples/volume-cafe/README.md','examples/volume-cafe/profile-excerpt.json'))
    paths.update(root/p for p in DOCS if (root/p).is_file())
    # The portfolio demo packages selected reviewed media and replay inputs.
    portfolio = root/'docs/campaigns/buffalo/portfolio'
    for name in ('case.json', 'edition.json', 'poster-portrait.png', 'poster-square.png',
                 'poster-portrait-390.png', 'poster-square-390.png', 'campaign.zip',
                 'campaign.json', 'comparison.jpg', 'REVIEW.md', 'pricing.json', 'browser-result.json', 'rejected.json',
                 'gemini-critique.md', 'gemini-final-critique.md'):
        paths.add(portfolio/name)
    paths.update(path for path in (portfolio/'prompts').glob('*.prompt') if source_candidate(path, root))
    for path in (root/'poster/campaign_scenes').glob('*.jpg'):
        if re.fullmatch(r'[0-9a-f]{64}', path.stem) and source_candidate(path, root):
            paths.add(path)
    for directory, names in CAMPAIGN_DOCS.items():
        paths.update(root/directory/name for name in names)
    for directory in CAMPAIGN_SNAPSHOTS:
        paths.update(path for path in (root/directory).glob('*')
                     if source_candidate(path, root) and path.suffix in {'.html', '.json'})
    for study in ('editing', 'lifecycle'):
        paths.update(root/'docs/campaigns'/study/name for name in ('PROTOCOL.md', 'cases.json'))
    paths.update(root/'docs/campaigns'/name for name in ('INTERVIEW.md', 'buffalo/editions.json'))
    paths.update(root/'docs/experiments/profile-refresh'/name for name in (
        'RESULT.md', 'browser-result.json', 'desktop-recovery.png', 'mobile-recovery.png'))
    paths.update(root/'docs/experiments/review-shadow'/name for name in (
        'RESULT.md', 'PROTOCOL.md', 'cases.json', 'labels.json', 'comparison.json',
        'browser-result.json', 'desktop-changed-evidence.png', 'mobile-changed-evidence.png'))
    paths.update(root/'docs/v2'/name for name in (
        'EXECUTION.md', 'research-release.json', 'research-result.json', 'public-research.json',
        'copy-release.json', 'copy-result.json', 'browser-result.json', 'campaign-desktop.png',
        'campaign-mobile.png', 'exception.png'))
    extra=('POSTER_ECONOMIC_REPAIR_RESULT.md','tests/test_poster_economic_repair.py',
        'tests/test_poster_qa_brand_roles.py','tests/test_poster_candidate_persistence.py',
        'tests/test_poster_design_controls.py','tests/test_poster_acceptance_red_boundary.py',
        'tests/test_gate2c_canonical_poster.py','tests/test_compiler_source_support.py',
        'docs/upgrade_audit/evidence/claim_assessment/rendered_escape.png',
        'tests/test_vision_qa_hard_gate_matrix.py','tests/test_poster_oneshot_best_attempt.py',
        'tests/test_arabic_copy_is_typeset_not_stacked.py','tests/test_the_cta_arrow_is_an_arrow_not_a_tofu_box.py',
        'tools/check_local_poster_preview.py','docs/experiments/poster_compiler_source_support_20261018/RESULT.md',
        'docs/experiments/poster_compiler_source_support_20261018/context.json')
    paths.update(root/p for p in extra if (root/p).is_file())
    # Third-party optional artwork / private portfolio inputs are deliberately absent.
    paths.update(root/'docs/architecture'/name for name in ('app_publication.md','app_review.md','review_workflow.md','review_graph.mmd'))
    paths.update(p for p in (root/'docs/images').glob('*') if p.suffix in {'.png','.jpg'} and not p.name.startswith('portfolio-'))
    # Static public fixtures are dependencies of retained application tests.
    for package in ('reviews','social_intel'):
        paths.update(p for p in (root/package/'fixtures').rglob('*') if p.is_file() and p.suffix in {'.json','.html'})
    paths.update(root/p for p in ('benchmark/ground_truth.json','benchmark/urls.json','benchmark/urls.md','instagram_intelligence/requirements.txt','instagram_intelligence/README.md'))
    paths.update(root/'docs/validation'/name for name in ('app-review-integration.md','review-foundation.md'))
    paths.add(root/'config/runtime.production.toml')
    paths = {p for p in paths if p.is_file()}
    findings = []
    patterns = {'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'google_key':r'AIza[0-9A-Za-z_-]{30,}',
        'provider_token':r'\b(?:sk-proj-|sk-ant-|ghp_)[A-Za-z0-9_-]{20,}',
        'aws_access_key':r'\bAKIA[A-Z0-9]{16}\b'}
    rows = []
    for source in sorted(paths):
        rel = source.relative_to(root)
        raw = source.read_bytes()
        if source.suffix not in {'.png', '.ttf', '.webp'}:
            text = raw.decode('utf-8',errors='replace')
            for rule,pattern in patterns.items():
                for m in re.finditer(pattern,text):
                    findings.append({'path':str(rel),'line':text.count('\n',0,m.start())+1,'rule':rule})
        out = target/rel; out.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,out)
        rows.append({'path':str(rel),'sha256':hashlib.sha256(raw).hexdigest()})
    # Verify navigable public docs without importing application or touching private data.
    broken_links = []
    public_docs = [target/'README.md', target/'COPY_EDITOR_RESULT.md', target/'POSTER_V1_RELEASE_RESULT.md', target/'POSTER_V1_PUBLICATION_REVIEW.md', target/'POSTER_ECONOMIC_REPAIR_RESULT.md', target/'docs/demo/README.md', target/'docs/getting-started.md', target/'docs/results/README.md', target/'examples/volume-cafe/README.md',
                   target/'docs/v2/EXECUTION.md', *list((target/'docs/architecture').glob('*.md'))]
    public_docs.extend(target/directory/name for directory, names in CAMPAIGN_DOCS.items()
                       for name in names if name.endswith('.md'))
    for doc in public_docs:
        if not doc.exists(): continue
        markdown = doc.read_text(encoding='utf-8')
        embedded = ImageTargets()
        embedded.feed(markdown)
        for link in re.findall(r'\]\(([^)]+)\)', markdown) + embedded.targets:
            if re.match(r'^(https?://|mailto:|#)', link): continue
            if not (doc.parent/link.split('#')[0]).exists():
                broken_links.append({'path':str(doc.relative_to(target)), 'target':link})
    if broken_links: raise ValueError('Broken public documentation links: '+str(broken_links))
    manifest = {'public_document_links': {'checked':True,'broken':broken_links}, 'scope':'Public Poster V1 source with fictional demo and synthetic assets; no live provider certification',
        'asset_notice':'THIRD_PARTY_ASSETS.md',
        'files':rows,'secret_scan':{'patterns':list(patterns),'findings':findings},
        'exclusions':['.env* except placeholder-only .env.example','database_export','outputs','scrapes','runs','registry','private root JSON/media',
                      'third-party/customer showcase imagery (only explicit synthetic replacements included)',
                      'Firefox artwork / branded optional portfolio / opaque logo.png / historical marketing concepts',
                      'historical audit manifests / validation logs / unrelated tests','preview original-state.json / usage-records.json / manifest.json',
                      'venvs','checkpoints','Git metadata']}
    (target/'PUBLICATION_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'candidate':str(target),'files':len(rows),'secret_findings':findings}))
    if findings: raise SystemExit('Candidate contains credential findings; do not publish')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination')
    prepare(parser.parse_args().destination)
