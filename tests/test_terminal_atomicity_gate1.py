# -*- coding: utf-8 -*-
"""§1/§2 — Run success, Job terminal status and the terminal event are ONE transaction.

This is the structural elimination of the C3 window, and it is worth stating precisely what was
wrong before. `commit_scrape_success` already committed Brand + Profile + Run + Job-terminal
together, and `append_job_event` was correct on its own — but it opened its OWN UnitOfWork. So the
only way to record the terminal event was a second transaction after the business one, and a crash
in between leaves:

    Run succeeded  +  Job succeeded  +  no terminal event

A reader replaying the durable log then never sees the stream end: the job is finished and its own
history says it is still going. That is C3 arriving from the other side, and no reconciler can
distinguish it from a job that genuinely has not finished yet.

The fix is not a retry or a compensating write — it is composition. `_apply_event_locked` works
inside the caller's UnitOfWork; repositories flush, only the UnitOfWork commits. These tests inject
a failure at each seam and assert the transaction leaves NOTHING behind, so the contradictory state
is unreachable rather than merely unlikely.
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
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the terminal atomicity proof")


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


def _scrape_input(service, domain_key: str):
    from api.user_auth import hash_password
    from db.services import ProfileData, ScrapeSuccess

    user = service.create_user(email=f"atom{uuid.uuid4().hex[:8]}@test.local",
                               hashed_password=hash_password("password123"))
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=domain_key)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)
    return ScrapeSuccess(
        domain_key=domain_key, user_id=user.id, job_id=job.id, worker_id=str(job.id),
        profile=ProfileData(payload={"name": {"value": "Atomic"}}, schema_version=1),
        display_name="Atomic", source_url=f"https://{domain_key}.test/",
    ), job


def _durable_state(domain_key: str, job_id):
    """(brand rows, run rows, job status, terminal event count) read in a fresh session."""
    from sqlalchemy import select

    from db.models import Brand, Job, JobEvent, Run
    from db.session import get_session_factory

    with get_session_factory()() as session:
        brands = list(session.scalars(select(Brand).where(Brand.domain_key == domain_key)).all())
        runs = []
        if brands:
            runs = list(session.scalars(select(Run).where(Run.brand_id == brands[0].id)).all())
        job = session.get(Job, job_id)
        terminal = list(session.scalars(
            select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.is_terminal.is_(True))).all())
        return brands, runs, (job.status if job else None), len(terminal)


def test_a_successful_scrape_commits_run_job_and_terminal_event_together(service):
    domain = f"atomok{uuid.uuid4().hex[:8]}_test"
    data, job = _scrape_input(service, domain)
    service.commit_scrape_success(data)

    brands, runs, status, terminals = _durable_state(domain, job.id)
    assert len(brands) == 1 and len(runs) == 1
    assert status == "succeeded"
    assert terminals == 1, "the business completed but its durable log has no ending"

    events = service.job_events_since(job_id=job.id)
    assert events[-1].is_terminal and events[-1].stage == "final" and events[-1].status == "done"
    assert events[-1].payload["run_id"] == str(runs[0].id)


#: Every seam between the first business write and the commit. A failure at ANY of them must leave
#: the database exactly as it was — no brand, no run, no terminal status, no terminal event.
@pytest.mark.parametrize("seam", [
    "after_brand_insert",
    "after_profile_insert",
    "after_run_insert",
    "before_job_terminal_update",
    "after_job_terminal_update",
    "after_terminal_event_insert",
    "before_commit",
])
def test_a_failure_at_any_seam_leaves_no_contradictory_state(service, seam):
    domain = f"atom{seam[:6]}{uuid.uuid4().hex[:6]}_test"
    data, job = _scrape_input(service, domain)

    class _Injected(RuntimeError):
        pass

    def inject(point: str) -> None:
        if point == seam:
            raise _Injected(point)

    with pytest.raises(_Injected):
        service.commit_scrape_success(data, inject=inject)

    brands, runs, status, terminals = _durable_state(domain, job.id)
    assert brands == [], f"a brand survived a rollback at {seam}"
    assert runs == [], f"a run survived a rollback at {seam}"
    assert status not in ("succeeded", "failed"), (
        f"the job went terminal although the business transaction rolled back at {seam}")
    assert terminals == 0, f"a terminal event survived a rollback at {seam}"


def test_the_contradictory_state_is_unreachable_not_merely_unlikely(service):
    """The property stated directly: no durable state has a terminal event without its Run.

    Injecting exactly between the event insert and the commit is the window that would have existed
    had the event been written by a second transaction.
    """
    domain = f"atomwin{uuid.uuid4().hex[:8]}_test"
    data, job = _scrape_input(service, domain)

    def inject(point: str) -> None:
        if point == "after_terminal_event_insert":
            raise RuntimeError("crash between the event and the commit")

    with pytest.raises(RuntimeError):
        service.commit_scrape_success(data, inject=inject)

    brands, runs, status, terminals = _durable_state(domain, job.id)
    assert (brands, runs, terminals) == ([], [], 0)
    assert status == "running", "the job should still be claimable/reconcilable, not terminal"

    # And the same input succeeds cleanly afterwards — the failed attempt left nothing to trip on.
    service.commit_scrape_success(data)
    brands, runs, status, terminals = _durable_state(domain, job.id)
    assert len(brands) == 1 and len(runs) == 1 and status == "succeeded" and terminals == 1
