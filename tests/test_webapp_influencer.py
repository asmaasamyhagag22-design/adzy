"""م5 — AI influencer: portrait -> validate -> poster. Greenfield + degrade-safe (placeholder
cards, no live paid call). Service functions, the flow-order gating, and a job end to end."""
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

import base64  # noqa: E402
import io  # noqa: E402

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import webapp_influencer as influ  # noqa: E402
from api.brand_store import BrandStore  # noqa: E402
from api.brand_translate import profile_to_brand  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret")
    import api.user_auth as ua
    ua._SECRET_CACHE = None
    yield


@pytest.fixture
def client():
    from api.main import app
    with TestClient(app) as c:
        yield c


def _auth(client):
    tok = client.post("/api/auth/signup",
                      json={"email": "o@b.eg", "password": "supersecret"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _brand() -> dict:
    p = {"name": {"value": "Volume Cafe"}, "visual": {"palette_hex": ["#3b2b20"]},
         "source_url": "https://volumeqatar.com/"}
    b = profile_to_brand(p, source_url="https://volumeqatar.com/")
    BrandStore().save(b)
    return b


_CFG = {"gender": "female", "age": "28", "hair_color": "black", "hair_length": "long",
        "expression": "confident", "persona": "a Doha coffee enthusiast"}


# ---------- service functions ----------

def test_generate_portrait_patch():
    patch = influ.generate_portrait(_brand(), {**_CFG, "domain_key": "volumeqatar_com"})
    ai = patch["ai_influencer"]
    assert ai["has_portrait"] is True and ai["validated"] is False
    assert ai["portrait_url"].startswith("/api/generated/")
    assert "coffee enthusiast" in ai["config"]["influencer_persona"]


def _apply(brand, patch):
    """Merge a patch the way BrandStore does — `dict.update` REPLACES `ai_influencer` wholesale,
    which drops the candidates the very next call needs."""
    ai = dict(brand.get("ai_influencer") or {})
    for key, value in (patch.get("ai_influencer") or {}).items():
        if isinstance(value, dict) and isinstance(ai.get(key), dict):
            merged = dict(ai[key]); merged.update(value); ai[key] = merged
        else:
            ai[key] = value
    brand["ai_influencer"] = ai
    return brand


def test_validate_and_poster_patches():
    b = _brand()
    _apply(b, influ.generate_candidate(b, {**_CFG, "domain_key": "volumeqatar_com"}))
    _apply(b, influ.select_candidate(b, next(iter(b['ai_influencer']['candidates']))))
    assert b["ai_influencer"]["validated"] is True
    patch = influ.generate_influencer_poster(b, product_hint="cold brew", scene_description="terrace")
    ai = patch["ai_influencer"]
    assert ai["has_poster"] is True and ai["poster_url"].startswith("/api/generated/")
    assert ai["config"]["product_hint"] == "cold brew"


# ---------- routes: flow-order gating ----------

def test_influencer_routes_require_auth(client):
    assert client.post("/api/influencer/create", json={"domain_key": "x"}).status_code == 401
    assert client.post("/api/influencer/poster", json={"domain_key": "x"}).status_code == 401


def test_flow_order_is_enforced(client):
    _brand()
    h = _auth(client)
    dk = "volumeqatar_com"
    # validate before a portrait exists -> 409
    assert client.post("/api/influencer/validate",
                       json={"domain_key": dk, "approved": True}, headers=h).status_code == 409
    # poster before an approved portrait -> 409
    assert client.post("/api/influencer/poster",
                       json={"domain_key": dk}, headers=h).status_code == 409
    # create is allowed -> {job_id}
    r = client.post("/api/influencer/create", json={"domain_key": dk, **_CFG}, headers=h)
    assert r.status_code == 200 and "job_id" in r.json()


def test_create_unknown_brand_404(client):
    r = client.post("/api/influencer/create", json={"domain_key": "nope"}, headers=_auth(client))
    assert r.status_code == 404


# ---------- job end to end ----------

def test_portrait_job_then_validate_then_poster(client):
    import asyncio
    from api.jobs.store import JobStore, serving_store
    from api.webapp_jobs import run_brand_job
    from api.schemas import JobStatus

    _brand()
    dk = "volumeqatar_com"

    def _drive(work):
        async def run_it():
            store = JobStore(ttl_seconds=60)
            with serving_store(store):
                job = store.create()
                job.domain_key = dk
                store.attach_loop(job.job_id, asyncio.get_running_loop())
                await run_brand_job(job_id=job.job_id, domain_key=dk, work=work)
                return store.get(job.job_id).status
        return asyncio.run(run_it())

    # 1) portrait
    assert _drive(lambda b: influ.generate_candidate(b, {**_CFG, "domain_key": dk})) == JobStatus.SUCCEEDED
    ai = BrandStore().get(dk)["ai_influencer"]
    assert ai["has_portrait"] is True and ai["validated"] is False
    # portrait URL serves a PNG
    img = client.get(ai["portrait_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(img.content)).format == "PNG"

    # 2) validate (sync route)
    h = _auth(client)
    assert client.post("/api/influencer/validate",
                       json={"domain_key": dk, "approved": True}, headers=h).status_code == 200
    assert BrandStore().get(dk)["ai_influencer"]["validated"] is True

    # 3) poster
    assert _drive(lambda b: influ.generate_influencer_poster(
        b, product_hint="cold brew", scene_description="terrace")) == JobStatus.SUCCEEDED
    ai = BrandStore().get(dk)["ai_influencer"]
    assert ai["has_poster"] is True and ai["poster_url"].startswith("/api/generated/")
