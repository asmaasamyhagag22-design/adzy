# -*- coding: utf-8 -*-
"""B4/C — the durable job event log, against real PostgreSQL.

Four properties carry the design, and each is here because the alternative is a real failure mode:

  ORDER FROM `seq`     two events emitted inside one millisecond order arbitrarily by timestamp, and
                       a clock adjustment inverts them outright. A stream replayed in the wrong
                       order is not cosmetic: `started` after `done` reopens a finished job in the
                       reader's eyes.

  TERMINAL COHERENCE   the event and the status it implies commit TOGETHER. Two transactions would
                       let a crash leave a DONE event beside a running job — a durable record that
                       contradicts itself, which is worse than either half being missing.

  TERMINAL IMMUTABLE   a delayed callback must not turn a durable-commit failure into success. The
                       in-process store already refuses post-terminal events; the durable one must
                       too, or the cutover silently loses a guarantee.

  ONE ENDING           the partial unique index means a job cannot acquire two terminal events, so a
                       restarted reader replaying the log can never find two answers.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the durable job event proof")


def _migrate() -> None:
    """Provision as an operator would; the migration suites downgrade this database to base."""
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
def job(service):
    from api.user_auth import hash_password

    user = service.create_user(email=f"ev{uuid.uuid4().hex[:8]}@test.local",
                               hashed_password=hash_password("password123"))
    created = service.create_user_job(owner_user_id=user.id,
                                      requested_domain_key=f"ev{uuid.uuid4().hex[:6]}_test")
    service.claim_job(job_id=created.id, worker_id="w1", lease_seconds=600)
    return created


def _emit(service, job, stage, status, *, when=None, **kw):
    return service.append_job_event(
        job_id=job.id, stage=stage, status=status,
        occurred_at=when or datetime.now(timezone.utc), worker_id="w1", **kw)


def _job_row(job_id):
    from db.models import Job
    from db.session import get_session_factory
    with get_session_factory()() as session:
        row = session.get(Job, job_id)
        return (row.status, row.current_stage, row.error)


def test_the_sequence_is_monotonic_and_independent_of_timestamps(service, job):
    """Emit with DECREASING timestamps; replay order must still be emission order."""
    base = datetime.now(timezone.utc)
    for index, stage in enumerate(("scrape", "rules", "llm")):
        _emit(service, job, stage, "started", when=base - timedelta(minutes=index))

    events = service.job_events_since(job_id=job.id)
    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.stage for e in events] == ["scrape", "rules", "llm"], (
        "replay followed timestamps instead of the durable sequence")


def test_a_started_event_moves_the_job_to_running(service, job):
    _emit(service, job, "scrape", "started")
    status, stage, _error = _job_row(job.id)
    assert status == "running" and stage == "scrape"


def test_a_terminal_event_and_the_terminal_status_commit_together(service, job):
    _emit(service, job, "scrape", "started")
    event = _emit(service, job, "final", "done", payload={"ok": True})
    assert event.is_terminal is True

    status, _stage, _error = _job_row(job.id)
    assert status == "succeeded"
    # The durable log and the durable status agree; neither can be read without the other.
    assert service.job_events_since(job_id=job.id)[-1].is_terminal is True


def test_an_error_event_fails_the_job_and_keeps_its_message(service, job):
    _emit(service, job, "scrape", "started")
    event = _emit(service, job, "final", "error", error="crawl failed")
    assert event.is_terminal is True and event.error == "crawl failed"

    status, _stage, error = _job_row(job.id)
    assert status == "failed" and error == "crawl failed"


def test_a_terminal_job_refuses_further_events_and_cannot_reopen(service, job):
    _emit(service, job, "final", "done")
    assert _emit(service, job, "scrape", "started") is None, "a terminal job accepted a new event"
    assert _emit(service, job, "final", "error", error="late") is None

    status, _stage, _error = _job_row(job.id)
    assert status == "succeeded", "a delayed event reopened a finished job"
    assert len(service.job_events_since(job_id=job.id)) == 1


def test_the_public_event_fields_survive_a_round_trip(service, job):
    """No payload overloading: every StageEvent field comes back as itself.

    The payload deliberately carries an `error` key. Had these been stored inside `payload`, this is
    the call that would have silently replaced the event's own error with a caller's string.
    """
    when = datetime(2026, 8, 18, 12, 30, 45, tzinfo=timezone.utc)
    _emit(service, job, "llm", "done", when=when, duration_ms=1234,
          payload={"error": "a caller key that must not be mistaken for the event error"})

    event = service.job_events_since(job_id=job.id)[-1]
    assert event.occurred_at == when
    assert event.duration_ms == 1234
    assert event.error is None, "a caller payload key overwrote the event error field"
    assert event.payload["error"].startswith("a caller key")


def test_replay_after_a_sequence_returns_only_newer_events(service, job):
    for stage in ("scrape", "rules", "llm"):
        _emit(service, job, stage, "started")
    tail = service.job_events_since(job_id=job.id, after_seq=1)
    assert [e.stage for e in tail] == ["rules", "llm"]


def test_a_second_terminal_event_is_impossible_at_the_database_level(service, job):
    """The backstop under the application check: a direct insert cannot add a second ending."""
    from sqlalchemy.exc import IntegrityError

    from db.models import JobEvent
    from db.session import get_session_factory

    _emit(service, job, "final", "done")
    with get_session_factory()() as session:
        session.add(JobEvent(job_id=job.id, seq=99, stage="final", status="error",
                             is_terminal=True, occurred_at=datetime.now(timezone.utc)))
        with pytest.raises(IntegrityError):
            session.commit()
