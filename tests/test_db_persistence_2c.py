# -*- coding: utf-8 -*-
"""DB Phase 2C — repositories, service transactions and real PostgreSQL concurrency."""
from __future__ import annotations

import ast
import concurrent.futures
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_URL = os.environ.get("BASEERA_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _URL,
    reason="set BASEERA_TEST_DATABASE_URL to a disposable PostgreSQL database",
)


@pytest.fixture(scope="module")
def engine():
    """A disposable schema built by Alembic, never create_all and never SQLite."""
    from alembic import command
    from alembic.config import Config

    from db.config import DatabaseSettings
    from db.models import Base
    from db.session import create_engine_from
    from db.test_database_safety import require_disposable_test_database

    require_disposable_test_database(_URL)
    db_engine = create_engine_from(DatabaseSettings(url=_URL, pool_size=8, max_overflow=4))
    Base.metadata.drop_all(db_engine, checkfirst=True)
    with db_engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")

    previous = os.environ.get("BASEERA_DATABASE_URL")
    os.environ["BASEERA_DATABASE_URL"] = _URL
    try:
        command.upgrade(Config(str(_ROOT / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("BASEERA_DATABASE_URL", None)
        else:
            os.environ["BASEERA_DATABASE_URL"] = previous

    yield db_engine

    Base.metadata.drop_all(db_engine, checkfirst=True)
    with db_engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine):
    from db.models import Base

    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.exec_driver_sql(f'TRUNCATE TABLE "{table.name}" CASCADE')
    yield
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.exec_driver_sql(f'TRUNCATE TABLE "{table.name}" CASCADE')


@pytest.fixture()
def service(engine):
    from db.services import PersistenceService
    from db.session import session_factory_from
    from db.uow import unit_of_work_factory

    return PersistenceService(unit_of_work_factory(session_factory_from(engine)))


def _user(service, prefix="u"):
    return service.create_user(
        email=f"{prefix}-{uuid.uuid4().hex[:10]}@test.local", hashed_password="legacy-hash")


def _open_job(service, user, domain, worker="worker-1"):
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=domain)
    return service.claim_job(job_id=job.id, worker_id=worker, lease_seconds=600)


def _commit_brand(service, user, domain=None, *, profile=None, worker="worker-1"):
    from db.services import ProfileData, ScrapeSuccess

    domain = domain or f"brand{uuid.uuid4().hex[:10]}_com"
    job = _open_job(service, user, domain, worker)
    result = service.commit_scrape_success(ScrapeSuccess(
        domain_key=domain, user_id=user.id, job_id=job.id, worker_id=worker,
        display_name=domain, source_url=f"https://{domain.replace('_', '.')}",
        profile=ProfileData(payload=profile or {"name": domain}, schema_version=1),
    ))
    return result


def _counts(engine, domain):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        brand = session.execute(
            text("SELECT id, owner_id, current_profile_id, deleted_at FROM brands "
                 "WHERE domain_key=:d"), {"d": domain}).one_or_none()
        if brand is None:
            return None, 0, 0
        profiles = session.execute(
            text("SELECT count(*) FROM profile_snapshots WHERE brand_id=:b"),
            {"b": brand.id}).scalar_one()
        runs = session.execute(
            text("SELECT count(*) FROM runs WHERE brand_id=:b"),
            {"b": brand.id}).scalar_one()
        return brand, profiles, runs


# =====================================================================
# REPOSITORY PRIMITIVES / UOW
# =====================================================================

def test_repo_modules_never_import_api_or_fastapi():
    offenders = {}
    for path in (_ROOT / "db").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        bad = sorted(name for name in imported
                     if name == "api" or name.startswith("api.") or name.startswith("fastapi"))
        if bad:
            offenders[path.name] = bad
    assert not offenders


def test_repo_methods_have_no_commit_and_no_mutable_dict_facade():
    path = _ROOT / "db" / "repositories.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    commits = [node.lineno for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "commit"]
    methods = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert commits == []
    assert not methods.intersection({"update", "mutate", "replace_block", "add_nested", "save"})


def test_repo_uow_gives_every_repository_the_same_session_and_closes_it(engine):
    from db.session import session_factory_from
    from db.uow import UnitOfWork

    uow = UnitOfWork(session_factory_from(engine))
    with uow:
        session = uow.session
        assert session is not None
        repositories = (
            uow.users, uow.brands, uow.profiles, uow.runs, uow.approvals, uow.jobs,
            uow.calendars, uow.assets, uow.attempts, uow.owner_inputs,
            uow.competitors, uow.usage,
        )
        assert {id(repository.session) for repository in repositories} == {id(session)}
    assert uow.session is None


def test_repo_uow_rolls_back_when_caller_does_not_commit(engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from db.session import session_factory_from
    from db.uow import UnitOfWork

    factory = session_factory_from(engine)
    with UnitOfWork(factory) as uow:
        uow.users.create(email="rollback@test.local", hashed_password="x")
    with Session(engine) as session:
        assert session.execute(text(
            "SELECT count(*) FROM users WHERE email='rollback@test.local'"
        )).scalar_one() == 0


def test_repo_user_identity_is_uuid_with_canonical_and_legacy_lookups(service):
    legacy = "0123456789abcdef"
    created = service.create_user(
        email="  Mixed@Test.Local ", hashed_password="kept-as-is",
        full_name=" Person ", legacy_user_id=legacy)
    assert created.email == "mixed@test.local"
    assert service.user_by_id(created.id) == created
    assert service.user_by_email(" MIXED@test.local ") == created
    assert service.user_by_legacy_id(legacy) == created
    assert str(created.id) != legacy


def test_repo_capability_stores_only_domain_separated_digest(service):
    from db.security import capability_digest

    raw = "raw-secret-" + uuid.uuid4().hex
    digest = capability_digest(raw)
    job = service.create_capability_job(capability_digest=digest)
    fetched = service.job_for_capability(job_id=job.id, capability_digest=digest)
    assert fetched.id == job.id and fetched.capability_digest == digest
    assert raw not in repr(job)
    assert raw != fetched.capability_digest


def test_repo_importing_current_application_without_database_configuration_is_safe():
    import subprocess
    import sys

    env = dict(os.environ)
    env.pop("BASEERA_DATABASE_URL", None)
    env.pop("BASEERA_TEST_DATABASE_URL", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-s", "-c",
         "import api,api.brand_store,api.user_auth,api.jobs.store; print('legacy-import-ok')"],
        cwd=str(_ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "legacy-import-ok" in result.stdout


# =====================================================================
# SERVICE TRANSACTIONS / SEMANTICS
# =====================================================================

def test_service_first_ever_success_commits_complete_business_truth(service, engine):
    user = _user(service, "first")
    domain = f"first{uuid.uuid4().hex[:8]}_com"
    result = _commit_brand(service, user, domain, profile={"version": 1})
    brand, profiles, runs = _counts(engine, domain)
    assert brand is not None and str(brand.owner_id) == str(user.id)
    assert str(brand.current_profile_id) == str(result.profile.id)
    assert profiles == 1 and runs == 1
    assert result.run.status == "succeeded" and result.run.kind_seq == 1
    assert result.job.status == "succeeded"
    assert result.job.brand_id == result.brand.id and result.job.run_id == result.run.id


def test_service_first_ever_failure_does_not_squat_domain(service, engine):
    user = _user(service, "fail")
    domain = f"fail{uuid.uuid4().hex[:8]}_com"
    job = _open_job(service, user, domain)
    failed, changed = service.finish_job_failure(
        job_id=job.id, worker_id="worker-1", error="scrape failed")
    assert changed and failed.status == "failed"
    assert _counts(engine, domain) == (None, 0, 0)


@pytest.mark.parametrize("point", [
    "after_brand_insert", "after_profile_insert", "before_current_profile_update",
    "after_run_insert", "before_job_terminal_update", "before_commit",
])
def test_service_first_ever_failure_injection_rolls_back_everything(
    service, engine, point,
):
    from db.services import ProfileData, ScrapeSuccess

    user = _user(service, "inject")
    domain = f"inject{uuid.uuid4().hex[:8]}_com"
    job = _open_job(service, user, domain)

    def inject(actual):
        if actual == point:
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match=point):
        service.commit_scrape_success(ScrapeSuccess(
            domain_key=domain, user_id=user.id, job_id=job.id, worker_id="worker-1",
            profile=ProfileData(payload={"point": point}),
        ), inject=inject)
    assert _counts(engine, domain) == (None, 0, 0)
    assert service.job_for_user(job_id=job.id, user_id=user.id).status == "running"


def test_service_unclaimed_claim_failure_rolls_owner_and_result_back(service, engine):
    from sqlalchemy.orm import Session

    from db.models import Brand
    from db.services import ProfileData, ScrapeSuccess

    user = _user(service, "claimfail")
    domain = f"unclaimed{uuid.uuid4().hex[:8]}_com"
    with Session(engine) as session:
        session.add(Brand(domain_key=domain, owner_id=None))
        session.commit()
    job = _open_job(service, user, domain)

    def inject(point):
        if point == "after_ownership_claim":
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match="after_ownership_claim"):
        service.commit_scrape_success(ScrapeSuccess(
            domain_key=domain, user_id=user.id, job_id=job.id, worker_id="worker-1",
            profile=ProfileData(payload={"never": "committed"}),
        ), inject=inject)
    brand, profiles, runs = _counts(engine, domain)
    assert brand is not None and brand.owner_id is None and brand.current_profile_id is None
    assert profiles == 0 and runs == 0


def test_service_rescrape_creates_immutable_profile_version_and_moves_pointer(service, engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    user = _user(service, "rescrape")
    first = _commit_brand(service, user, profile={"immutable": "old"})
    second = _commit_brand(service, user, first.brand.domain_key, profile={"immutable": "new"})
    assert first.profile.version == 1 and second.profile.version == 2
    assert first.profile.id != second.profile.id
    with Session(engine) as session:
        rows = session.execute(text(
            "SELECT id,version,profile FROM profile_snapshots WHERE brand_id=:b ORDER BY version"),
            {"b": first.brand.id}).all()
        pointer = session.execute(text(
            "SELECT current_profile_id FROM brands WHERE id=:b"), {"b": first.brand.id}).scalar_one()
    assert rows[0].profile == {"immutable": "old"}
    assert rows[1].profile == {"immutable": "new"}
    assert str(pointer) == str(second.profile.id)


def test_service_foreign_owner_final_authority_refuses_without_preflight(service, engine):
    from db.errors import ForeignOwner
    from db.services import ProfileData, ScrapeSuccess

    owner, attacker = _user(service, "owner"), _user(service, "attacker")
    existing = _commit_brand(service, owner)
    job = _open_job(service, attacker, existing.brand.domain_key, worker="evil-worker")
    with pytest.raises(ForeignOwner):
        service.commit_scrape_success(ScrapeSuccess(
            domain_key=existing.brand.domain_key, user_id=attacker.id,
            job_id=job.id, worker_id="evil-worker",
            profile=ProfileData(payload={"foreign": True}),
        ))
    brand, profiles, runs = _counts(engine, existing.brand.domain_key)
    assert str(brand.owner_id) == str(owner.id)
    assert profiles == 1 and runs == 1


def test_service_current_successful_run_uses_kind_sequence_not_nullable_time(service, engine):
    from sqlalchemy.orm import Session

    from db.models import Run
    from db.services import LiveRunData

    user = _user(service, "current")
    committed = _commit_brand(service, user)
    with Session(engine) as session:
        imported = Run(
            brand_id=committed.brand.id, kind="swot", status="succeeded",
            origin="legacy_import", lineage_complete=False, kind_seq=None,
            created_at=None, imported_at=datetime.now(timezone.utc),
        )
        session.add(imported)
        session.commit()
        imported_id = imported.id
    assert service.current_successful_run(
        brand_id=committed.brand.id, kind="swot", user_id=user.id).id == imported_id

    live1 = service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=user.id, kind="swot", status="succeeded"))
    failed = service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=user.id, kind="swot",
        status="failed", error="no result"))
    assert failed.kind_seq == 2
    assert service.current_successful_run(
        brand_id=committed.brand.id, kind="swot", user_id=user.id).id == live1.id
    live3 = service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=user.id, kind="swot", status="succeeded"))
    assert live3.kind_seq == 3
    assert service.current_successful_run(
        brand_id=committed.brand.id, kind="swot", user_id=user.id).id == live3.id


