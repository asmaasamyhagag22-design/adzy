"""Offline candidate evaluation; synthetic reference labels, no semantic qualification.
Run python -m grounding.evaluate_poster_claims --canonical for real HTTP/SQL gate checks too.
"""
import argparse
import json
import os
import tempfile
from pathlib import Path
import subprocess
import sys
from grounding.measure_claim_recall import load_cases, evaluate, summarise
from poster.claim_assessment import POLICY, METHOD

# Scenario IDs, not paraphrases, define the split. Held-out families were not used to tune policy.
SCENARIOS = [
 ('dev-onboarding','en','Guided specialist onboarding','Guided specialist onboarding','SUPPORTED',True),
 ('dev-speed','en','Published delivery process','Fast specialist onboarding','INSUFFICIENT_EVIDENCE',True),
 ('dev-negation','ar','لا نوفر خدمة التوصيل','نوفر خدمة التوصيل','CONTRADICTED',True),
 ('dev-discount','en','Up to 30% off selected products','30% off everything','INSUFFICIENT_EVIDENCE',True),
 ('dev-currency','en','Plan A costs USD 500','Plan A costs EUR 500','CONTRADICTED',True),
 ('dev-review','en','One reviewer praised Product A','Every product is guaranteed','INSUFFICIENT_EVIDENCE',True),
 ('dev-invitation','ar','','تعالى زورنا الأسبوع ده','NONFACTUAL',False),
 ('dev-entity','ar','المنتج أ متاح اليوم','المنتج ب متاح اليوم','INSUFFICIENT_EVIDENCE',True),
 ('dev-missing','en','','Open around the clock','UNAVAILABLE',True),
 ('dev-paraphrase','en','Delivery is free','Delivery costs you nothing','SUPPORTED',True),
 ('held-warranty','en','Repairs covered for two years','Repairs covered for two years','SUPPORTED',True),
 ('held-hours','ar','الفرع مغلق يوم الجمعة','الفرع مفتوح يوم الجمعة','CONTRADICTED',True),
 ('held-sale','ar','خصم يصل إلى ٢٠٪ على أحذية مختارة','خصم ٢٠٪ على كل الأحذية','INSUFFICIENT_EVIDENCE',True),
 ('held-time','en','Courier dispatch on weekdays','Your order arrives before evening','INSUFFICIENT_EVIDENCE',True),
 ('held-product','en','Product Cedar contains zinc','Product Maple contains zinc','INSUFFICIENT_EVIDENCE',True),
 ('held-count','ar','لدينا ثلاثة فروع','لدينا ثلاثون فرعًا','CONTRADICTED',True),
 ('held-missing-refunds','ar','','نسترد قيمة الطلب دون شروط','UNAVAILABLE',True),
 ('held-creative','en','','A little joy for your day','NONFACTUAL',False),
 ('held-maintenance-paraphrase','ar','الصيانة تشمل قطع الغيار','قطع الغيار مشمولة في الصيانة','SUPPORTED',True),
 ('held-ambiguous','en','A customer mentioned convenience','The most convenient service','INSUFFICIENT_EVIDENCE',True),
]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--canonical',action='store_true')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--semantic-plan',type=Path)
    parser.add_argument('--semantic-report',type=Path)
    parser.add_argument('--semantic-live',type=Path)
    parser.add_argument('--authorization',type=Path)
    parser.add_argument('--batch',choices=['smoke3','development_remaining7','held_out10','diagnostic1'])
    args=parser.parse_args()
    if args.semantic_plan:
        freeze_semantic_plan(args.semantic_plan); return 0
    if args.semantic_report:
        report=semantic_comparison(args.semantic_report); print(json.dumps({'decision':report['decision'],'candidate_model_calls':report['candidate_model_calls']})); return 0
    if args.semantic_live:
        run_semantic_live(args.semantic_live, args.authorization, args.batch); return 0
    from poster.strategy_contract import CreativeContract
    from poster.claim_assessment import assess_copy
    # Build actual compiler input from the canonical application fixture through existing tests
    # only in --canonical mode. Offline corpus uses a serialized explicit synthetic contract.
    rows=[]
    template=json.loads(Path(__file__).with_name('poster_claim_contract_fixture.json').read_text())
    for scenario,language,quote,copy,label,hard in SCENARIOS:
        value={**template, 'copy_evidence': ([{'evidence_id':'e','excerpt':quote,'source_url':'https://fiction.example.test/'+scenario,
            'availability':'AVAILABLE','source_class':'BRAND_OWNED','subject_ref':template['domain_reference']}] if quote else []),
            'allowed_claims':[{'claim_id':'c','statement':quote,'verification_status':'SUPPORTED','supporting_evidence_ids':['e']}],
            'allowed_claim_ids':['c'], 'internal_supporting_claim_ids':[]}
        assessment=assess_copy(brief={'headline':copy},contract=CreativeContract.model_validate(value))
        predicted=assessment.judgments[0].verdict
        rows.append({'scenario':scenario,'split':'held_out' if scenario.startswith('held-') else 'development',
            'language':language,'reference_label':label,'reference_origin':'SYNTHETIC_NOT_HUMAN_REVIEWED',
            'predicted':predicted,'hard':hard,'eligible':assessment.authored_copy_eligible})
    result={'policy':POLICY,'method':METHOD,'semantic_quality_measured':False,
        'original_corpus_scope':'Legacy direct/held_out are paraphrases of the same underlying kinds; extraction only, not independent semantic support evaluation.',
        'original_corpus':summarise(evaluate(load_cases())['rows']),
        'atomic_extraction_precision':{'numerator':None,'denominator':None,'reason':'Whole-field candidates; no atomic semantic extractor qualified.'},
        'atomic_extraction_recall':{'numerator':None,'denominator':None},'splits':{},'rows':rows}
    for split in ('development','held_out'):
        subset=[r for r in rows if r['split']==split];hard=[r for r in subset if r['hard']];supported=[r for r in subset if r['reference_label']=='SUPPORTED']
        predicted=[r for r in subset if r['predicted']=='SUPPORTED']
        result['splits'][split]={
            'whole_field_candidate_precision':{'numerator':len(hard),'denominator':len(subset)},
            'whole_field_candidate_recall':{'numerator':len(hard),'denominator':len(hard)},
            'support_false_positive':{'numerator':sum(r['reference_label']!='SUPPORTED' for r in predicted),'denominator':len(predicted)},
            'support_verdict_disagreement':{'numerator':sum(r['predicted']!=r['reference_label'] for r in hard),'denominator':len(hard)},
            'false_block_supported':{'numerator':sum(not r['eligible'] for r in supported),'denominator':len(supported)},
            'abstention':{'numerator':sum(r['predicted'] in ('UNAVAILABLE','INSUFFICIENT_EVIDENCE') for r in subset),'denominator':len(subset)},
            'full_HTTP_accepted_escape':{'numerator':None,'denominator':None,'reason':'Corpus is offline assessment only; canonical HTTP/SQL tests reported separately.'}}
    if args.canonical:
        with tempfile.TemporaryDirectory(prefix='adzy-claim-evaluation-') as directory:
            rows_path=Path(directory)/'rows.jsonl'
            completed=subprocess.run([sys.executable,'-m','pytest','-q','tests/test_poster_claim_assessment.py'],
                capture_output=True,text=True,env={**os.environ,'ADZY_CLAIM_EVALUATION_ROWS':str(rows_path)})
            actual=[json.loads(line) for line in rows_path.read_text().splitlines()] if rows_path.exists() else []
        result['canonical_tests']={'exit_code':completed.returncode,'output':completed.stdout+completed.stderr,'rows':actual}
        for split in ('development','held_out'):
            subset=[r for r in actual if r['split']==split]
            unsupported=[r for r in subset if r['reference_label'] in ('CONTRADICTED','INSUFFICIENT_EVIDENCE','UNAVAILABLE')]
            supported=[r for r in subset if r['reference_label']=='SUPPORTED']
            result['splits'][split]['full_HTTP_accepted_escape']={'numerator':sum(r['actual_accepted'] for r in unsupported),'denominator':len(unsupported)}
            result['splits'][split]['full_HTTP_false_block_supported']={'numerator':sum(not r['actual_accepted'] for r in supported),'denominator':len(supported)}
            result['splits'][split]['full_HTTP_cases_completed']=len(subset)
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output: args.output.write_text(output+'\n')
    else: print(output)
    return result.get('canonical_tests',{}).get('exit_code',0)


