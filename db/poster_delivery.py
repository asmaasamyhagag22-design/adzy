"""Read-only delivery of an exact canonical poster revision under current policy."""
import hashlib
from uuid import UUID
from db.errors import NotFound, NotVisible, ForeignOwner, UnclaimedBrand


class DeliveryError(ValueError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def inspect(service, *, actor, run_id, load, lock=True):
    from poster.strategy_contract import CreativeContractCompiler, CreativeRequest, CreativeContractError
    from api.persistence import _truthful_poster_entry
    try:
        actor, run_id = UUID(str(actor)), UUID(str(run_id))
    except ValueError:
        raise DeliveryError(404, 'POSTER_NOT_FOUND') from None
    try:
        with service._uow_factory() as uow:
            visible = uow.runs.get_visible(run_id=run_id, user_id=actor)
            brand = (uow.brands.lock_owned(brand_id=visible.brand_id, user_id=actor) if lock
                     else uow.brands.get_visible(brand_id=visible.brand_id,user_id=actor))
            run = uow.runs.lock(run_id) if lock else visible
            row = uow.gallery_entries.poster_result(user_id=actor, run_id=run_id)
            if run.kind != 'poster' or row is None:
                raise DeliveryError(404, 'POSTER_NOT_FOUND')
            item = dict(row.metadata)
            attempt_id = item.get('generation_attempt_id')
            try:
                attempt = uow.attempts.get_visible(attempt_id=UUID(str(attempt_id)), user_id=actor)
                asset = uow.assets.get_visible(asset_id=attempt.asset_id, user_id=actor)
            except (ValueError, TypeError):
                raise DeliveryError(409, 'POSTER_REVISION_MISMATCH') from None
            if (attempt.run_id != run.id or attempt.brand_id != run.brand_id
                    or str(item.get('asset_id')) != str(asset.id)
                    or asset.brand_id != run.brand_id or row.brand_id != run.brand_id
                    or row.storage_key != asset.storage_key or row.checksum != asset.checksum_sha256):
                raise DeliveryError(409, 'POSTER_REVISION_MISMATCH')
            payload = load(asset.storage_key)
            if payload is None:
                raise DeliveryError(409, 'POSTER_BYTES_UNAVAILABLE')
            checksum = hashlib.sha256(payload).hexdigest()
            if checksum != asset.checksum_sha256 or len(payload) != asset.bytes or len(payload) != row.byte_size:
                raise DeliveryError(409, 'POSTER_BYTES_CHANGED')
            reasons = []
            if run.status != 'succeeded' or attempt.status != 'succeeded' or run.selected_attempt_id != attempt.id:
                reasons.append('REVISION_NOT_SELECTED')
            contract = (run.effective_config or {}).get('creative_contract')
            # Persisted Run and gallery must describe the same content and assessments.
            for field in ('authored_copy','headline','subtext','cta','factual_assessment','visual_qa','creative_contract','strategy_verification','trust_violation','editor_recipe','copy_revision','editor_checks'):
                if item.get(field) != (run.output or {}).get(field):
                    reasons.append('REVISION_METADATA_CHANGED')
                    break
            if item.get('creative_contract') != contract:
                reasons.append('CONTRACT_CHANGED')
            # Re-evaluate exact QA and factual bindings; caller booleans never grant delivery.
            checked, visual = _truthful_poster_entry({**item, 'creative_contract':contract}, payload=payload)
            if (attempt.qa_passed is not True or attempt.qa.get('visual') != item.get('visual_qa')
                    or attempt.qa.get('strategy') != item.get('strategy_verification')
                    or bool(attempt.qa.get('trust_violation')) != bool(item.get('trust_violation'))
                    or not visual.deliverable or not checked.get('deliverable')
                    or item.get('accepted') is not True):
                reasons.append('REQUIRED_CHECKS_UNRESOLVED')
            if item.get('copy_revision'):
                from poster.copy_editor import evaluate
                from poster.schemas import PosterBrief
                from poster.strategy_contract import CreativeContract
                from poster.claim_assessment import digest
                try:
                    recipe=item['editor_recipe']; checks=item['editor_checks']; lineage=item['copy_revision']
                    for rid in (lineage['parent_run_id'],lineage['root_run_id']):
                        parent=uow.runs.get_visible(run_id=UUID(rid),user_id=actor)
                        if parent.brand_id!=run.brand_id: raise DeliveryError(404,'POSTER_NOT_FOUND')
                    _, strategy=evaluate(PosterBrief.model_validate(recipe['brief']),CreativeContract.model_validate(contract),attempt_id=str(attempt.id),payload=payload)
                    if (not strategy['overall_pass'] or strategy!=item.get('strategy_verification')
                            or checks['local_layout']['defects'] or checks['render_sha256']!=checksum
                            or lineage['recipe_hash']!=recipe['hash']
                            or recipe['hash']!=digest({k:v for k,v in recipe.items() if k!='hash'})
                            or lineage['copy_hash']!=digest(item['authored_copy'])
                            or lineage['spec_hash']!=digest(recipe['spec'])
                            or lineage['background_sha256']!=recipe['background']['sha256']
                            or attempt.qa.get('copy_editor')!=checks):
                        reasons.append('COPY_EDITOR_CHECKS_UNRESOLVED')
                except DeliveryError: raise
                except (ValueError,KeyError,TypeError): reasons.append('COPY_EDITOR_CHECKS_UNRESOLVED')
            current = uow.runs.current_successful(brand_id=brand.id, kind='strategy')
            approval = uow.approvals.latest_for_run(current.id) if current else None
            profile = uow.profiles.by_id(current.input_profile_id) if current and current.input_profile_id else None
            if (not contract or not current or str(current.id) != contract.get('strategy_version_id')
                    or current.input_profile_id != brand.current_profile_id
                    or str(brand.current_profile_id) != contract.get('profile_snapshot_id')
                    or not approval or approval.decision != 'approved'):
                reasons.append('CURRENT_APPROVAL_REQUIRED')
            else:
                context = {'strategy_version_id':str(current.id),'run_kind':current.kind,
                    'run_status':current.status,'run_brand_id':str(current.brand_id),'brand_id':str(brand.id),
                    'domain_reference':brand.domain_key,'is_current':True,'approval_state':approval.decision,
                    'profile_snapshot_id':str(profile.id) if profile else '',
                    'run_input_profile_id':str(current.input_profile_id),'profile':dict(profile.profile) if profile else {},
                    'requested_config':dict(current.requested_config or {}),'output':dict(current.output or {})}
                req = (run.requested_config or {}).get('request', {})
                try:
                    rebuilt = CreativeContractCompiler().compile(context, request=CreativeRequest(
                        product_focus=req.get('product_name',''),style_preference=req.get('style',''),
                        cta_preference=req.get('cta',''),creative_angle=req.get('user_context') or req.get('headline',''),
                        requested_factual_claims=[req['subtext']] if req.get('subtext') else [],language=req.get('language','auto')))
                    if rebuilt.model_dump(mode='json') != contract:
                        reasons.append('CURRENT_CONTRACT_CHANGED')
                except (CreativeContractError, ValueError):
                    reasons.append('CURRENT_CONTRACT_UNAVAILABLE')
            return {'generation_run_id':str(run.id),'generation_attempt_id':str(attempt.id),
                'asset_id':str(asset.id),'checksum':checksum,'mime':asset.mime,
                'final_eligible':not reasons,'draft_export_available':True,'reasons':list(dict.fromkeys(reasons)),
                'factual_scope':'Authored copy quotations only; image pixel text and general semantics are not certified.',
                'human_acceptance_required':False}, payload
    except (NotFound, NotVisible, ForeignOwner, UnclaimedBrand):
        raise DeliveryError(404, 'POSTER_NOT_FOUND') from None