def test_service_new_swot_never_inherits_previous_approval(service):
    from db.services import LiveRunData

    user = _user(service, "approval")
    committed = _commit_brand(service, user)
    first = service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=user.id, kind="swot", status="succeeded"))
    approval = service.approve_current_swot(brand_id=committed.brand.id, user_id=user.id, expected_run_id=first.id)
    assert approval.subject_run_id == first.id and approval.decided_by_user_id == user.id
    second = service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=user.id, kind="swot", status="succeeded"))
    assert second.id != first.id
    assert service.approval_for_current_swot(
        brand_id=committed.brand.id, user_id=user.id) is None


def test_service_run_terminal_state_is_idempotent_only_for_identical_replay(service):
    from db.errors import TerminalState
    from db.services import LiveRunData

    user = _user(service, "runterm")
    brand = _commit_brand(service, user).brand
    run = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="strategy", status="running"))
    first, changed = service.finalize_run_success(
        run_id=run.id, user_id=user.id, output={"answer": 1})
    replay, changed_again = service.finalize_run_success(
        run_id=run.id, user_id=user.id, output={"answer": 1})
    assert changed and not changed_again and first == replay
    with pytest.raises(TerminalState):
        service.finalize_run_failure(run_id=run.id, user_id=user.id, error="late failure")


