# -*- coding: utf-8 -*-
"""§37 — the wired auth endpoints answer the same way whichever backend is selected.

DB2G routes signup / login / me / job-ownership through the persistence provider. Two claims have
to hold for that to be safe, and neither is self-evident:

  LEGACY IS UNCHANGED  — with no `PERSISTENCE_BACKEND` set, these endpoints must behave exactly as
                         they did before the provider existed. The provider is an indirection, not
                         a behaviour change, and a regression here reaches every logged-in user.

  POSTGRES MATCHES     — with the switch thrown, the same requests must produce the same status
                         codes and the same response SHAPE. A backend that authenticates correctly
                         but returns a differently-keyed user object still breaks the SPA.

The parity is asserted on status codes and on the response CONTRACT (which keys exist, which must
never exist), not on ids: the two stores legitimately mint different id formats — 16-hex vs UUID —
and the token carries whichever the active store issued.
"""
from __future__ import annotations

import os
import subprocess
import sys
import types
import uuid
from pathlib import Path

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

from api.persistence import ENV_BACKEND, POSTGRES, reset_persistence  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

#: Keys the SPA reads off the user object, and keys that must never be serialised to a client.
_REQUIRED_USER_KEYS = {"id", "email"}
_FORBIDDEN_USER_KEYS = {"hashed_password", "password_hash", "password", "hash"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-parity")
    import api.user_auth as ua
    ua._SECRET_CACHE = None
    reset_persistence()
    yield
    reset_persistence()


@pytest.fixture()
def client():
    from api.main import app
    return TestClient(app)


def _signup(client, email, password="password123", full_name="Parity"):
    return client.post("/api/auth/signup",
                       json={"email": email, "password": password, "full_name": full_name})


def _exercise(client) -> dict:
    """Run the whole auth contract once and return a backend-independent description of it."""
    email = f"parity{uuid.uuid4().hex[:8]}@test.local"

    created = _signup(client, email)
    duplicate = _signup(client, email)
    short = _signup(client, f"short{uuid.uuid4().hex[:6]}@test.local", password="abc")
    good = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    bad = client.post("/api/auth/login", json={"email": email, "password": "wrong"})
    missing = client.post("/api/auth/login",
                          json={"email": f"ghost{uuid.uuid4().hex[:6]}@test.local",
                                "password": "password123"})

    token = good.json()["access_token"] if good.status_code == 200 else ""
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    no_token = client.get("/api/auth/me")
    junk = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.token"})

    user = created.json().get("user", {}) if created.status_code == 200 else {}
    return {
        "signup": created.status_code,
        "duplicate": duplicate.status_code,
        "short_password": short.status_code,
        "login": good.status_code,
        "wrong_password": bad.status_code,
        "unknown_email": missing.status_code,
        "me": me.status_code,
        "me_without_token": no_token.status_code,
        "me_with_junk_token": junk.status_code,
        "user_has_required_keys": _REQUIRED_USER_KEYS <= set(user),
        "user_leaks_secrets": bool(_FORBIDDEN_USER_KEYS & set(user)),
        "me_matches_signup": me.status_code == 200 and me.json().get("email") == email,
        "email_echoed_canonical": user.get("email") == email,
    }


#: The contract, stated once. Both backends are compared against THIS, not against each other, so
#: a shared regression cannot make the parity test pass by breaking both sides equally.
_EXPECTED = {
    "signup": 200,
    "duplicate": 409,
    "short_password": 422,
    "login": 200,
    "wrong_password": 401,
    "unknown_email": 401,
    "me": 200,
    "me_without_token": 401,
    "me_with_junk_token": 401,
    "user_has_required_keys": True,
    "user_leaks_secrets": False,
    "me_matches_signup": True,
    "email_echoed_canonical": True,
}


def test_the_legacy_backend_is_unchanged_by_the_provider(client):
    """No switch set: the endpoints answer exactly as they did before DB2G."""
    assert _exercise(client) == _EXPECTED


def test_the_default_route_path_touches_no_database(tmp_path):
    """The provider must not have quietly made auth a database-dependent path.

    A fresh process with a DSN pointing at a closed port: if signup opened a connection, this would
    fail rather than pass, which is the whole claim of the inactive switch at the ROUTE level.

    The child gets `tmp_path` and a unique address. An earlier draft used a fixed directory and a
    fixed email, which meant the second-ever run answered 409 from state the FIRST run had left
    behind — inside the repository. A test that persists across its own runs is not measuring the
    thing it names.
    """
    address = f"noconn{uuid.uuid4().hex[:8]}@test.local"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", str(_ROOT)),
        "BASEERA_WEBAPP_DIR": str(tmp_path / "webapp"),
        "BASEERA_AUTH_SECRET": "test-secret-parity",
        "BASEERA_DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:1/nope",
        # The child has no PYTEST_CURRENT_TEST, so `guard_public_signup` reads it as a real
        # deployment and 403s before the persistence layer is ever reached — which would make this
        # test pass for the wrong reason. Opened deliberately so signup actually executes.
        "BASEERA_ALLOW_PUBLIC_SIGNUP": "1",
    }
    script = (
        "import types, sys;"
        "m = types.ModuleType('playwright'); s = types.ModuleType('playwright.sync_api');"
        "[setattr(s, n, type('S', (), {})) for n in "
        "('Browser','BrowserContext','Page','sync_playwright')];"
        "s.TimeoutError = type('T', (Exception,), {}); s.Error = type('E', (Exception,), {});"
        "sys.modules['playwright'] = m; sys.modules['playwright.sync_api'] = s;"
        "from fastapi.testclient import TestClient;"
        "from api.main import app;"
        "c = TestClient(app);"
        f"r = c.post('/api/auth/signup', json={{'email': '{address}',"
        " 'password': 'password123', 'full_name': 'X'});"
        "print('STATUS=' + str(r.status_code));"
        "from api.persistence import get_persistence;"
        "print('BACKEND=' + get_persistence().name)"
    )
    proc = subprocess.run([sys.executable, "-s", "-c", script],
                          cwd=str(_ROOT), capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "STATUS=200" in proc.stdout, proc.stdout
    assert "BACKEND=legacy" in proc.stdout, proc.stdout


def test_the_token_and_the_job_boundary_resolve_the_same_principal(client):
    """§46.18 — `require_user` and job-ownership checks share one resolver, so they cannot diverge.

    Before DB2G both happened to call UserStore directly. Now both go through the provider; this
    pins that they still agree, which is the property that makes a mid-cutover token unambiguous.
    """
    from api.job_access import user_for_token as job_side
    from api.routes.auth import user_for_token as route_side

    email = f"same{uuid.uuid4().hex[:8]}@test.local"
    token = _signup(client, email).json()["access_token"]

    assert route_side is job_side, "the two boundaries no longer share one resolver"
    resolved = route_side(token)
    assert resolved and resolved["email"] == email
    assert route_side("not.a.token") is None


# =====================================================================
# the same contract, against a real PostgreSQL
# =====================================================================

postgres = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the postgres parity proof")


@pytest.fixture()
def pg_client(monkeypatch):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, f"could not provision the database:\n{proc.stderr[-2000:]}"

    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    from db.session import dispose
    dispose()
    reset_persistence()
    from api.main import app
    yield TestClient(app)
    dispose()


@postgres
def test_the_postgres_backend_answers_the_same_contract(pg_client):
    assert _exercise(pg_client) == _EXPECTED


@postgres
def test_the_postgres_backend_serves_auth_without_writing_to_the_legacy_store(
        pg_client, monkeypatch):
    """§5 at the ROUTE level: a real HTTP signup must not shadow-write the JSON file."""
    import api.user_auth as ua

    calls: list[str] = []
    for method in ("create", "_save", "authenticate"):
        if hasattr(ua.UserStore, method):
            monkeypatch.setattr(
                ua.UserStore, method,
                (lambda n: lambda *a, **k: calls.append(n))(f"UserStore.{method}"),
                raising=False)

    email = f"routeonly{uuid.uuid4().hex[:8]}@test.local"
    assert _signup(pg_client, email).status_code == 200
    assert pg_client.post("/api/auth/login",
                          json={"email": email, "password": "password123"}).status_code == 200
    assert calls == [], f"the postgres route path also used the legacy store: {calls}"


@postgres
def test_a_postgres_error_does_not_leak_internals_to_the_client(pg_client, monkeypatch):
    """§36 — a persistence failure becomes a plain 5xx, never SQL text or a constraint name."""
    import api.persistence as module

    def _explode(*_a, **_k):
        raise RuntimeError(
            'duplicate key value violates unique constraint "uq_users_email" '
            'DETAIL: Key (email)=(x@y.z) already exists.')

    monkeypatch.setattr(module.PostgresPersistence, "authenticate", _explode, raising=True)

    # A client that RETURNS the 500 instead of re-raising it — the client's-eye view is the thing
    # under test here, not the server-side traceback.
    from api.main import app
    seen_by_client = TestClient(app, raise_server_exceptions=False)
    response = seen_by_client.post("/api/auth/login",
                                   json={"email": "a@test.local", "password": "password123"})
    assert response.status_code >= 500
    body = response.text.lower()
    for leak in ("unique constraint", "uq_users_email", "traceback", "psycopg", "sqlalchemy"):
        assert leak not in body, f"the response leaked {leak!r}"
