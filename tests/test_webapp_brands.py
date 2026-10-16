"""م2 — brand store + scrape. Translation (BusinessProfile -> brand object), persistence, the CRUD
routes (auth-gated), and the scrape wire (pipeline -> brand) end-to-end, all hermetic."""
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
from api.brand_translate import domain_key_from_url, profile_to_brand  # noqa: E402


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


def _token(client) -> str:
    r = client.post("/api/auth/signup",
                    json={"email": "o@b.eg", "password": "supersecret", "full_name": "O"})
    return r.json()["access_token"]


def _auth(client):
    return {"Authorization": f"Bearer {_token(client)}"}


# ---------- domain_key (must match frontend domainKeyFromUrl) ----------

# The hyphen case CHANGED on 2026-07-25, and the old expectation was locking a defect: mapping both
# "." and "-" onto "_" made `foo-bar.com` and `foo.bar.com` one key, so one brand's stored record
# overwrote the other's and every later stage ran on a blend of two companies
# (tests/test_domain_key_is_injective.py). "-" now encodes as "__". Dot-only hosts are untouched,
# which is why the other two rows here are unchanged and the brands already on disk kept their keys.
@pytest.mark.parametrize("url,key", [
    ("https://www.brand.com.eg/x", "brand_com_eg"),
    ("https://sub.my-brand.qa", "sub_my__brand_qa"),
    ("http://Example.COM", "example_com"),
])
def test_domain_key_matches_frontend_rule(url, key):
    assert domain_key_from_url(url) == key


# ---------- translation ----------

def _profile() -> dict:
    return {
        "name": {"value": "Volume Cafe"},
        "tagline": {"value": "Specialty coffee in Doha"},
        "description": {"value": "A specialty coffee roastery."},
        "market_category": {"value": "cafe"},
        "category": {"value": "cafe"},
        "tone_of_voice": {"value": "warm"},
        "audience_type": {"value": "B2C"},
        "value_propositions": [{"value": "single-origin beans"}, {"value": "in-house roasting"}],
        "other_unique_insights": [{"value": "Pearl-Qatar location"}],
        "offerings": [{"name": "Flat White", "price_text": "QAR 18", "page_url": "https://x/m"}],
        "social_presence": [{"platform": "Instagram", "url": "https://instagram.com/volume"}],
        "contact_channels": {"phones": [{"raw": "+974 5000 0000"}], "emails": ["hi@volume.qa"],
                             "physical_addresses": ["Pearl, Doha"]},
        "locations": [{"city": "Doha", "country": "Qatar", "address_text": "Pearl"}],
        "languages": ["en", "ar"],
        "visual": {"palette_hex": ["#3b2b20", "#d9c2a3"], "primary_color": "#3b2b20",
                   "heading_font": "Fraunces", "body_font": "Inter",
                   "logo_url": "https://x/logo.png", "content_images": ["https://x/1.jpg"]},
        "readiness": {"swot_richness_score": 72, "ready_for_strategy": True},
        "quality": {"has_name": True, "has_visual": True, "has_contact": True},
        "extraction_meta": {"pages_scraped": 10},
        "source_url": "https://volumeqatar.com/",
    }


def test_profile_to_brand_maps_all_identity_fields():
    b = profile_to_brand(_profile(), source_url="https://volumeqatar.com/")
    assert b["domain_key"] == "volumeqatar_com"
    assert b["organization_name"] == "Volume Cafe"
    assert b["tagline"] == "Specialty coffee in Doha"
    assert b["brand_colors"] == ["#3b2b20", "#d9c2a3"]
    assert b["fonts"] == ["Fraunces", "Inter"]
    assert b["logo_url"] == "https://x/logo.png"
    assert b["social_links"]["instagram"] == "https://instagram.com/volume"
    assert b["phone"] == "+974 5000 0000" and b["email"] == "hi@volume.qa"
    assert b["country"] == "Qatar" and b["city"] == "Doha"
    assert b["languages"] == ["en", "ar"]
    assert b["readiness_score"] == 72 and b["readiness_status"] == "ready"
    assert b["products"][0]["name"] == "Flat White" and b["products"][0]["price"] == "QAR 18"
    assert "single-origin beans" in b["key_messages"]
    # analysis blocks start empty -> the UI shows "generate" buttons
    assert b["has_swot"] is False and b["has_strategy"] is False and b["swot"] is None
    assert b["ai_influencer"]["has_portrait"] is False


