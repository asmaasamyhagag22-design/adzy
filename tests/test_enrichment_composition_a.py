# -*- coding: utf-8 -*-
"""§A — the enrichment path itself must be composable, not just the primitive under it.

WHY THIS EXISTS. `tests/test_composable_enrichment_gate1.py` proved the PRIMITIVE composes:
`_create_live_run_locked(uow, data)` inserts a Run without committing, so a Run and a terminal Job
event can land as one fact. It proved that by calling the primitive directly.

The application does not call the primitive directly. Every enrichment in the running system goes
through `PostgresPersistence.record_enrichment`, and that method calls `create_live_run` — the
COMMITTING wrapper. So the composable primitive sits underneath a call path that cannot reach it:
eight live call sites (three in `api/routes/brands.py`, one in `api/routes/generate.py`, three in
`api/routes/influencer.py`, one in `api/webapp_jobs.py`) each commit their Run alone.

That is precisely the C3 Class-A window — a successful durable Run beside a Job that is still
running — and wiring durable Jobs behind those routes without this seam would reopen on every
enrichment path at once the window that was closed on the scrape path.

The fix mirrors the naming already established one layer down (`create_live_run` /
`_create_live_run_locked`): a route-facing form that owns its transaction, and a `_locked` form
that joins the caller's. Both build their `LiveRunData` from ONE helper, because two constructions
of the same record are two things that can drift.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the enrichment-composition proof")


def _migrate() -> None:
    import subprocess
    import sys

    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, proc.stderr[-2000:]


@pytest.fixture()
def store(monkeypatch):
    """The real `PostgresPersistence`, on the real service — the object the routes hold."""
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
def brand(store):
    """A committed brand with a current profile, via the proven scrape transaction."""
    from api.user_auth import hash_password
    from db.services import ProfileData, ScrapeSuccess

    _, service = store
    domain = f"enr{uuid.uuid4().hex[:8]}_test"
    user = service.create_user(email=f"enr{uuid.uuid4().hex[:8]}@test.local",
                               hashed_password=hash_password("password123"))
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=domain)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)
    result = service.commit_scrape_success(ScrapeSuccess(
        domain_key=domain, user_id=user.id, job_id=job.id, worker_id=str(job.id),
        profile=ProfileData(payload={"name": {"value": "Enrichment"}}, schema_version=1),
        display_name="Enrichment", source_url=f"https://{domain}.test/"))
    return result.brand, user, domain


def _uow():
    """One UnitOfWork, ready to enter — the caller-owned transaction a composed finalizer holds."""
    from db.session import get_session_factory
    from db.uow import unit_of_work_factory
    return unit_of_work_factory(get_session_factory())()


def _runs_of(brand_id, kind):
    from sqlalchemy import select

    from db.models import Run
    from db.session import get_session_factory
    with get_session_factory()() as session:
        return list(session.scalars(
            select(Run).where(Run.brand_id == brand_id, Run.kind == kind)).all())


# =====================================================================
# the seam exists at all
# =====================================================================

def test_the_locked_enrichment_form_exists_on_the_provider(store):
    """Named as a contract, so its absence is a failure rather than an AttributeError elsewhere."""
    provider, _ = store
    assert hasattr(provider, "record_enrichment_locked"), (
        "record_enrichment cannot be composed into a caller-owned UnitOfWork; a job-backed "
        "finalizer would have to commit the Run separately from the Job")


def test_the_locked_form_is_not_on_the_route_facing_protocol():
    """A UnitOfWork is a storage concept; routes must not be able to take one.

    The provider protocol is deliberately 'domain operations, not storage operations'. Publishing
    the composed form there would invite a route to open a transaction, which is the branch-on-
    backend the whole provider exists to prevent.
    """
    from api.persistence import PersistenceProvider
    assert not hasattr(PersistenceProvider, "record_enrichment_locked")


# =====================================================================
# it does not commit, and it composes
# =====================================================================

def test_the_locked_form_does_not_commit_on_its_own(store, brand):
    """A rolled-back UnitOfWork must leave no enrichment Run behind."""
    provider, _ = store
    row, user, domain = brand

    with _uow() as uow:
        provider.record_enrichment_locked(
            uow, domain_key=domain, user_id=str(user.id), kind="swot",
            patch={"marker": "rolled-back"})
        uow.rollback()

    assert _runs_of(row.id, "swot") == [], "the locked form committed a Run on its own"


def test_an_enrichment_run_and_a_terminal_job_event_commit_as_one_fact(store, brand):
    """The capability §A exists to unlock, on the REAL enrichment path."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from db.models import Job, JobEvent
    from db.services import _apply_event_locked
    from db.session import get_session_factory

    provider, service = store
    row, user, domain = brand
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=domain)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)

    with _uow() as uow:
        run = provider.record_enrichment_locked(
            uow, domain_key=domain, user_id=str(user.id), kind="swot",
            patch={"marker": "atomic"})
        _apply_event_locked(uow, job_id=job.id, stage="final", status="done",
                            occurred_at=datetime.now(timezone.utc), worker_id=str(job.id),
                            payload={"run_id": str(run.id)})
        uow.commit()

    with get_session_factory()() as session:
        assert session.get(Job, job.id).status == "succeeded"
        terminals = list(session.scalars(
            select(JobEvent).where(JobEvent.job_id == job.id,
                                   JobEvent.is_terminal.is_(True))).all())
    runs = _runs_of(row.id, "swot")
    assert len(terminals) == 1 and len(runs) == 1
    assert terminals[0].payload["run_id"] == str(runs[0].id)


