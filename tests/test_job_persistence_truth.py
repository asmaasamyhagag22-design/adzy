# -*- coding: utf-8 -*-
"""F-11: terminal success requires every required durable commit to succeed.

The tests drive the real background wrappers and inspect the public polling/result/SSE projections.
Only computation and storage failure seams are replaced; no network, renderer, or paid provider is
called.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.brand_store import BrandStore
from api.job_access import user_job_principal
from api.jobs.store import serving_store
from api.jobs.store import STREAM_CLOSE
from api.schemas import JobStatus, Stage, StageEvent, StageStatus


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "f11-hermetic-auth-secret")
    import api.user_auth as user_auth

    user_auth._SECRET_CACHE = None
    yield
    user_auth._SECRET_CACHE = None


@pytest.fixture
def client():
    from api.main import app

    with TestClient(app) as test_client:
        yield test_client


def _identity(client: TestClient, email: str = "f11-owner@test.local") -> tuple[dict, str]:
    signup = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "password123", "full_name": "F11 Owner"},
    )
    assert signup.status_code == 200, signup.text
    headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    user_id = client.get("/api/auth/me", headers=headers).json()["id"]
    return headers, user_id


def _owned_job(client: TestClient, user_id: str, domain_key: str = "x_com"):
    store = client.app.state.store
    job = store.create(owner_principal=user_job_principal(user_id))
    job.domain_key = domain_key
    return store, job


def _stream_events(client: TestClient, job_id: str, headers: dict) -> list[dict]:
    with client.stream("GET", f"/api/jobs/{job_id}/stream", headers=headers) as response:
        assert response.status_code == 200
        raw = b"".join(response.iter_bytes()).decode("utf-8", errors="replace")
    events = []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            events.append(json.loads(line[len("data: "):]))
        except json.JSONDecodeError:
            pass
    return events


# The fault is injected at the PERSISTENCE PROVIDER, not at BrandStore.
#
# DB2G-B moved every brand write behind `api.persistence`, so patching the file store would now miss
# the path under test entirely — and, worse, would pass. Injecting at the provider is also the more
# honest seam: it is backend-agnostic, so these same tests describe the PostgreSQL path the moment
# the switch is thrown, instead of describing one store's failure modes.
def _reading_provider(monkeypatch, *, on_write):
    """Provider whose reads succeed and whose enrichment write fails as `on_write` dictates."""
    from api import persistence

    monkeypatch.setattr(persistence.LegacyPersistence, "visible_brand",
                        lambda self, **_kw: {"domain_key": "x_com"}, raising=True)
    monkeypatch.setattr(persistence.LegacyPersistence, "record_enrichment",
                        lambda self, **_kw: on_write(), raising=True)
    persistence.reset_persistence()


def _write_returns_none():
    return None


def _write_raises():
    raise OSError(r"C:\private\brands\x_com.json could not be replaced")


def test_required_brand_update_returning_none_fails_poll_result_and_sse(
        client, monkeypatch):
    from api import webapp_jobs

    headers, user_id = _identity(client)
    store, job = _owned_job(client, user_id)
    queue = store.subscribe(job.job_id)                 # connected before persistence fails
    _reading_provider(monkeypatch, on_write=_write_returns_none)

    with serving_store(store):
        asyncio.run(webapp_jobs.run_brand_job(
            job_id=job.job_id, domain_key="x_com",
            work=lambda _brand: {"has_strategy": True}, owner_id=user_id, kind="strategy",
        ))

    poll = client.get(f"/api/jobs/{job.job_id}", headers=headers)
    result = client.get(f"/api/jobs/{job.job_id}/result", headers=headers)
    events = _stream_events(client, job.job_id, headers)
    queued = []
    while queue is not None and not queue.empty():
        queued.append(queue.get_nowait())
    if queue is not None:
        store.unsubscribe(job.job_id, queue)

    assert poll.status_code == 200
    assert poll.json()["status"] == "error"
    assert poll.json()["error"] == "Brand result could not be saved."
    assert result.status_code == 500
    assert store.get(job.job_id).status == JobStatus.FAILED
    assert store.get(job.job_id).result is None
    assert any(e.get("stage") == "final" and e.get("status") == "error" for e in events)
    assert not any(e.get("stage") == "final" and e.get("status") == "done" for e in events)
    assert any(isinstance(item, StageEvent) and item.status == StageStatus.ERROR for item in queued)
    assert STREAM_CLOSE in queued


def test_required_brand_update_exception_is_failed_and_public_error_is_safe(client, monkeypatch):
    from api import webapp_jobs

    headers, user_id = _identity(client, "f11-raise@test.local")
    store, job = _owned_job(client, user_id)
    _reading_provider(monkeypatch, on_write=_write_raises)

    with serving_store(store):
        asyncio.run(webapp_jobs.run_brand_job(
            job_id=job.job_id, domain_key="x_com",
            work=lambda _brand: {"has_strategy": True}, owner_id=user_id, kind="strategy",
        ))

    poll = client.get(f"/api/jobs/{job.job_id}", headers=headers)
    result = client.get(f"/api/jobs/{job.job_id}/result", headers=headers)
    assert poll.json()["status"] == "error"
    assert poll.json()["error"] == "Brand result could not be saved."
    assert result.status_code == 500
    assert "C:\\private" not in poll.text + result.text
    assert store.get(job.job_id).result is None


def test_concurrent_brand_delete_prevents_false_success(client):
    from api import webapp_jobs

    headers, user_id = _identity(client, "f11-delete@test.local")
    BrandStore().save({"domain_key": "delete_com", "field": "old"}, owner_id=user_id)
    store, job = _owned_job(client, user_id, "delete_com")

    def compute_then_delete(_brand):
        assert BrandStore().delete("delete_com", for_user=user_id) == "deleted"
        return {"field": "computed"}

    with serving_store(store):
        asyncio.run(webapp_jobs.run_brand_job(
            job_id=job.job_id, domain_key="delete_com",
            work=compute_then_delete, owner_id=user_id,
        ))

    assert client.get(f"/api/jobs/{job.job_id}", headers=headers).json()["status"] == "error"
    assert client.get(f"/api/jobs/{job.job_id}/result", headers=headers).status_code == 500
    assert BrandStore().get("delete_com", for_user=user_id) is None


def test_ownership_mismatch_cannot_be_reported_as_success(client):
    from api import webapp_jobs

    _owner_headers, owner_id = _identity(client, "f11-brand-owner@test.local")
    worker_headers, worker_id = _identity(client, "f11-wrong-worker@test.local")
    BrandStore().save({"domain_key": "owned_com", "field": "old"}, owner_id=owner_id)
    store, job = _owned_job(client, worker_id, "owned_com")

    with serving_store(store):
        asyncio.run(webapp_jobs.run_brand_job(
            job_id=job.job_id, domain_key="owned_com",
            work=lambda _brand: {"field": "stolen"}, owner_id=worker_id,
        ))

    assert client.get(f"/api/jobs/{job.job_id}", headers=worker_headers).json()["status"] == "error"
    assert BrandStore().get("owned_com", for_user=owner_id)["field"] == "old"


def test_successful_brand_persistence_exposes_the_committed_result(client):
    from api import webapp_jobs

    headers, user_id = _identity(client, "f11-success@test.local")
    BrandStore().save({"domain_key": "ok_com", "field": "old"}, owner_id=user_id)
    store, job = _owned_job(client, user_id, "ok_com")

    with serving_store(store):
        asyncio.run(webapp_jobs.run_brand_job(
            job_id=job.job_id, domain_key="ok_com",
            work=lambda _brand: {"field": "durable"}, owner_id=user_id,
        ))

    poll = client.get(f"/api/jobs/{job.job_id}", headers=headers)
    result = client.get(f"/api/jobs/{job.job_id}/result", headers=headers)
    assert poll.json()["status"] == "done"
    assert result.status_code == 200
    assert result.json()["profile"]["field"] == "durable"
    assert BrandStore().get("ok_com", for_user=user_id)["field"] == "durable"


def test_computation_failure_remains_failed_and_has_no_success_result(client):
    from api import webapp_jobs

    headers, user_id = _identity(client, "f11-compute@test.local")
    BrandStore().save({"domain_key": "compute_com"}, owner_id=user_id)
    store, job = _owned_job(client, user_id, "compute_com")

    def fail_compute(_brand):
        raise RuntimeError("private computation detail")

    with serving_store(store):
        asyncio.run(webapp_jobs.run_brand_job(
            job_id=job.job_id, domain_key="compute_com",
            work=fail_compute, owner_id=user_id,
        ))
    assert client.get(f"/api/jobs/{job.job_id}", headers=headers).json()["status"] == "error"
    assert client.get(f"/api/jobs/{job.job_id}/result", headers=headers).status_code == 500
    assert store.get(job.job_id).result is None


def test_scrape_pipeline_success_waits_for_required_brand_persistence(client, monkeypatch):
    from api.routes import scrape

    headers, user_id = _identity(client, "f11-scrape@test.local")
    store, job = _owned_job(client, user_id, "scrape_com")
    store.attach_loop(job.job_id, asyncio.new_event_loop())

    def fake_pipeline(job_id, _url, job_store, **kwargs):
        profile = {"name": {"value": "Synthetic"}, "source_url": "https://scrape.invalid/"}
        job_store.set_result_threadsafe(job_id, profile)
        payload = {"ready_for_strategy": False, "profile_path": None}
        if kwargs.get("defer_final_event"):
            return payload
        job_store.emit_threadsafe(job_id, StageEvent(
            job_id=job_id, stage=Stage.FINAL, status=StageStatus.DONE,
            timestamp=datetime.now(timezone.utc), payload=payload,
        ))
        return None

    from api import persistence

    monkeypatch.setattr(scrape, "run_pipeline_job", fake_pipeline)
    monkeypatch.setattr(scrape, "has_llm", lambda: False)
    # The commit refuses — a race lost, or ownership changed under the preflight. Injected at the
    # provider, so this describes the PostgreSQL commit transaction as well as the file upsert.
    monkeypatch.setattr(persistence.LegacyPersistence, "commit_scrape",
                        lambda self, **_kw: None, raising=True)
    persistence.reset_persistence()

    async def drive():
        from api.jobs.recorder import JobRecorder
        from api.persistence import get_persistence

        store.attach_loop(job.job_id, asyncio.get_running_loop())
        await scrape._run_scrape_job(
            job_id=job.job_id, url="https://scrape.invalid/",
            recorder=JobRecorder(job_id=job.job_id, provider=get_persistence()),
            domain_key="scrape_com",
            max_pages=20, max_depth=3, delay=1.0, owner_id=user_id)

    with serving_store(store):
        asyncio.run(drive())

    poll = client.get(f"/api/jobs/{job.job_id}", headers=headers)
    result = client.get(f"/api/jobs/{job.job_id}/result", headers=headers)
    assert poll.json()["status"] == "error"
    assert poll.json()["error"] == "Brand profile could not be saved."
    assert result.status_code == 500
    assert store.get(job.job_id).result is None
    assert not any(e.stage == Stage.FINAL and e.status == StageStatus.DONE
                   for e in store.get(job.job_id).events)


def test_duplicate_terminal_success_cannot_overwrite_failure(client):
    _headers, user_id = _identity(client, "f11-terminal@test.local")
    store, job = _owned_job(client, user_id)
    failure = StageEvent(
        job_id=job.job_id, stage=Stage.FINAL, status=StageStatus.ERROR,
        timestamp=datetime.now(timezone.utc), error="safe failure",
    )
    success = StageEvent(
        job_id=job.job_id, stage=Stage.FINAL, status=StageStatus.DONE,
        timestamp=datetime.now(timezone.utc), payload={"success": True},
    )
    store.append_event(job.job_id, failure)
    # A delayed worker callback must not re-attach a provisional result after the failure revoked
    # it, and a delayed FINAL/DONE must not flip the terminal state.
    store.set_result(job.job_id, {"stale": "provisional"})
    store.append_event(job.job_id, success)
    assert store.get(job.job_id).status == JobStatus.FAILED
    assert store.get(job.job_id).error == "safe failure"
    assert store.get(job.job_id).result is None
    assert not any(e.status == StageStatus.DONE for e in store.get(job.job_id).events)
