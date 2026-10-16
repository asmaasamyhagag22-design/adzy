# -*- coding: utf-8 -*-
"""The gallery served end to end with its BYTES in a remote object store.

WHY THIS EXISTS SEPARATELY FROM `test_gallery_remote_blob_store.py`. That file proves the object
store CONTRACT. This one proves the PRODUCT on top of it: a real `/api/generate` through FastAPI's
own routing, a real `PERSISTENCE_BACKEND=postgres` provider against a real PostgreSQL, and the
managed bytes living in a remote bucket instead of on the host. "The adapter works" and "the gallery
works on the adapter" are different claims, and only the second one is the cutover's question.

WHAT IS REAL HERE: routes, provider, database, gallery domain logic, key derivation, checksum,
read-back, and `/api/history` reconstituting `image_b64`. WHAT IS REPLACED: the image RENDER (it
costs money and reaches the network) and the HTTP TRANSPORT under the Supabase store — the request
construction and status handling are still the shipping code.

WHAT THIS DOES NOT PROVE: the live Supabase service. Latency, SSL, bucket policy, RLS and the real
API's exact responses need a configured project, and the runbook lists them as pre-cutover work.
"""
from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
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

from api.persistence import ENV_BACKEND, POSTGRES, reset_persistence  # noqa: E402

httpx = pytest.importorskip("httpx")

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the remote gallery route proof")

URL = "https://project-ref.supabase.co"
KEY = "service-role-key-value-that-must-never-be-logged"
BUCKET = "baseera-gallery"
_PREFIX = f"/storage/v1/object/{BUCKET}/"


def _png(seed: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + seed.encode("utf-8").ljust(32, b"\x00")


def _migrate() -> None:
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, f"could not provision the database:\n{proc.stderr[-2000:]}"


@pytest.fixture()
def bucket():
    """The remote bucket, as a dict reached over HTTP. Shared across every store instance the
    process builds, which is what makes it a REMOTE store rather than a per-object one."""
    return {}


@pytest.fixture()
def webapp_root(tmp_path):
    return tmp_path / "w"


@pytest.fixture()
def client(webapp_root, bucket, monkeypatch):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    _migrate()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert path.startswith(_PREFIX), f"unexpected storage path {path!r}"
        key = path[len(_PREFIX):]
        if request.method == "POST":
            bucket[key] = bytes(request.content)
            return httpx.Response(200, json={"Key": key})
        if request.method in ("GET", "HEAD"):
            if key not in bucket:
                return httpx.Response(404)
            body = b"" if request.method == "HEAD" else bucket[key]
            return httpx.Response(200, content=body)
        return httpx.Response(405)

    from api import blob_store
    transport = httpx.MockTransport(handler)
    real = blob_store.SupabaseObjectStore
    monkeypatch.setattr(blob_store, "SupabaseObjectStore",
                        lambda *a, **k: real(*a, **{**k, "transport": transport}))

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(webapp_root))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-remote-gallery")
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    # THE POINT: authority is stated in words for BOTH stores.
    monkeypatch.setenv(blob_store.ENV_BACKEND, blob_store.BACKEND_SUPABASE)
    monkeypatch.setenv(blob_store.ENV_SUPABASE_URL, URL)
    monkeypatch.setenv(blob_store.ENV_SUPABASE_KEY, KEY)
    monkeypatch.setenv(blob_store.ENV_SUPABASE_BUCKET, BUCKET)

    import api.user_auth as ua
    ua._SECRET_CACHE = None
    from db.session import dispose
    dispose()
    reset_persistence()
    from api.main import app
    with TestClient(app) as test_client:
        yield test_client
    dispose()
    reset_persistence()


@pytest.fixture()
def no_legacy_gallery(monkeypatch):
    """Under postgres the legacy gallery index must be reached ZERO times, read or write."""
    import api.brand_store as bs

    calls: list[str] = []
    monkeypatch.setattr(bs, "record_history",
                        lambda *a, **k: calls.append("record_history"), raising=False)
    monkeypatch.setattr(bs, "read_history",
                        lambda *a, **k: calls.append("read_history") or [], raising=False)
    yield calls
    assert calls == [], f"postgres mode reached the legacy gallery index: {calls}"


@pytest.fixture()
def renders(monkeypatch):
    from api.routes import generate as generate_route

    calls: list[dict] = []

    def fake_generate(brand, **kwargs):
        calls.append(dict(kwargs))
        seed = kwargs.get("headline") or f"render-{len(calls)}"
        return {"image_b64": base64.b64encode(_png(seed)).decode("ascii"),
                "filename": f"{seed}.png", "headline": seed, "subtext": "", "cta": "",
                "is_preview": True, "trust_violation": False, "qa_status": "pass"}

    monkeypatch.setattr(generate_route.images, "generate_image", fake_generate)
    fake_generate.calls = calls          # type: ignore[attr-defined]
    return fake_generate


def _account(client) -> tuple[dict, str]:
    address = f"rg{uuid.uuid4().hex[:8]}@test.local"
    response = client.post("/api/auth/signup",
                           json={"email": address, "password": "password123", "full_name": "RG"})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


