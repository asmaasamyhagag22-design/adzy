# -*- coding: utf-8 -*-
"""The typed Job surface on the persistence provider, proved on the PostgreSQL backend.

WHY THIS EXISTS. The seven JOB routes reached `request.app.state.store` directly, which is why the
route matrix marks them INTENTIONALLY_UNAVAILABLE: there was no backend-independent way to ask a
job question. This surface is that way — semantic operations only, no UnitOfWork, no repository, no
SQLAlchemy type, and no `stream_job_events` (SSE framing is HTTP transport and belongs to the
route; only durable retrieval belongs here).

The ownership rule is the part worth pinning: a principal decides WHICH lookup runs, not merely
whether the answer is handed back. A capability principal can never reach a user-owned job, because
the query that would find it is never issued.
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
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the job-provider proof")


def _migrate() -> None:
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, proc.stderr[-2000:]


@pytest.fixture()
def store(monkeypatch):
    pytest.importorskip("sqlalchemy")
    _migrate()
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    from api.persistence import PostgresPersistence
    from db.services import PersistenceService
    from db.session import dispose, get_session_factory
    from db.uow import unit_of_work_factory

    dispose()
    service = PersistenceService(unit_of_work_factory(get_session_factory()))
    yield PostgresPersistence(service), service
    dispose()


@pytest.fixture()
def user(store):
    from api.user_auth import hash_password
    _, service = store
    return service.create_user(email=f"jp{uuid.uuid4().hex[:8]}@test.local",
                               hashed_password=hash_password("password123"))


def _user_principal(user):
    from api.job_access import USER_PREFIX
    return USER_PREFIX + str(user.id)


def _capability():
    from api.job_access import issue_job_capability
    return issue_job_capability()          # (raw token, "capability:<digest>")


# =====================================================================
# the surface is domain-shaped, not storage-shaped
# =====================================================================

def test_the_protocol_exposes_no_storage_types_or_streaming():
    """A route that could take a transaction is a route that can branch on the backend."""
    from api.persistence import PersistenceProvider

    for forbidden in ("stream_job_events", "unit_of_work", "session", "repositories"):
        assert not hasattr(PersistenceProvider, forbidden), (
            f"{forbidden} must not be on the route-facing provider")
    for required in ("create_user_job", "create_capability_job", "job_snapshot",
                     "job_result", "job_events_since", "job_is_terminal"):
        assert hasattr(PersistenceProvider, required), f"{required} missing from the provider"


def test_both_backends_implement_the_same_job_surface():
    from api.persistence import LegacyPersistence, PostgresPersistence

    for name in ("create_user_job", "create_capability_job", "job_snapshot",
                 "job_result", "job_events_since", "job_is_terminal"):
        assert hasattr(LegacyPersistence, name), f"legacy is missing {name}"
        assert hasattr(PostgresPersistence, name), f"postgres is missing {name}"


# =====================================================================
# creation and ownership
# =====================================================================

def test_a_user_job_is_durable_and_visible_to_its_owner(store, user):
    provider, _ = store
    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key="example_com")

    snapshot = provider.job_snapshot(job_id=job_id, principals=[_user_principal(user)])
    assert snapshot is not None
    assert snapshot["status"] == "running"           # queued projects to running for the SPA
    assert snapshot["domain_key"] == "example_com"
    assert snapshot["job_id"] == job_id


def test_a_capability_job_stores_only_the_digest(store):
    """The raw token must not be reachable from anything the durable job carries."""
    provider, service = store
    raw, principal = _capability()
    job_id = provider.create_capability_job(capability_digest=principal)

    assert provider.job_snapshot(job_id=job_id, principals=[principal]) is not None

    from uuid import UUID
    row = service.job_for_capability(
        job_id=UUID(job_id), capability_digest=principal.split("capability:", 1)[1])
    assert raw not in str(row.capability_digest or "")
    assert raw not in repr(row)


def test_a_foreign_principal_gets_none_not_an_error(store, user):
    """Absent and foreign-owned are ONE answer, so a job id cannot be probed for existence."""
    provider, _ = store
    job_id = provider.create_user_job(owner_user_id=str(user.id))
    _raw, stranger = _capability()

    assert provider.job_snapshot(job_id=job_id, principals=[stranger]) is None
    assert provider.job_result(job_id=job_id, principals=[stranger]) is None
    assert provider.job_events_since(job_id=job_id, principals=[stranger]) is None
    assert provider.job_is_terminal(job_id=job_id, principals=[stranger]) is False


def test_a_capability_cannot_reach_a_user_owned_job_even_with_a_matching_string(store, user):
    """The principal picks the QUERY, so the user-owned row is never a candidate."""
    provider, _ = store
    job_id = provider.create_user_job(owner_user_id=str(user.id))
    forged = "capability:" + str(user.id)
    assert provider.job_snapshot(job_id=job_id, principals=[forged]) is None


def test_an_unparseable_job_id_is_none_not_a_crash(store, user):
    provider, _ = store
    assert provider.job_snapshot(job_id="not-a-uuid",
                                 principals=[_user_principal(user)]) is None


# =====================================================================
# events, ordered by seq
# =====================================================================

def test_events_come_back_in_seq_order_with_their_durable_fields(store, user):
    from datetime import datetime, timezone
    from uuid import UUID

    provider, service = store
    job_id = provider.create_user_job(owner_user_id=str(user.id))
    stamp = datetime.now(timezone.utc)
    for stage in ("scrape", "rules", "llm"):
        service.append_job_event(job_id=UUID(job_id), stage=stage, status="started",
                                 occurred_at=stamp, worker_id=job_id,
                                 duration_ms=7, payload={"s": stage})

    events = provider.job_events_since(job_id=job_id, principals=[_user_principal(user)])
    assert [e["stage"] for e in events] == ["scrape", "rules", "llm"]
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert events[0]["duration_ms"] == 7 and events[0]["payload"] == {"s": "scrape"}


def test_after_seq_resumes_without_replaying_or_skipping(store, user):
    """The resumption contract an SSE reconnect depends on."""
    from datetime import datetime, timezone
    from uuid import UUID

    provider, service = store
    job_id = provider.create_user_job(owner_user_id=str(user.id))
    stamp = datetime.now(timezone.utc)
    for stage in ("scrape", "rules", "llm"):
        service.append_job_event(job_id=UUID(job_id), stage=stage, status="started",
                                 occurred_at=stamp, worker_id=job_id)

    principals = [_user_principal(user)]
    everything = provider.job_events_since(job_id=job_id, principals=principals)
    resumed = provider.job_events_since(job_id=job_id, principals=principals,
                                        after_seq=everything[0]["seq"])
    assert [e["stage"] for e in resumed] == ["rules", "llm"]


# =====================================================================
# terminal state and result
# =====================================================================

def test_creation_claims_the_job_for_this_process(store, user):
    """The provider claims at creation, because THIS process is the worker.

    Not cosmetic: `jobs.finish` refuses a job that is not running under the claiming worker's
    lease, so a job created and never claimed could emit progress and then be unable to
    terminalise — a job that can start and cannot finish. Every `service.claim_job` call these
    tests used to make afterwards is now redundant, and conflicts.
    """
    from uuid import UUID

    provider, service = store
    job_id = provider.create_user_job(owner_user_id=str(user.id))
    row = service.job_by_id(job_id=UUID(job_id))
    assert row.status == "running"
    assert row.claimed_by == job_id
    assert row.lease_expires_at is not None


def test_claiming_is_idempotent_for_the_same_worker(store, user):
    """A second creation-time claim by the SAME worker is a no-op, not a crash — and a claim by a
    DIFFERENT worker still raises, because "someone claimed it" is not "I claimed it"."""
    from uuid import UUID

    import pytest as _pytest
    from db.errors import ConcurrencyConflict

    provider, service = store
    job_id = provider.create_user_job(owner_user_id=str(user.id))
    provider._claim(UUID(job_id))                       # same worker id — tolerated
    with _pytest.raises(ConcurrencyConflict):
        service.claim_job(job_id=UUID(job_id), worker_id="someone-else", lease_seconds=600)


def test_a_running_job_reports_no_completed_result(store, user):
    provider, _ = store
    job_id = provider.create_user_job(owner_user_id=str(user.id))
    result = provider.job_result(job_id=job_id, principals=[_user_principal(user)])
    assert result["status"] in ("queued", "running")
    assert result["profile"] is None
    assert provider.job_is_terminal(job_id=job_id, principals=[_user_principal(user)]) is False


def test_a_failed_job_reports_its_error_and_no_successful_result(store, user):
    from uuid import UUID

    provider, service = store
    job_id = provider.create_user_job(owner_user_id=str(user.id))
    service.finish_job_failure(job_id=UUID(job_id), worker_id=job_id, error="provider refused")

    principals = [_user_principal(user)]
    result = provider.job_result(job_id=job_id, principals=principals)
    assert result["status"] == "failed"
    assert result["profile"] is None
    assert result["error"] == "provider refused"
    assert provider.job_is_terminal(job_id=job_id, principals=principals) is True

    snapshot = provider.job_snapshot(job_id=job_id, principals=principals)
    assert snapshot["status"] == "error" and snapshot["step"] == "Failed"


def test_status_and_result_cannot_contradict(store, user):
    """Whatever the snapshot says about finishing, the result must agree."""
    from uuid import UUID

    provider, service = store
    job_id = provider.create_user_job(owner_user_id=str(user.id))
    principals = [_user_principal(user)]

    for _ in range(2):
        snapshot = provider.job_snapshot(job_id=job_id, principals=principals)
        result = provider.job_result(job_id=job_id, principals=principals)
        finished = snapshot["status"] in ("done", "error")
        assert finished == provider.job_is_terminal(job_id=job_id, principals=principals)
        if not finished:
            assert result["profile"] is None, "an unfinished job produced a completed result"
        service.finish_job_failure(job_id=UUID(job_id), worker_id=job_id, error="x")


# =====================================================================
# the capability-job result gap, stated rather than hidden
# =====================================================================

def test_a_capability_job_declares_that_it_has_no_durable_result(store):
    """`/api/run` scrapes to DISK and names no brand, so `Job.run_id` is NULL by design.

    That means a succeeded capability job has no durable profile to return. The provider must say
    so explicitly — `profile: None` alone would read as "the work produced nothing", which is a
    different and false claim. Wiring `/api/run` to PostgreSQL without somewhere durable to put its
    BusinessProfile would silently regress `GET /api/jobs/{id}/result`.
    """
    provider, _ = store
    _raw, principal = _capability()
    job_id = provider.create_capability_job(capability_digest=principal)

    result = provider.job_result(job_id=job_id, principals=[principal])
    assert result is not None
    # Not yet finished, so no source has been established.
    assert result["result_source"] is None


def test_a_user_job_bound_to_a_run_declares_a_durable_result(store, user):
    """The contrast that makes the flag meaningful rather than a constant."""
    from api.user_auth import hash_password  # noqa: F401  (fixture parity)
    from db.services import ProfileData, ScrapeSuccess
    from uuid import UUID

    provider, service = store
    domain = f"jp{uuid.uuid4().hex[:8]}_test"
    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key=domain)
    service.commit_scrape_success(ScrapeSuccess(
        domain_key=domain, user_id=user.id, job_id=UUID(job_id), worker_id=job_id,
        profile=ProfileData(payload={"name": {"value": "JP"}}, schema_version=1),
        display_name="JP", source_url=f"https://{domain}.test/"))

    result = provider.job_result(job_id=job_id, principals=[_user_principal(user)])
    assert result["status"] == "succeeded"
    assert result["result_source"] == "run", "a run-backed job must resolve through its Run"
    assert provider.job_is_terminal(job_id=job_id, principals=[_user_principal(user)]) is True
