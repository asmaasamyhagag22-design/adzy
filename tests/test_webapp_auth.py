"""م1 — web-app foundation: static serving + user auth (signup/login/me) + showcase.

The AdArchitect SPA gates everything behind Bearer-JWT auth and treats 401 as "log out". These tests
lock that contract: signup/login issue a token, /auth/me needs a valid token and 401s otherwise,
passwords are never stored in the clear, and the app HTML + assets are served same-origin.
"""
from __future__ import annotations

import sys
import types

# Stub playwright before importing the app (matches the other api tests).
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


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Each test gets a fresh user store + signing secret (no cross-test leakage)."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-abc")
    # reset the cached secret so the env value is picked up
    import api.user_auth as ua
    ua._SECRET_CACHE = None
    yield


@pytest.fixture
def client():
    from api.main import app
    with TestClient(app) as c:
        yield c


_SIGNUP = {"email": "Owner@Brand.EG", "password": "supersecret", "full_name": "Asmaa"}


# ---------- auth ----------

def test_signup_issues_token_and_public_user(client):
    r = client.post("/api/auth/signup", json=_SIGNUP)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["token_type"] == "bearer"
    assert body["user"]["email"] == "owner@brand.eg"          # normalized lowercase
    assert body["user"]["full_name"] == "Asmaa"
    assert "password" not in body["user"] and "password_hash" not in body["user"]


def test_duplicate_signup_is_409(client):
    client.post("/api/auth/signup", json=_SIGNUP)
    r = client.post("/api/auth/signup", json=_SIGNUP)
    assert r.status_code == 409


def test_short_password_rejected(client):
    r = client.post("/api/auth/signup", json={**_SIGNUP, "password": "short"})
    assert r.status_code == 422


def test_login_then_me_roundtrip(client):
    client.post("/api/auth/signup", json=_SIGNUP)
    r = client.post("/api/auth/login", json={"email": "owner@brand.eg", "password": "supersecret"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "owner@brand.eg"


def test_wrong_password_is_401(client):
    client.post("/api/auth/signup", json=_SIGNUP)
    r = client.post("/api/auth/login", json={"email": "owner@brand.eg", "password": "WRONG"})
    assert r.status_code == 401


def test_me_without_token_is_401_not_403(client):
    # 403 would trap the SPA; the frontend logs out only on 401.
    assert client.get("/api/auth/me").status_code == 401


def test_me_with_garbage_token_is_401(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


def test_tampered_token_is_rejected(client):
    tok = client.post("/api/auth/signup", json=_SIGNUP).json()["access_token"]
    h, p, s = tok.split(".")
    forged = f"{h}.{p}.{'A' * len(s)}"
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_password_never_stored_in_clear(client, tmp_path):
    client.post("/api/auth/signup", json=_SIGNUP)
    users_file = tmp_path / "webapp" / "users.json"
    raw = users_file.read_text(encoding="utf-8")
    assert "supersecret" not in raw
    assert "pbkdf2_sha256$" in raw          # hashed, not plaintext


# ---------- serving + showcase ----------

def test_landing_and_app_are_served(client):
    root = client.get("/")
    assert root.status_code == 200 and "text/html" in root.headers["content-type"]
    app_page = client.get("/app")
    assert app_page.status_code == 200 and "text/html" in app_page.headers["content-type"]


def test_showcase_is_public_and_lists_assets(client):
    r = client.get("/api/showcase")                    # no auth header
    assert r.status_code == 200
    images = r.json()["images"]
    assert isinstance(images, list)
    assert all(u.startswith("/assets/showcase/") for u in images)