def test_service_job_lease_and_terminal_state_are_conditional(service):
    from db.errors import ClaimLost, TerminalState

    user = _user(service, "job")
    job = service.create_user_job(owner_user_id=user.id)
    claimed = service.claim_job(job_id=job.id, worker_id="worker-a", lease_seconds=600)
    assert claimed.status == "running"
    with pytest.raises(ClaimLost):
        service.renew_job(job_id=job.id, worker_id="worker-b")
    renewed = service.renew_job(job_id=job.id, worker_id="worker-a")
    assert renewed.lease_expires_at > claimed.claimed_at
    staged = service.set_job_stage(
        job_id=job.id, worker_id="worker-a", stage="profile")
    assert staged.current_stage == "profile"
    with pytest.raises(ClaimLost):
        service.set_job_stage(job_id=job.id, worker_id="worker-b", stage="stolen")
    terminal, changed = service.finish_job_success(job_id=job.id, worker_id="worker-a")
    replay, replay_changed = service.finish_job_success(job_id=job.id, worker_id="worker-a")
    assert changed and terminal.status == "succeeded" and not replay_changed
    assert replay.status == "succeeded"
    with pytest.raises(TerminalState):
        service.finish_job_failure(job_id=job.id, worker_id="worker-a", error="late")
    with pytest.raises(TerminalState):
        service.set_job_stage(job_id=job.id, worker_id="worker-a", stage="late")


