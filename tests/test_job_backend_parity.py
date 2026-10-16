# -*- coding: utf-8 -*-
"""The SAME job workflow on BOTH backends, compared field by field (§7).

The public contract of a job is four things — `status`, `progress`, `step`, `error` — plus the
result and the event stream. If the two backends derive those differently, nothing fails: the SPA
just finishes its progress bar at the wrong moment, on one backend only, with neither store
disagreeing with itself. That is a defect no single-backend test can see, so it is asserted by
running one scripted workflow twice and diffing the answers.

WHAT IS COMPARED, AND WHAT DELIBERATELY IS NOT. `manifest_path` / `profile_path` differ by design:
the file store answers with disk locations because that is where its result lives, and the durable
store answers None because under PostgreSQL the file on disk is not the business authority. Every
other public field must match exactly.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import types
import uuid
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
from fastapi.testclient import TestClient  # noqa: E402

from api.persistence import ENV_BACKEND, LEGACY, POSTGRES, reset_persistence  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required to compare the two backends")

_PROFILE = {"name": {"value": "Parity"}, "primary_language": "en"}


def _migrate() -> None:
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, f"could not provision the database:\n{proc.stderr[-2000:]}"


def _pipeline(*, fail: bool = False, reached=None, release=None):
    def pipeline(job_id, _url, recorder, **kwargs):
        from datetime import datetime, timezone

        from api.schemas import Stage, StageEvent, StageStatus

        def emit(stage, status, **kw):
            recorder.emit_threadsafe(job_id, StageEvent(
                job_id=job_id, stage=stage, status=status,
                timestamp=datetime.now(timezone.utc), **kw))

        emit(Stage.SCRAPE, StageStatus.STARTED)
        emit(Stage.SCRAPE, StageStatus.DONE, payload={"pages_succeeded": 2})
        emit(Stage.RULES, StageStatus.STARTED)
        if reached is not None:
            # Hold the job at a KNOWN stage so both backends are polled at the same point. Polling
            # a running pipeline and comparing whatever each sample happened to catch compares the
            # scheduler, not the projection.
            reached.set()
            assert release.wait(30), "the parity gate was never released"
        if fail:
            emit(Stage.RULES, StageStatus.ERROR, error="rules stage failed (RuntimeError)",
                 payload={"error_type": "RuntimeError"})
            return None
        emit(Stage.RULES, StageStatus.DONE, payload={"fields_extracted": 2})
        recorder.set_result_threadsafe(job_id, dict(_PROFILE))
        return {"ready_for_strategy": False, "profile_path": None}

    return pipeline


def _run_workflow(backend: str, tmp_path: Path, monkeypatch, *, fail: bool, url: str) -> dict:
    """Drive one full scrape over real HTTP on `backend` and return every public answer."""
    import time

    import api.user_auth as ua
    from api.routes import scrape as scrape_route
    from db.session import dispose

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / backend))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-parity")
    monkeypatch.setenv(ENV_BACKEND, backend)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    ua._SECRET_CACHE = None
    dispose()
    reset_persistence()

    reached, release = threading.Event(), threading.Event()
    monkeypatch.setattr(scrape_route, "run_pipeline_job",
                        _pipeline(fail=fail, reached=reached, release=release))
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)

    from api.main import app
    observed: list[dict] = []
    with TestClient(app) as client:
        signup = client.post("/api/auth/signup", json={
            "email": f"pa{uuid.uuid4().hex[:8]}@test.local",
            "password": "password123", "full_name": "PA"})
        assert signup.status_code == 200, signup.text
        headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

        started = client.post("/api/scrape", json={"url": url}, headers=headers)
        assert started.status_code == 200, started.text
        job_id = started.json()["job_id"]

        # The job is now parked at rules/started on BOTH backends, so this sample is comparable.
        assert reached.wait(30), f"[{backend}] the pipeline never reached the gate"
        mid = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        mid_result = client.get(f"/api/jobs/{job_id}/result", headers=headers)
        release.set()

        terminal = None
        for _ in range(400):
            body = client.get(f"/api/jobs/{job_id}", headers=headers).json()
            observed.append(body)
            if body.get("status") in ("done", "error"):
                terminal = body
                break
            time.sleep(0.03)
        assert terminal is not None, f"[{backend}] the job never finished"

        result = client.get(f"/api/jobs/{job_id}/result", headers=headers)
        with client.stream("GET", f"/api/jobs/{job_id}/stream", headers=headers) as response:
            raw = b"".join(response.iter_bytes()).decode("utf-8", errors="replace")
        events = [json.loads(line[len("data: "):]) for line in raw.splitlines()
                  if line.startswith("data: ")]

        missing = client.get(f"/api/jobs/{uuid.uuid4()}", headers=headers)
        unowned = client.get(f"/api/jobs/{job_id}")

    dispose()
    reset_persistence()
    return {
        "terminal": {k: terminal.get(k) for k in ("status", "progress", "step", "error")},
        "mid": {k: mid.get(k) for k in ("status", "progress", "step", "error")},
        "mid_result_status": mid_result.status_code,
        "progress_seen": sorted({b.get("progress") for b in observed}),
        "steps_seen": sorted({b.get("step") for b in observed}),
        "domain_key": terminal.get("domain_key"),
        "result_status": result.status_code,
        "result_profile": (result.json().get("profile") if result.status_code == 200 else None),
        "events": [(e["stage"], e["status"]) for e in events],
        "missing_status": missing.status_code,
        "unowned_status": unowned.status_code,
    }


@pytest.fixture()
def both(tmp_path, monkeypatch):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    _migrate()

    def run(*, fail: bool) -> tuple[dict, dict, str]:
        # One fresh domain per PAIR: both backends see the same input, and no pair inherits the
        # brand a previous pair claimed in the shared database.
        url = f"https://parity{uuid.uuid4().hex[:8]}.test/"
        from api.brand_translate import domain_key_from_url
        return (_run_workflow(LEGACY, tmp_path, monkeypatch, fail=fail, url=url),
                _run_workflow(POSTGRES, tmp_path, monkeypatch, fail=fail, url=url),
                domain_key_from_url(url))

    return run


def test_a_successful_job_projects_identically_on_both_backends(both):
    legacy, postgres, domain_key = both(fail=False)

    assert legacy["terminal"] == postgres["terminal"] == {
        "status": "done", "progress": 100, "step": "Done", "error": None}
    assert legacy["domain_key"] == postgres["domain_key"] == domain_key
    assert legacy["result_status"] == postgres["result_status"] == 200
    assert legacy["result_profile"] == postgres["result_profile"] == _PROFILE
    assert legacy["events"] == postgres["events"], (
        "the two backends published different event streams for the same pipeline")
    assert legacy["missing_status"] == postgres["missing_status"] == 404
    assert legacy["unowned_status"] == postgres["unowned_status"] == 404


def test_a_failed_job_projects_identically_on_both_backends(both):
    legacy, postgres, _domain = both(fail=True)

    assert legacy["terminal"]["status"] == postgres["terminal"]["status"] == "error"
    assert legacy["terminal"]["step"] == postgres["terminal"]["step"] == "Failed"
    assert legacy["terminal"]["error"] == postgres["terminal"]["error"], (
        "the same failure produced two different client-facing messages")
    assert legacy["result_status"] == postgres["result_status"] == 500
    assert legacy["events"] == postgres["events"]


def test_a_job_parked_at_one_stage_reads_the_same_on_both_backends(both):
    """The drift this file exists for: a backend that reached `done` a stage early would fail no
    assertion anywhere else — it would just finish the bar at the wrong moment.

    Both jobs are held at `rules/started` while the sample is taken, so this compares the
    PROJECTION rather than which stage each scheduler happened to be in when it was polled.
    """
    legacy, postgres, _domain = both(fail=False)

    assert legacy["mid"] == postgres["mid"] == {
        "status": "running", "progress": 35, "step": "Extracting brand facts", "error": None}
    # And an unfinished job is refused identically — 409, not a partial answer.
    assert legacy["mid_result_status"] == postgres["mid_result_status"] == 409
    assert 100 in legacy["progress_seen"] and 100 in postgres["progress_seen"]