def test_profile_to_brand_never_leaks_enum_or_dict_repr():
    b = profile_to_brand(_profile(), source_url="https://volumeqatar.com/")
    import json
    flat = json.dumps(b, ensure_ascii=False)
    assert "{'value'" not in flat and "EvidencedField(" not in flat and "AudienceType." not in flat


# ---------- store ----------

def test_brand_store_roundtrip(tmp_path):
    store = BrandStore(tmp_path / "b")
    store.save({"domain_key": "x_com", "organization_name": "X"})
    assert store.get("x_com")["organization_name"] == "X"
    store.update("x_com", {"has_swot": True})
    assert store.get("x_com")["has_swot"] is True
    assert any(b["domain_key"] == "x_com" for b in store.list())
    assert store.delete("x_com") and store.get("x_com") is None


# ---------- routes: auth-gated CRUD ----------

def test_brands_require_auth(client):
    assert client.get("/api/brands").status_code == 401
    assert client.get("/api/brands/x_com").status_code == 401
    assert client.post("/api/scrape", json={"url": "https://x.com/"}).status_code == 401


def test_brand_crud_via_http(client, tmp_path):
    # The brand is seeded WITH an owner, because that is how one is really created: POST /api/scrape
    # stamps the requesting account onto the result (api/routes/scrape.py). Seeding it ownerless was
    # a fixture shortcut, and once ownership landed it produced a state the real flow cannot reach —
    # an unclaimed brand, which DELETE deliberately refuses with 409 until a human claims it.
    h = _auth(client)
    me = client.get("/api/auth/me", headers=h).json()
    BrandStore().save(profile_to_brand(_profile(), source_url="https://volumeqatar.com/"),
                      owner_id=me["id"])
    grid = client.get("/api/brands", headers=h).json()["brands"]
    assert any(b["domain_key"] == "volumeqatar_com" for b in grid)
    one = client.get("/api/brands/volumeqatar_com", headers=h)
    assert one.status_code == 200 and one.json()["organization_name"] == "Volume Cafe"
    assert client.delete("/api/brands/volumeqatar_com", headers=h).status_code == 200
    assert client.get("/api/brands/volumeqatar_com", headers=h).status_code == 404


def test_history_shape(client):
    r = client.get("/api/history", headers=_auth(client))
    assert r.status_code == 200 and isinstance(r.json()["history"], list)


def test_scrape_returns_job_id(client):
    r = client.post("/api/scrape", json={"url": "https://example.com/"}, headers=_auth(client))
    assert r.status_code == 200 and isinstance(r.json()["job_id"], str)


# ---------- scrape wire (pipeline -> brand), end to end ----------

def test_scrape_and_store_persists_a_brand(monkeypatch):
    """Drive the real async scrape flow with a fake scraper; the brand must land in the store, keyed
    by the deterministic domain_key, and the job must carry status->done + that domain_key."""
    import asyncio
    from api.jobs.recorder import JobRecorder
    from api.jobs.store import JobStore, serving_store
    from api.persistence import get_persistence
    from api.routes.scrape import _run_scrape_job
    from api.brand_translate import domain_key_from_url as dk
    from tests.test_api_run import _fake_scraper_fn

    monkeypatch.setattr("api.jobs.runner.scraper_scrape", _fake_scraper_fn)
    monkeypatch.setattr("api.routes.scrape.has_llm", lambda: False)  # rules-only, free

    async def run_it():
        store = JobStore(ttl_seconds=60)
        with serving_store(store):
            job = store.create()
            job.domain_key = dk("https://example.com/")
            store.attach_loop(job.job_id, asyncio.get_running_loop())
            await _run_scrape_job(
                job_id=job.job_id,
                url="https://example.com/",
                recorder=JobRecorder(job_id=job.job_id, provider=get_persistence()),
                domain_key=dk("https://example.com/"),
                max_pages=20,
                max_depth=3,
                delay=1.0,
            )
            return store, job.job_id

    store, job_id = asyncio.run(run_it())
    from api.schemas import JobStatus
    assert store.get(job_id).status == JobStatus.SUCCEEDED
    assert store.get(job_id).domain_key == "example_com"
    brand = BrandStore().get("example_com")
    assert brand is not None and brand["domain_key"] == "example_com"
    assert brand["organization_name"]                     # a name came through the pipeline