def test_service_crash_class_a_b_and_healthy_controls(service, engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from db.services import LiveRunData

    user = _user(service, "stuck")
    brand = _commit_brand(service, user).brand
    succeeded = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="swot", status="succeeded"))
    running = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="strategy", status="running"))
    healthy_run = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="poster", status="running"))

    class_a = _open_job(service, user, brand.domain_key, worker="a-worker")
    class_b = _open_job(service, user, brand.domain_key, worker="b-worker")
    healthy = _open_job(service, user, brand.domain_key, worker="healthy-worker")
    with Session(engine) as session:
        session.execute(text("UPDATE jobs SET brand_id=:b,run_id=:r WHERE id=:j"),
                        {"b": brand.id, "r": succeeded.id, "j": class_a.id})
        session.execute(text(
            "UPDATE jobs SET brand_id=:b,run_id=:r,lease_expires_at=:past WHERE id=:j"),
            {"b": brand.id, "r": running.id, "j": class_b.id,
             "past": datetime.now(timezone.utc) - timedelta(minutes=5)})
        session.execute(text("UPDATE jobs SET brand_id=:b,run_id=:r WHERE id=:j"),
                        {"b": brand.id, "r": healthy_run.id, "j": healthy.id})
        session.commit()
    assert class_a.id in {job.id for job in service.detect_crash_class_a()}
    assert class_b.id in {job.id for job in service.detect_crash_class_b()}
    assert healthy.id not in {job.id for job in service.detect_crash_class_a()}
    assert healthy.id not in {job.id for job in service.detect_crash_class_b()}


def test_service_calendar_preserves_duplicate_dates_and_stable_ordinals(service):
    user = _user(service, "calendar")
    committed = _commit_brand(service, user)
    run, posts = service.create_calendar(
        brand_id=committed.brand.id, user_id=user.id,
        input_profile_id=committed.profile.id,
        posts=[
            {"date": "2026-09-01", "platform": "instagram", "topic": "A"},
            {"date": "2026-09-01", "platform": "facebook", "topic": "B"},
        ])
    assert run.status == "succeeded"
    assert [post.ordinal for post in posts] == [0, 1]
    assert posts[0].scheduled_date == posts[1].scheduled_date
    assert posts[0].id != posts[1].id
    assert service.calendar_post(post_id=posts[1].id, user_id=user.id).ordinal == 1


def test_service_creative_rejects_metadata_before_bytes_are_durable(service, engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from db.errors import AssetNotDurable
    from db.services import CreativeFinalization, LiveRunData, VerifiedAssetData

    user = _user(service, "asset")
    brand = _commit_brand(service, user).brand
    run = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="poster", status="running"))
    attempt = service.create_creative_attempt(run_id=run.id, user_id=user.id)
    with pytest.raises(AssetNotDurable):
        service.finalize_creative(CreativeFinalization(
            user_id=user.id, run_id=run.id, attempt_id=attempt.id,
            asset=VerifiedAssetData(
                storage_key="missing.png", kind="poster", mime="image/png",
                byte_count=10, checksum_sha256="a" * 64, durable=False),
            qa={"checked": True}, qa_passed=True, output={"ok": True},
        ))
    with Session(engine) as session:
        assert session.execute(text("SELECT count(*) FROM assets")).scalar_one() == 0
        assert session.execute(text(
            "SELECT status FROM attempts WHERE id=:i"), {"i": attempt.id}).scalar_one() == "running"


