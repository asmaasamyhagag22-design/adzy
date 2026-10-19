"""Copy revisions using canonical Run/Attempt/Asset/Gallery repositories, one atomic commit."""
import base64
import hashlib
from uuid import UUID, uuid4
from sqlalchemy import select
from db.models import Run, Asset
from db.errors import NotFound, NotVisible, ForeignOwner, UnclaimedBrand
from db.poster_delivery import DeliveryError
from poster.claim_assessment import digest, inventory
from poster.copy_editor import validate_recipe, evaluate, render, source_options
from poster.schemas import PosterBrief
from poster.strategy_contract import CreativeContract


class EditorError(ValueError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


class CopyEditor:
    def __init__(self, persistence):
        self.p = persistence
        self.factory = persistence._service._uow_factory

    def _source(self, actor, run_id, *, current=True):
        from api import gallery_store
        try:
            actor_id, ident = UUID(actor), UUID(run_id)
            with self.factory() as uow:
                run = uow.runs.get_visible(run_id=ident, user_id=actor_id)
                row = uow.gallery_entries.poster_result(user_id=actor_id, run_id=ident)
                if run.kind!='poster' or not row: raise EditorError(404,'POSTER_NOT_FOUND')
                item = dict(row.metadata)
            from db.poster_delivery import inspect
            status, payload = inspect(self.p._service,actor=actor,run_id=run_id,load=gallery_store.load,lock=False)
            if current and any(r.startswith(('CURRENT_', 'CONTRACT_')) for r in status['reasons']):
                raise EditorError(409,'CURRENT_STRATEGY_APPROVAL_REQUIRED')
            contract = CreativeContract.model_validate((run.effective_config or {}).get('creative_contract'))
            recipe = item.get('editor_recipe')
            if item.get('copy_revision'):
                parent = item['copy_revision']['parent_run_id']
                root = item['copy_revision']['root_run_id']
                # Prove all lineage subjects remain same-owner/same-brand before revealing sources.
                with self.factory() as uow:
                    for rid in (parent,root):
                        ancestor = uow.runs.get_visible(run_id=UUID(rid), user_id=actor_id)
                        if ancestor.brand_id != run.brand_id: raise EditorError(404,'POSTER_NOT_FOUND')
            return run, item, status, payload, contract, recipe
        except (ValueError, TypeError, NotFound, NotVisible, ForeignOwner, UnclaimedBrand) as exc:
            if isinstance(exc,EditorError): raise
            if isinstance(exc,DeliveryError): raise EditorError(exc.status,exc.code) from None
            raise EditorError(404,'POSTER_NOT_FOUND') from None

    def _head(self, uow, root):
        row = uow.session.scalar(select(Run).where(Run.kind=='poster',
            Run.requested_config['copy_editor']['root_run_id'].astext==root,
            Run.status=='succeeded').order_by(Run.kind_seq.desc()).limit(1))
        return str(row.id) if row else root

    def context(self, actor, run_id):
        run,item,status,payload,contract,recipe = self._source(actor,run_id,current=False)
        reason = ''
        try: validate_recipe(recipe)
        except (ValueError,KeyError,OSError): reason = 'Editing unavailable for this revision: managed background or structured design inputs are missing or changed.'
        if any(r.startswith(('CURRENT_', 'CONTRACT_')) for r in status['reasons']):
            reason = 'Editing unavailable for this revision: current strategy approval and profile are required.'
        root = item.get('copy_revision',{}).get('root_run_id',run_id)
        with self.factory() as uow:
            head = self._head(uow,root)
            revisions = list(uow.session.scalars(select(Run).where(Run.brand_id==run.brand_id,
                Run.kind=='poster',Run.requested_config['copy_editor']['root_run_id'].astext==root,
                Run.status=='succeeded').order_by(Run.kind_seq)))
            versions = [{'run_id':root,'label':'Original'}]+[{'run_id':str(r.id),'label':f'Revised · {r.kind_seq}'} for r in revisions]
        copy = {k:(recipe or {}).get('brief',item.get('authored_copy') or {}).get(k,'') or '' for k in ('headline','subheadline','cta_text')}
        return {'available':not reason,'reason':reason,'run_id':run_id,
            'base_hash':digest([run_id,status['generation_attempt_id'],status['checksum'],(recipe or {}).get('hash')]),
            'head_run_id':head,'root_run_id':root,'copy':copy,'sources':source_options(contract),
            'cta_direction':contract.cta_direction,'revisions':versions,
            'checks':item.get('editor_checks'), 'lineage':item.get('copy_revision'),
            'image_b64':base64.b64encode(payload).decode(), 'delivery':status,
            'result':{k:v for k,v in self.p.poster_gallery_result(user_id=actor,run_id=run_id).items() if k!='editor_recipe'}}

    def preview(self, actor, run_id, edit):
        run,item,status,payload,contract,recipe = self._source(actor,run_id)
        try:
            png,revised,local = render(recipe,edit)
        except (ValueError,KeyError,OSError): raise EditorError(409,'EDITING_INPUTS_UNAVAILABLE') from None
        except Exception: raise EditorError(422,'LOCAL_RENDER_FAILED') from None
        attempt = str(uuid4())
        assessment,strategy = evaluate(PosterBrief.model_validate(revised['brief']),contract,attempt_id=attempt,payload=png)
        return {'image_b64':base64.b64encode(png).decode(),'checksum':hashlib.sha256(png).hexdigest(),
            'attempt_id':attempt,'factual_assessment':assessment,'strategy_verification':strategy,
            'local_layout':local,'visual_review':'NOT_CHECKED','final_eligible':False,
            'background_sha256':recipe['background']['sha256']}, png,revised,local

    def save(self, actor, run_id, request):
        from api import gallery_store
        from api.persistence import _truthful_poster_entry
        from poster.vision_qa import PosterQAVerdict, poster_attempt_identity
        run,item,status,payload,contract,recipe = self._source(actor,run_id)
        fingerprint = digest(request.model_dump(mode='json'))
        def existing(uow):
            row = uow.session.scalar(select(Run).where(Run.brand_id==run.brand_id,
                Run.created_by_user_id==UUID(actor),Run.requested_config['copy_editor']['key'].astext==request.idempotency_key))
            if row and row.requested_config['copy_editor']['request_hash']!=fingerprint:
                raise EditorError(409,'IDEMPOTENCY_KEY_REUSED')
            return str(row.id) if row else None
        with self.factory() as uow:
            found = existing(uow)
        if found: return self.context(actor,found)
        ctx = self.context(actor,run_id)
        if request.expected_base_hash != ctx['base_hash']: raise EditorError(409,'BASE_REVISION_CHANGED')
        if request.expected_head_run_id != ctx['head_run_id']: raise EditorError(409,'REVISION_CONFLICT')
        if ctx['head_run_id'] != run_id and not request.branch: raise EditorError(409,'EXPLICIT_BRANCH_REQUIRED')
        preview,png,revised,local = self.preview(actor,run_id,request.edited_copy)
        stored = gallery_store.store(png)
        # No DB lock during Chromium. Recheck under the same Brand lock all canonical writers use.
        with self.factory() as uow:
            uow.brands.lock_owned(brand_id=run.brand_id,user_id=UUID(actor),allow_unclaimed=False)
            found = existing(uow)
            if found: return self.context(actor,found)
            latest = self.context(actor,run_id)
            self._source(actor,run_id)  # current approval/profile, again under writer lock
            if latest['base_hash']!=request.expected_base_hash: raise EditorError(409,'BASE_REVISION_CHANGED')
            if self._head(uow,ctx['root_run_id'])!=request.expected_head_run_id: raise EditorError(409,'REVISION_CONFLICT')
            lineage={'parent_run_id':run_id,'parent_attempt_id':status['generation_attempt_id'],
                'parent_checksum':status['checksum'],'root_run_id':ctx['root_run_id'],
                'actor':actor,'background_sha256':recipe['background']['sha256'],
                'copy_hash':digest(inventory(revised['brief'])),'spec_hash':digest(revised['spec']),
                'recipe_hash':revised['hash'],'profile_snapshot_id':contract.profile_snapshot_id,
                'strategy_version_id':contract.strategy_version_id,'contract_id':contract.creative_contract_id,
                'branch':request.branch}
            child = uow.runs.create_live_for_locked_brand(brand_id=run.brand_id,kind='poster',
                created_by_user_id=UUID(actor),status='running',input_profile_id=run.input_profile_id,
                requested_config={**(run.requested_config or {}),'copy_editor':{
                    'key':request.idempotency_key,'request_hash':fingerprint,'root_run_id':ctx['root_run_id']}},
                effective_config={'creative_contract':contract.model_dump(mode='json')},
                dependencies=((UUID(contract.strategy_version_id),'uses_strategy'),))
            attempt = uow.attempts.create_for_locked_run(run_id=child.id,brand_id=run.brand_id)
            assessment,strategy = evaluate(PosterBrief.model_validate(revised['brief']),contract,attempt_id=str(attempt.id),payload=png)
            qa = PosterQAVerdict(checked=False,reason='Copy changed: this render needs its own authorized visual review.',
                attempt_identity=poster_attempt_identity(png,attempt_id=str(attempt.id),candidate_id=str(uuid4())))
            output={k:v for k,v in item.items() if k not in ('image_b64','copy_revision','editor_checks','editor_recipe','asset_id')}
            output.update(generation_run_id=str(child.id),generation_attempt_id=str(attempt.id),
                headline=request.edited_copy.headline,subtext=request.edited_copy.subheadline,cta=request.edited_copy.cta_text,
                authored_copy=inventory(revised['brief']),factual_assessment=assessment,
                visual_qa=qa.model_dump(mode='json'),strategy_verification=strategy,strategy_grounded=strategy['overall_pass'],trust_violation=False,
                editor_recipe=revised,copy_revision=lineage,
                editor_checks={'local_layout':local,'visual_review':'NOT_CHECKED','render_sha256':stored.checksum},
                is_preview=True,canonical=True)
            output,decision=_truthful_poster_entry(output,payload=png)
            # The compositor result is a fresh Asset even when bytes happen to match an earlier one.
            # Existing Asset schema deduplicates storage keys; reuse that physical identity only.
            asset=uow.session.scalar(select(Asset).where(Asset.storage_key==stored.storage_key))
            if asset and asset.brand_id!=run.brand_id: raise EditorError(409,'ASSET_BRAND_CONFLICT')
            if not asset:
                asset=uow.assets.record_verified(brand_id=run.brand_id,storage_key=stored.storage_key,kind='poster',
                    mime=stored.mime,byte_count=stored.byte_size,checksum_sha256=stored.checksum,durable=True,
                    created_by_user_id=UUID(actor),width=2160,height=2700)
            output['asset_id']=str(asset.id)
            uow.attempts.finalize_success(attempt_id=attempt.id,asset_id=asset.id,qa={
                'visual':output['visual_qa'],'strategy':strategy,'trust_violation':False,
                'acceptance':decision.model_dump(mode='json'),'copy_editor':output['editor_checks']},qa_passed=False)
            uow.runs.finalize_success(run_id=child.id,output=output,output_schema_version=1,selected_attempt_id=None)
            background=revised['background']
            uow.gallery_blobs.ensure(checksum=background['sha256'],byte_size=background['bytes'],mime=background['mime'],storage_key=background['storage_key'])
            blob=uow.gallery_blobs.ensure(checksum=stored.checksum,byte_size=stored.byte_size,mime=stored.mime,storage_key=stored.storage_key)
            uow.gallery_entries.create(checksum=blob.checksum,filename=f'copy-{child.id}.png',
                owner_user_id=UUID(actor),brand_id=run.brand_id,domain_key=contract.domain_reference,
                brand_name=item.get('brand_name'),content_type='post',metadata=output,origin='live')
            uow.commit()
            saved=str(child.id)
        return self.context(actor,saved)
