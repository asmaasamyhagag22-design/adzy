# -*- coding: utf-8 -*-
"""§1 — the composable Run primitive: identical business state, and now atomically composable.

WHY THIS EXISTS. `create_live_run` opened and committed its own UnitOfWork. That is correct for a
standalone call and impossible for a composed one: a Job-backed completion has to commit the Run,
the Job status and the terminal event as ONE fact, and a nested service commit splits them. It is
the same defect already found and fixed on the scrape path — and the reason this primitive had to
land BEFORE any durable Job is wired behind the analysis and creative routes. Wiring those first
would reopen, on six paths at once, the window that was just closed on one.

The refactor is a pure extraction: `_create_live_run_locked(uow, data)` does the brand lock and the
run insert, `create_live_run` wraps it in a transaction. `kind_seq` allocation, `input_profile_id`
linkage, dependencies and schema versioning all stay inside `runs.create_live_for_locked_brand` —
nothing is reimplemented, which is what makes "equivalent" checkable rather than hopeful.
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
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the composable-run proof")


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
def brand(service):
    """A committed brand with a current profile, via the proven scrape transaction."""
    from api.user_auth import hash_password
    from db.services import ProfileData, ScrapeSuccess

    domain = f"comp{uuid.uuid4().hex[:8]}_test"
    user = service.create_user(email=f"comp{uuid.uuid4().hex[:8]}@test.local",
                               hashed_password=hash_password("password123"))
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=domain)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)
    result = service.commit_scrape_success(ScrapeSuccess(
        domain_key=domain, user_id=user.id, job_id=job.id, worker_id=str(job.id),
        profile=ProfileData(payload={"name": {"value": "Composable"}}, schema_version=1),
        display_name="Composable", source_url=f"https://{domain}.test/"))
    return result.brand, user


def _run_data(brand, user, *, kind="swot", marker="x"):
    from db.services import LiveRunData
    return LiveRunData(
        brand_id=brand.id, user_id=user.id, kind=kind, status="succeeded",
        input_profile_id=brand.current_profile_id,
        output={"marker": marker}, output_schema_version=1)


def test_the_wrapper_and_the_primitive_produce_equivalent_business_state(service, brand):
    """Same inputs, same durable shape — including the invariants easiest to lose in a refactor."""
    from db.services import _create_live_run_locked
    from db.session import get_session_factory
    from db.uow import unit_of_work_factory

    row, user = brand
    via_wrapper = service.create_live_run(_run_data(row, user, marker="wrapper"))

    factory = unit_of_work_factory(get_session_factory())
    with factory() as uow:
        via_primitive = _create_live_run_locked(uow, _run_data(row, user, marker="primitive"))
        uow.commit()

    assert via_wrapper.kind == via_primitive.kind == "swot"
    assert via_wrapper.status == via_primitive.status == "succeeded"
    # kind_seq must keep advancing — the currentness allocation is the invariant a refactor is
    # likeliest to drop, and dropping it silently makes "which SWOT is current" unanswerable.
    assert via_primitive.kind_seq == via_wrapper.kind_seq + 1
    assert via_wrapper.input_profile_id == via_primitive.input_profile_id == row.current_profile_id
    assert via_primitive.output["marker"] == "primitive"

    current = service.current_successful_run(brand_id=row.id, kind="swot", user_id=user.id)
    assert current.id == via_primitive.id, "the newer run did not become current"


def test_the_primitive_does_not_commit_on_its_own(service, brand):
    """The whole point: a rolled-back UnitOfWork must leave no Run behind."""
    from sqlalchemy import select

    from db.models import Run
    from db.services import _create_live_run_locked
    from db.session import get_session_factory
    from db.uow import unit_of_work_factory

    row, user = brand
    factory = unit_of_work_factory(get_session_factory())

    class _Injected(RuntimeError):
        pass

    with pytest.raises(_Injected):
        with factory() as uow:
            _create_live_run_locked(uow, _run_data(row, user, kind="strategy", marker="rolled"))
            raise _Injected("crash before commit")

    with get_session_factory()() as session:
        strategy_runs = list(session.scalars(
            select(Run).where(Run.brand_id == row.id, Run.kind == "strategy")).all())
    assert strategy_runs == [], "the primitive committed by itself"


def test_a_run_and_a_terminal_job_event_can_now_commit_as_one_fact(service, brand):
    """The capability this unlocks — and the shape every job-backed finalizer will use.

    A crash anywhere in this block must leave NEITHER the run NOR the terminal job state, which is
    what makes 'Run succeeded + Job nonterminal' unreachable for the paths still to be wired.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from db.models import Job, JobEvent, Run
    from db.services import _apply_event_locked, _create_live_run_locked
    from db.session import get_session_factory
    from db.uow import unit_of_work_factory

    row, user = brand
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=row.domain_key)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)

    factory = unit_of_work_factory(get_session_factory())
    with factory() as uow:
        run = _create_live_run_locked(uow, _run_data(row, user, kind="calendar", marker="atomic"))
        _apply_event_locked(uow, job_id=job.id, stage="final", status="done",
                            occurred_at=datetime.now(timezone.utc), worker_id=str(job.id),
                            payload={"run_id": str(run.id)})
        uow.commit()

    with get_session_factory()() as session:
        assert session.get(Job, job.id).status == "succeeded"
        terminals = list(session.scalars(
            select(JobEvent).where(JobEvent.job_id == job.id,
                                   JobEvent.is_terminal.is_(True))).all())
        runs = list(session.scalars(
            select(Run).where(Run.brand_id == row.id, Run.kind == "calendar")).all())
    assert len(terminals) == 1 and len(runs) == 1
    assert terminals[0].payload["run_id"] == str(runs[0].id)


def test_a_crash_in_the_composed_block_leaves_neither_the_run_nor_the_terminal_job(service, brand):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from db.models import Job, JobEvent, Run
    from db.services import _apply_event_locked, _create_live_run_locked
    from db.session import get_session_factory
    from db.uow import unit_of_work_factory

    row, user = brand
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=row.domain_key)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)

    factory = unit_of_work_factory(get_session_factory())
    with pytest.raises(RuntimeError, match="crash between the two facts"):
        with factory() as uow:
            _create_live_run_locked(uow, _run_data(row, user, kind="social", marker="lost"))
            _apply_event_locked(uow, job_id=job.id, stage="final", status="done",
                                occurred_at=datetime.now(timezone.utc), worker_id=str(job.id))
            raise RuntimeError("crash between the two facts and the commit")

    with get_session_factory()() as session:
        assert session.get(Job, job.id).status == "running", (
            "the job went terminal although its run rolled back")
        assert list(session.scalars(
            select(JobEvent).where(JobEvent.job_id == job.id))) == []
        assert list(session.scalars(
            select(Run).where(Run.brand_id == row.id, Run.kind == "social"))) == []
