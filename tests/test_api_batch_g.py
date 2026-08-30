# -*- coding: utf-8 -*-
"""Batch G / A4 (2026-07-18): api/jobs production-hardening.
* CS-80: the UNAUTHENTICATED /jobs/{id} route served the full traceback + raw exception message.
* CS-81: a PARTIAL LLM failure (e.g. 3/4 groups) reported SUCCEEDED with no warning.
* CS-82: one shared queue split the SSE stream across concurrent viewers (fan-out now).
* CS-83: a stuck non-terminal job could never be evicted (memory leak).
* CS-96: has_llm() accepted OPENAI_API_KEY for a Gemini-only pipeline.
* CS-127: JOB_TTL_SECONDS=0 busy-spun the cleanup loop.
* CS-128: a single sweep exception permanently disabled cleanup.
Hermetic — no network; playwright stubbed; MockCaller for the LLM."""
from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")
class _Stub: ...
for n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

from api.jobs.runner import has_llm, run_pipeline_job  # noqa: E402
from api.jobs.store import STREAM_CLOSE, JobStore  # noqa: E402
from api.schemas import JobStatus, Stage, StageEvent, StageStatus  # noqa: E402
from business_profile.llm.caller import MockCaller  # noqa: E402

from tests.test_api_run import _fake_scraper_fn  # noqa: E402
from tests.test_merger import _full_staged_responses  # noqa: E402


# ---- CS-96: has_llm gates on Gemini only --------------------------------------------

def test_has_llm_ignores_openai_key(monkeypatch):
    for k in ("GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-only-openai")
    assert has_llm() is False, "an OpenAI key must not satisfy a Gemini-only pipeline"
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    assert has_llm() is True


# ---- CS-127 / CS-83: cleanup interval floor + stuck-job eviction --------------------

def test_sweep_interval_never_busy_spins():
    assert JobStore(ttl_seconds=0)._sweep_interval() >= 30      # no 0-sleep busy loop
    assert JobStore(ttl_seconds=5)._sweep_interval() >= 30
    assert JobStore(ttl_seconds=3600)._sweep_interval() == 300  # capped at 5 min


def test_stuck_non_terminal_job_is_evictable():
    import time
    store = JobStore(ttl_seconds=60)
    job = store.create()                                        # QUEUED, never terminal
    # wedge its last-update far into the past (older than the stuck grace window)
    job.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)
    expired = store._expired_ids(now=time.monotonic(), wall=time.time())
    assert job.job_id in expired                                # reclaimed, not leaked forever
    # a FRESH non-terminal job is NOT evicted
    fresh = store.create()
    assert fresh.job_id not in store._expired_ids(now=time.monotonic(), wall=time.time())


# ---- CS-128: one failing sweep must not kill the cleanup loop -----------------------

def test_cleanup_loop_survives_a_failing_sweep(monkeypatch):
    async def run():
        store = JobStore(ttl_seconds=0)
        calls = {"sweep": 0, "sleep": 0}

        def _boom(now, wall):
            calls["sweep"] += 1
            if calls["sweep"] == 1:
                raise RuntimeError("sweep boom")
            return []
        monkeypatch.setattr(store, "_expired_ids", _boom)

        async def _fast_sleep(_):
            calls["sleep"] += 1
            if calls["sleep"] >= 3:
                raise asyncio.CancelledError()
        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

        with pytest.raises(asyncio.CancelledError):
            await store._cleanup_loop()
        # the loop ran a SECOND sweep after the first one raised -> it survived (CS-128)
        assert calls["sweep"] >= 2
    asyncio.run(run())


# ---- CS-82: SSE fan-out — every subscriber gets every event -------------------------

def test_fanout_delivers_every_event_to_every_subscriber():
    async def run():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        store.attach_loop(job.job_id, asyncio.get_running_loop())
        q1 = store.subscribe(job.job_id)
        q2 = store.subscribe(job.job_id)
        ev = StageEvent(job_id=job.job_id, stage=Stage.SCRAPE, status=StageStatus.STARTED,
                        timestamp=datetime.now(timezone.utc))
        store.append_event(job.job_id, ev)
        # BOTH subscribers receive it — the shared queue used to hand it to only one (CS-82)
        assert (await asyncio.wait_for(q1.get(), 1.0)).stage == Stage.SCRAPE
        assert (await asyncio.wait_for(q2.get(), 1.0)).stage == Stage.SCRAPE
        # a terminal event closes EVERY subscriber's stream
        done = StageEvent(job_id=job.job_id, stage=Stage.FINAL, status=StageStatus.DONE,
                          timestamp=datetime.now(timezone.utc))
        store.append_event(job.job_id, done)
        for q in (q1, q2):
            got = [await asyncio.wait_for(q.get(), 1.0), await asyncio.wait_for(q.get(), 1.0)]
            assert STREAM_CLOSE in got
    asyncio.run(run())


# ---- CS-80: no traceback / raw exception message leaked to the client ---------------

def test_pipeline_error_does_not_leak_traceback_or_raw_message():
    def _boom_scraper(*a, **k):
        raise RuntimeError("secret token at C:/Users/Admin/.env line 3")

    async def run():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        store.attach_loop(job.job_id, asyncio.get_running_loop())
        await asyncio.to_thread(
            run_pipeline_job, job.job_id, "https://example.com/", store,
            skip_llm=True, persist_to_disk=False, scraper_fn=_boom_scraper)
        await asyncio.sleep(0)
        return store, job.job_id

    store, jid = asyncio.run(run())
    job = store.get(jid)
    assert job.status == JobStatus.FAILED
    err_events = [e for e in job.events if e.status == StageStatus.ERROR]
    assert err_events
    ev = err_events[-1]
    blob = f"{ev.error} {ev.payload}"
    assert "secret token" not in blob and ".env" not in blob        # raw message not leaked
    assert "traceback" not in (ev.payload or {})                    # no traceback payload
    assert "Traceback (most recent call last)" not in blob
    assert "RuntimeError" in blob                                   # the safe class name is fine


# ---- CS-81: a PARTIAL LLM failure surfaces a warning --------------------------------

def test_partial_llm_failure_emits_a_warning():
    def _partial_caller():
        # 3 of 4 groups fail -> total_calls=1, total_failed=3 (used to report clean SUCCESS)
        return MockCaller(_full_staged_responses(),
                          raise_on_group={"offerings", "positioning", "trust"}, model="gemini")

    async def run():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        store.attach_loop(job.job_id, asyncio.get_running_loop())
        await asyncio.to_thread(
            run_pipeline_job, job.job_id, "https://example.com/", store,
            skip_llm=False, persist_to_disk=False,
            caller_factory=_partial_caller, scraper_fn=_fake_scraper_fn)
        await asyncio.sleep(0)
        return store, job.job_id

    store, jid = asyncio.run(run())
    job = store.get(jid)
    llm_done = [e for e in job.events if e.stage == Stage.LLM and e.status == StageStatus.DONE]
    assert llm_done
    payload = llm_done[-1].payload or {}
    assert payload.get("warning") is not None and "partial" in str(payload["warning"]).lower()
    assert payload.get("failed", 0) >= 1
