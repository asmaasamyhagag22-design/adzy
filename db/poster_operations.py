"""Single-process synchronous poster boundary. PostgreSQL owns identity and admission.
Session advisory locks fence a live executor; an absent lock triggers recovery, never replay.
"""
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from uuid import UUID

from sqlalchemy import select, func, text, null
from db.models import Run, User, Attempt, UsageRecord, Job, ReservationAdjustment
from db.services import LiveRunData, _create_live_run_locked


class PosterOperationError(Exception):
    def __init__(self, status_code, detail):
        self.status_code=status_code
        self.detail=detail
        super().__init__(str(detail))


def money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0 or result.as_tuple().exponent < -9:
            raise ValueError()
        return result
    except (InvalidOperation, ValueError):
        raise PosterOperationError(503, detail={'code':'POSTER_BUDGET_CONFIGURATION_REQUIRED'}) from None


def fingerprint(request):
    return hashlib.sha256(json.dumps(request,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class PosterOperations:
    fingerprint = staticmethod(fingerprint)

    def __init__(self, persistence):
        self.persistence = persistence
        self.factory = persistence._service._uow_factory

    def owned(self, uow, actor, key):
        return uow.session.scalar(select(Run).where(Run.created_by_user_id==UUID(actor),Run.operation_key==key))

    def existing(self, actor, key, request_hash):
        with self.factory() as uow:
            row = self.owned(uow,actor,key)
            if row is None: return None
            if row.request_hash != request_hash: raise PosterOperationError(409,detail={'code':'OPERATION_KEY_REUSED'})
            return str(row.id)

    def admit(self, actor, key, request, compile_contract):
        digest = fingerprint(request)
        # Owner lock serializes ALL reservations and keys, including different brands.
        with self.factory() as uow:
            uow.session.scalar(select(User).where(User.id==UUID(actor)).with_for_update())
            row = self.owned(uow,actor,key)
            if row:
                if row.request_hash != digest: raise PosterOperationError(409,detail={'code':'OPERATION_KEY_REUSED'})
                return str(row.id),False,None
            brand = self.persistence._brand_row(domain_key=request['domain_key'],user_id=actor)
            if brand is None: raise PosterOperationError(404,detail='Brand not found.')
            uow.brands.lock_owned(brand_id=brand.id,user_id=UUID(actor),allow_unclaimed=False)
            # Recompile against current exact approval/profile while canonical brand writers are fenced.
            contract = compile_contract()
            reserve = money(os.getenv('ADZY_POSTER_RESERVATION_USD',''))
            cap = money(os.getenv('ADZY_POSTER_BUDGET_USD',''))
            charged = uow.session.scalar(select(func.coalesce(func.sum(
                func.coalesce(Run.reserved_usd,0)+func.coalesce(Run.settled_usd,0)),0)).where(
                    Run.created_by_user_id==UUID(actor),Run.operation_key.is_not(None)))
            if charged + reserve > cap: raise PosterOperationError(409,detail={'code':'POSTER_BUDGET_EXHAUSTED'})
            dispatch_policy = None
            if os.getenv('ADZY_POSTER_DISPATCH_POLICY_JSON'):
                from poster.dispatch_policy import DispatchPolicy
                try:
                    dispatch_policy = DispatchPolicy.model_validate_json(os.environ['ADZY_POSTER_DISPATCH_POLICY_JSON'])
                    if dispatch_policy.request_hashes.get(request.get('language')) != digest:
                        raise ValueError()
                    previous = list(uow.session.scalars(select(Run).where(Run.created_by_user_id==UUID(actor), Run.operation_key.is_not(None))))
                    for prior in previous:
                        frozen = (prior.effective_config or {}).get('dispatch_policy')
                        if frozen and frozen['plan_id'] == dispatch_policy.plan_id and (prior.requested_config or {}).get('request',{}).get('language') == request['language']:
                            raise PosterOperationError(409, detail={'code':'POSTER_CASE_ALREADY_ADMITTED'})
                except (ValueError, KeyError, TypeError):
                    raise PosterOperationError(503, detail={'code':'POSTER_DISPATCH_POLICY_INVALID'}) from None
            run = _create_live_run_locked(uow,LiveRunData(brand_id=brand.id,user_id=UUID(actor),kind='poster',status='running',
                input_profile_id=UUID(contract.profile_snapshot_id),
                requested_config={'request':request,'creative_contract_id':contract.creative_contract_id},
                effective_config={'creative_contract':contract.model_dump(mode='json'), **({'dispatch_policy':dispatch_policy.model_dump()} if dispatch_policy else {})},config_schema_version=1,
                dependencies=((UUID(contract.strategy_version_id),'uses_strategy'),)))
            attempt = uow.attempts.create_for_locked_run(run_id=run.id,brand_id=brand.id)
            run=uow.session.get(Run,run.id)
            run.operation_key=key; run.request_hash=digest; run.operation_state='admitted'
            run.reserved_usd=reserve; run.estimated_usd=Decimal(0)
            uow.commit()
            return str(run.id),True,contract

    @contextmanager
    def executor(self, run_id, blocking=False):
        # Autocommit dedicated session; no open transaction across provider I/O.
        with self.factory() as uow:
            engine = uow.session.get_bind()
        lock = int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:8],'big',signed=True)
        with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            if blocking:
                conn.execute(text('SELECT pg_advisory_lock(:key)'),{'key':lock})
                acquired=True
            else:
                acquired = conn.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':lock})
            try: yield acquired
            finally:
                if acquired: conn.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':lock})

    def execution(self, run_id):
        with self.factory() as uow:
            attempt = uow.session.scalar(select(Attempt).where(Attempt.run_id==UUID(run_id)).order_by(Attempt.ordinal))
            return {'run_id':run_id,'attempt_id':str(attempt.id)}

    def dispatch_policy(self, run_id):
        from poster.dispatch_policy import DispatchPolicy
        with self.factory() as uow:
            run = uow.session.get(Run, UUID(run_id))
            value = (run.effective_config or {}).get('dispatch_policy')
            return DispatchPolicy.model_validate(value) if value else None

    def start_call(self, run_id, stage, model):
        # Operator estimates, deliberately NOT advertised as certified invoice ceilings.
        frozen = self.dispatch_policy(run_id)
        try:
            policy = ({f'{r.stage}:{r.model}':r.estimate_usd for r in frozen.routes} if frozen else json.loads(os.environ['ADZY_POSTER_CALL_ESTIMATES_JSON']))
            if frozen: frozen.route(stage, model)
            key=f'{stage}:{model}'
            if key not in policy: key=f"{stage.split('/',1)[0]}:{model}"
            estimate = money(policy[key])
        except (KeyError,ValueError,TypeError):
            raise PosterOperationError(503,detail={'code':'POSTER_CALL_PRICING_UNKNOWN'}) from None
        with self.factory() as uow:
            actor = uow.session.scalar(select(Run.created_by_user_id).where(Run.id==UUID(run_id)))
            uow.session.scalar(select(User).where(User.id==actor).with_for_update())
            run = uow.session.scalar(select(Run).where(Run.id==UUID(run_id)).with_for_update())
            pending = uow.session.scalar(select(UsageRecord.id).where(UsageRecord.run_id==run.id,UsageRecord.state.in_(['sending','unknown'])))
            closed = uow.session.scalar(select(ReservationAdjustment.id).where(ReservationAdjustment.run_id==run.id))
            if run.operation_state not in ('admitted','running') or pending or closed:
                raise PosterOperationError(409,detail={'code':'POSTER_RECONCILIATION_REQUIRED'})
            if frozen:
                local = uow.session.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.run_id==run.id))
                failed = uow.session.scalar(select(UsageRecord.id).where(UsageRecord.run_id==run.id, UsageRecord.state != 'received'))
                if local >= frozen.max_dispatches:
                    raise PosterOperationError(409, detail={'code':'POSTER_DISPATCH_LIMIT_EXHAUSTED'})
                if failed:
                    raise PosterOperationError(409, detail={'code':'POSTER_RECONCILIATION_REQUIRED'})
            if run.reserved_usd is None or run.reserved_usd <= 0 or run.estimated_usd+estimate > run.reserved_usd:
                raise PosterOperationError(409,detail={'code':'POSTER_STAGE_BUDGET_EXHAUSTED'})
            authority=(run.requested_config or {}).get('request',{}).get('service_request_authorization')
            if authority:
                prior=uow.session.scalar(select(func.count()).select_from(UsageRecord).join(Run,UsageRecord.run_id==Run.id).where(
                    Run.created_by_user_id==actor,Run.operation_key.is_not(None)))
                local=uow.session.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.run_id==run.id))
                if (prior+authority['historical_metadata_requests']>=authority['cumulative_limit']
                        or local>=authority['new_dispatch_limit']):
                    raise PosterOperationError(409,detail={'code':'CUMULATIVE_SERVICE_REQUEST_LIMIT_EXHAUSTED'})
            row=UsageRecord(run_id=run.id,brand_id=run.brand_id,attempt_id=UUID(self.execution(run_id)['attempt_id']),provider=(frozen.route(stage,model).provider if frozen else 'google-genai'),stage=stage,model=model,state='sending',estimate_usd=estimate,cost_usd=null(),input_tokens=null(),output_tokens=null(),thoughts_tokens=null())
            uow.session.add(row); run.estimated_usd+=estimate; run.operation_state='running'
            uow.session.flush(); ident=str(row.id); uow.commit(); return ident

    def finish_call(self, call_id, state, response=None, reference=None):
        with self.factory() as uow:
            row=uow.session.get(UsageRecord,UUID(call_id))
            row.state=state
            row.provider_reference=reference or getattr(response,'response_id',None) or getattr(response,'name',None)
            usage=getattr(response,'usage_metadata',None)
            diagnostic=(row.usage or {}).get('failure_diagnostic')
            row.usage=usage.model_dump(mode='json') if hasattr(usage,'model_dump') else usage if isinstance(usage,dict) else None
            if diagnostic:
                row.usage={**(row.usage or {}),'failure_diagnostic':diagnostic}
            if row.usage:
                row.input_tokens=row.usage.get('prompt_token_count')
                row.output_tokens=row.usage.get('candidates_token_count')
                row.thoughts_tokens=row.usage.get('thoughts_token_count')
            uow.commit()

    def record_candidate(self, run_id, path, *, identity, policy, qa, acceptance,
                         brief, spec, local_checks, background_path):
        """Byte-first diagnostic snapshots on the existing Attempt JSONB, never gallery acceptance."""
        from pathlib import Path
        from api import gallery_store
        from poster.vision_qa import poster_attempt_identity
        import re
        def mask(value):
            # Keep usable owner-visible reasons while removing credentials, contacts and locations.
            value=str(value)
            for pattern in (r'https?://\S+',r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',
                            r'(?i)(?:Bearer\s+|AIza|sk-|ghp_)[A-Za-z0-9_./+-]{12,}',
                            r'(?:/[\w.-]+){3,}',r'\+?\d[\d ()-]{8,}\d'):
                value=re.sub(pattern,'[REDACTED]',value)
            return value
        payload=Path(path).read_bytes()
        current=poster_attempt_identity(payload,attempt_id=identity.attempt_id,candidate_id=identity.candidate_id)
        if current != identity or (qa.attempt_identity and qa.attempt_identity != identity):
            raise ValueError('CANDIDATE_IDENTITY_MISMATCH')
        stored=gallery_store.store(payload,expected_checksum=identity.content_sha256)
        # Metadata is inspectable but contains no source prompts, URLs, inline assets, credentials or raw errors.
        visual=qa.model_dump(mode='json')
        visual['reason']=mask(str(visual.get('reason','')))[:1000]
        visual['text_lines']=[mask(str(line))[:300] for line in visual.get('text_lines',[])[:32]]
        snapshot={'identity':identity.model_dump(),'policy':policy.model_dump(), 'visual_qa':visual,
            'acceptance':acceptance.model_dump(mode='json') if acceptance else None,
            'storage_key':stored.storage_key,'checksum':stored.checksum,'bytes':stored.byte_size,'mime':stored.mime,
            'local_checks':local_checks,'render_spec':{**spec.model_dump(mode='json'),'rationale':mask(spec.rationale or '')} if spec is not None else None,
            'authored_copy':{k:mask(str(getattr(brief,k,'') or ''))[:500] for k in ('headline','subheadline','cta_text')},
            'background_sha256':hashlib.sha256(Path(background_path).read_bytes()).hexdigest(),
            'accepted':False,'kind':'DIAGNOSTIC_CANDIDATE_NOT_FINAL'}
        with self.factory() as uow:
            run=uow.session.scalar(select(Run).where(Run.id==UUID(run_id)).with_for_update())
            if run.operation_state not in ('admitted','running'):
                raise ValueError('CANDIDATE_OPERATION_TERMINAL')
            attempt=uow.session.scalar(select(Attempt).where(Attempt.id==UUID(identity.attempt_id),Attempt.run_id==run.id).with_for_update())
            if attempt is None or attempt.status!='running': raise ValueError('CANDIDATE_ATTEMPT_INVALID')
            history=list((attempt.qa or {}).get('candidates',[]))
            prior=next((i for i,c in enumerate(history) if c['identity']['candidate_id']==identity.candidate_id),None)
            if prior is not None:
                if history[prior]['identity']!=identity.model_dump():raise ValueError('CANDIDATE_BYTES_CHANGED')
                history[prior]=snapshot
            else:
                if len(history)>=6: raise ValueError('CANDIDATE_LIMIT_EXHAUSTED')
                history.append(snapshot)
            attempt.qa={**(attempt.qa or {}),'candidates':history}
            uow.commit()

    def candidate_preview(self, actor, key, candidate_id):
        from api import gallery_store
        with self.factory() as uow:
            run=self.owned(uow,actor,key)
            if run is None: raise PosterOperationError(404,detail='Candidate not found.')
            attempt=uow.session.scalar(select(Attempt).where(Attempt.run_id==run.id).order_by(Attempt.ordinal))
            row=next((c for c in (attempt.qa or {}).get('candidates',[]) if c['identity']['candidate_id']==candidate_id),None)
            if row is None:raise PosterOperationError(404,detail='Candidate not found.')
        payload=gallery_store.load(row['storage_key'])
        if payload is None or hashlib.sha256(payload).hexdigest()!=row['checksum'] or len(payload)!=row['bytes']:
            raise PosterOperationError(409,detail='Candidate bytes unavailable or changed.')
        return payload,row['mime']

    def record_call_diagnostic(self, call_id, diagnostic):
        # Existing JSONB envelope; no migration, billing fields or outcome changes.
        with self.factory() as uow:
            row = uow.session.scalar(select(UsageRecord).where(UsageRecord.id==UUID(call_id)).with_for_update())
            run = uow.session.get(Run, row.run_id)
            row.usage = {**(row.usage or {}), 'failure_diagnostic': {
                **diagnostic, 'run_id': str(row.run_id), 'call_id': str(row.id),
                'operation_key': run.operation_key}}
            uow.commit()

    def finish(self, run_id, state, reason=""):
        with self.factory() as uow:
            run=uow.session.scalar(select(Run).where(Run.id==UUID(run_id)).with_for_update())
            if run.operation_state in ('accepted','draft','failed','shadow_complete'): return
            calls=list(uow.session.scalars(select(UsageRecord).where(UsageRecord.run_id==run.id)))
            if any(c.state in ('sending','unknown') for c in calls): state='unknown'
            run.operation_state=state
            if reason: run.operation_reason=reason
            # Zero is proved only when nothing could have been accepted. Usage absence is NULL.
            if not calls:
                run.reserved_usd=Decimal(0);run.settled_usd=Decimal(0);run.known_usage_usd=Decimal(0)
            uow.commit()

    def finish_shadow(self, actor, key, assessment):
        """Persist a separate outcome on a dedicated admitted shadow operation. Never select media."""
        from poster.claim_assessment import (SemanticCopyShadow, evidence_snapshot, digest, inventory, SHADOW_MODEL,
            SHADOW_VERSION, copy_source_catalog, resolve_source_quotes, EXTRACTION_SCHEMA_VERSION, ANCHOR_METHOD)
        from poster.copy_semantic_prompts import EXTRACTION_PROMPT, SUPPORT_PROMPT, EXTRACTION_VERSION, SUPPORT_VERSION
        from poster.strategy_contract import CreativeContract
        value = SemanticCopyShadow.model_validate(assessment)
        with self.factory() as uow:
            row = self.owned(uow, actor, key)
            if row is None: raise PosterOperationError(404, detail='Operation not found.')
            uow.brands.lock_owned(brand_id=row.brand_id, user_id=UUID(actor), allow_unclaimed=False)
            row = uow.session.scalar(select(Run).where(Run.id==row.id).with_for_update())
            request = (row.requested_config or {}).get('request', {})
            contract = CreativeContract.model_validate((row.effective_config or {}).get('creative_contract'))
            if (request.get('purpose') != 'semantic_shadow' or value.dispatch_run_id != str(row.id)
                    or value.copy_hash != digest(inventory(request.get('authored_copy') or {}))
                    or value.evidence_hash != digest(evidence_snapshot(contract))
                    or value.strategy_version_id != contract.strategy_version_id
                    or value.contract_id != contract.creative_contract_id
                    or value.profile_snapshot_id != contract.profile_snapshot_id
                    or value.requested_model != SHADOW_MODEL or value.method != SHADOW_VERSION
                    or value.extraction_schema_version != EXTRACTION_SCHEMA_VERSION or value.anchor_method != ANCHOR_METHOD
                    or value.source_catalog_hash != digest(copy_source_catalog(inventory(request.get('authored_copy') or {})))
                    or value.prompt_versions != {'extraction':EXTRACTION_VERSION,'support':SUPPORT_VERSION}
                    or value.prompt_hashes.get('extraction') != digest(EXTRACTION_PROMPT)
                    or value.prompt_hashes.get('support') != digest(SUPPORT_PROMPT)
                    or value.source_attempt_id != request.get('source_attempt_id','')
                    or value.artifact_sha256 != request.get('artifact_sha256',hashlib.sha256(b'').hexdigest())):
                raise PosterOperationError(409, detail={'code':'SHADOW_BINDING_MISMATCH'})
            if value.anchor_status == 'RESOLVED':
                if value.provider_extraction is None or value.extraction is None:
                    raise PosterOperationError(409, detail={'code':'SHADOW_ANCHOR_BINDING_MISMATCH'})
                try:
                    resolved=resolve_source_quotes(value.provider_extraction,inventory(request.get('authored_copy') or {}))
                except ValueError:
                    raise PosterOperationError(409, detail={'code':'SHADOW_ANCHOR_BINDING_MISMATCH'}) from None
                if resolved != value.extraction:
                    raise PosterOperationError(409, detail={'code':'SHADOW_ANCHOR_BINDING_MISMATCH'})
            elif value.extraction is not None:
                raise PosterOperationError(409, detail={'code':'SHADOW_ANCHOR_BINDING_MISMATCH'})
            output = {'semantic_shadow_assessment':value.model_dump(mode='json'), 'media_requested':False}
            if row.operation_state == 'shadow_complete':
                if row.output != output: raise PosterOperationError(409, detail={'code':'SHADOW_RESULT_CHANGED'})
                return
            if row.operation_state not in ('admitted','running','unknown'):
                raise PosterOperationError(409, detail={'code':'SHADOW_OPERATION_TERMINAL'})
            calls = list(uow.session.scalars(select(UsageRecord).where(UsageRecord.run_id==row.id)))
            if not calls:
                row.reserved_usd=Decimal(0);row.settled_usd=Decimal(0);row.known_usage_usd=Decimal(0)
            if any(c.state in ('sending','unknown') for c in calls):
                row.output=output;row.output_schema_version=1;row.operation_state='unknown'
                row.operation_reason='POSTER_PROVIDER_OUTCOME_UNKNOWN'
            else:
                attempt = uow.session.scalar(select(Attempt).where(Attempt.run_id==row.id).order_by(Attempt.ordinal))
                uow.attempts.finalize_failure(attempt_id=attempt.id, error='NO_MEDIA_REQUESTED_SHADOW_ONLY',
                    qa={'semantic_shadow':value.execution_status, 'accepted':False})
                uow.runs.finalize_success(run_id=row.id, output=output, output_schema_version=1, selected_attempt_id=None)
                row.operation_state='shadow_complete';row.operation_reason='SEMANTIC_SHADOW_ONLY_NO_MEDIA'
            uow.commit()


    def status(self, actor, key, recover=True):
        with self.factory() as uow:
            run=self.owned(uow,actor,key)
            if run is None: raise PosterOperationError(404,detail='Operation not found.')
            ident=str(run.id)
            state=run.operation_state
        if recover and state in ('admitted','running'):
            with self.executor(actor+':'+key) as acquired:
                if acquired:
                    # Orphan executor: durable gallery proves acceptance; dispatch uncertainty never proves failure.
                    accepted=self.persistence.poster_gallery_result(user_id=actor,run_id=ident)
                    with self.factory() as uow:
                        calls=list(uow.session.scalars(select(UsageRecord).where(UsageRecord.run_id==UUID(ident))))
                    self.finish(ident,('accepted' if accepted.get('accepted') is True else 'draft') if accepted else 'unknown' if calls else 'failed')
                    if not calls:
                        self.persistence.fail_canonical_poster_run(**self.execution(ident),user_id=actor,error='Executor stopped before dispatch.')
        with self.factory() as uow:
            run=self.owned(uow,actor,key)
            calls=list(uow.session.scalars(select(UsageRecord).where(UsageRecord.run_id==run.id).order_by(UsageRecord.created_at)))
            state=run.operation_state
            shadow_result=(run.output or {}).get('semantic_shadow_assessment')
            out={'operation_key':key,'generation_run_id':ident,**{'generation_attempt_id':self.execution(ident)['attempt_id']},
                 'state':state,'reason_code':run.operation_reason or '','required_action': 'RECONCILE_WITH_PROVIDER' if state=='unknown' else 'NEW_VARIATION' if state=='failed' else 'CHECK_STATUS' if state in ('running','admitted') else 'REVIEW_RESULT',
                 'reserved_usd':str(run.reserved_usd),'estimated_usd':str(run.estimated_usd),
                 'known_usage_usd':None if run.known_usage_usd is None else str(run.known_usage_usd),
                 'settled_usd':None if run.settled_usd is None else str(run.settled_usd),
                 'calls':[{'id':str(c.id),'stage':c.stage,'model':c.model,'state':c.state,'provider_reference':c.provider_reference,'usage':c.usage,
                           **({'failure_diagnostic':c.usage['failure_diagnostic']} if c.usage and 'failure_diagnostic' in c.usage else {})} for c in calls], 'result':None}
        with self.factory() as uow:
            attempt=uow.session.scalar(select(Attempt).where(Attempt.run_id==UUID(ident)).order_by(Attempt.ordinal))
            candidates=(attempt.qa or {}).get('candidates',[]) if attempt else []
            out['candidates']=[{k:v for k,v in c.items() if k!='storage_key'} for c in candidates]
        if shadow_result:
            out['semantic_shadow_assessment'] = shadow_result
        if state in ('accepted','draft'):
            out['result']=self.persistence.poster_gallery_result(user_id=actor,run_id=ident)
        return out

    def latest(self,actor,domain):
        brand=self.persistence._brand_row(domain_key=domain,user_id=actor)
        if brand is None: raise PosterOperationError(404,detail='Brand not found.')
        with self.factory() as uow:
            key=uow.session.scalar(select(Run.operation_key).where(Run.created_by_user_id==UUID(actor),Run.brand_id==brand.id,Run.operation_key.is_not(None),func.coalesce(Run.requested_config['request']['purpose'].astext,'') != 'semantic_shadow').order_by(Run.kind_seq.desc()).limit(1))
        return None if key is None else self.status(actor,key)

    def release_unused_reservation(self, actor, key, *, reason):
        """Trusted accounting action: close planned capacity, retain all attempted exposure.

        Canonical executor -> owner -> brand -> run, matching execution/admission ordering.
        The durable adjustment also fences start_call if another writer revives the run state.
        No provider calls, usage estimates, costs, outputs or receipts are rewritten.
        """
        version = 'adzy.unused-reservation-release@1'
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('An adjustment reason is required')
        def result(row, replay=False):
            return {'adjustment_id':str(row.id),'version':row.version,'run_id':str(row.run_id),
                'operation_key':key,'owner_id':str(row.owner_id),'reason':row.reason,
                'before_usd':str(row.before_usd),'after_usd':str(row.after_usd),
                'unused_reservation_released_usd':str(row.released_usd),
                'retained_attempted_exposure_usd':str(row.after_usd),
                'exposure':row.exposure,'idempotent_replay':replay,'settlement_created':False}
        with self.executor(actor+':'+key, blocking=True) as acquired:
            if not acquired: raise PosterOperationError(409,detail={'code':'POSTER_EXECUTOR_ACTIVE'})
            with self.factory() as uow:
                uow.session.scalar(select(User).where(User.id==UUID(actor)).with_for_update())
                run=self.owned(uow,actor,key)
                if run is None: raise PosterOperationError(404,detail='Operation not found.')
                uow.brands.lock_owned(brand_id=run.brand_id,user_id=UUID(actor),allow_unclaimed=False)
                run=uow.session.scalar(select(Run).where(Run.id==run.id).with_for_update())
                prior=uow.session.scalar(select(ReservationAdjustment).where(
                    ReservationAdjustment.run_id==run.id,ReservationAdjustment.version==version))
                if prior: return result(prior, replay=True)
                if run.operation_state not in ('accepted','draft','failed','shadow_complete'):
                    raise PosterOperationError(409,detail={'code':'RESERVATION_OPERATION_NOT_CLOSED'})
                calls=list(uow.session.scalars(select(UsageRecord).where(UsageRecord.run_id==run.id).with_for_update()))
                if any(c.state not in ('received','rejected','settled') for c in calls):
                    raise PosterOperationError(409,detail={'code':'RESERVATION_DISPATCH_EXPOSURE_UNRESOLVED'})
                if uow.session.scalar(select(Job.id).where(Job.run_id==run.id,Job.status.in_(('queued','running')))):
                    raise PosterOperationError(409,detail={'code':'RESERVATION_WORKER_OR_CONTINUATION_PENDING'})
                if run.settled_usd is not None:
                    raise PosterOperationError(409,detail={'code':'RESERVATION_ALREADY_SETTLED'})
                def amount(v):
                    n=Decimal(str(v))
                    if not n.is_finite() or n<0:raise ValueError('Unknown or invalid retention amount')
                    return n
                if any(c.estimate_usd is None for c in calls) or run.reserved_usd is None or run.estimated_usd is None:
                    raise PosterOperationError(409,detail={'code':'RESERVATION_RETENTION_ESTIMATE_UNKNOWN'})
                retained=max(sum((max(amount(c.estimate_usd),amount(c.cost_usd) if c.cost_usd is not None else Decimal(0)) for c in calls),Decimal(0)),
                    amount(run.estimated_usd),amount(run.known_usage_usd) if run.known_usage_usd is not None else Decimal(0))
                before=amount(run.reserved_usd)
                if retained>before:
                    raise PosterOperationError(409,detail={'code':'RESERVATION_KNOWN_EXPOSURE_EXCEEDS_HOLD'})
                row=ReservationAdjustment(run_id=run.id,owner_id=UUID(actor),version=version,reason=reason.strip(),
                    before_usd=before,after_usd=retained,released_usd=before-retained,
                    exposure={'operation_state':run.operation_state,'estimated_dispatch_commitment_usd':str(run.estimated_usd),
                        'reported_usage_estimate_usd':None if run.known_usage_usd is None else str(run.known_usage_usd),
                        'settled_provider_charges_usd':None,'provider_billing_unknown':True,
                        'calls':[{'id':str(c.id),'state':c.state,'estimate_usd':str(c.estimate_usd),
                                  'known_cost_usd':None if c.cost_usd is None else str(c.cost_usd)} for c in calls]})
                uow.session.add(row);run.reserved_usd=retained;uow.session.flush()
                output=result(row);uow.commit();return output

    def reconcile_receipt(self, actor, key, *, receipt_reference, final_costs, terminal_confirmed):
        """Trusted operator boundary, NOT an owner HTTP assertion or provider lookup.

        Call only after verifying a final provider/billing receipt for EVERY dispatch. A timeout,
        cancellation or local deadline is not such evidence. No generation is ever invoked here.
        Prices may exceed the estimate: record the invoice honestly, even above the admission cap.
        """
        if terminal_confirmed is not True or not str(receipt_reference).strip():
            raise ValueError('A verified final provider receipt is required')
        costs={}
        for ident,value in final_costs.items():
            amount=Decimal(str(value))
            if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -9:
                raise ValueError('Invalid receipt cost')
            costs[str(UUID(ident))]=amount
        with self.executor(actor+':'+key) as acquired:
            if not acquired: raise PosterOperationError(409,detail={'code':'POSTER_EXECUTOR_ACTIVE'})
            with self.factory() as uow:
                uow.session.scalar(select(User).where(User.id==UUID(actor)).with_for_update())
                run=self.owned(uow,actor,key)
                if run is None: raise PosterOperationError(404,detail='Operation not found.')
                run=uow.session.scalar(select(Run).where(Run.id==run.id).with_for_update())
                calls=list(uow.session.scalars(select(UsageRecord).where(UsageRecord.run_id==run.id)))
                if not calls or {str(c.id) for c in calls} != set(costs):
                    raise ValueError('Receipt must account for every dispatch')
                if run.settled_usd is not None:
                    if all(c.cost_usd==costs[str(c.id)] and (c.usage or {}).get('final_receipt_reference')==receipt_reference for c in calls):
                        return  # exact replay of verified reconciliation
                    raise ValueError('Settled receipt is immutable')
                for call in calls:
                    call.cost_usd=costs[str(call.id)];call.state='settled'
                    call.usage={**(call.usage or {}),'final_receipt_reference':receipt_reference}
                run.settled_usd=sum(costs.values(),Decimal(0))
                run.known_usage_usd=run.settled_usd
                run.reserved_usd=Decimal(0)
                artifact=self.persistence.poster_gallery_result(user_id=actor,run_id=str(run.id))
                accepted=artifact is not None
                run.operation_state=('accepted' if artifact.get('accepted') is True else 'draft') if accepted else 'failed'
                ident=str(run.id)
                uow.commit()
            if not accepted:
                self.persistence.fail_canonical_poster_run(**self.execution(ident),user_id=actor,error='Provider final receipt reconciled; no accepted local image.')
