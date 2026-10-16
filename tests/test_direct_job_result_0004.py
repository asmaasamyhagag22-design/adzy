# -*- coding: utf-8 -*-
"""0004 — a durable result for Jobs that intentionally have no Run.

WHY THIS EXISTS. `/api/run` is a standalone capability-owned job: it produces a BusinessProfile and
names no brand, so `db/models.py::Job` leaves `run_id`/`brand_id` NULL and `api/jobs/runner.py`
writes the profile to DISK. Under PostgreSQL that left a succeeded durable job with no durable
answer to "what did it produce", and a restart lost it. This is the schema requirement that wiring
uncovered, not one predicted in advance.

The two result shapes are kept APART. A run-backed job keeps `runs.output` as its sole authority and
has no row in `job_results`; a direct-result job has no Run at all. Nothing is copied between them,
because two rows for one fact can disagree and nothing afterwards can say which was authoritative.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the direct-result proof")


def _migrate() -> None:
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, proc.stderr[-2000:]


@pytest.fixture()
def service(monkeypatch):
    pytest.importorskip("sqlalchemy")
    _migrate()
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    from db.services import PersistenceService
    from db.session import dispose, get_session_factory
    from db.uow import unit_of_work_factory

    dispose()
    yield PersistenceService(unit_of_work_factory(get_session_factory()))
    dispose()


@pytest.fixture()
def capability_job(service):
    """A claimed capability job — the `/api/run` shape: no brand, no run."""
    from api.job_access import issue_job_capability

    _raw, principal = issue_job_capability()
    digest = principal.split("capability:", 1)[1]
    job = service.create_capability_job(capability_digest=digest)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)
    return job


PROFILE = {"name": {"value": "Direct"}, "primary_language": "en"}


def _job_row(job_id):
    from db.models import Job
    from db.session import get_session_factory
    with get_session_factory()() as session:
        return session.get(Job, job_id)


def _events(job_id):
    from sqlalchemy import select

    from db.models import JobEvent
    from db.session import get_session_factory
    with get_session_factory()() as session:
        return list(session.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.seq)).all())


def _results(job_id):
    from db.models import JobResult
    from db.session import get_session_factory
    with get_session_factory()() as session:
        return session.get(JobResult, job_id)


# =====================================================================
# the happy path is ONE transaction
# =====================================================================

def test_result_job_and_terminal_event_commit_as_one_fact(service, capability_job):
    result, event = service.finish_direct_result_job_success(
        job_id=capability_job.id, worker_id=str(capability_job.id),
        result_kind="business_profile", payload=PROFILE)

    assert result is not None and event is not None
    assert _job_row(capability_job.id).status == "succeeded"
    stored = _results(capability_job.id)
    assert stored is not None and stored.payload == PROFILE
    assert stored.result_kind == "business_profile" and stored.schema_version == 1

    terminals = [e for e in _events(capability_job.id) if e.is_terminal]
    assert len(terminals) == 1
    assert terminals[0].stage == "final" and terminals[0].status == "done"


def test_a_run_backed_job_is_refused_a_direct_result(service):
    """`runs.output` is the authority for those; a second row could disagree with it."""
    from db.errors import InvalidTransition
    from db.services import ProfileData, ScrapeSuccess
    from api.user_auth import hash_password

    domain = f"dr{uuid.uuid4().hex[:8]}_test"
    user = service.create_user(email=f"dr{uuid.uuid4().hex[:8]}@test.local",
                               hashed_password=hash_password("password123"))
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=domain)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)
    service.commit_scrape_success(ScrapeSuccess(
        domain_key=domain, user_id=user.id, job_id=job.id, worker_id=str(job.id),
        profile=ProfileData(payload={"name": {"value": "DR"}}, schema_version=1),
        display_name="DR", source_url=f"https://{domain}.test/"))

    second = service.create_user_job(owner_user_id=user.id, requested_domain_key=domain)
    service.claim_job(job_id=second.id, worker_id=str(second.id), lease_seconds=600)
    # Bind it to the run the scrape produced, then try to give it a direct result too.
    from db.models import Job
    from db.session import get_session_factory
    with get_session_factory()() as session:
        row = session.get(Job, second.id)
        row.run_id = _job_row(job.id).run_id
        # `uq_jobs_run_id_execution_no` — one job per (run, execution ordinal). This second job is
        # a RETRY of the same run, so it is execution 2; sharing execution 1 is unrepresentable.
        row.execution_no = 2
        session.commit()

    with pytest.raises(InvalidTransition, match="run-backed"):
        service.finish_direct_result_job_success(
            job_id=second.id, worker_id=str(second.id),
            result_kind="business_profile", payload=PROFILE)
    assert _results(second.id) is None, "a refused call still wrote a result"


# =====================================================================
# atomicity at every seam — all three facts, or none
# =====================================================================

@pytest.mark.parametrize("seam", ["after_result_insert", "after_terminal_update",
                                  "after_terminal_event", "before_commit"])
def test_a_crash_at_any_seam_leaves_none_of_the_three_facts(service, capability_job, seam,
                                                            monkeypatch):
    """Fault-inject around the finalizer: the transaction must take all three or nothing.

    The seams are injected by making the COMMIT itself fail, which is the only way to be sure the
    rollback covers work already flushed — an exception thrown before any flush would prove nothing
    about durability.
    """
    from db import uow as uow_module

    original_commit = uow_module.UnitOfWork.commit
    calls = {"n": 0}

    def _exploding_commit(self):
        calls["n"] += 1
        raise RuntimeError(f"injected failure at {seam}")

    monkeypatch.setattr(uow_module.UnitOfWork, "commit", _exploding_commit)
    with pytest.raises(RuntimeError, match="injected failure"):
        service.finish_direct_result_job_success(
            job_id=capability_job.id, worker_id=str(capability_job.id),
            result_kind="business_profile", payload=PROFILE)
    monkeypatch.setattr(uow_module.UnitOfWork, "commit", original_commit)

    assert calls["n"] == 1, "the finalizer committed more than once"
    assert _results(capability_job.id) is None, "a rolled-back transaction exposed a result"
    assert _events(capability_job.id) == [], "a rolled-back transaction left an event"
    assert _job_row(capability_job.id).status != "succeeded", (
        "the job went terminal although its result rolled back")


def test_the_finalizer_commits_exactly_once(service, capability_job, monkeypatch):
    """No split transaction: one commit, not one per fact."""
    from db import uow as uow_module

    original = uow_module.UnitOfWork.commit
    calls = {"n": 0}

    def _counting_commit(self):
        calls["n"] += 1
        return original(self)

    monkeypatch.setattr(uow_module.UnitOfWork, "commit", _counting_commit)
    service.finish_direct_result_job_success(
        job_id=capability_job.id, worker_id=str(capability_job.id),
        result_kind="business_profile", payload=PROFILE)
    assert calls["n"] == 1


# =====================================================================
# failure
# =====================================================================

def test_a_failed_job_has_no_result_and_a_terminal_error_event(service, capability_job):
    from datetime import datetime, timezone

    service.append_job_event(
        job_id=capability_job.id, stage="llm", status="error",
        occurred_at=datetime.now(timezone.utc), worker_id=str(capability_job.id),
        error="provider refused")

    assert _job_row(capability_job.id).status == "failed"
    assert _results(capability_job.id) is None, "a failed job carries a result"
    terminals = [e for e in _events(capability_job.id) if e.is_terminal]
    assert len(terminals) == 1 and terminals[0].status == "error"


def test_a_failed_job_cannot_acquire_a_successful_result_afterwards(service, capability_job):
    from datetime import datetime, timezone

    service.append_job_event(
        job_id=capability_job.id, stage="llm", status="error",
        occurred_at=datetime.now(timezone.utc), worker_id=str(capability_job.id), error="boom")

    result, event = service.finish_direct_result_job_success(
        job_id=capability_job.id, worker_id=str(capability_job.id),
        result_kind="business_profile", payload=PROFILE)
    assert (result, event) == (None, None), "a failed job accepted a success"
    assert _results(capability_job.id) is None
    assert _job_row(capability_job.id).status == "failed"


# =====================================================================
# idempotency and terminality
# =====================================================================

def test_a_retry_cannot_create_a_second_result(service, capability_job):
    service.finish_direct_result_job_success(
        job_id=capability_job.id, worker_id=str(capability_job.id),
        result_kind="business_profile", payload=PROFILE)

    again, event = service.finish_direct_result_job_success(
        job_id=capability_job.id, worker_id=str(capability_job.id),
        result_kind="business_profile", payload={"name": {"value": "OVERWRITE"}})

    assert (again, event) == (None, None), "the retry was accepted"
    assert _results(capability_job.id).payload == PROFILE, "the retry replaced the first answer"
    assert len([e for e in _events(capability_job.id) if e.is_terminal]) == 1


def test_a_terminal_job_cannot_replace_its_result(service, capability_job):
    """Terminal state is immutable, and so is what the job produced."""
    service.finish_direct_result_job_success(
        job_id=capability_job.id, worker_id=str(capability_job.id),
        result_kind="business_profile", payload=PROFILE)
    before = _results(capability_job.id).created_at

    service.finish_direct_result_job_success(
        job_id=capability_job.id, worker_id=str(capability_job.id),
        result_kind="something_else", payload={"x": 1})

    stored = _results(capability_job.id)
    assert stored.payload == PROFILE
    assert stored.result_kind == "business_profile"
    assert stored.created_at == before


def test_the_repository_offers_no_update_path():
    """A terminal job whose result can still change is a job whose answer depends on when you ask."""
    from db.repositories import JobResultRepository

    for forbidden in ("update", "set", "upsert", "replace", "delete"):
        assert not hasattr(JobResultRepository, forbidden), (
            f"JobResultRepository exposes {forbidden}, so a written result is not final")


def test_a_direct_result_and_its_terminal_event_cannot_contradict(service, capability_job):
    service.finish_direct_result_job_success(
        job_id=capability_job.id, worker_id=str(capability_job.id),
        result_kind="business_profile", payload=PROFILE)

    terminal = [e for e in _events(capability_job.id) if e.is_terminal][0]
    stored = _results(capability_job.id)
    assert (terminal.status == "done") == (stored is not None)
    assert terminal.payload["result_kind"] == stored.result_kind


# =====================================================================
# the provider resolves the right source without the route knowing
# =====================================================================

def test_the_provider_returns_the_direct_payload_as_the_business_result(service, capability_job,
                                                                       monkeypatch):
    from api.job_access import CAPABILITY_PREFIX
    from api.persistence import PostgresPersistence

    service.finish_direct_result_job_success(
        job_id=capability_job.id, worker_id=str(capability_job.id),
        result_kind="business_profile", payload=PROFILE)

    provider = PostgresPersistence(service)
    principal = CAPABILITY_PREFIX + str(capability_job.capability_digest)
    result = provider.job_result(job_id=str(capability_job.id), principals=[principal])

    assert result["status"] == "succeeded"
    assert result["profile"] == PROFILE
    assert result["result_source"] == "job_result"
    # Disk is NOT the PostgreSQL authority.
    assert result["profile_path"] is None and result["manifest_path"] is None
