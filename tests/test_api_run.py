"""End-to-end /api/run tests with stubbed scraper and mocked LLM.

These don't make any network calls. We:
- Inject a fake scraper_fn that returns a hand-built ScrapeManifest
- Inject a MockCaller that returns pre-staged LLM responses
- Drive the runner directly through the FastAPI app

The pipeline plumbing itself is exercised; only the OS-level
playwright launch + OpenAI HTTP call are bypassed.
"""
from __future__ import annotations

import sys
import time
import types
from datetime import datetime, timezone

# Stub playwright BEFORE importing scraper
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

from api import jobs as jobs_pkg  # noqa: E402
from api.job_access import issue_job_capability  # noqa: E402
from api.jobs.runner import run_pipeline_job  # noqa: E402
from api.main import app  # noqa: E402
from api.schemas import JobStatus, Stage, StageStatus  # noqa: E402


# Reuse the rich manifest + staged LLM responses from the merger tests
from tests.test_merger import _full_staged_responses, _rich_manifest  # noqa: E402
from business_profile.llm.caller import MockCaller  # noqa: E402


def _fake_scraper_fn(url, output_root, **_crawl_options):
    """Return a fake (manifest, out_dir) tuple as the real scraper would."""
    import tempfile
    from pathlib import Path
    manifest = _rich_manifest()
    # Use a real temp dir so the runner can write business_profile.json there
    out_dir = Path(tempfile.mkdtemp(prefix="api_test_"))
    (out_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8",
    )
    return manifest, out_dir


def _make_caller():
    return MockCaller(_full_staged_responses(),
                       input_tokens=500, output_tokens=200,
                       model="gpt-4o-mini")


# ---------------------------------------------------------------------
# Direct runner test (no HTTP): proves the stage stream + final result
# ---------------------------------------------------------------------

def test_runner_emits_all_stages_in_order():
    """Drive run_pipeline_job directly and verify the event sequence."""
    import asyncio
    from api.jobs.store import JobStore

    async def run_test():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        loop = asyncio.get_running_loop()
        store.attach_loop(job.job_id, loop)

        # Run in a thread so the threadsafe emits work as in production
        await asyncio.to_thread(
            run_pipeline_job,
            job.job_id, "https://example.com/", store,
            skip_llm=False,
            persist_to_disk=True,
            caller_factory=_make_caller,
            scraper_fn=_fake_scraper_fn,
        )
        # Drain the queue scheduling callbacks
        await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

        return store, job.job_id

    store, job_id = asyncio.run(run_test())
    job = store.get(job_id)
    assert job is not None

    # Expect 7 stage pairs (started+done), plus FINAL/DONE
    stage_sequence = [
        (Stage.SCRAPE, StageStatus.STARTED),
        (Stage.SCRAPE, StageStatus.DONE),
        (Stage.RULES, StageStatus.STARTED),
        (Stage.RULES, StageStatus.DONE),
        (Stage.EVIDENCE_PACK, StageStatus.STARTED),
        (Stage.EVIDENCE_PACK, StageStatus.DONE),
        (Stage.LLM, StageStatus.STARTED),
        (Stage.LLM, StageStatus.DONE),
        (Stage.VALIDATE, StageStatus.STARTED),
        (Stage.VALIDATE, StageStatus.DONE),
        (Stage.MERGE, StageStatus.STARTED),
        (Stage.MERGE, StageStatus.DONE),
        (Stage.FINAL, StageStatus.DONE),
    ]
    actual = [(e.stage, e.status) for e in job.events]
    assert actual == stage_sequence, f"Got: {actual}"

    # Job succeeded
    assert job.status == JobStatus.SUCCEEDED
    assert job.result is not None
    # Profile core fields present
    assert job.result["name"]["value"] == "SP Clinic"
    assert job.result["quality"]["ready_for_strategy"] is True


def test_runner_persists_profile_to_disk():
    """When persist_to_disk=True, business_profile.json is written next to manifest."""
    import asyncio
    from pathlib import Path
    from api.jobs.store import JobStore

    async def run_test():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        store.attach_loop(job.job_id, asyncio.get_running_loop())
        await asyncio.to_thread(
            run_pipeline_job,
            job.job_id, "https://example.com/", store,
            skip_llm=False,
            persist_to_disk=True,
            caller_factory=_make_caller,
            scraper_fn=_fake_scraper_fn,
        )
        await asyncio.sleep(0)
        return store, job.job_id

    store, job_id = asyncio.run(run_test())
    job = store.get(job_id)
    assert job.profile_path is not None
    assert Path(job.profile_path).exists()
    # Round-trip the persisted file
    import json
    persisted = json.loads(Path(job.profile_path).read_text())
    assert persisted["name"]["value"] == "SP Clinic"


