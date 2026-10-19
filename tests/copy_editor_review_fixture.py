"""Explicit fake QA fixture for guarded disposable browser tests; never imported by the application."""
import json

def fake_review(directory, run_id):
    """Test fixture materialization, not a product API or authority to perform live QA."""
    from workflows.demo import connect
    from db.test_database_safety import require_disposable_test_database
    from uuid import UUID
    from sqlalchemy import select
    from db.models import Run,Attempt,Asset,GalleryEntry
    from api import gallery_store
    from api.persistence import _truthful_poster_entry
    from poster.vision_qa import PosterQAVerdict,required_poster_qa_policy,poster_attempt_identity
    m=json.loads((directory/'demo.json').read_text());require_disposable_test_database(m['dsn'],environ={'BASEERA_DESTRUCTIVE_TEST_DATABASE':'baseera_pytest_foundation'})
    if m.get('synthetic') is not True:
        raise ValueError('Fake QA is restricted to explicitly synthetic test databases')
    import os
    os.environ['BASEERA_WEBAPP_DIR']=str(directory/'application')
    service,p=connect(m)
    with service._uow_factory() as uow:
        run=uow.session.get(Run,UUID(run_id));assert str(run.created_by_user_id)==m['actor']
        attempt=uow.session.scalar(select(Attempt).where(Attempt.run_id==run.id))
        row=uow.session.scalar(select(GalleryEntry).where(GalleryEntry.metadata_json['generation_run_id'].astext==run_id))
        payload=gallery_store.load(uow.session.get(Asset,attempt.asset_id).storage_key)
        policy=required_poster_qa_policy(arabic=False,logo_composited=False,expect_real_products=False)
        qa=PosterQAVerdict(checked=True,overall_pass=True,critic_pass=True,score=8,
            text_lines=[run.output['headline'],run.output['cta']],
            reason='SYNTHETIC_TEST: fake visual reviewer fixture; not live quality evidence.',
            qa_source='poster.vision_qa.poster_vision_qa',policy_id=policy.policy_id,policy_version=policy.version,
            policy_context=policy.context,mandatory_checks_expected=policy.mandatory_checks,
            mandatory_checks_completed=policy.mandatory_checks,non_applicable_checks=policy.non_applicable_checks,
            attempt_identity=poster_attempt_identity(payload,attempt_id=str(attempt.id),candidate_id='synthetic-copy-review'))
        value,decision=_truthful_poster_entry({**run.output,'visual_qa':qa.model_dump(mode='json')},payload=payload)
        assert decision.accepted,decision.model_dump(mode='json')
        assert value['factual_eligible'] and value['strategy_verification']['overall_pass']
        assert not value['editor_checks']['local_layout']['defects']
        value['editor_checks']={**value['editor_checks'],'visual_review':'SYNTHETIC_TEST_CHECKED'}
        run.output=value;run.selected_attempt_id=attempt.id;row.metadata_json=value
        attempt.qa={**attempt.qa,'visual':value['visual_qa'],'acceptance':decision.model_dump(mode='json'),'copy_editor':value['editor_checks']};attempt.qa_passed=True
        uow.commit()
