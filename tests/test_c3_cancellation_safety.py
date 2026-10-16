# -*- coding: utf-8 -*-
"""C3 residual: what happens when a job coroutine is cancelled mid-persistence.

Moving the BrandStore write off the loop (see test_c3_event_loop_not_blocked_by_storage.py) fixes
the freeze and, honestly, WIDENS one window. Before, the write was a synchronous call: once the
loop entered it nothing could interleave, so the write and the Job transition happened in one
uninterruptible stretch. Now there is an `await` around it, and an await is a cancellation point.

Two runtime facts make the consequence unavoidable:

  1. `asyncio.CancelledError` inherits from BaseException, NOT Exception (asserted below). Every
     `except Exception` in the job runners therefore misses it.
  2. `asyncio.to_thread` work cannot be cancelled. Cancelling the coroutine abandons the FUTURE;
     the worker thread keeps running and its write still commits.

So a cancellation delivered while the worker is writing produces: brand persisted, coroutine
unwound, and NEITHER `finish_success` nor `finish_failure` reached. The Job never becomes terminal.

This file draws the line the recovery brief asks for:

  SAFETY   — nothing is corrupted, nothing lies. Asserted as PASSING regressions; these must
             never break.
  LIVENESS — the Job should eventually become terminal. It does not. Asserted as ONE narrow
             strict xfail, so that whoever fixes it is forced to come back and delete the marker.

Deliberately NOT patched here: the honest fix needs the worker's outcome to survive the
cancellation (shield, or a durable job row a reconciler can finish). Guessing SUCCEEDED risks a
false success when the write never landed; guessing FAILED lies about a brand that is on disk.
That decision belongs with durable Jobs — Phase 0C's Class-A reconciliation predicate
(`job non-terminal AND run succeeded`) is exactly this case.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import types

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

from api.brand_store import BrandStore  # noqa: E402

_OWNER = "user-alice"
_KEY = "example_com"


def test_cancellederror_is_not_an_exception_subclass():
    """The premise of everything below, checked against THIS runtime rather than remembered."""
    assert issubclass(asyncio.CancelledError, BaseException)
    assert not issubclass(asyncio.CancelledError, Exception), (
        "CancelledError now subclasses Exception; the `except Exception` handlers in the job "
        "runners would catch it and this whole analysis must be redone")


def _run_cancelled_mid_persistence(tmp_path, monkeypatch):
    """Cancel `run_brand_job` at the exact moment the worker thread is inside the real write.

    Coordination is Events, not sleeps: the patched writer announces that it has entered the
    write, and blocks there until the test has cancelled the coroutine.
    """
    import api.brand_store as bs
    from api.jobs.store import JobStore, serving_store
    from api.webapp_jobs import run_brand_job

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    store_dir = None
    BrandStore().save({"domain_key": _KEY, "organization_name": "Example"}, owner_id=_OWNER)

    reached_write = threading.Event()     # worker: "I am inside the real write"
    release_write = threading.Event()     # test:   "you may finish"
    write_done = threading.Event()        # worker: "the bytes are committed"
    original = bs._write_atomic

    def paused_write(path, payload):
        reached_write.set()
        if not release_write.wait(15):
            raise TimeoutError("test never released the paused writer")
        try:
            return original(path, payload)
        finally:
            write_done.set()

    monkeypatch.setattr(bs, "_write_atomic", paused_write)

    async def scenario():
        jobs = JobStore(ttl_seconds=3600)
        binding = serving_store(jobs)
        binding.__enter__()
        job = jobs.create(owner_principal="user:" + _OWNER)
        job.domain_key = _KEY
        jobs.attach_loop(job.job_id, asyncio.get_running_loop())

        task = asyncio.create_task(run_brand_job(
            job_id=job.job_id, domain_key=_KEY,
            work=lambda b: {"generated": "committed-by-worker"}, owner_id=_OWNER))

        # Wait until the worker is genuinely inside the write, then cancel.
        assert await asyncio.to_thread(reached_write.wait, 15), "worker never reached the write"
        task.cancel()
        release_write.set()

        outcome = "no-exception"
        try:
            await task
        except asyncio.CancelledError:
            outcome = "cancelled"
        # Let the abandoned worker finish committing.
        assert await asyncio.to_thread(write_done.wait, 15), "worker never finished its write"
        await asyncio.sleep(0.05)
        return outcome, jobs, job.job_id

    return asyncio.run(scenario())


# =====================================================================
# SAFETY — permanent regressions. These must never break.
# =====================================================================

def test_cancelled_persistence_is_safe(tmp_path, monkeypatch):
    """Nothing is corrupted and nothing lies, even though the coroutine never finished."""
    outcome, jobs, job_id = _run_cancelled_mid_persistence(tmp_path, monkeypatch)
    assert outcome == "cancelled", "the coroutine was expected to unwind with CancelledError"

    store = BrandStore()
    path = store._path(_KEY)

    # 1. storage is valid, complete JSON — the abandoned worker still wrote atomically
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["generated"] == "committed-by-worker"
    assert not list(path.parent.glob("*.tmp")), "a staging file was left behind"

    # 2. ownership survived the cancellation untouched (C2 / F-09)
    assert on_disk["owner_id"] == _OWNER
    assert on_disk["domain_key"] == _KEY

    # 3. THE JOB AGREES WITH THE DISK — and this is what changed.
    #
    # This used to assert the opposite, because the terminal transition lived in the COROUTINE,
    # after `await asyncio.to_thread(write)`. Cancellation landed on that await: the thread went on
    # to commit the brand, and the transition it was supposed to be paired with never ran. The job
    # stayed RUNNING forever beside a committed result — Class A, recorded below as a strict xfail.
    #
    # The write and the transition are now ONE unit inside the thread (`finish_enrichment_job`),
    # and `to_thread` work cannot be cancelled. So there is no longer an await between "the work
    # committed" and "the record says so" for cancellation to land on.
    job = jobs.get(job_id)
    assert job.is_terminal(), "the brand was persisted but the job never reached a terminal state"
    assert job.status.value == "succeeded", (
        "the work committed; reporting anything but success would be the lie in the other "
        "direction")

    # 4. the result exposed is the result that committed — not a provisional one
    assert job.result is not None
    assert job.result["generated"] == "committed-by-worker"


def test_cancelled_persistence_serves_exactly_what_committed(tmp_path, monkeypatch):
    """The HTTP surface must agree with the bytes on disk — in BOTH directions.

    The old contract here was "refuse to serve a cancelled job's payload", which was right while a
    cancelled coroutine could commit a brand and leave the job non-terminal: serving a result for a
    job that never said it finished would have been a guess. Now the commit and the terminal
    transition cannot be separated, so the surface is servable exactly when something was committed
    — and refusing THAT would be the false negative, telling the owner their finished work failed.
    """
    from api.schemas import JobStatus

    _outcome, jobs, job_id = _run_cancelled_mid_persistence(tmp_path, monkeypatch)
    job = jobs.get(job_id)
    on_disk = json.loads(BrandStore()._path(_KEY).read_text(encoding="utf-8"))

    # The exact predicate `GET /api/jobs/{id}/result` applies before returning a payload.
    servable = job.status == JobStatus.SUCCEEDED and job.result is not None
    committed = on_disk.get("generated") == "committed-by-worker"
    assert servable == committed, (
        "the result endpoint and the storage disagree about whether this job produced anything")


# =====================================================================
# LIVENESS — was the one genuinely broken property; now closed.
# =====================================================================

def test_job_eventually_becomes_terminal_after_a_cancelled_commit(tmp_path, monkeypatch):
    """A job whose work demonstrably committed must not stay non-terminal forever.

    Carried a strict xfail for the whole of the JobStore era: `to_thread` cannot be cancelled, the
    transition lived after the await, and there was no reconciler. It passes now for the reason the
    marker predicted — the Class-A window was closed rather than papered over — so the marker is
    gone. Under PostgreSQL the same property holds through one transaction instead of one thread.
    """
    _outcome, jobs, job_id = _run_cancelled_mid_persistence(tmp_path, monkeypatch)
    assert jobs.get(job_id).is_terminal(), (
        "the brand was persisted but the job never reached a terminal state")