def test_service_creative_finalization_is_atomic_and_scoped(service, engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from db.services import CreativeFinalization, LiveRunData, VerifiedAssetData

    user = _user(service, "creative")
    brand = _commit_brand(service, user).brand
    run = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="poster", status="running"))
    attempt = service.create_creative_attempt(run_id=run.id, user_id=user.id)
    committed = service.finalize_creative(CreativeFinalization(
        user_id=user.id, run_id=run.id, attempt_id=attempt.id,
        asset=VerifiedAssetData(
            storage_key=f"{uuid.uuid4().hex}.png", kind="poster", mime="image/png",
            byte_count=321, checksum_sha256="b" * 64, durable=True,
            created_by_user_id=user.id),
        qa={"checked": True, "pass": True}, qa_passed=True,
        output={"selected": True},
    ))
    assert committed.run.status == "succeeded"
    assert committed.run.selected_attempt_id == committed.attempt.id
    assert committed.attempt.asset_id == committed.asset.id
    with Session(engine) as session:
        links = session.execute(text(
            "SELECT r.selected_attempt_id,a.asset_id FROM runs r JOIN attempts a ON a.run_id=r.id "
            "WHERE r.id=:r"), {"r": run.id}).one()
    assert str(links.selected_attempt_id) == str(attempt.id)
    assert str(links.asset_id) == str(committed.asset.id)


def test_service_creative_failure_before_commit_rolls_back_all_metadata(service, engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from db.services import CreativeFinalization, LiveRunData, VerifiedAssetData

    user = _user(service, "creativefail")
    brand = _commit_brand(service, user).brand
    run = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="poster", status="running"))
    attempt = service.create_creative_attempt(run_id=run.id, user_id=user.id)

    def inject(point):
        if point == "before_commit":
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match="before_commit"):
        service.finalize_creative(CreativeFinalization(
            user_id=user.id, run_id=run.id, attempt_id=attempt.id,
            asset=VerifiedAssetData(
                storage_key="orphan-bytes-safe.png", kind="poster", mime="image/png",
                byte_count=123, checksum_sha256="c" * 64, durable=True),
            qa={"pass": True}, qa_passed=True, output={"ok": True},
        ), inject=inject)
    with Session(engine) as session:
        assert session.execute(text("SELECT count(*) FROM assets")).scalar_one() == 0
        assert session.execute(text(
            "SELECT status FROM attempts WHERE id=:i"), {"i": attempt.id}).scalar_one() == "running"
        assert session.execute(text(
            "SELECT status FROM runs WHERE id=:i"), {"i": run.id}).scalar_one() == "running"


@pytest.mark.parametrize(
    "point", ["after_attempt_finalize", "before_job_terminal_update", "before_commit"])
def test_service_creative_job_failure_injection_rolls_back_every_entity(
    service, engine, point,
):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from db.services import CreativeFinalization, LiveRunData, VerifiedAssetData

    user = _user(service, "creative-job-rollback")
    brand = _commit_brand(service, user).brand
    run = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="poster", status="running"))
    attempt = service.create_creative_attempt(run_id=run.id, user_id=user.id)
    job = _open_job(service, user, brand.domain_key, worker="creative-worker")

    def inject(actual):
        if actual == point:
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match=point):
        service.finalize_creative(CreativeFinalization(
            user_id=user.id, run_id=run.id, attempt_id=attempt.id,
            asset=VerifiedAssetData(
                storage_key=f"rollback-{point}.png", kind="poster", mime="image/png",
                byte_count=123, checksum_sha256="d" * 64, durable=True),
            qa={"pass": True}, qa_passed=True, output={"ok": True},
            job_id=job.id, worker_id="creative-worker",
        ), inject=inject)

    with Session(engine) as session:
        assert session.execute(text(
            "SELECT count(*) FROM assets WHERE storage_key=:k"),
            {"k": f"rollback-{point}.png"}).scalar_one() == 0
        assert session.execute(text(
            "SELECT status FROM attempts WHERE id=:i"), {"i": attempt.id}).scalar_one() == "running"
        assert session.execute(text(
            "SELECT status FROM runs WHERE id=:r"), {"r": run.id}).scalar_one() == "running"
        assert session.execute(text(
            "SELECT status FROM jobs WHERE id=:j"), {"j": job.id}).scalar_one() == "running"


