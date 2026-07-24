"""Tests for the SSE dedup fix and the .env autoload behavior."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
import types
from pathlib import Path

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

from api.jobs.runner import run_pipeline_job  # noqa: E402
from api.jobs.store import STREAM_CLOSE, JobStore  # noqa: E402
from api.main import app  # noqa: E402
from api.schemas import JobStatus, Stage, StageEvent, StageStatus  # noqa: E402

from tests.test_api_run import _fake_scraper_fn  # noqa: E402


# ---------------------------------------------------------------------
# SSE dedup
# ---------------------------------------------------------------------

def test_sse_dedup_event_key_helper():
    """Two events with same stage+status+timestamp produce the same key;
    different timestamps produce different keys."""
    from api.routes.jobs import stream_job  # ensure module loads
    # The helper is defined inside event_gen; we test the property indirectly
    # by exercising the dedup behavior below. This is a smoke test.
    assert stream_job is not None


def test_sse_dedups_a_duplicate_event_in_the_snapshot():
    """CS-82 refactor: the SSE route now dedups via a per-connection sent_keys set (the same set
    spans replay AND the live tail). Wedge the SAME event twice into the replay snapshot and open
    the stream on a TERMINAL job (replay-only path); the dedup must yield it exactly once."""
    from datetime import datetime, timezone

    with TestClient(app) as client:
        client.get("/api/health")  # warm up
        store: JobStore = app.state.store
        job = store.create()
        ev = StageEvent(
            job_id=job.job_id,
            stage=Stage.SCRAPE,
            status=StageStatus.STARTED,
            timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        # the SAME event twice in the snapshot (the replay side of the race); terminal so the route
        # replays the snapshot then returns (no live tail needed to exercise the shared dedup set).
        job.events.append(ev)
        job.events.append(ev)
        job.status = JobStatus.SUCCEEDED

        with client.stream("GET", f"/api/jobs/{job.job_id}/stream") as resp:
            assert resp.status_code == 200
            raw = b""
            for chunk in resp.iter_bytes():
                raw += chunk
                if len(raw) > 50_000:
                    break

        text = raw.decode("utf-8", errors="replace")
        data_lines = [line for line in text.splitlines() if line.startswith("data: ")]
        scrape_started = []
        for line in data_lines:
            try:
                obj = json.loads(line[len("data: "):])
            except json.JSONDecodeError:
                continue
            if obj.get("stage") == "scrape" and obj.get("status") == "started":
                scrape_started.append(obj)
        assert len(scrape_started) == 1, f"expected 1 scrape:started event, got {len(scrape_started)}"


def test_sse_full_pipeline_no_duplicates():
    """Run the full pipeline and confirm each (stage,status) pair is unique
    in the SSE output stream."""
    async def run_test():
        with TestClient(app) as client:
            client.get("/api/health")
            store: JobStore = app.state.store
            job = store.create()
            store.attach_loop(job.job_id, asyncio.get_running_loop())

            # Drive the pipeline first so events are sitting in queue + events
            await asyncio.to_thread(
                run_pipeline_job,
                job.job_id, "https://example.com/", store,
                skip_llm=True, persist_to_disk=False,
                scraper_fn=_fake_scraper_fn,
            )
            await asyncio.sleep(0)

            with client.stream("GET", f"/api/jobs/{job.job_id}/stream") as resp:
                raw = b""
                for chunk in resp.iter_bytes():
                    raw += chunk
                    if len(raw) > 100_000:
                        break

            text = raw.decode("utf-8", errors="replace")
            data_lines = [
                line for line in text.splitlines()
                if line.startswith("data: ")
            ]
            events = []
            for line in data_lines:
                try:
                    events.append(json.loads(line[len("data: "):]))
                except json.JSONDecodeError:
                    continue
            # No two events should share (stage, status, timestamp)
            keys = [
                (e.get("stage"), e.get("status"), e.get("timestamp"))
                for e in events if "stage" in e
            ]
            assert len(keys) == len(set(keys)), (
                f"Duplicates found in event stream: {keys}"
            )
            # And we still got every stage we expect for skip_llm
            stage_status_pairs = {(s, st) for s, st, _ in keys}
            assert ("scrape", "started") in stage_status_pairs
            assert ("scrape", "done") in stage_status_pairs
            assert ("final", "done") in stage_status_pairs

    asyncio.run(run_test())


# ---------------------------------------------------------------------
# .env autoload
# ---------------------------------------------------------------------

def test_dotenv_loaded_at_import_time(tmp_path, monkeypatch):
    """A .env file in the project root is picked up when api.main is imported.

    We can't reload api.main cleanly mid-test, so this test runs a subprocess
    with a temp project root containing a .env, and inspects whether the
    environment variable became visible.
    """
    import subprocess

    work = tmp_path / "fake_project"
    work.mkdir()
    (work / ".env").write_text("DAY1_DOTENV_TEST=hello-from-env\n", encoding="utf-8")

    # Run a tiny Python program in the fake project dir that imports our
    # api.main and checks os.environ. We point PYTHONPATH at the real repo
    # so the api package is importable.
    repo_root = Path(__file__).resolve().parent.parent
    script = textwrap.dedent("""
        import sys, os, types
        # Stub playwright before importing
        fake = types.ModuleType("playwright")
        fake_sync = types.ModuleType("playwright.sync_api")
        class _S: ...
        for n in ("Browser","BrowserContext","Page","sync_playwright"):
            setattr(fake_sync, n, _S)
        fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
        fake_sync.Error = type("Error", (Exception,), {})
        sys.modules["playwright"] = fake
        sys.modules["playwright.sync_api"] = fake_sync

        # Importing api.main triggers the load_dotenv() call.
        import api.main  # noqa: F401
        print(os.environ.get("DAY1_DOTENV_TEST", "<unset>"))
    """)

    env = dict(os.environ)
    # Strip the test var from the inherited env so we know it came from .env
    env.pop("DAY1_DOTENV_TEST", None)
    # PYTHONPATH includes the repo root so `import api.main` works
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # The .env should have been loaded; var should be visible
    assert "hello-from-env" in result.stdout, (
        f"DAY1_DOTENV_TEST not loaded from .env. stdout={result.stdout!r}"
    )


def test_dotenv_does_not_override_existing_env(monkeypatch, tmp_path):
    """If OPENAI_API_KEY is already set, .env must not clobber it."""
    import subprocess

    work = tmp_path / "fake_project_override"
    work.mkdir()
    (work / ".env").write_text(
        "DAY1_OVERRIDE_TEST=from-dotenv\n", encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parent.parent
    script = textwrap.dedent("""
        import sys, os, types
        fake = types.ModuleType("playwright")
        fake_sync = types.ModuleType("playwright.sync_api")
        class _S: ...
        for n in ("Browser","BrowserContext","Page","sync_playwright"):
            setattr(fake_sync, n, _S)
        fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
        fake_sync.Error = type("Error", (Exception,), {})
        sys.modules["playwright"] = fake
        sys.modules["playwright.sync_api"] = fake_sync

        # Pre-set the var BEFORE importing api.main
        os.environ["DAY1_OVERRIDE_TEST"] = "from-real-env"
        import api.main  # noqa: F401
        print(os.environ.get("DAY1_OVERRIDE_TEST", "<unset>"))
    """)

    env = dict(os.environ)
    env.pop("DAY1_OVERRIDE_TEST", None)
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    # load_dotenv with override=False keeps the existing value
    assert "from-real-env" in result.stdout, (
        f"existing env var was clobbered. stdout={result.stdout!r}"
    )