def _brand(client, monkeypatch, headers) -> str:
    import time

    from api.brand_translate import domain_key_from_url
    from api.routes import scrape as scrape_route

    url = f"https://rem{uuid.uuid4().hex[:8]}.test/"

    def pipeline(job_id, _url, recorder, **kwargs):
        recorder.set_result_threadsafe(job_id, {"name": {"value": "Remote Brand"},
                                                "primary_language": "en"})
        return {"ready_for_strategy": False, "profile_path": None}

    monkeypatch.setattr(scrape_route, "run_pipeline_job", pipeline)
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)
    started = client.post("/api/scrape", json={"url": url}, headers=headers)
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    for _ in range(300):
        body = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        if body.get("status") in ("done", "error"):
            assert body["status"] == "done", body
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the brand scrape never finished")
    return domain_key_from_url(url)


def _generate(client, headers, domain_key: str, *, headline: str) -> dict:
    response = client.post("/api/generate", headers=headers, json={
        "domain_key": domain_key, "content_type": "post", "style": "bold", "headline": headline})
    assert response.status_code == 200, response.text
    return response.json()


def _history(client, headers, domain_key: str = "") -> list[dict]:
    """This test's OWN history rows.

    `/api/history` deliberately returns the caller's entries PLUS the unattributed ones, and the
    PostgreSQL test database is shared by every file in the suite — so an absolute count here
    measures whatever else has run today, not this test. Scoping by the brand this test created is
    what makes the assertion about this test.
    """
    response = client.get("/api/history?limit=200", headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()["history"]
    return [r for r in rows if r.get("domain_key") == domain_key] if domain_key else rows


# =====================================================================

def test_a_generation_puts_its_bytes_in_the_bucket_and_history_serves_them_back(
        client, monkeypatch, renders, no_legacy_gallery, bucket, webapp_root):
    """The whole path: render → durable remote bytes → relational entry → history with real bytes."""
    from sqlalchemy import select

    from db.models import GalleryBlob, GalleryEntry
    from db.session import get_session_factory

    headers, _user_id = _account(client)
    domain_key = _brand(client, monkeypatch, headers)

    out = _generate(client, headers, domain_key, headline="remote-one")
    assert out["image_b64"]

    expected = _png("remote-one")
    checksum = hashlib.sha256(expected).hexdigest()

    # The BYTES are in the bucket, at the content-addressed key, and hash to what the row claims.
    key = f"gallery/{checksum[:2]}/{checksum}.png"
    assert key in bucket, sorted(bucket)
    assert hashlib.sha256(bucket[key]).hexdigest() == checksum

    with get_session_factory()() as session:
        # Scoped to THIS brand: the database is shared by the whole suite.
        entries = list(session.scalars(
            select(GalleryEntry).where(GalleryEntry.domain_key == domain_key)).all())
        blobs = list(session.scalars(
            select(GalleryBlob).where(GalleryBlob.checksum == checksum)).all())
    assert len(entries) == 1 and len(blobs) == 1
    assert blobs[0].storage_key == key
    assert blobs[0].checksum == checksum

    # And the product serves those bytes back through the real route.
    history = _history(client, headers, domain_key)
    assert len(history) == 1
    assert base64.b64decode(history[0]["image_b64"]) == expected


def test_no_gallery_byte_ever_lands_on_the_host_filesystem(
        client, monkeypatch, renders, no_legacy_gallery, bucket, webapp_root):
    """The requirement that made this change necessary. A gallery byte written to the host is a
    machine-local authority, and the next machine renders a broken image."""
    headers, _ = _account(client)
    domain_key = _brand(client, monkeypatch, headers)

    _generate(client, headers, domain_key, headline="remote-a")
    _generate(client, headers, domain_key, headline="remote-b")

    managed = webapp_root / "gallery"
    stray = [p for p in managed.rglob("*") if p.is_file()] if managed.exists() else []
    assert stray == [], f"gallery bytes landed on the host: {stray}"
    assert len(bucket) == 2


def test_two_publications_of_identical_bytes_keep_two_histories_and_one_object(
        client, monkeypatch, renders, no_legacy_gallery, bucket):
    """28 logical / 25 physical, at the product boundary: dedup must not eat a publication."""
    headers, _ = _account(client)
    domain_key = _brand(client, monkeypatch, headers)

    _generate(client, headers, domain_key, headline="same")
    _generate(client, headers, domain_key, headline="same")

    history = _history(client, headers, domain_key)
    assert len(history) == 2, "a logical publication was lost to physical dedup"
    assert len(bucket) == 1, "identical bytes were stored twice"
    expected = _png("same")
    assert [base64.b64decode(item["image_b64"]) for item in history] == [expected, expected]


def test_a_missing_object_is_reported_as_missing_not_invented(
        client, monkeypatch, renders, no_legacy_gallery, bucket):
    """Failure J at the product boundary. If the bytes are gone the entry is still listed — it is a
    real publication whose object is missing — but WITHOUT `image_b64`. Hiding it would quietly
    shrink someone's history to make a storage fault invisible."""
    headers, _ = _account(client)
    domain_key = _brand(client, monkeypatch, headers)
    _generate(client, headers, domain_key, headline="vanishing")

    assert len(bucket) == 1
    bucket.clear()                      # the object is gone; the row is not

    history = _history(client, headers, domain_key)
    assert len(history) == 1
    assert "image_b64" not in history[0]