# Semantic qualification orchestration extends this existing evaluator; it is not another judge.
def semantic_case(row):
    from poster.strategy_contract import CreativeContract
    template=json.loads(Path(__file__).with_name('poster_claim_contract_fixture.json').read_text())
    quote=row['evidence']
    value={**template,'copy_evidence':([{'evidence_id':'e','excerpt':quote,
        'source_url':'https://fiction.example.test/'+row['scenario'],'availability':'AVAILABLE',
        'source_class':'BRAND_OWNED','subject_ref':template['domain_reference']}] if quote else []),
        'allowed_claims':[{'claim_id':'c','statement':quote,'verification_status':'SUPPORTED','supporting_evidence_ids':['e']}],
        'allowed_claim_ids':['c'],'internal_supporting_claim_ids':[]}
    return {'headline':row['copy']},CreativeContract.model_validate(value)


def freeze_semantic_plan(directory):
    from poster.claim_assessment import (SHADOW_MODEL, SHADOW_VERSION, MAX_PROMPT_BYTES, digest,
        ANCHOR_METHOD,EXTRACTION_SCHEMA_VERSION,SOURCE_SEGMENT_CHARACTERS)
    from poster.copy_semantic_prompts import EXTRACTION_PROMPT,SUPPORT_PROMPT,EXTRACTION_VERSION,SUPPORT_VERSION
    root=Path(directory)
    if (root/'experiment_manifest.v1.json').exists(): raise ValueError('Experiment already frozen; use a new version, never overwrite.')
    dataset=json.loads((root/'dataset.json').read_text())
    dispatches=[]
    for row in dataset:
        brief,contract=semantic_case(row)
        for stage in ('poster_copy_extract','poster_copy_support'):
            dispatches.append({'scenario':row['scenario'],'split':row['split'],'stage':stage,
                'model':SHADOW_MODEL,'copy_hash':digest(brief),'evidence_snapshot_hash':digest(contract.copy_evidence),
                'estimate_usd':'0.183500800','condition':'always' if stage.endswith('extract') else 'only after complete valid nonempty extraction; no silent truncation'})
    manifest={'experiment_version':'adzy.semantic-copy-experiment@1','created_at':'2026-10-17',
        'decision_before_live':'BLOCKED','owner_authorized_budget_usd':None,'human_review_status':'PENDING',
        'model':{'requested_id':SHADOW_MODEL,'surface':'VERTEX_AI','location':'global','account_access':'UNVERIFIED',
            'official_model_url':'https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-flash-lite',
            'official_lifecycle_url':'https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions',
            'documentation_checked_at':'2026-10-17','ga':True,'retirement_not_before':'2027-07-21'},
        'pricing':{'url':'https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing',
            'checked_at':'2026-10-17','recheck_after':'2026-10-24','service':'Vertex / Agent Platform Standard PayGo global text',
            'usd_per_million_input':'0.30','usd_per_million_output_including_reasoning':'2.50',
            'input_token_estimate_allowance':65536,'output_token_estimate_allowance':65536,
            'estimate_is_invoice_ceiling':False,'per_dispatch_estimate_usd':'0.183500800'},
        'limits':{'input_bytes_including_system_and_schema':MAX_PROMPT_BYTES,'max_propositions':20,'max_coverage_spans':120,'max_citations_per_judgment':8,'max_span_characters':2000,'max_quote_characters':4000,
            'extraction_max_output_tokens':8192,'support_max_output_tokens':4096,'thinking_level':'MINIMAL',
            'temperature':'provider default; custom setting omitted','timeout_seconds':60,'sdk_attempts':1,
            'application_retries':0,'tools':False,'grounding':False,'repair_dispatches':0},
        'prompts':{'extraction_version':EXTRACTION_VERSION,'support_version':SUPPORT_VERSION,
            'extraction_hash':digest(EXTRACTION_PROMPT),'support_hash':digest(SUPPORT_PROMPT),'shadow_method':SHADOW_VERSION},
        'split':{s:[r['scenario'] for r in dataset if r['split']==s] for s in ('development','held_out')},
        'split_limitations':['Same 20 scenario/evidence groups as literal experiment; historical synthetic labels already exposed.',
            'No semantic held-out predictions observed; frozen candidate v1 receives no held-out-guided prompt changes.',
            'Header-only cohort cannot establish complete-poster or pixel-text eligibility.'],
        'smoke_scenarios':['dev-onboarding','dev-paraphrase','dev-discount'],
        'batches':['smoke3','development_remaining7','held_out10'],
        'continuation_requires_operator_review':True,
        'smoke_max_dispatches':6,'smoke_estimated_reservation_usd':'1.101004800',
        'max_dispatches':40,'max_planned_reservations_usd':'7.340032000','proposed_budget_request_usd':'7.35','dispatches':dispatches,
        'preregistered_criteria':{
            'human_atomic_labels':'Independent review and adjudication for all eligible evaluation cases; unknown labels excluded with denominator disclosed.',
            'unsupported_candidate_support':'0/7 each split; a false supported unsupported assertion stops the run, never promoted.',
            'supported_candidate_support':'2/2 each split, improving observed literal 1/2; exact quotes must remain supported.',
            'abstention':'at most 3/10 each split, compared with literal 9/10; prevents block-everything qualification.',
            'atomic_extraction':'zero observed missed or falsely asserted factual propositions on independently reviewed evaluated cases; report counts, no population guarantee.',
            'runtime':'No invalid span/reference, unexplained truncation, scope failure, unknown receipt or unrecorded dispatch in a qualifying cohort.',
            'latency':'report all values and denominators; no latency threshold invented after results.',
            'rationale':'For a small pilot, first require useful improvement and no observed safety regression. This does not prove zero population error.',
            'activation':'never automatic; new owner pilot decision required'},
        'stop_conditions':['unexpected unsupported would-support','malformed/truncated/incomplete output','invalid citation/span',
            'unknown/missing usage or provider outcome','model/version drift','input or proposition cap exceeded',
            'missing estimate or exhausted reservation','private evidence exported','any attempted promotion'],
        'artifacts':{}}
    files=[root/'dataset.json',root/'human_review_packet.json',root/'review_id_map.json',*sorted(p for p in (root/'frozen').glob('*') if p.is_file())]
    for path in files: manifest['artifacts'][str(path.relative_to(root))]=__import__('hashlib').sha256(path.read_bytes()).hexdigest()
    import importlib.metadata
    source_root=Path(__file__).resolve().parents[1]
    manifest['candidate_build']={'python':sys.version.split()[0],
        'packages':{name:importlib.metadata.version(name) for name in ('google-genai','pydantic','SQLAlchemy')},
        'literal_policy_after_deterministic_correction':'adzy.poster.copy-evidence@1.1',
        'schemas':{'extraction':digest(__import__('poster.claim_assessment',fromlist=['SourceQuotedExtraction']).SourceQuotedExtraction.model_json_schema()),
                   'canonical_extraction':digest(__import__('poster.claim_assessment',fromlist=['AtomicCopyExtraction']).AtomicCopyExtraction.model_json_schema()),
                   'support':digest(__import__('poster.claim_assessment',fromlist=['AtomicCopySupport']).AtomicCopySupport.model_json_schema())},
        'source_sha256':{name:__import__('hashlib').sha256((source_root/name).read_bytes()).hexdigest() for name in (
            'poster/claim_assessment.py','poster/copy_semantic_prompts.py','poster/strategy_contract.py',
            'poster/paid_boundary.py','poster/provider_diagnostics.py','business_profile/llm/caller.py','business_profile/llm/validator.py',
            'grounding/ledger.py','grounding/evaluate_poster_claims.py','db/poster_operations.py','workflows/demo.py')}}
    from poster.claim_assessment import provider_schema_projection,SourceQuotedExtraction,AtomicCopySupport
    manifest['provider_schema_projection']={'version':'adzy.copy-provider-schema@1',
        'extraction_sha256':digest(provider_schema_projection(SourceQuotedExtraction)),
        'support_sha256':digest(provider_schema_projection(AtomicCopySupport)),
        'strict_local_validation':'unchanged full Pydantic models plus span/evidence/scope/number validators'}
    manifest['source_anchoring']={'method':ANCHOR_METHOD,'extraction_schema_version':EXTRACTION_SCHEMA_VERSION,
        'segment_characters':SOURCE_SEGMENT_CHARACTERS,'unit':'Python Unicode code points',
        'matching':'exact unique occurrence in hash-bound field/segment; no normalization',
        'coverage':'mechanically resolved model declarations; not semantic completeness'}
    (root/'experiment_manifest.v1.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest


def verify_semantic_manifest(directory):
    from poster.claim_assessment import digest,SHADOW_MODEL
    from poster.copy_semantic_prompts import EXTRACTION_PROMPT,SUPPORT_PROMPT
    root=Path(directory);manifest=json.loads((root/'experiment_manifest.v1.json').read_text())
    for name,expected in manifest['artifacts'].items():
        if __import__('hashlib').sha256((root/name).read_bytes()).hexdigest()!=expected: raise ValueError('FROZEN_ARTIFACT_CHANGED')
    if (manifest['prompts']['extraction_hash']!=digest(EXTRACTION_PROMPT)
            or manifest['prompts']['support_hash']!=digest(SUPPORT_PROMPT) or manifest['model']['requested_id']!=SHADOW_MODEL):
        raise ValueError('CANDIDATE_VERSION_CHANGED_USE_NEW_EXPERIMENT')
    import importlib.metadata
    for name,version in manifest.get('candidate_build',{}).get('packages',{}).items():
        if importlib.metadata.version(name)!=version: raise ValueError('CANDIDATE_RUNTIME_CHANGED_USE_NEW_EXPERIMENT')
    source_root=Path(__file__).resolve().parents[1]
    for name,expected in manifest.get('candidate_build',{}).get('source_sha256',{}).items():
        if __import__('hashlib').sha256((source_root/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('CANDIDATE_SOURCE_CHANGED_USE_NEW_EXPERIMENT')
    if 'provider_schema_projection' in manifest:
        from poster.claim_assessment import provider_schema_projection,SourceQuotedExtraction,AtomicCopySupport
        for stage,schema in (('extraction',SourceQuotedExtraction),('support',AtomicCopySupport)):
            if digest(provider_schema_projection(schema))!=manifest['provider_schema_projection'][stage+'_sha256']:
                raise ValueError('PROVIDER_SCHEMA_PROJECTION_CHANGED')
    return manifest


def semantic_comparison(directory):
    import time
    from poster.claim_assessment import assess_copy
    root=Path(directory);manifest=verify_semantic_manifest(root)
    dataset=json.loads((root/'dataset.json').read_text())
    baseline=json.loads((root/'frozen/docs__upgrade_audit__evidence__claim_assessment__evaluation.json').read_text())
    before={r['scenario']:r for r in baseline['rows']}
    rows=[]
    for row in dataset:
        brief,contract=semantic_case(row);started=time.monotonic();literal=assess_copy(brief=brief,contract=contract)
        rows.append({'scenario':row['scenario'],'split':row['split'],'label_origin':row['label_origin'],
            'synthetic_label':row['synthetic_support_label'],'frozen_literal_eligible':before[row['scenario']]['eligible'],
            'current_literal_eligible':literal.authored_copy_eligible,
            'current_literal_verdict':literal.judgments[0].verdict,'literal_latency_ms':(time.monotonic()-started)*1000})
    report={'decision':'BLOCKED','candidate_model_calls':0,'candidate_semantic_quality_measured':False,
        'human_reviewed_outputs':0,'scripted_fixture_outputs_used_for_quality':0,'actual_paid_spend_usd':'0',
        'candidate_observed_cost_usd':None,'candidate_latency_ms':None,'candidate_metrics':None,
        'atomic_extraction_precision':{'numerator':None,'denominator':None},
        'atomic_extraction_recall':{'numerator':None,'denominator':None},
        'complete_poster_eligibility':{'numerator':None,'denominator':None,'reason':'Header-only cohort; no complete-poster semantic evaluation.'},
        'prerequisites':['Concrete owner live budget','Current project/model access verification','Independent human atomic/support labels'],
        'baseline_comparison':{},'rows':rows}
    for split in ('development','held_out'):
        subset=[r for r in rows if r['split']==split]
        supported=[r for r in subset if r['synthetic_label']=='SUPPORTED']
        unsupported=[r for r in subset if r['synthetic_label'] in ('CONTRADICTED','INSUFFICIENT_EVIDENCE','UNAVAILABLE')]
        contradicted=[r for r in subset if r['synthetic_label']=='CONTRADICTED']
        report['baseline_comparison'][split]={
            'synthetic_labeled':{'numerator':len(subset),'denominator':len(subset)},
            'human_reviewed':{'numerator':0,'denominator':len(subset)},
            'frozen_supported_acceptance':{'numerator':sum(r['frozen_literal_eligible'] for r in supported),'denominator':len(supported)},
            'corrected_supported_acceptance':{'numerator':sum(r['current_literal_eligible'] for r in supported),'denominator':len(supported)},
            'corrected_unsupported_acceptance':{'numerator':sum(r['current_literal_eligible'] for r in unsupported),'denominator':len(unsupported)},
            'literal_contradiction_misclassification':{'numerator':sum(r['current_literal_verdict']!='CONTRADICTED' for r in contradicted),'denominator':len(contradicted)},
            'literal_abstention':{'numerator':sum(r['current_literal_verdict'] in ('UNAVAILABLE','INSUFFICIENT_EVIDENCE') for r in subset),'denominator':len(subset)},
            'candidate_supported_acceptance':{'numerator':None,'denominator':0},
            'candidate_unsupported_acceptance':{'numerator':None,'denominator':0},
            'candidate_abstention':{'numerator':None,'denominator':0},
            'candidate_coverage':{'numerator':None,'denominator':0},
            'candidate_contradiction_errors':{'numerator':None,'denominator':0}}
    report['deterministic_metadata_probe'] = deterministic_metadata_probe(root)
    report = compare_live_captures(root, manifest, dataset, report)
    (root/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def validate_live_authorization(root, authorization, batch):
    """Operator authorization is an external prerequisite; no funded authorization is generated."""
    from datetime import date, datetime, timezone
    from decimal import Decimal
    import hashlib
    manifest=verify_semantic_manifest(root)
    if authorization is None: raise ValueError('LIVE_BUDGET_AUTHORIZATION_REQUIRED')
    auth=json.loads(Path(authorization).read_text())
    identity=hashlib.sha256((Path(root)/'experiment_manifest.v1.json').read_bytes()).hexdigest()
    if (auth.get('experiment_sha256')!=identity or not auth.get('owner_authorization_reference')
            or auth.get('model')!=manifest['model']['requested_id'] or auth.get('surface')!='VERTEX_AI'
            or auth.get('location')!='global' or not auth.get('project')):
        raise ValueError('LIVE_AUTHORIZATION_BINDING_INVALID')
    budget=Decimal(str(auth.get('budget_usd')))
    if not budget.is_finite() or budget<=0: raise ValueError('POSITIVE_LIVE_BUDGET_REQUIRED')
    if date.today()>date.fromisoformat(manifest['pricing']['recheck_after']): raise ValueError('OFFICIAL_PRICING_RECHECK_REQUIRED_NEW_MANIFEST')
    if date.today()>date.fromisoformat(auth['expires_on']): raise ValueError('LIVE_AUTHORIZATION_EXPIRED')
    access=auth.get('model_access_verification',{})
    if (not access.get('receipt_reference') or access.get('model')!=auth['model']
            or access.get('project')!=auth['project'] or access.get('location')!='global'
            or access.get('checked_on')!=date.today().isoformat()):
        raise ValueError('CURRENT_PROJECT_MODEL_ACCESS_VERIFICATION_REQUIRED')
    if batch not in manifest['batches']: raise ValueError('EXPLICIT_BATCH_REQUIRED')
    if batch=='diagnostic1':
        validate_diagnostic_successor(root,manifest,auth)
    count={'smoke3':3,'development_remaining7':7,'held_out10':10,'diagnostic1':1}[batch]
    if budget < Decimal(manifest['pricing']['per_dispatch_estimate_usd'])*2*count:
        raise ValueError('BATCH_BUDGET_INSUFFICIENT')
    if batch not in ('smoke3','diagnostic1'):
        preceding='smoke3' if batch=='development_remaining7' else 'development_remaining7'
        results_file=Path(root)/'live'/f'{preceding}.json'
        review_file=Path(root)/'live'/f'{preceding}.operator_review.json'
        if not results_file.exists() or not review_file.exists(): raise ValueError('PRECEDING_BATCH_AND_OPERATOR_REVIEW_REQUIRED')
        review=json.loads(review_file.read_text())
        if (review.get('batch_sha256')!=hashlib.sha256(results_file.read_bytes()).hexdigest()
                or review.get('approved_to_continue') is not True or not review.get('reviewer_reference')):
            raise ValueError('PRECEDING_BATCH_REVIEW_INVALID')
    return manifest,auth,budget


def validate_diagnostic_successor(root, manifest, auth):
    """Bind an intentional single-case attempt to the unchanged prior owner and evidence."""
    import hashlib
    from decimal import Decimal
    lineage=manifest.get('successor',{})
    parent=(Path(root)/lineage['predecessor_directory']).resolve()
    def bound(path, expected):
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('SUCCESSOR_PREDECESSOR_BINDING_INVALID')
    bound(parent/'experiment_manifest.v1.json',lineage['predecessor_sha256'])
    authorization_file=lineage.get('predecessor_authorization_file','live/smoke3.authorization.json')
    bound(parent/authorization_file,lineage['predecessor_authorization_sha256'])
    bound(parent/'live/dev-onboarding.json',lineage['predecessor_capture_sha256'])
    old=json.loads((parent/'experiment_manifest.v1.json').read_text())
    old_auth=json.loads((parent/authorization_file).read_text())
    old_capture=json.loads((parent/'live/dev-onboarding.json').read_text())['operation']
    if (lineage.get('predecessor_run_id')!=old_capture['generation_run_id']
            or lineage.get('predecessor_operation')!=old_capture['operation_key']):
        raise ValueError('SUCCESSOR_INCIDENT_LINKAGE_INVALID')
    frozen=old
    anchor=lineage.get('source_anchor_candidate')
    if anchor:
        candidate=(Path(root)/anchor['directory']).resolve()
        bound(candidate/'experiment_manifest.v1.json',anchor['sha256'])
        frozen=json.loads((candidate/'experiment_manifest.v1.json').read_text())
        if (frozen['prompts']['extraction_version']!='adzy.atomic-copy-extraction@2'
                or frozen['source_anchoring']['method']!='adzy.copy-source-anchor@1'
                or frozen['model']!=old['model'] or frozen['pricing']!=old['pricing']
                or manifest.get('prior_service_requests')!=4
                or auth.get('new_metadata_requests_allowed')!=0):
            raise ValueError('SOURCE_ANCHOR_CANDIDATE_AUTHORIZATION_INVALID')
        for name,expected in frozen['artifacts'].items():bound(candidate/name,expected)
    for field in ('model','pricing','limits','prompts','split','artifacts'):
        if manifest[field]!=frozen[field]: raise ValueError('SUCCESSOR_INPUT_OR_CONFIGURATION_CHANGED')
    if manifest['candidate_build']['schemas']!=frozen['candidate_build']['schemas']:
        raise ValueError('SUCCESSOR_SCHEMA_CHANGED')
    for name,expected in old['artifacts'].items(): bound(parent/name,expected)
    if (auth.get('project')!=old_auth['project'] or auth.get('experimental_owner')!=old_auth['experimental_owner']
            or auth.get('attempt_reference')!=lineage.get('attempt_reference')
            or not auth.get('attempt_reference') or auth.get('selected_scenarios')!=['dev-onboarding']
            or auth.get('batch')!='diagnostic1' or auth.get('max_new_generate_content_requests')!=2
            or auth.get('max_provider_submissions')!=6
            or auth.get('expected_prior_service_requests')!=manifest.get('prior_service_requests',2)
            or not 2<=auth.get('expected_prior_service_requests',0)<=4
            or manifest.get('batches')!=['diagnostic1'] or manifest.get('max_dispatches')!=2
            or Decimal(str(auth['budget_usd']))!=Decimal('1.11')
            or Decimal(str(auth['budget_usd']))>Decimal(str(old_auth['budget_usd']))):
        raise ValueError('SUCCESSOR_ATTEMPT_AUTHORIZATION_INVALID')
    access=auth['model_access_verification']
    account_origin=(Path(root)/lineage['accounting_origin_directory']).resolve() if 'accounting_origin_directory' in lineage else parent
    if account_origin!=parent:
        bound(account_origin/'experiment_manifest.v1.json',lineage['accounting_origin_sha256'])
        original_auth=json.loads((account_origin/'live/smoke3.authorization.json').read_text())
        if auth['experimental_owner']!=original_auth['experimental_owner'] or auth['project']!=original_auth['project']:
            raise ValueError('SUCCESSOR_ACCOUNTING_ORIGIN_OWNER_MISMATCH')
    bound(account_origin/'live/smoke3.model_access.json',access['receipt_sha256'])
    if access['receipt_sha256']!=old_auth['model_access_verification']['receipt_sha256']:
        raise ValueError('SUCCESSOR_ACCESS_RECEIPT_CHANGED')
    return account_origin


def run_semantic_live(directory, authorization, batch):
    """Explicit synthetic shadow experiment using existing canonical approvals/reservations.

    No images or canonical acceptances. A complete shadow run may only propose a later pilot.
    """
    from decimal import Decimal
    from uuid import UUID
    from poster.claim_assessment import ProtectedSemanticCaller, assess_semantic_shadow, inventory
    from poster.strategy_contract import CreativeContractCompiler
    from poster.paid_boundary import paid_scope
    root=Path(directory).resolve()
    manifest,auth,budget=validate_live_authorization(root,authorization,batch)  # BEFORE SDK/DB/network creation
    parent=validate_diagnostic_successor(root,manifest,auth) if batch=='diagnostic1' else None
    import config_env
    config_env._loaded=True
    # Disable automatic trace/input capture; this standalone experiment does not load private .env.
    os.environ.update(ADZY_LANGFUSE_ENABLED='0',LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false')
    live=root/'live';live.mkdir(exist_ok=True)
    from workflows.demo import init,connect
    from workflows.synthetic import brand,field
    database_path=(parent/'live/db/demo.json') if parent else live/'db/demo.json'
    if parent and not database_path.exists(): raise ValueError('EXISTING_EXPERIMENT_DATABASE_REQUIRED')
    if not parent and not database_path.exists(): init(live/'db')
    database=json.loads(database_path.read_text())
    if parent and database['actor']!=auth['experimental_owner']: raise ValueError('SUCCESSOR_OWNER_MISMATCH')
    from db.test_database_safety import require_disposable_test_database
    require_disposable_test_database(database['dsn'],environ={'BASEERA_DESTRUCTIVE_TEST_DATABASE':'baseera_pytest_foundation'})
    service,persistence=connect(database)
    operations=persistence.poster_operations()
    experiment_hash=__import__('hashlib').sha256((root/'experiment_manifest.v1.json').read_bytes()).hexdigest()
    dataset=json.loads((root/'dataset.json').read_text())
    if batch=='diagnostic1': cases=[r for r in dataset if r['scenario']=='dev-onboarding' and r['split']=='development']
    elif batch=='smoke3': cases=[r for r in dataset if r['scenario'] in manifest['smoke_scenarios']]
    elif batch=='development_remaining7': cases=[r for r in dataset if r['split']=='development' and r['scenario'] not in manifest['smoke_scenarios']]
    else: cases=[r for r in dataset if r['split']=='held_out']
    state_path=(parent/'live/subjects.json') if parent else live/'subjects.json'
    result_path=live/(batch+'.json')
    if result_path.exists(): raise ValueError('BATCH_ALREADY_CAPTURED_NO_REPLAY')
    # The existing DB executor lock serializes experiment-wide admission across all fixture owners.
    with operations.executor('semantic-experiment:'+experiment_hash,blocking=False) as acquired:
        if not acquired: raise ValueError('EXPERIMENT_ALREADY_RUNNING')
        subjects=json.loads(state_path.read_text()) if state_path.exists() else {}
        reserve=Decimal(manifest['pricing']['per_dispatch_estimate_usd'])*2
        os.environ.update(ADZY_POSTER_BUDGET_USD=str(budget),ADZY_POSTER_RESERVATION_USD=str(reserve),
            ADZY_POSTER_CALL_ESTIMATES_JSON=json.dumps({f'generate_content/{stage}:{auth["model"]}':manifest['pricing']['per_dispatch_estimate_usd']
                for stage in ('poster_copy_extract','poster_copy_support')}))
        from sqlalchemy import select,func
        from db.models import Run,UsageRecord
        with service._uow_factory() as uow:
            charged=uow.session.scalar(select(func.coalesce(func.sum(func.coalesce(Run.reserved_usd,0)+func.coalesce(Run.settled_usd,0)),0)).where(
                Run.created_by_user_id==UUID(database['actor']),Run.operation_key.is_not(None)))
            if parent:
                prior_runs=list(uow.session.scalars(select(Run).where(Run.created_by_user_id==UUID(database['actor']),Run.operation_key.is_not(None))))
                dispatch_count=uow.session.scalar(select(func.count()).select_from(UsageRecord).join(Run,UsageRecord.run_id==Run.id).where(
                    Run.created_by_user_id==UUID(database['actor']),Run.operation_key.is_not(None)))
                # The one historical metadata GET has a retained, hash-bound receipt.
                account={'owner':database['actor'],'budget_usd':str(budget),'reserved_plus_settled_for_admission_usd':str(charged),
                    'new_reservation_usd':str(reserve),'projected_admission_usd':str(charged+reserve),
                    'historical_metadata_requests':1,'existing_generate_content_records':dispatch_count,
                    'service_requests_before':dispatch_count+1,'new_generate_content_limit':2,'cumulative_limit':6,
                    'operations':[{'run_id':str(r.id),'operation_key':r.operation_key,'state':r.operation_state,
                        'reserved_usd':str(r.reserved_usd),'settled_usd':None if r.settled_usd is None else str(r.settled_usd),
                        'known_usage_usd':None if r.known_usage_usd is None else str(r.known_usage_usd)} for r in prior_runs]}
                (live/'diagnostic1.account_before.json').write_text(json.dumps(account,indent=2)+'\n')
                if dispatch_count+1!=auth['expected_prior_service_requests'] or dispatch_count+1+2>auth['max_provider_submissions']:
                    raise ValueError('CUMULATIVE_SERVICE_REQUEST_LIMIT_OR_HISTORY_MISMATCH')
                if len(cases)!=1 or 'dev-onboarding' not in subjects or subjects['dev-onboarding']['actor']!=database['actor']:
                    raise ValueError('EXISTING_DIAGNOSTIC_SUBJECT_REQUIRED')
        new_cases=0
        for case in cases:
            subject=subjects.get(case['scenario'])
            if not subject:
                new_cases+=1;continue
            key='semantic_'+experiment_hash[:16]+'_'+case['scenario']
            with service._uow_factory() as uow:
                prior=uow.session.scalar(select(Run).where(Run.created_by_user_id==UUID(subject['actor']),Run.operation_key==key))
                if prior is None: new_cases+=1
                elif prior.operation_state!='shadow_complete': raise ValueError('PREVIOUS_OPERATION_REQUIRES_RECONCILIATION_NO_REPLAY')
        if charged+reserve*new_cases>budget: raise ValueError('FULL_BATCH_AVAILABLE_RESERVATION_INSUFFICIENT')
        rows=[]
        for case in cases:
            scenario=case['scenario']
            if scenario not in subjects:
                def source(domain):
                    value=brand(domain);quote=case['evidence']
                    if quote:
                        value['_profile']['value_propositions']=[field(quote),value['_profile']['value_propositions'][1]]
                        value['_profile']['trust_signals']=[field('Published '+quote),value['_profile']['trust_signals'][1]]
                        value['_full_result']['competitor_intel']['Synthetic peer']['usps']=[field('Peer alternative: '+quote,'https://peer.example.test/about')]
                    return value
                from workflows.demo import seed
                owner=service.user_by_id(UUID(database['actor']))
                subject=seed(service,persistence,brand_source=source,owner=owner)
                run=service.run_for_user(run_id=UUID(subject['subject']),user_id=UUID(subject['actor']))
                service.decide_current_strategy(brand_id=run.brand_id,user_id=UUID(subject['actor']),run_id=run.id,decision='approved')
                subjects[scenario]=subject
                state_path.write_text(json.dumps(subjects,indent=2))
            subject=subjects[scenario]
            request={'domain_key':subject['domain'],'purpose':'semantic_shadow','experiment_hash':experiment_hash,
                     'scenario':scenario,'authored_copy':inventory({'headline':case['copy']})}
            if parent:
                request['intentional_attempt']={'reference':auth['attempt_reference'],
                    'predecessor_experiment_sha256':manifest['successor']['predecessor_sha256'],
                    'predecessor_run_id':manifest['successor']['predecessor_run_id'],
                    'predecessor_operation':manifest['successor']['predecessor_operation']}
                if manifest['successor'].get('source_anchor_candidate'):
                    request['service_request_authorization']={'cumulative_limit':auth['max_provider_submissions'],
                        'historical_metadata_requests':1,'new_dispatch_limit':auth['max_new_generate_content_requests']}
            key='semantic_'+experiment_hash[:16]+'_'+scenario
            prior=operations.existing(subject['actor'],key,operations.fingerprint(request))
            if prior:
                status=operations.status(subject['actor'],key,recover=False)
                if status['state']!='shadow_complete': raise ValueError('PREVIOUS_OPERATION_REQUIRES_RECONCILIATION_NO_REPLAY')
            else:
                from sqlalchemy import select,func
                from db.models import Run
                with service._uow_factory() as uow:
                    charged=uow.session.scalar(select(func.coalesce(func.sum(func.coalesce(Run.reserved_usd,0)+func.coalesce(Run.settled_usd,0)),0)).where(
                        Run.requested_config['request']['experiment_hash'].astext==experiment_hash))
                if charged+reserve>budget: raise ValueError('EXPERIMENT_BUDGET_EXHAUSTED')
                def compile_current():
                    contract=CreativeContractCompiler().compile(persistence.canonical_strategy_context(
                        domain_key=subject['domain'],user_id=subject['actor'],run_id=subject['subject']))
                    if parent:
                        from poster.claim_assessment import digest,evidence_snapshot
                        original=json.loads((parent/'live/dev-onboarding.json').read_text())['operation']['semantic_shadow_assessment']
                        if contract.creative_contract_id!=original['contract_id'] or digest(evidence_snapshot(contract))!=original['evidence_hash']:
                            raise ValueError('SUCCESSOR_CONTRACT_OR_EVIDENCE_CHANGED')
                    return contract
                run_id,created,contract=operations.admit(subject['actor'],key,request,compile_current)
                if not created: raise ValueError('UNEXPECTED_DUPLICATE_ADMISSION')
                with operations.executor(subject['actor']+':'+key,blocking=True):
                    with paid_scope(operations,run_id):
                        assessment=assess_semantic_shadow(brief=request['authored_copy'],contract=contract,
                            caller=ProtectedSemanticCaller(project=auth['project']),dispatch_run_id=run_id)
                    operations.finish_shadow(subject['actor'],key,assessment.model_dump(mode='json'))
                status=operations.status(subject['actor'],key,recover=False)
            record={'scenario':scenario,'split':case['split'],'capture_origin':'LIVE_SHADOW','experiment_sha256':experiment_hash,
                    'operation':status,'reference_origin':'SYNTHETIC_NOT_HUMAN_REVIEWED'}
            (live/(scenario+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            shadow=status.get('semantic_shadow_assessment') or {}
            versions={(call.get('usage') or {}).get('resolved_model_version') for call in shadow.get('calls',[])} - {None}
            version_file=live/'resolved_model_version.json'
            prior_version=json.loads(version_file.read_text())['version'] if version_file.exists() else None
            version_drift=len(versions)>1 or bool(prior_version and versions and versions!={prior_version})
            if len(versions)==1 and not prior_version:
                version_file.write_text(json.dumps({'version':next(iter(versions)),'requested_id':auth['model']}))

            rows.append(record)
            shadow=status.get('semantic_shadow_assessment') or {}
            if (version_drift or status['state']!='shadow_complete' or (shadow.get('execution_status')!='COMPLETED' and shadow.get('coverage_status')!='MODEL_DECLARED_NONFACTUAL_UNQUALIFIED')
                    or any(not (call.get('usage') or {}).get('usage_available') or not (call.get('usage') or {}).get('resolved_model_version') for call in shadow.get('calls',[]))
                    or (case['synthetic_support_label'] in ('CONTRADICTED','INSUFFICIENT_EVIDENCE','UNAVAILABLE')
                        and shadow.get('candidate_would_support_authored_copy'))):
                result_path.write_text(json.dumps({'stopped':True,'rows':rows},ensure_ascii=False,indent=2)+'\n')
                raise ValueError('SMOKE_OR_BATCH_STOP_CONDITION_RESULTS_PRESERVED')
        result_path.write_text(json.dumps({'stopped':False,'rows':rows},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'batch':batch,'captured_cases':len(rows),'shadow_only':True,'operator_review_required':True}))




def deterministic_metadata_probe(root):
    """Replay the frozen original evaluator and current policy on one identical sourced fixture."""
    import importlib.util
    from poster.strategy_contract import CreativeContract
    from poster.claim_assessment import assess_copy
    template=json.loads((root/'frozen/grounding__poster_claim_contract_fixture.json').read_text())
    template.update(domain_reference='fiction_example_test',allowed_claim_ids=['c'],internal_supporting_claim_ids=[],
        allowed_claims=[{'claim_id':'c','statement':'Guided specialist onboarding','verification_status':'SUPPORTED','supporting_evidence_ids':['e']}],
        copy_evidence=[{'evidence_id':'e','excerpt':'Guided specialist onboarding','availability':'AVAILABLE',
            'subject_ref':'fiction_example_test','source_class':'BRAND_OWNED','source_url':'https://fiction.example.test/process'}],
        copy_metadata_evidence={
            'business_name':[{'value':'Fictional Studio','quote':'Fictional Studio','page_url':'https://fiction.example.test/about','profile_field':'name'}],
            'contact_line':[{'value':'hello@fiction.example.test','quote':'hello@fiction.example.test','page_url':'https://fiction.example.test/contact','profile_field':'contact_channels'}]})
    contract=CreativeContract.model_validate(template)
    brief={'business_name':'Fictional Studio','contact_line':'hello@fiction.example.test','headline':'Guided specialist onboarding'}
    spec=importlib.util.spec_from_file_location('adzy_frozen_literal_baseline',root/'frozen/poster__claim_assessment.py')
    original=importlib.util.module_from_spec(spec);sys.modules[spec.name]=original;spec.loader.exec_module(original)
    before=original.assess_copy(brief=brief,contract=contract)
    after=assess_copy(brief=brief,contract=contract)
    metadata_ids={p.proposition_id for p in before.propositions if p.location in ('business_name','contact_line')}
    return {'scope':'One synthetic authored-copy fixture with sourced identity/contact, outside the 20-case cohort.',
        'source':'Deterministic field handling; zero model calls.',
        'before_authored_copy_eligible':before.authored_copy_eligible,'after_authored_copy_eligible':after.authored_copy_eligible,
        'before_metadata_supported':{'numerator':sum(j.verdict=='SUPPORTED' for j in before.judgments if j.proposition_id in metadata_ids),'denominator':2},
        'after_metadata_supported':{'numerator':sum(j.verdict=='SUPPORTED' for j in after.judgments if j.proposition_id in metadata_ids),'denominator':2},
        'complete_poster_eligibility':'NOT_MEASURED'}


def compare_live_captures(root, manifest, dataset, report):
    """Compare actual captures only. Unit-test responses are never imported as quality data."""
    from decimal import Decimal
    import hashlib
    from poster.claim_assessment import SemanticCopyShadow,digest,inventory
    experiment_hash=hashlib.sha256((root/'experiment_manifest.v1.json').read_bytes()).hexdigest()
    captures=[];dispatches=[];known_usage=[];latencies=[]
    for row in dataset:
        path=root/'live'/(row['scenario']+'.json')
        if not path.exists(): continue
        capture=json.loads(path.read_text())
        if capture.get('capture_origin')!='LIVE_SHADOW' or capture.get('experiment_sha256')!=experiment_hash:
            raise ValueError('UNATTESTED_OR_DIFFERENT_EXPERIMENT_CAPTURE')
        operation=capture['operation']
        shadow=SemanticCopyShadow.model_validate(operation['semantic_shadow_assessment'])
        if (shadow.copy_hash!=digest(inventory({'headline':row['copy']}))
                or shadow.prompt_hashes['extraction']!=manifest['prompts']['extraction_hash']
                or shadow.prompt_hashes['support']!=manifest['prompts']['support_hash']):
            raise ValueError('CAPTURE_INPUT_OR_PROMPT_MISMATCH')
        calls=operation['calls'];dispatches.extend(calls);latencies.append(shadow.elapsed_ms)
        for call in calls:
            usage=call.get('usage')
            if usage and all(usage.get(k) is not None for k in ('prompt_token_count','candidates_token_count','thoughts_token_count')):
                known_usage.append((Decimal(usage['prompt_token_count'])*Decimal(manifest['pricing']['usd_per_million_input'])+
                    (Decimal(usage['candidates_token_count'])+Decimal(usage['thoughts_token_count']))*
                    Decimal(manifest['pricing']['usd_per_million_output_including_reasoning']))/Decimal(1000000))
        captures.append({'scenario':row['scenario'],'split':row['split'],'synthetic_label':row['synthetic_support_label'],
            'failure_diagnostics':shadow.failure_diagnostics,
            'candidate_would_support':shadow.candidate_would_support_authored_copy,
            'execution_status':shadow.execution_status,'model_declared_coverage':shadow.coverage_status=='MODEL_DECLARED_COVERAGE_UNQUALIFIED',
            'literal_same_input_eligible':shadow.baseline['authored_copy_eligible'],
            'candidate_verdicts':[j.verdict for j in shadow.support.judgments] if shadow.support else [],
            'actual_accepted':False,'complete_poster_eligibility':'NOT_ESTABLISHED'})
    if not captures: return report
    # Actual model responses without independent labels can only support exploratory observations.
    report.update(decision='REMAIN_SHADOW',candidate_model_calls=len(dispatches),candidate_semantic_quality_measured=False,
        observed_live_model_behavior=True,actual_paid_spend_usd=None,
        candidate_observed_cost_usd=None,known_usage_rate_estimate_usd=str(sum(known_usage,Decimal(0))),
        costed_dispatches={'numerator':len(known_usage),'denominator':len(dispatches)},
        estimated_dispatch_commitment_usd=str(Decimal(manifest['pricing']['per_dispatch_estimate_usd'])*len(dispatches)),
        unbilled_or_unknown_usage_dispatches=len(dispatches)-len(known_usage),candidate_latency_ms=latencies,
        candidate_metrics={'reference_origin':'SYNTHETIC_NOT_HUMAN_REVIEWED','human_reviewed':0,
            'meaning':'Hypothetical authored-copy support; current literal acceptance is never overridden.','rows':captures})
    for split in ('development','held_out'):
        subset=[r for r in captures if r['split']==split]
        supported=[r for r in subset if r['synthetic_label']=='SUPPORTED']
        unsupported=[r for r in subset if r['synthetic_label'] in ('CONTRADICTED','INSUFFICIENT_EVIDENCE','UNAVAILABLE')]
        contradictions=[r for r in subset if r['synthetic_label']=='CONTRADICTED']
        report['baseline_comparison'][split].update(
            captured_cases=len(subset),
            same_input_literal_supported_acceptance={'numerator':sum(r['literal_same_input_eligible'] for r in supported),'denominator':len(supported)},
            candidate_supported_acceptance={'numerator':sum(r['candidate_would_support'] for r in supported),'denominator':len(supported)},
            candidate_unsupported_acceptance={'numerator':sum(r['candidate_would_support'] for r in unsupported),'denominator':len(unsupported)},
            candidate_contradiction_errors={'numerator':sum(r['candidate_verdicts']!=['CONTRADICTED'] for r in contradictions),'denominator':len(contradictions)},
            candidate_abstention={'numerator':sum(r['execution_status']!='COMPLETED' or not r['candidate_verdicts'] or
                any(v in ('UNAVAILABLE','INSUFFICIENT_EVIDENCE') for v in r['candidate_verdicts']) for r in subset),'denominator':len(subset)},
            candidate_model_declared_coverage={'numerator':sum(r['model_declared_coverage'] for r in subset),'denominator':len(subset)},
            candidate_independently_verified_coverage={'numerator':None,'denominator':None})
    return report


if __name__=='__main__':raise SystemExit(main())
