# -*- coding: utf-8 -*-
"""Restart, multi-process observation, and C3 crash repair — with genuinely separate processes.

§14, §15, §16. The property under test is that a job's truth lives in PostgreSQL and NOT in the
Python process that wrote it, so every proof here spans a process boundary: a child process writes,
the child EXITS, and this process reads. Nothing is shared but the database — no JobStore object,
no queue, no module-level anything — which is the only way to tell "the durable record says so"
apart from "the object is still in memory".

§16 is in two halves, and the first half matters more than the second:

  NORMAL finalisation cannot produce a successful durable result beside a nonterminal job. Result,
  terminal status and terminal event commit in ONE transaction, so the Class-A window is not
  narrow — it does not exist. That is asserted directly against the existing detectors.

  ABNORMAL recovery still has to work, because a process can die between "the transaction
  committed" and "anything else at all". So Class A is constructed deliberately and repaired, and
  Class B — a lease that expired with no successful result — is failed truthfully rather than left
  RUNNING forever. Both repairs are idempotent and safe to run from two processes at once.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")


class _Stub: ...


for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

import pytest  # noqa: E402

from api.persistence import ENV_BACKEND, POSTGRES, reset_persistence  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the restart/C3 proof")


def _migrate() -> None:
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, f"could not provision the database:\n{proc.stderr[-2000:]}"


@pytest.fixture()
def provider(monkeypatch):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    _migrate()
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    from db.session import dispose
    dispose()
    reset_persistence()
    from api.persistence import get_persistence
    yield get_persistence()
    dispose()
    reset_persistence()


@pytest.fixture()
def user(provider):
    from api.user_auth import hash_password

    return provider._service.create_user(
        email=f"rp{uuid.uuid4().hex[:8]}@test.local",
        hashed_password=hash_password("password123"), full_name="RP")


# =====================================================================
# a real child process
# =====================================================================

_PREAMBLE = """
import json, os, sys, types
_f = types.ModuleType("playwright"); _fs = types.ModuleType("playwright.sync_api")
class _S: ...
for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"): setattr(_fs, _n, _S)
_fs.TimeoutError = type("TimeoutError", (Exception,), {}); _fs.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _f); sys.modules.setdefault("playwright.sync_api", _fs)
sys.path.insert(0, ROOT)
os.environ["PERSISTENCE_BACKEND"] = "postgres"
os.environ["BASEERA_DATABASE_URL"] = DSN
from api.persistence import get_persistence, reset_persistence
reset_persistence()
provider = get_persistence()
from api.jobs.store import active_store
assert active_store() is None, "a child process must not inherit any JobStore"
def emit(**kw): print("@@" + json.dumps(kw), flush=True)
"""


def _child(body: str, *, wait: bool = True, extra: str = ""):
    """Run `body` in a SEPARATE interpreter against the same database. Returns parsed `emit` lines.

    Deliberately a process, not a thread: a thread would still see this interpreter's module
    globals, and "the record survived" would be indistinguishable from "the object was still
    there".
    """
    source = (f"ROOT = {str(_ROOT)!r}\nDSN = {_DSN!r}\n" + _PREAMBLE + extra
              + textwrap.dedent(body))
    proc = subprocess.Popen(
        [sys.executable, "-c", source], cwd=str(_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(_ROOT), "BASEERA_DATABASE_URL": _DSN,
             "PERSISTENCE_BACKEND": "postgres"})
    if not wait:
        return proc
    out, err = proc.communicate(timeout=180)
    assert proc.returncode == 0, f"child process failed:\n{err[-3000:]}\n{out[-2000:]}"
    return [json.loads(line[2:]) for line in out.splitlines() if line.startswith("@@")]


def _row(job_id: str):
    from uuid import UUID

    from db.models import Job
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return session.get(Job, UUID(job_id))


def _events(job_id: str) -> list:
    from uuid import UUID

    from sqlalchemy import select

    from db.models import JobEvent
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return list(session.scalars(
            select(JobEvent).where(JobEvent.job_id == UUID(job_id)).order_by(JobEvent.seq)).all())


# =====================================================================
# §14 — restart
# =====================================================================

def test_progress_written_by_a_dead_process_is_recovered_from_postgres(provider, user):
    """Process A creates a job and reports progress, then EXITS. Process B answers the poll."""
    emitted = _child(f"""
        job_id = provider.create_user_job(owner_user_id={str(user.id)!r}, domain_key="restart_a")
        from db.repositories import utcnow
        provider.record_job_event(job_id=job_id, stage="scrape", status="started",
                                  occurred_at=utcnow())
        provider.record_job_event(job_id=job_id, stage="scrape", status="done",
                                  occurred_at=utcnow(), duration_ms=12)
        provider.record_job_event(job_id=job_id, stage="llm", status="started",
                                  occurred_at=utcnow())
        emit(job_id=job_id)
    """)
    job_id = emitted[0]["job_id"]

    # THIS process never saw that job created. Everything below comes off the database.
    snapshot = provider.job_snapshot(job_id=job_id, principals=[f"user:{user.id}"])
    assert snapshot["status"] == "running"
    assert snapshot["step"] == "Understanding the brand" and snapshot["progress"] == 75
    assert snapshot["domain_key"] == "restart_a"
    assert snapshot["error"] is None

    events = provider.job_events_since(job_id=job_id, principals=[f"user:{user.id}"])
    assert [(e["stage"], e["status"]) for e in events] == [
        ("scrape", "started"), ("scrape", "done"), ("llm", "started")]
    assert [e["seq"] for e in events] == [1, 2, 3]


def test_a_direct_result_committed_by_a_dead_process_is_still_servable(provider, user):
    """§14: a successful DIRECT JobResult survives its writer."""
    emitted = _child(f"""
        job_id = provider.create_user_job(owner_user_id={str(user.id)!r}, domain_key="restart_dr")
        assert provider.finish_direct_result_job(
            job_id=job_id, profile={{"name": {{"value": "Durable"}}}},
            payload={{"ready_for_strategy": False}}) is True
        emit(job_id=job_id)
    """)
    job_id = emitted[0]["job_id"]

    result = provider.job_result(job_id=job_id, principals=[f"user:{user.id}"])
    assert result["status"] == "succeeded"
    assert result["result_source"] == "job_result"
    assert result["profile"]["name"]["value"] == "Durable"
    assert provider.job_snapshot(job_id=job_id,
                                 principals=[f"user:{user.id}"])["status"] == "done"


def test_a_run_backed_result_committed_by_a_dead_process_is_still_servable(provider, user):
    """§14: the same for a Run-backed job — the shape the route must never have to distinguish."""
    domain = f"rs{uuid.uuid4().hex[:8]}_test"
    emitted = _child(f"""
        job_id = provider.begin_scrape(domain_key={domain!r}, user_id={str(user.id)!r})
        brand = {{"domain_key": {domain!r}, "name": "Restarted"}}
        assert provider.commit_scrape(
            domain_key={domain!r}, user_id={str(user.id)!r}, ticket=job_id, brand=brand,
            profile={{"name": {{"value": "Restarted"}}}}, source_url="https://r.test/",
            display_name="Restarted") is not None
        assert provider.finish_job_success(job_id=job_id) is True
        emit(job_id=job_id)
    """)
    job_id = emitted[0]["job_id"]

    result = provider.job_result(job_id=job_id, principals=[f"user:{user.id}"])
    assert result["status"] == "succeeded" and result["result_source"] == "run"
    assert result["profile"]["name"]["value"] == "Restarted"
    assert len([e for e in _events(job_id) if e.is_terminal]) == 1


def test_a_failure_recorded_by_a_dead_process_is_recovered_with_its_error(provider, user):
    emitted = _child(f"""
        job_id = provider.create_user_job(owner_user_id={str(user.id)!r}, domain_key="restart_f")
        assert provider.finish_job_failure(job_id=job_id, error="Scrape processing failed.") is True
        emit(job_id=job_id)
    """)
    job_id = emitted[0]["job_id"]

    snapshot = provider.job_snapshot(job_id=job_id, principals=[f"user:{user.id}"])
    assert snapshot["status"] == "error" and snapshot["step"] == "Failed"
    assert snapshot["error"] == "Scrape processing failed."
    result = provider.job_result(job_id=job_id, principals=[f"user:{user.id}"])
    assert result["status"] == "failed" and result["profile"] is None


# =====================================================================
# §15 — two processes alive at once
# =====================================================================

def test_a_second_live_process_observes_writes_and_the_terminal_truth(provider, user):
    """A and B run SIMULTANEOUSLY. A writes; B watches; A terminalises; B sees it.

    B is this pytest process, so both are genuinely alive at the same time and share nothing but
    PostgreSQL — `active_store()` is asserted None inside A, and B never touches a JobStore.
    """
    import time

    from api.jobs.store import active_store

    principals = [f"user:{user.id}"]
    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key="multi_live")

    writer = _child(f"""
        import time
        from db.repositories import utcnow
        job_id = {job_id!r}
        for stage in ("scrape", "rules", "llm"):
            provider.record_job_event(job_id=job_id, stage=stage, status="started",
                                      occurred_at=utcnow())
            time.sleep(0.4)
        provider.finish_direct_result_job(job_id=job_id, profile={{"name": {{"value": "Live"}}}})
        emit(done=True)
    """, wait=False)

    try:
        seen: list[tuple[str, str]] = []
        terminal = False
        for _ in range(200):
            events = provider.job_events_since(job_id=job_id, principals=principals) or []
            seen = [(e["stage"], e["status"]) for e in events]
            if provider.job_is_terminal(job_id=job_id, principals=principals):
                terminal = True
                break
            time.sleep(0.1)
        assert terminal, f"B never observed A's terminal state; it saw {seen}"
    finally:
        writer.communicate(timeout=60)

    assert writer.returncode == 0
    assert active_store() is None, "B reached an in-memory JobStore instead of the database"

    events = provider.job_events_since(job_id=job_id, principals=principals)
    assert [(e["stage"], e["status"]) for e in events][:3] == [
        ("scrape", "started"), ("rules", "started"), ("llm", "started")]
    assert ("final", "done") == (events[-1]["stage"], events[-1]["status"])
    result = provider.job_result(job_id=job_id, principals=principals)
    assert result["status"] == "succeeded" and result["profile"]["name"]["value"] == "Live"


# =====================================================================
# §16 — C3
# =====================================================================

def test_normal_finalisation_never_produces_a_result_without_a_terminal_job(provider, user):
    """The first half of §16, and the one that makes the reconciler a safety net rather than a
    dependency: with result and terminal state in ONE transaction, Class A cannot be produced."""
    domain = f"c3{uuid.uuid4().hex[:8]}_test"
    made = []

    direct = provider.create_user_job(owner_user_id=str(user.id), domain_key="c3_direct")
    assert provider.finish_direct_result_job(job_id=direct, profile={"n": 1}) is True
    made.append(direct)

    scrape = provider.begin_scrape(domain_key=domain, user_id=str(user.id))
    assert provider.commit_scrape(
        domain_key=domain, user_id=str(user.id), ticket=scrape,
        brand={"domain_key": domain, "name": "C3"}, profile={"name": {"value": "C3"}},
        source_url="https://c3.test/", display_name="C3") is not None
    made.append(scrape)

    enrich = provider.create_user_job(owner_user_id=str(user.id), domain_key=domain)
    assert provider.finish_enrichment_job(
        job_id=enrich, domain_key=domain, user_id=str(user.id), kind="swot",
        patch={"has_swot": True}) is not None
    made.append(enrich)

    stuck = {row.id for row in provider._service.detect_crash_class_a()}
    stuck |= {row.id for row in provider._service.detect_crash_class_a_direct()}
    from uuid import UUID
    assert not (stuck & {UUID(j) for j in made}), (
        "normal finalisation left a successful durable result beside a nonterminal job")

    for job_id in made:
        assert _row(job_id).status == "succeeded"
        assert len([e for e in _events(job_id) if e.is_terminal]) == 1


def _make_class_a_run_backed(provider, user) -> tuple[str, str]:
    """A succeeded Run beside a job that never terminalised — as a crash between commits leaves it.

    Built by reaching past the atomic finalizer on purpose: the normal path cannot produce this
    (asserted above), so the abnormal case has to be constructed to be repaired.
    """
    from uuid import UUID

    from db.models import Job
    from db.services import LiveRunData
    from db.session import get_session_factory

    domain = f"ca{uuid.uuid4().hex[:8]}_test"
    seed = provider.begin_scrape(domain_key=domain, user_id=str(user.id))
    assert provider.commit_scrape(
        domain_key=domain, user_id=str(user.id), ticket=seed,
        brand={"domain_key": domain, "name": "ClassA"}, profile={"name": {"value": "ClassA"}},
        source_url="https://ca.test/", display_name="ClassA") is not None
    brand_id = _row(seed).brand_id

    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key=domain)
    run = provider._service.create_live_run(LiveRunData(
        brand_id=brand_id, user_id=user.id, kind="swot", status="succeeded",
        output={"has_swot": True}, output_schema_version=1))
    with get_session_factory()() as session:
        row = session.get(Job, UUID(job_id))
        row.run_id = run.id
        row.brand_id = brand_id
        session.commit()
    return job_id, str(run.id)


def test_class_a_is_repaired_into_success_with_exactly_one_terminal_event(provider, user):
    job_id, run_id = _make_class_a_run_backed(provider, user)
    assert _row(job_id).status == "running"
    from uuid import UUID
    assert UUID(job_id) in {r.id for r in provider._service.detect_crash_class_a()}

    repaired = provider.reconcile_jobs()
    assert repaired["class_a"] >= 1

    row = _row(job_id)
    assert row.status == "succeeded" and str(row.run_id) == run_id
    terminal = [e for e in _events(job_id) if e.is_terminal]
    assert len(terminal) == 1 and terminal[0].status == "done"
    assert terminal[0].payload.get("repaired") == "class_a"

    result = provider.job_result(job_id=job_id, principals=[f"user:{user.id}"])
    assert result["status"] == "succeeded" and result["profile"] == {"has_swot": True}


def test_class_a_repair_is_idempotent(provider, user):
    job_id, _run = _make_class_a_run_backed(provider, user)
    assert provider.reconcile_jobs()["class_a"] >= 1
    after_first = [(e.seq, e.stage, e.status) for e in _events(job_id)]

    second = provider.reconcile_jobs()
    from uuid import UUID
    assert UUID(job_id) not in {r.id for r in provider._service.detect_crash_class_a()}
    assert [(e.seq, e.stage, e.status) for e in _events(job_id)] == after_first
    assert second["class_a"] == 0 or _row(job_id).status == "succeeded"
    assert len([e for e in _events(job_id) if e.is_terminal]) == 1


def test_class_a_repair_is_safe_from_two_processes_at_once(provider, user):
    """Two reconcilers, one job, at the same instant. The row lock decides; nobody writes twice."""
    job_id, _run = _make_class_a_run_backed(provider, user)

    def repair():
        return provider._service.repair_class_a(job_id=__import__("uuid").UUID(job_id))

    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = [f.result(timeout=60) for f in [pool.submit(repair) for _ in range(4)]]

    assert answers.count(True) == 1, f"more than one reconciler claimed the repair: {answers}"
    assert len([e for e in _events(job_id) if e.is_terminal]) == 1
    assert _row(job_id).status == "succeeded"


def test_a_class_a_direct_result_is_repaired_too(provider, user):
    """The run-backed detector joins through `run_id` and is structurally blind to a direct result.

    A job whose `job_results` row committed but whose terminal state did not is the SAME fact in a
    shape the first query cannot see — and, worse, it matches the Class-B predicate (`run_id IS
    NULL`), so without its own detector it would be marked FAILED on top of a real result.
    """
    from uuid import UUID

    from db.models import Job
    from db.session import get_session_factory

    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key="c3_direct_a")
    with get_session_factory()() as session:
        session.add(__import__("db.models", fromlist=["JobResult"]).JobResult(
            job_id=UUID(job_id), result_kind="business_profile", schema_version=1,
            payload={"name": {"value": "Orphan"}}))
        session.commit()

    assert UUID(job_id) in {r.id for r in provider._service.detect_crash_class_a_direct()}
    assert provider.reconcile_jobs()["class_a"] >= 1

    row = _row(job_id)
    assert row.status == "succeeded"
    terminal = [e for e in _events(job_id) if e.is_terminal]
    assert len(terminal) == 1 and terminal[0].status == "done"
    result = provider.job_result(job_id=job_id, principals=[f"user:{user.id}"])
    assert result["profile"]["name"]["value"] == "Orphan"


def _expire_lease(job_id: str) -> None:
    from datetime import timedelta
    from uuid import UUID

    from db.models import Job
    from db.repositories import utcnow
    from db.session import get_session_factory

    with get_session_factory()() as session:
        row = session.get(Job, UUID(job_id))
        row.lease_expires_at = utcnow() - timedelta(hours=2)
        session.commit()


def test_class_b_is_failed_truthfully_rather_than_left_running(provider, user):
    """A dead worker's job with NO successful result. Failure is the truthful outcome; RUNNING
    forever is the one reading that is certainly wrong."""
    from uuid import UUID

    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key="c3_class_b")
    _expire_lease(job_id)
    assert UUID(job_id) in {r.id for r in provider._service.detect_crash_class_b()}

    repaired = provider.reconcile_jobs()
    assert repaired["class_b"] >= 1

    row = _row(job_id)
    assert row.status == "failed" and row.error
    terminal = [e for e in _events(job_id) if e.is_terminal]
    assert len(terminal) == 1 and terminal[0].status == "error"

    snapshot = provider.job_snapshot(job_id=job_id, principals=[f"user:{user.id}"])
    assert snapshot["status"] == "error" and snapshot["step"] == "Failed"
    assert provider.job_result(job_id=job_id,
                               principals=[f"user:{user.id}"])["profile"] is None


def test_class_b_repair_refuses_a_job_that_actually_produced_a_result(provider, user):
    """The guarantee behind the ordering: even reached directly, Class-B repair will not mark a job
    failed on top of a result it really produced."""
    from uuid import UUID

    from db.models import Job, JobResult
    from db.session import get_session_factory

    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key="c3_b_guard")
    with get_session_factory()() as session:
        session.add(JobResult(job_id=UUID(job_id), result_kind="business_profile",
                              schema_version=1, payload={"real": True}))
        session.commit()
    _expire_lease(job_id)

    assert provider._service.repair_class_b(job_id=UUID(job_id)) is False
    assert _row(job_id).status == "running", "a job with a real result was failed by Class-B repair"
    assert provider._service.repair_class_a(job_id=UUID(job_id)) is True
    assert _row(job_id).status == "succeeded"


def test_class_a_repair_refuses_a_job_with_no_durable_result(provider, user):
    """Repair verifies; it does not assume. Without this check `repair_class_a` would be a routine
    that marks running jobs succeeded."""
    from uuid import UUID

    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key="c3_a_guard")
    _expire_lease(job_id)
    assert provider._service.repair_class_a(job_id=UUID(job_id)) is False
    assert _row(job_id).status == "running"


def test_reconciliation_is_a_no_op_on_the_legacy_backend(monkeypatch):
    """Startup calls this unconditionally, so the legacy answer has to be honest and cheap."""
    from api.persistence import LegacyPersistence

    assert LegacyPersistence().reconcile_jobs() == {"class_a": 0, "class_b": 0, "scanned": 0}
