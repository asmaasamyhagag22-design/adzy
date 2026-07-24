"""P-54: the paid generation routes must be gated behind an API key.

Before this, anyone who could reach `/api/run`, `/api/poster/from-profile`,
`/api/reel/from-profile` or `/api/swot/from-profile` could spend real money — no key, no rate limit.
These tests fail if the gate is removed from any paid router, and confirm the free routes stay open.
"""
from __future__ import annotations

import sys
import types

# Stub playwright BEFORE importing the api app (matches the other api tests).
_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")
class _Stub: ...
for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from tests.test_api_run import _fake_scraper_fn  # noqa: E402

_BODY = {"url": "https://example.com/", "skip_llm": True, "persist_to_disk": False}


def test_paid_route_rejects_missing_key_when_configured(monkeypatch):
    monkeypatch.setenv("BASEERA_API_KEY", "s3cret")
    with TestClient(app) as client:
        r = client.post("/api/run", json=_BODY)     # no X-API-Key header
    assert r.status_code == 401, r.text


def test_paid_route_rejects_wrong_key(monkeypatch):
    monkeypatch.setenv("BASEERA_API_KEY", "s3cret")
    with TestClient(app) as client:
        r = client.post("/api/run", json=_BODY, headers={"X-API-Key": "nope"})
    assert r.status_code == 401, r.text


def test_paid_route_accepts_correct_key(monkeypatch):
    monkeypatch.setenv("BASEERA_API_KEY", "s3cret")
    monkeypatch.setattr("api.jobs.runner.scraper_scrape", _fake_scraper_fn)
    with TestClient(app) as client:
        r = client.post("/api/run", json=_BODY, headers={"X-API-Key": "s3cret"})
    assert r.status_code == 200, r.text
    assert "job_id" in r.json()


def test_all_four_paid_routers_are_gated(monkeypatch):
    """The gate must be on every spend route, not just /api/run."""
    monkeypatch.setenv("BASEERA_API_KEY", "s3cret")
    with TestClient(app) as client:
        # swot + poster with no key -> 401 (reel is a paid router too but we never poke Veo).
        assert client.post("/api/swot/from-profile", json={}).status_code == 401
        assert client.post("/api/poster/from-profile", json={}).status_code == 401


def test_no_key_configured_rejects_remote_caller(monkeypatch):
    """Fail closed: a deploy that forgets to set a key can't be drained by a remote caller.
    The TestClient's peer host is 'testclient' (non-loopback); with pytest's marker removed the
    gate must refuse with 503 rather than spend for the internet."""
    monkeypatch.delenv("BASEERA_API_KEY", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with TestClient(app) as client:
        r = client.post("/api/run", json=_BODY)
    assert r.status_code == 503, r.text
    assert "BASEERA_API_KEY" in r.json()["detail"]


def test_free_routes_stay_open(monkeypatch):
    """health + job status never spend, so they must work with no key."""
    monkeypatch.setenv("BASEERA_API_KEY", "s3cret")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/jobs/does-not-exist").status_code == 404  # reached handler, not gated


# ---- P-56: bound the client-supplied profile ----

def test_oversized_profile_rejected(monkeypatch):
    monkeypatch.setenv("BASEERA_API_KEY", "s3cret")
    huge = {"blob": "a" * (2 * 1024 * 1024 + 64)}   # > 2 MiB serialized
    with TestClient(app) as client:
        r = client.post("/api/swot/from-profile", json={"profile": huge},
                        headers={"X-API-Key": "s3cret"})
    assert r.status_code == 413, r.text


def test_pathologically_deep_profile_rejected(monkeypatch):
    monkeypatch.setenv("BASEERA_API_KEY", "s3cret")
    deep: dict = {}
    node = deep
    for _ in range(40):                              # deeper than the depth cap
        node["n"] = {}
        node = node["n"]
    with TestClient(app) as client:
        r = client.post("/api/poster/from-profile", json={"profile": deep},
                        headers={"X-API-Key": "s3cret"})
    assert r.status_code == 413, r.text