def test_a_crash_leaves_neither_the_enrichment_run_nor_the_terminal_job(store, brand):
    """Class A must be unreachable on this path, not merely unlikely."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from db.models import Job, JobEvent
    from db.services import _apply_event_locked
    from db.session import get_session_factory

    provider, service = store
    row, user, domain = brand
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=domain)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)

    with pytest.raises(RuntimeError, match="crash between the two facts"):
        with _uow() as uow:
            provider.record_enrichment_locked(
                uow, domain_key=domain, user_id=str(user.id), kind="trends",
                patch={"marker": "lost"})
            _apply_event_locked(uow, job_id=job.id, stage="final", status="done",
                                occurred_at=datetime.now(timezone.utc), worker_id=str(job.id))
            raise RuntimeError("crash between the two facts and the commit")

    with get_session_factory()() as session:
        assert session.get(Job, job.id).status == "running", (
            "the job went terminal although its enrichment run rolled back")
        assert list(session.scalars(
            select(JobEvent).where(JobEvent.job_id == job.id))) == []
    assert _runs_of(row.id, "trends") == []


# =====================================================================
# equivalence — the two forms must not drift
# =====================================================================

def test_both_forms_produce_equivalent_business_state(store, brand):
    """Same brand, same kind: the locked form must allocate currentness exactly as the wrapper does."""
    provider, service = store
    row, user, domain = brand

    provider.record_enrichment(domain_key=domain, user_id=str(user.id), kind="swot",
                               patch={"marker": "wrapper"})
    via_wrapper = service.current_successful_run(brand_id=row.id, kind="swot", user_id=user.id)

    with _uow() as uow:
        via_locked = provider.record_enrichment_locked(
            uow, domain_key=domain, user_id=str(user.id), kind="swot",
            patch={"marker": "locked"})
        uow.commit()

    assert via_locked.kind == via_wrapper.kind == "swot"
    assert via_locked.status == via_wrapper.status == "succeeded"
    # The invariant a refactor is likeliest to drop silently; dropping it makes "which SWOT is
    # current" unanswerable.
    assert via_locked.kind_seq == via_wrapper.kind_seq + 1
    assert via_locked.input_profile_id == via_wrapper.input_profile_id == row.current_profile_id
    assert via_locked.output["marker"] == "locked"

    current = service.current_successful_run(brand_id=row.id, kind="swot", user_id=user.id)
    assert current.id == via_locked.id, "the newer enrichment run did not become current"


def test_an_unknown_kind_is_refused_by_both_forms(store, brand):
    """The database would refuse it anyway; refusing here keeps it a caller bug, not a mid-transaction
    constraint violation that has already consumed the caller's transaction."""
    provider, _ = store
    _row, user, domain = brand

    with pytest.raises(ValueError, match="is not one of"):
        provider.record_enrichment(domain_key=domain, user_id=str(user.id),
                                   kind="not_a_kind", patch={})
    with pytest.raises(ValueError, match="is not one of"):
        with _uow() as uow:
            provider.record_enrichment_locked(uow, domain_key=domain, user_id=str(user.id),
                                              kind="not_a_kind", patch={})


def test_an_invisible_brand_yields_none_from_both_forms(store, brand):
    """Absent and foreign-owned stay one answer, in the composed form too."""
    provider, _ = store
    _row, user, _domain = brand
    absent = f"nosuch{uuid.uuid4().hex[:8]}_test"

    assert provider.record_enrichment(domain_key=absent, user_id=str(user.id),
                                      kind="swot", patch={"m": 1}) is None
    with _uow() as uow:
        assert provider.record_enrichment_locked(uow, domain_key=absent, user_id=str(user.id),
                                                 kind="swot", patch={"m": 1}) is None


# =====================================================================
# the route-facing form must not change
# =====================================================================

def test_the_route_facing_form_still_commits_and_returns_the_brand_document(store, brand):
    """§A refactors HOW the run is created, not what the eight existing call sites observe."""
    provider, _ = store
    row, user, domain = brand

    returned = provider.record_enrichment(domain_key=domain, user_id=str(user.id),
                                          kind="trends", patch={"marker": "route"})
    assert isinstance(returned, dict) and returned, "the route-facing form stopped returning a brand"
    assert len(_runs_of(row.id, "trends")) == 1, "the route-facing form stopped committing"
