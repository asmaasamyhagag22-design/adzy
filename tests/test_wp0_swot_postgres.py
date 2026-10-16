"""Real PostgreSQL WP0 tests. No fixture can use a production/demo database."""
from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import UUID

import pytest
import os

pytestmark = pytest.mark.skipif(
    not os.environ.get("BASEERA_TEST_DATABASE_URL"),
    reason="WP0 real PostgreSQL proof requires a guarded disposable database")
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_db_persistence_2c import engine, clean_database, service, _user, _commit_brand, _race
from db.errors import StaleApprovalSubject
from db.models import Approval, Brand
from db.services import LiveRunData


def seed(service):
    user = _user(service, 'wp0')
    brand = _commit_brand(service, user).brand
    a = service.create_live_run(LiveRunData(brand_id=brand.id, user_id=user.id, kind='swot', status='succeeded'))
    return user, brand, a


def rows(engine):
    with Session(engine) as session:
        return [(r.id, r.subject_run_id) for r in session.scalars(select(Approval)).all()]


def test_replayed_approved_a_is_stale_after_b_and_never_approves_b(service, engine):
    user, brand, a = seed(service)
    approved = service.approve_current_swot(brand_id=brand.id, user_id=user.id, expected_run_id=a.id)
    b = service.create_live_run(LiveRunData(brand_id=brand.id, user_id=user.id, kind='swot', status='succeeded'))
    with pytest.raises(StaleApprovalSubject):
        service.approve_current_swot(brand_id=brand.id, user_id=user.id, expected_run_id=a.id)
    assert rows(engine) == [(approved.id, a.id)]
    assert service.approval_for_current_swot(brand_id=brand.id, user_id=user.id) is None


def test_two_simultaneous_approvals_for_a_write_one_row(service, engine):
    user, brand, a = seed(service)
    results, errors = _race(*[lambda: service.approve_current_swot(
        brand_id=brand.id, user_id=user.id, expected_run_id=a.id) for _ in range(2)])
    assert not errors and len(results) == 2
    assert results[0].id == results[1].id
    assert rows(engine) == [(results[0].id, a.id)]


@pytest.mark.parametrize('writer', ['create_succeeded', 'finalize_success', 'finish_enrichment'])
def test_generation_holds_same_brand_lock_then_old_approval_is_stale(service, engine, monkeypatch, writer):
    """Hold writer after its brand lock, start approval on another connection, commit B first."""
    from db.repositories import BrandRepository
    user, brand, a = seed(service)
    queued = service.create_live_run(LiveRunData(brand_id=brand.id, user_id=user.id, kind='swot')) if writer == 'finalize_success' else None
    job = None
    if writer == 'finish_enrichment':
        job = service.create_user_job(owner_user_id=user.id, requested_domain_key=brand.domain_key)
        service.claim_job(job_id=job.id, worker_id='wp0-worker', lease_seconds=600)
    locked, release, started = threading.Event(), threading.Event(), threading.Event()
    original = BrandRepository.lock_owned
    def holding(repo, **kw):
        result = original(repo, **kw)
        if threading.current_thread().name.startswith('wp0-writer'):
            locked.set()
            assert release.wait(15), 'test failed to release brand lock'
        return result
    monkeypatch.setattr(BrandRepository, 'lock_owned', holding)
    data = LiveRunData(brand_id=brand.id, user_id=user.id, kind='swot', status='succeeded', output={'has_swot': True, 'swot': {'summary': 'B'}})
    def generate():
        if writer == 'create_succeeded': return service.create_live_run(data)
        if writer == 'finalize_success': return service.finalize_run_success(run_id=queued.id, user_id=user.id, output=data.output)
        return service.finish_enrichment_job_success(job_id=job.id, worker_id='wp0-worker', data=data)
    def approve():
        started.set()
        return service.approve_current_swot(brand_id=brand.id, user_id=user.id, expected_run_id=a.id)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='wp0-writer') as writers, ThreadPoolExecutor(max_workers=1) as readers:
        generation = writers.submit(generate)
        try:
            assert locked.wait(15)
            approval = readers.submit(approve)
            assert started.wait(15)
            # A separate NOWAIT query proves the brand row is locked by the writer transaction.
            from sqlalchemy.exc import OperationalError
            with Session(engine) as check:
                with pytest.raises(OperationalError):
                    check.scalar(select(Brand).where(Brand.id == brand.id).with_for_update(nowait=True))
            assert not approval.done()
        finally:
            release.set()
        generation.result(timeout=20)
        with pytest.raises(StaleApprovalSubject): approval.result(timeout=20)
    assert rows(engine) == []


def test_synthetic_import_replay_locks_existing_brand_before_run_writes(engine, tmp_path):
    """Administrative writer also respects the approval serialization point. Synthetic source only."""
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from test_db_migration_tools_2e import _source
    from db.migration_tools.plan import build_migration_plan
    from db.migration_tools.assets import stage_assets
    from db.migration_tools.apply import apply_plan
    source = _source(tmp_path)
    plan, _, _ = build_migration_plan(source)
    target = tmp_path / 'assets'
    stage_assets(plan, source_root=source, target_root=target)
    def apply():
        return apply_plan(plan, source_root=source, asset_root=target,
                          dsn=os.environ['BASEERA_TEST_DATABASE_URL'],
                          quarantine_ack_digest=plan['quarantine_manifest_digest'])
    apply()
    brand_id = UUID(plan['brands'][0]['id'])
    before = rows(engine)
    locked, release = threading.Event(), threading.Event()
    def hold(session, instance):
        if isinstance(instance, Brand) and threading.current_thread().name.startswith('wp0-import'):
            locked.set()
            assert release.wait(15)
    event.listen(Session, 'loaded_as_persistent', hold)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='wp0-import') as pool:
            replay = pool.submit(apply)
            try:
                assert locked.wait(15)
                with Session(engine) as competing:
                    with pytest.raises(OperationalError):
                        competing.scalar(select(Brand).where(Brand.id == brand_id).with_for_update(nowait=True))
            finally:
                release.set()
            replay.result(timeout=20)
    finally:
        event.remove(Session, 'loaded_as_persistent', hold)
    assert rows(engine) == before