def test_runner_skip_llm_path():
    """skip_llm=true means LLM/Validate/Merge stages are skipped; we jump to FINAL."""
    import asyncio
    from api.jobs.store import JobStore

    async def run_test():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        store.attach_loop(job.job_id, asyncio.get_running_loop())
        await asyncio.to_thread(
            run_pipeline_job,
            job.job_id, "https://example.com/", store,
            skip_llm=True,
            persist_to_disk=False,
            scraper_fn=_fake_scraper_fn,
        )
        await asyncio.sleep(0)
        return store, job.job_id

    store, job_id = asyncio.run(run_test())
    job = store.get(job_id)
    stages_seen = {e.stage for e in job.events}
    # Only scrape/rules/final — no LLM/validate/merge
    assert Stage.SCRAPE in stages_seen
    assert Stage.RULES in stages_seen
    assert Stage.FINAL in stages_seen
    assert Stage.LLM not in stages_seen
    assert Stage.VALIDATE not in stages_seen
    assert Stage.MERGE not in stages_seen
    assert job.status == JobStatus.SUCCEEDED


def test_runner_scrape_error_propagates():
    """If the scraper raises, the job ends in FAILED with an ERROR event."""
    import asyncio
    from api.jobs.store import JobStore

    def broken_scraper(url, output_root):
        raise RuntimeError("network unreachable")

    async def run_test():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        store.attach_loop(job.job_id, asyncio.get_running_loop())
        await asyncio.to_thread(
            run_pipeline_job,
            job.job_id, "https://example.com/", store,
            skip_llm=True,
            scraper_fn=broken_scraper,
        )
        await asyncio.sleep(0)
        return store, job.job_id

    store, job_id = asyncio.run(run_test())
    job = store.get(job_id)
    assert job.status == JobStatus.FAILED
    # CS-80: the client-facing error names the STAGE + exception CLASS, but NOT the raw exception
    # message (the /jobs/{id} route is unauthenticated — a raw message can leak paths/secrets).
    assert "scrape" in (job.error or "").lower() and "RuntimeError" in (job.error or "")
    assert "network unreachable" not in (job.error or "")
    # Last event must be a scrape ERROR
    last = job.events[-1]
    assert last.stage == Stage.SCRAPE
    assert last.status == StageStatus.ERROR


# ---------------------------------------------------------------------
# HTTP-level tests using TestClient
# ---------------------------------------------------------------------

def test_run_endpoint_returns_job_id(monkeypatch):
    """POST /api/run with skip_llm=true creates a job and returns its id.
    We monkey-patch the scraper so no real network call happens."""
    monkeypatch.setattr("api.jobs.runner.scraper_scrape", _fake_scraper_fn)
    with TestClient(app) as client:
        r = client.post("/api/run", json={
            "url": "https://example.com/",
            "skip_llm": True,
            "persist_to_disk": False,
        })
    assert r.status_code == 200, r.text
    data = r.json()
    assert "job_id" in data and isinstance(data["job_id"], str)


def test_run_endpoint_rejects_when_llm_required_but_no_provider(monkeypatch):
    """If skip_llm=false and NO LLM provider (Gemini OR OpenAI) is configured, return 400."""
    for k in ("OPENAI_API_KEY", "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with TestClient(app) as client:
        r = client.post("/api/run", json={
            "url": "https://example.com/",
            "skip_llm": False,
        })
    assert r.status_code == 400
    assert "LLM provider" in r.json()["detail"]


def test_run_endpoint_rejects_invalid_url():
    """Pydantic validates the URL shape."""
    with TestClient(app) as client:
        r = client.post("/api/run", json={"url": "not-a-url"})
    assert r.status_code == 422  # Pydantic validation


def test_job_status_404_for_unknown_id():
    with TestClient(app) as client:
        r = client.get("/api/jobs/does-not-exist")
    assert r.status_code == 404


def test_job_result_409_when_not_finished(monkeypatch):
    """Result endpoint returns 409 if the job is still running.

    We create a job but never run anything, so it stays in QUEUED.
    """
    with TestClient(app) as client:
        # Manually create a job through the store to skip the runner
        with TestClient(app) as c2:
            # First spin up the app so app.state.store exists
            c2.get("/api/health")
            store = app.state.store
            job_token, owner_principal = issue_job_capability()
            job = store.create(owner_principal=owner_principal)
            r = c2.get(f"/api/jobs/{job.job_id}/result",
                       headers={"X-Job-Token": job_token})
        assert r.status_code == 409
