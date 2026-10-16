"""م4 — image generation. Degrade-safe placeholder by default (free, no paid call), the real
pipeline attempted only when BASEERA_ENABLE_PAID_IMAGES=1, and all endpoints (JSON + multipart +
calendar) return the frontend's shape. Never calls a live image API."""
from __future__ import annotations

import base64
import io
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
from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import webapp_images as images  # noqa: E402
from api.brand_store import BrandStore  # noqa: E402
from api.brand_translate import profile_to_brand  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret")
    monkeypatch.delenv("BASEERA_ENABLE_PAID_IMAGES", raising=False)  # default: no paid
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
    p = {"name": {"value": "Volume Cafe"}, "tagline": {"value": "Specialty coffee in Doha"},
         "visual": {"palette_hex": ["#3b2b20", "#d9c2a3"]}, "source_url": "https://volumeqatar.com/"}
    b = profile_to_brand(p, source_url="https://volumeqatar.com/")
    b["_profile"] = p
    b["content_calendar"] = {"calendar": [
        {"date": "2026-08-01", "topic": "Launch week", "cta": "Visit us", "channel": "instagram"}]}
    BrandStore().save(b)
    return b


def _caption_caller(monkeypatch, **payload):
    def caller(system, user, schema, **kwargs):
        return schema(**payload), {"total_tokens": 0}

    import business_profile.llm as llm
    monkeypatch.setattr(llm, "default_caller", lambda **kwargs: caller)


def _is_png(b64: str) -> bool:
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    return img.format == "PNG" and img.size == (1080, 1350)


# ---------- placeholder renderer ----------

def test_placeholder_is_a_real_branded_png():
    png = images.render_placeholder({"organization_name": "Volume", "brand_colors": ["#3b2b20"]},
                                    headline="Great coffee", cta="Order now")
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.size == (1080, 1350)


def test_generate_image_defaults_to_placeholder_no_paid_call(monkeypatch):
    # if the real path were called, this would blow up — prove it is NOT called by default
    monkeypatch.setattr(images, "_render_real",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("paid path called!")))
    out = images.generate_image(_brand_dict(), headline="Hi", cta="Go")
    assert _is_png(out["image_b64"]) and out["filename"].endswith(".png")


def _brand_dict():
    return {"domain_key": "volumeqatar_com", "organization_name": "Volume Cafe",
            "brand_colors": ["#3b2b20"], "tagline": "coffee"}


def test_paid_path_attempted_only_when_enabled(monkeypatch):
    calls = {"n": 0}
    def _fake_real(*a, **k):
        calls["n"] += 1
        return None  # simulate a 403 -> falls back to placeholder
    monkeypatch.setattr(images, "_render_real", _fake_real)

    images.generate_image(_brand_dict(), headline="x")          # disabled -> not called
    assert calls["n"] == 0
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")
    out = images.generate_image(_brand_dict(), headline="x")     # enabled -> attempted, then fallback
    assert calls["n"] == 1 and _is_png(out["image_b64"])         # still returns a valid image


# ---------- endpoints ----------

def test_generate_json_endpoint(client):
    _brand()
    r = client.post("/api/generate", headers=_auth(client),
                    json={"domain_key": "volumeqatar_com", "headline": "Fresh roast", "cta": "Order"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert _is_png(body["image_b64"]) and body["headline"] == "Fresh roast"


def test_generated_image_response_exposes_only_the_grounded_caption(client, monkeypatch):
    _brand()
    _caption_caller(
        monkeypatch,
        hook="The only certified cafe",
        body="Save 77% today.",
        cta="Join 9000 customers",
        hashtags=["#1", "#VolumeCafe"],
    )
    headers = _auth(client)

    body = client.post(
        "/api/generate",
        headers=headers,
        json={"domain_key": "volumeqatar_com", "headline": "Fresh roast"},
    ).json()

    caption = body["social_caption"]["caption"].lower()
    assert all(token not in caption for token in ("only", "certified", "77%", "9000", "#1"))
    # Image history stores the image and disclosure metadata, not a second caption projection.
    history = client.get("/api/history", headers=headers).json()["history"]
    assert history and "social_caption" not in history[0] and "caption" not in history[0]


def test_content_only_api_response_exposes_only_the_grounded_caption(client, monkeypatch):
    _brand()
    _caption_caller(
        monkeypatch,
        hook="The only certified cafe",
        body="Save 77% today.",
        cta="Join 9000 customers",
        hashtags=["#1", "#VolumeCafe"],
    )

    body = client.post(
        "/api/generate",
        headers=_auth(client),
        json={"domain_key": "volumeqatar_com", "content_only": True},
    ).json()

    assert body["content_only"] is True and "image_b64" not in body
    assert all(token not in body["caption"].lower()
               for token in ("only", "certified", "77%", "9000", "#1"))


def test_generate_requires_auth(client):
    assert client.post("/api/generate", json={"domain_key": "x"}).status_code == 401
    assert client.post("/api/calendar/generate-image", json={"domain_key": "x"}).status_code == 401


def test_upload_multipart_endpoint(client):
    _brand()
    r = client.post("/api/generate/upload", headers=_auth(client),
                    data={"domain_key": "volumeqatar_com", "headline": "Logo ad"},
                    files={"logo": ("logo.png", b"\x89PNG\r\n\x1a\n fake", "image/png")})
    assert r.status_code == 200, r.text
    assert _is_png(r.json()["image_b64"])


def test_inpaint_returns_the_image_roundtrip(client):
    _brand()
    src = images.render_placeholder(_brand_dict(), headline="orig")
    r = client.post("/api/edit/inpaint", headers=_auth(client),
                    data={"domain_key": "volumeqatar_com", "edit_prompt": "make it blue"},
                    files={"image": ("image.png", src, "image/png"),
                           "mask": ("mask.png", src, "image/png")})
    assert r.status_code == 200, r.text
    # stub echoes the input image unchanged
    assert base64.b64decode(r.json()["image_b64"]) == src


def test_calendar_generate_image_returns_servable_url(client):
    _brand()
    h = _auth(client)
    r = client.post("/api/calendar/generate-image", headers=h,
                    json={"domain_key": "volumeqatar_com", "date": "2026-08-01"})
    assert r.status_code == 200, r.text
    url = r.json()["image_url"]
    assert url.startswith("/api/generated/")
    # the URL is public + serves a PNG
    img = client.get(url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    # and the poster URL is persisted on the calendar entry
    brand = BrandStore().get("volumeqatar_com")
    assert brand["content_calendar"]["calendar"][0]["poster"]["url"] == url


def test_history_records_generated_image(client):
    _brand()
    h = _auth(client)
    client.post("/api/generate", headers=h,
                json={"domain_key": "volumeqatar_com", "headline": "H", "content_type": "story"})
    hist = client.get("/api/history", headers=h).json()["history"]
    assert hist and hist[0]["domain_key"] == "volumeqatar_com" and hist[0]["content_type"] == "story"