def test_service_owner_inputs_and_competitors_require_recorded_owner(service, engine):
    from sqlalchemy.orm import Session

    from db.errors import ForeignOwner, UnclaimedBrand
    from db.models import Brand

    owner, other = _user(service, "curator"), _user(service, "other")
    brand = _commit_brand(service, owner).brand
    assert service.set_owner_inputs(
        brand_id=brand.id, user_id=owner.id, payload={"monthly_budget": 1000}
    ) == {"monthly_budget": 1000}
    service.add_competitor(
        brand_id=brand.id, user_id=owner.id, key="peer", payload={"name": "Peer"})
    assert service.list_competitors(brand_id=brand.id, user_id=owner.id)[0]["name"] == "Peer"
    with pytest.raises(ForeignOwner):
        service.set_owner_inputs(brand_id=brand.id, user_id=other.id, payload={"x": 1})

    with Session(engine) as session:  # explicit unclaimed legacy-row seed
        unclaimed = Brand(domain_key=f"unowned{uuid.uuid4().hex[:8]}_com")
        session.add(unclaimed)
        session.commit()
        unclaimed_id = unclaimed.id
    with pytest.raises(UnclaimedBrand):
        service.set_owner_inputs(
            brand_id=unclaimed_id, user_id=owner.id, payload={"monthly_budget": 1})


def test_service_usage_records_only_new_calls_against_real_run_context(service, engine):
    from decimal import Decimal

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    user = _user(service, "usage")
    committed = _commit_brand(service, user)
    usage = service.record_usage(
        run_id=committed.run.id, brand_id=committed.brand.id,
        provider="offline-test", model="none", input_tokens=3,
        output_tokens=5, thoughts_tokens=7, cost_usd=Decimal("0.000000"),
        stage="proof",
    )
    assert usage.run_id == committed.run.id
    assert usage.brand_id == committed.brand.id
    assert usage.cost_usd == Decimal("0.000000")
    with Session(engine) as session:
        row = session.execute(text(
            "SELECT provider,model,input_tokens,output_tokens,thoughts_tokens,cost_usd "
            "FROM usage_records WHERE id=:i"), {"i": usage.id}).one()
    assert tuple(row[:5]) == ("offline-test", "none", 3, 5, 7)
    assert Decimal(row.cost_usd) == Decimal("0.000000")


def test_service_archive_hides_row_preserves_identity_and_only_owner_reactivates(service):
    from db.errors import ForeignOwner, NotVisible
    from db.services import ProfileData, ScrapeSuccess

    owner, foreign = _user(service, "archiver"), _user(service, "foreign")
    first = _commit_brand(service, owner)
    assert service.archive_brand(domain_key=first.brand.domain_key, user_id=owner.id)
    with pytest.raises(NotVisible):
        service.get_visible_brand(user_id=owner.id, brand_id=first.brand.id)

    foreign_job = _open_job(service, foreign, first.brand.domain_key, worker="foreign-worker")
    with pytest.raises(ForeignOwner):
        service.commit_scrape_success(ScrapeSuccess(
            domain_key=first.brand.domain_key, user_id=foreign.id,
            job_id=foreign_job.id, worker_id="foreign-worker",
            profile=ProfileData(payload={"must": "not reactivate"}),
        ))

    second = _commit_brand(service, owner, first.brand.domain_key, profile={"reactivated": True})
    assert second.brand.id == first.brand.id and second.brand.deleted_at is None
    assert service.get_visible_brand(user_id=owner.id, brand_id=first.brand.id).id == first.brand.id


def test_service_foreign_reads_hide_brand_run_asset_and_job(service):
    from db.errors import NotVisible
    from db.services import CreativeFinalization, LiveRunData, VerifiedAssetData

    owner, foreign = _user(service, "reader"), _user(service, "intruder")
    committed = _commit_brand(service, owner)
    job = service.create_user_job(owner_user_id=owner.id)
    run = service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=owner.id, kind="poster", status="running"))
    attempt = service.create_creative_attempt(run_id=run.id, user_id=owner.id)
    creative = service.finalize_creative(CreativeFinalization(
        user_id=owner.id, run_id=run.id, attempt_id=attempt.id,
        asset=VerifiedAssetData(
            storage_key=f"visible-{uuid.uuid4().hex}.png", kind="poster", mime="image/png",
            byte_count=1, checksum_sha256="d" * 64, durable=True),
        qa={"pass": True}, qa_passed=True, output={"ok": True},
    ))
    checks = (
        lambda: service.get_visible_brand(user_id=foreign.id, brand_id=committed.brand.id),
        lambda: service.get_visible_brand(
            user_id=foreign.id, domain_key=committed.brand.domain_key),
        lambda: service.run_for_user(run_id=run.id, user_id=foreign.id),
        lambda: service.asset_for_user(asset_id=creative.asset.id, user_id=foreign.id),
        lambda: service.job_for_user(job_id=job.id, user_id=foreign.id),
    )
    for check in checks:
        with pytest.raises(NotVisible):
            check()


