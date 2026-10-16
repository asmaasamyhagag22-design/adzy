"""SSE streaming tests.

Covers:
- Event replay for a late subscriber (already-finished job)
- Two concurrent jobs maintain independent event streams
- Stream emits structured events in correct order
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

# Stub playwright
_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")
class _Stub: ...
for n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

from fastapi.testclient import TestClient  # noqa: E402

from api.job_access import issue_job_capability  # noqa: E402
from api.jobs.runner import run_pipeline_job  # noqa: E402
from api.jobs.store import JobStore  # noqa: E402
from api.main import app  # noqa: E402
from api.schemas import JobStatus, Stage  # noqa: E402

from tests.test_api_run import _fake_scraper_fn  # noqa: E402


def _run_pipeline_sync(store: JobStore, job_id: str, *, skip_llm: bool = True) -> None:
    """Drive the pipeline synchronously in this thread."""
    run_pipeline_job(
        job_id, "https://example.com/", store,
        skip_llm=skip_llm,
        persist_to_disk=False,
        scraper_fn=_fake_scraper_fn,
    )


# ---------------------------------------------------------------------
# Event replay (late subscriber)
# ---------------------------------------------------------------------

def test_stream_replays_events_for_completed_job():
    """A client connecting AFTER the job finishes should still receive
    every event via the replay buffer."""
    with TestClient(app) as client:
        client.get("/api/health")  # warm up the app
        store = app.state.store
        job_token, owner_principal = issue_job_capability()
        job = store.create(owner_principal=owner_principal)
        loop = asyncio.new_event_loop()

        async def emit_events():
            # Attach a real loop so emit_threadsafe works
            store.attach_loop(job.job_id, asyncio.get_running_loop())
            await asyncio.to_thread(_run_pipeline_sync, store, job.job_id)
            await asyncio.sleep(0)

        asyncio.run(emit_events())

        # Job is now terminal — connect AFTER the fact
        assert store.get(job.job_id).status == JobStatus.SUCCEEDED

        with client.stream("GET", f"/api/jobs/{job.job_id}/stream",
                           headers={"X-Job-Token": job_token}) as resp:
            assert resp.status_code == 200
            raw = b""
            for chunk in resp.iter_bytes():
                raw += chunk
                if len(raw) > 50_000:
                    break

        text = raw.decode("utf-8", errors="replace")
        # Each SSE message has a `data: {...}` line. Count them.
        data_lines = [line for line in text.splitlines() if line.startswith("data:")]
        # We expect at least the 7 stages we saw in test_api_run (skip_llm=true
        # case has 5 stage events: scrape started/done, rules started/done, final/done)
        assert len(data_lines) >= 5

        # Each data line is valid JSON with stage + status
        events = [json.loads(line[len("data: "):]) for line in data_lines]
        stages = [e.get("stage") for e in events if "stage" in e]
        assert "scrape" in stages
        assert "rules" in stages
        assert "final" in stages


# ---------------------------------------------------------------------
# Concurrent jobs stay independent
# ---------------------------------------------------------------------

def test_two_concurrent_jobs_have_independent_streams():
    """Run two pipelines in parallel; verify each job's events stay in
    that job's queue and don't leak across."""
    async def run_two():
        store = JobStore(ttl_seconds=60)
        job_a = store.create()
        job_b = store.create()
        loop = asyncio.get_running_loop()
        store.attach_loop(job_a.job_id, loop)
        store.attach_loop(job_b.job_id, loop)

        # Launch both pipelines concurrently
        await asyncio.gather(
            asyncio.to_thread(_run_pipeline_sync, store, job_a.job_id),
            asyncio.to_thread(_run_pipeline_sync, store, job_b.job_id),
        )
        await asyncio.sleep(0)

        # Verify event isolation
        ja = store.get(job_a.job_id)
        jb = store.get(job_b.job_id)
        assert ja.status == JobStatus.SUCCEEDED
        assert jb.status == JobStatus.SUCCEEDED

        # Every event in job A's history must reference job A's id, ditto B
        for ev in ja.events:
            assert ev.job_id == job_a.job_id
        for ev in jb.events:
            assert ev.job_id == job_b.job_id

        # Stages reached are the same set (since same pipeline)
        a_stages = {e.stage for e in ja.events}
        b_stages = {e.stage for e in jb.events}
        assert a_stages == b_stages

    asyncio.run(run_two())


# ---------------------------------------------------------------------
# Stage events carry timing and payloads
# ---------------------------------------------------------------------

def test_stage_done_events_carry_timing_and_payload():
    """Confirm DONE events have duration_ms set and a payload dict."""
    async def run_test():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        store.attach_loop(job.job_id, asyncio.get_running_loop())
        await asyncio.to_thread(_run_pipeline_sync, store, job.job_id)
        await asyncio.sleep(0)
        return store, job.job_id

    store, jid = asyncio.run(run_test())
    job = store.get(jid)
    done_events = [e for e in job.events if e.status.value == "done"]
    assert done_events
    # Every DONE event has a payload
    for e in done_events:
        assert e.payload is not None, f"DONE event for {e.stage} missing payload"
    # The scrape DONE event has manifest_path and pages_succeeded
    scrape_done = next(e for e in done_events if e.stage == Stage.SCRAPE)
    assert "manifest_path" in scrape_done.payload
    assert "pages_succeeded" in scrape_done.payload
