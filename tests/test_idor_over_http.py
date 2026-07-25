# -*- coding: utf-8 -*-
"""The IDOR, reproduced as it was measured — over HTTP, with two real accounts.

The store-level rules live in tests/test_brands_are_owned.py. This file locks the thing that was
actually observed on the running server (localhost:8000, 2026-07-25): account B, which had scraped
nothing, called the real endpoints and received account A's catalog, A's full `_profile`, and a
`200 {"ok": true}` from DELETE. Any future endpoint that resolves a brand without the caller's
identity re-opens exactly this, so the assertions are on the HTTP responses, not on the store.

Also locked: the status code must not become an EXISTENCE ORACLE. "Not yours" and "never existed"
have to be indistinguishable, or the API answers a question the caller was refused."""
from __future__ import annotations

import sys
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
from fastapi.testclient import TestClient  # noqa: E402

from api.brand_store import BrandStore  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-abc")
    import api.user_auth as ua
    ua._SECRET_CACHE = None
    yield


@pytest.fixture
def client():
    from api.main import app
    # The context manager runs the lifespan, which creates `app.state.store`. Without it the
    # job-starting routes raise AttributeError instead of the 404 under test — a red that would
    # have looked like a scoping leak and was neither.
    with TestClient(app) as c:
        yield c


def _account(client, email):
    r = client.post("/api/auth/signup",
                    json={"email": email, "password": "password123", "full_name": email})
    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return {"Authorization": f"Bearer {token}"}, me["id"]


@pytest.fixture
def two_accounts(client):
    a_headers, a_id = _account(client, "owner-a@test.local")
    b_headers, _ = _account(client, "stranger-b@test.local")
    BrandStore().save({"domain_key": "a_brand_com", "organization_name": "A Brand",
                       "_profile": {"trade_secret": "A's evidenced profile"}}, owner_id=a_id)
    return a_headers, b_headers


def test_a_stranger_sees_an_empty_catalog(client, two_accounts):
    _, b = two_accounts
    assert client.get("/api/brands", headers=b).json()["brands"] == []


def test_the_owner_still_sees_their_own(client, two_accounts):
    a, _ = two_accounts
    keys = [x["domain_key"] for x in client.get("/api/brands", headers=a).json()["brands"]]
    assert keys == ["a_brand_com"]


def test_a_stranger_cannot_read_the_full_profile(client, two_accounts):
    _, b = two_accounts
    r = client.get("/api/brands/a_brand_com", headers=b)
    assert r.status_code == 404
    assert "trade_secret" not in r.text


def test_a_stranger_cannot_destroy_it(client, two_accounts):
    a, b = two_accounts
    client.delete("/api/brands/a_brand_com", headers=b)
    assert client.get("/api/brands/a_brand_com", headers=a).status_code == 200, \
        "a stranger deleted the owner's brand"


def test_delete_is_not_an_existence_oracle(client, two_accounts):
    """Someone else's brand and a brand that never existed must answer identically."""
    _, b = two_accounts
    theirs = client.delete("/api/brands/a_brand_com", headers=b)
    ghost = client.delete("/api/brands/never_existed_com", headers=b)
    assert theirs.status_code == ghost.status_code == 200
    assert theirs.json() == ghost.json(), (
        f"the response distinguishes another account's brand from an absent one: "
        f"{theirs.json()} vs {ghost.json()}")


def test_get_is_not_an_existence_oracle(client, two_accounts):
    _, b = two_accounts
    theirs = client.get("/api/brands/a_brand_com", headers=b)
    ghost = client.get("/api/brands/never_existed_com", headers=b)
    assert theirs.status_code == ghost.status_code == 404
    assert theirs.json() == ghost.json()


def test_a_stranger_cannot_start_paid_work_on_someone_elses_brand(client, two_accounts):
    """Every enrichment route resolves the brand; each one must refuse, or spend runs on A's data."""
    _, b = two_accounts
    for path, body in (
        ("/api/swot/generate", {"domain_key": "a_brand_com"}),
        ("/api/strategy/generate", {"domain_key": "a_brand_com"}),
        ("/api/marketing-plan/generate", {"domain_key": "a_brand_com"}),
        ("/api/generate", {"domain_key": "a_brand_com"}),
        ("/api/influencer/create", {"domain_key": "a_brand_com"}),
        ("/api/calendar/generate-image", {"domain_key": "a_brand_com", "date": "2026-01-01"}),
    ):
        r = client.post(path, json=body, headers=b)
        assert r.status_code == 404, f"{path} accepted another account's brand ({r.status_code})"


def test_the_image_gallery_is_scoped_too(client, two_accounts):
    from api.brand_store import record_history
    a, b = two_accounts
    a_id = client.get("/api/auth/me", headers=a).json()["id"]
    record_history({"image_b64": "AA==", "filename": "a.png", "domain_key": "a_brand_com"},
                   owner_id=a_id)
    assert client.get("/api/history", headers=b).json()["history"] == []
    assert len(client.get("/api/history", headers=a).json()["history"]) == 1