def test_service_leaves_no_idle_in_transaction_sessions(service, engine):
    from sqlalchemy import text

    user = _user(service, "leaks")
    _commit_brand(service, user)
    with engine.connect() as connection:
        count = connection.execute(text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname=current_database() AND state='idle in transaction' "
            "AND pid <> pg_backend_pid()"
        )).scalar_one()
    assert count == 0


# =====================================================================
# REAL CONCURRENCY — independent Sessions/transactions through service calls
# =====================================================================

def _race(call_a, call_b):
    barrier = threading.Barrier(2)

    def wrapped(call):
        barrier.wait(timeout=20)
        return call()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(wrapped, call_a), pool.submit(wrapped, call_b)]
        results, errors = [], []
        for future in futures:
            try:
                results.append(future.result(timeout=40))
            except BaseException as exc:  # asserted by each test
                errors.append(exc)
    return results, errors


def test_concurrent_first_ever_scrapes_have_one_owner_and_no_loser_rows(service, engine):
    from db.errors import ForeignOwner
    from db.services import ProfileData, ScrapeSuccess

    a, b = _user(service, "racea"), _user(service, "raceb")
    domain = f"firstrace{uuid.uuid4().hex[:8]}_com"
    ja = _open_job(service, a, domain, worker="worker-a")
    jb = _open_job(service, b, domain, worker="worker-b")

    def commit(user, job, worker):
        return service.commit_scrape_success(ScrapeSuccess(
            domain_key=domain, user_id=user.id, job_id=job.id, worker_id=worker,
            profile=ProfileData(payload={"winner": str(user.id)}),
        ))

    results, errors = _race(
        lambda: commit(a, ja, "worker-a"), lambda: commit(b, jb, "worker-b"))
    assert len(results) == 1 and len(errors) == 1 and isinstance(errors[0], ForeignOwner)
    winner = results[0]
    loser_job, loser_worker = ((jb, "worker-b") if winner.job.id == ja.id else (ja, "worker-a"))
    service.finish_job_failure(
        job_id=loser_job.id, worker_id=loser_worker, error="claim lost")
    brand, profiles, runs = _counts(engine, domain)
    assert str(brand.owner_id) == str(winner.brand.owner_id)
    assert profiles == 1 and runs == 1
    assert service.job_for_user(
        job_id=loser_job.id,
        user_id=b.id if loser_job.id == jb.id else a.id).status == "failed"


def test_concurrent_unclaimed_claims_have_exactly_one_winner(service, engine):
    from sqlalchemy.orm import Session

    from db.errors import ForeignOwner
    from db.models import Brand
    from db.services import ProfileData, ScrapeSuccess

    a, b = _user(service, "claima"), _user(service, "claimb")
    domain = f"claimrace{uuid.uuid4().hex[:8]}_com"
    with Session(engine) as session:
        session.add(Brand(domain_key=domain, owner_id=None))
        session.commit()
    ja = _open_job(service, a, domain, worker="worker-a")
    jb = _open_job(service, b, domain, worker="worker-b")

    def commit(user, job, worker):
        return service.commit_scrape_success(ScrapeSuccess(
            domain_key=domain, user_id=user.id, job_id=job.id, worker_id=worker,
            profile=ProfileData(payload={"winner": str(user.id)}),
        ))

    results, errors = _race(
        lambda: commit(a, ja, "worker-a"), lambda: commit(b, jb, "worker-b"))
    assert len(results) == 1 and len(errors) == 1 and isinstance(errors[0], ForeignOwner)
    winner = results[0]
    loser_job, loser_user, loser_worker = (
        (jb, b, "worker-b") if winner.job.id == ja.id else (ja, a, "worker-a"))
    failed, changed = service.finish_job_failure(
        job_id=loser_job.id, worker_id=loser_worker, error="claim lost")
    assert changed and failed.status == "failed"
    assert service.job_for_user(job_id=loser_job.id, user_id=loser_user.id).status == "failed"
    brand, profiles, runs = _counts(engine, domain)
    assert str(brand.owner_id) == str(winner.brand.owner_id)
    assert profiles == 1 and runs == 1


def test_concurrent_profile_allocators_produce_unique_versions(service):
    from db.services import ProfileData

    user = _user(service, "profile-race")
    committed = _commit_brand(service, user)
    results, errors = _race(
        lambda: service.create_profile_version(
            brand_id=committed.brand.id, user_id=user.id,
            profile=ProfileData(payload={"writer": "a"})),
        lambda: service.create_profile_version(
            brand_id=committed.brand.id, user_id=user.id,
            profile=ProfileData(payload={"writer": "b"})),
    )
    assert not errors
    assert sorted(row.version for row in results) == [2, 3]


