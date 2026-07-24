"""م3 — analysis chain: SWOT / strategy / marketing-plan+calendar. Service functions produce the
frontend shapes (hermetic, no LLM -> deterministic/standalone), the routes enforce the generate
order, and a generate job runs end-to-end and enriches the stored brand."""
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

from api import webapp_analysis as analysis  # noqa: E402
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


def _profile() -> dict:
    return {
        "name": {"value": "Volume Cafe"}, "tagline": {"value": "Specialty coffee in Doha"},
        "description": {"value": "A specialty coffee roastery."},
        "market_category": {"value": "cafe"}, "tone_of_voice": {"value": "warm"},
        "audience_type": {"value": "B2C"},
        "value_propositions": [{"value": "single-origin beans"}, {"value": "in-house roasting"}],
        "offerings": [{"name": "Flat White", "price_text": "QAR 18"},
                      {"name": "V60", "price_text": "QAR 22"}],
        "social_presence": [{"platform": "Instagram", "url": "https://instagram.com/volume"}],
        "languages": ["en"], "visual": {"palette_hex": ["#3b2b20"], "logo_url": "/assets/logo.png"},
        "readiness": {"swot_richness_score": 60, "ready_for_strategy": True},
        "quality": {"has_name": True}, "source_url": "https://volumeqatar.com/",
    }


def _brand() -> dict:
    p = _profile()
    b = profile_to_brand(p, source_url="https://volumeqatar.com/")
    b["_profile"] = p
    return b


# ---------- service functions (hermetic; no LLM -> deterministic/standalone) ----------

def test_generate_swot_produces_frontend_shape():
    patch = analysis.generate_swot(_brand())
    assert patch["has_swot"] is True
    swot = patch["swot"]
    for q in ("strengths", "weaknesses", "opportunities", "threats"):
        assert q in swot and isinstance(swot[q], list)
    assert "strategic_context" in swot and "quadrant_counts" in swot["strategic_context"]
    assert "_swot_raw" in patch                         # kept so strategy can rebuild the SWOT


def test_generate_strategy_produces_tows_matrix():
    b = _brand()
    b.update(analysis.generate_swot(b))                 # strategy consumes _swot_raw
    patch = analysis.generate_strategy(b)
    assert patch["has_strategy"] is True
    ms = patch["marketing_strategy"]
    assert set(ms["tows_matrix"].keys()) == {"SO", "ST", "WO", "WT"}
    assert "strategic_posture" in ms and isinstance(ms["priority_action_plan"], list)
    assert "instagram" in ms["channels"]                # grounded in the brand's own social


def test_generate_plan_produces_campaigns_and_calendar():
    b = _brand()
    b.update(analysis.generate_swot(b))
    patch = analysis.generate_plan(b, planning_period="1_month")
    assert patch["has_plan"] is True
    assert isinstance(patch["content_calendar"]["calendar"], list)
    assert patch["content_calendar"]["calendar"]        # a real (fallback) calendar, not empty
    assert isinstance(patch["marketing_plan"]["campaigns"], list)


# ---------- routes: auth + generate-order gating ----------

def test_analysis_routes_require_auth(client):
    assert client.post("/api/swot/generate", json={"domain_key": "x"}).status_code == 401
    assert client.post("/api/strategy/generate", json={"domain_key": "x"}).status_code == 401


def test_generate_order_is_enforced(client):
    BrandStore().save(_brand())
    h = _auth(client)
    dk = "volumeqatar_com"
    # approve before generate -> 409
    assert client.post("/api/swot/approve", json={"domain_key": dk}, headers=h).status_code == 409
    # strategy before swot approved -> 409
    assert client.post("/api/strategy/generate", json={"domain_key": dk}, headers=h).status_code == 409
    # plan before strategy -> 409
    assert client.post("/api/marketing-plan/generate",
                       json={"domain_key": dk}, headers=h).status_code == 409
    # swot generate is allowed -> {job_id}
    r = client.post("/api/swot/generate", json={"domain_key": dk}, headers=h)
    assert r.status_code == 200 and "job_id" in r.json()


def test_approve_flips_flag_after_swot(client):
    b = _brand()
    b.update(analysis.generate_swot(b))                 # pretend the swot job finished
    BrandStore().save(b)
    h = _auth(client)
    r = client.post("/api/swot/approve", json={"domain_key": "volumeqatar_com"}, headers=h)
    assert r.status_code == 200 and r.json()["swot_approved"] is True
    assert BrandStore().get("volumeqatar_com")["swot_approved"] is True


def test_generate_unknown_brand_is_404(client):
    r = client.post("/api/swot/generate", json={"domain_key": "nope_com"}, headers=_auth(client))
    assert r.status_code == 404


# ---------- job runner end to end ----------

def test_swot_job_enriches_the_brand(monkeypatch):
    import asyncio
    from api.jobs.store import JobStore
    from api.routes.analysis import _run
    from api.schemas import JobStatus

    BrandStore().save(_brand())

    async def run_it():
        store = JobStore(ttl_seconds=60)
        job = store.create()
        job.domain_key = "volumeqatar_com"
        store.attach_loop(job.job_id, asyncio.get_running_loop())
        await _run(job_id=job.job_id, domain_key="volumeqatar_com", store=store,
                   work=analysis.generate_swot)
        return store, job.job_id

    store, job_id = asyncio.run(run_it())
    assert store.get(job_id).status == JobStatus.SUCCEEDED
    brand = BrandStore().get("volumeqatar_com")
    assert brand["has_swot"] is True and brand["swot"] is not None