def test_concurrent_run_allocators_produce_unique_per_kind_sequences(service):
    from db.services import LiveRunData

    user = _user(service, "run-race")
    brand = _commit_brand(service, user).brand
    results, errors = _race(
        lambda: service.create_live_run(LiveRunData(
            brand_id=brand.id, user_id=user.id, kind="swot")),
        lambda: service.create_live_run(LiveRunData(
            brand_id=brand.id, user_id=user.id, kind="swot")),
    )
    assert not errors
    assert sorted(row.kind_seq for row in results) == [1, 2]
    other = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="strategy"))
    assert other.kind_seq == 1


def test_concurrent_job_claimers_yield_one_active_claimant(service):
    from db.errors import ConcurrencyConflict

    user = _user(service, "claimers")
    job = service.create_user_job(owner_user_id=user.id)
    results, errors = _race(
        lambda: service.claim_job(job_id=job.id, worker_id="worker-a"),
        lambda: service.claim_job(job_id=job.id, worker_id="worker-b"),
    )
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], ConcurrencyConflict)
    assert results[0].claimed_by in ("worker-a", "worker-b")


def test_concurrent_job_terminalizers_preserve_first_terminal_truth(service):
    from db.errors import TerminalState

    user = _user(service, "terminalizers")
    job = _open_job(service, user, f"terminal{uuid.uuid4().hex[:8]}_com", worker="worker")
    results, errors = _race(
        lambda: service.finish_job_success(job_id=job.id, worker_id="worker"),
        lambda: service.finish_job_failure(job_id=job.id, worker_id="worker", error="failed"),
    )
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], TerminalState)
    final = service.job_for_user(job_id=job.id, user_id=user.id)
    assert final.status in ("succeeded", "failed")


def test_concurrent_double_approval_creates_one_semantic_approval(service, engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from db.services import LiveRunData

    user = _user(service, "double-approve")
    brand = _commit_brand(service, user).brand
    run = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="swot", status="succeeded"))
    results, errors = _race(
        lambda: service.approve_current_swot(brand_id=brand.id, user_id=user.id, expected_run_id=run.id),
        lambda: service.approve_current_swot(brand_id=brand.id, user_id=user.id, expected_run_id=run.id),
    )
    assert not errors and len(results) == 2
    assert results[0].id == results[1].id and results[0].subject_run_id == run.id
    with Session(engine) as session:
        count = session.execute(text(
            "SELECT count(*) FROM approvals WHERE subject_run_id=:r"), {"r": run.id}).scalar_one()
    assert count == 1


def test_concurrent_new_swot_and_approval_serialize_without_leak(service):
    from db.errors import StaleApprovalSubject
    from db.services import LiveRunData

    user = _user(service, "approve-race")
    brand = _commit_brand(service, user).brand
    old = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="swot", status="succeeded"))
    results, errors = _race(
        lambda: service.approve_current_swot(
            brand_id=brand.id, user_id=user.id, expected_run_id=old.id),
        lambda: service.create_live_run(LiveRunData(
            brand_id=brand.id, user_id=user.id, kind="swot", status="succeeded")),
    )
    assert all(isinstance(error, StaleApprovalSubject) for error in errors)
    assert len(errors) <= 1
    approvals = [result for result in results if hasattr(result, "subject_run_id")]
    assert all(approval.subject_run_id == old.id for approval in approvals)
    assert service.approval_for_current_swot(brand_id=brand.id, user_id=user.id) is None


def test_concurrent_job_failure_vs_scrape_commit_has_no_deadlock_or_partial_truth(service, engine):
    from db.errors import InvalidTransition, TerminalState
    from db.services import ProfileData, ScrapeSuccess

    user = _user(service, "job-brand-race")
    domain = f"jobbrand{uuid.uuid4().hex[:8]}_com"
    job = _open_job(service, user, domain, worker="worker")
    results, errors = _race(
        lambda: service.commit_scrape_success(ScrapeSuccess(
            domain_key=domain, user_id=user.id, job_id=job.id, worker_id="worker",
            profile=ProfileData(payload={"race": True}))),
        lambda: service.finish_job_failure(
            job_id=job.id, worker_id="worker", error="cancelled"),
    )
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], (TerminalState, InvalidTransition))
    final = service.job_for_user(job_id=job.id, user_id=user.id)
    brand, profiles, runs = _counts(engine, domain)
    if final.status == "succeeded":
        assert brand is not None and profiles == 1 and runs == 1
    else:
        assert final.status == "failed"
        assert brand is None and profiles == 0 and runs == 0
