# -*- coding: utf-8 -*-
"""The web app's image response must carry the pipeline's honesty stamp and its own degrade reason.

TWO measured failures this fixes, both of the project's #1 failure class — a true statement that
answers a different question ("an image was produced" answering "was the image you asked for
produced?"):

1. TRUST STAMP DROPPED. `poster.pipeline` gained `trust_violation` / `trust_note_ar` precisely
   because "the qa verdict alone was MEASURED being ignored downstream" (its own comment, after
   nanocare 2026-07-24 shipped a fabricated «Lifoccmul Iron» silently). `webapp_images._render_real`
   then took `res.image_base64` and threw the stamp away — the new web app became the first
   downstream to repeat the exact mistake the field was added for.

2. SILENT DEGRADE. With `BASEERA_ENABLE_PAID_IMAGES=1`, a 403/quota failure — or a brand with no
   `_profile` — fell back to the placeholder and returned 200 with no signal at all, under a
   watermark reading "enable image generation for the final render" while it was already enabled.

`is_preview` states which renderer actually ran; `degraded_reason` states why, when the paid path
was asked for and did not deliver. Hermetic — no network, no paid call."""
from __future__ import annotations

import pytest

from api import webapp_images as images


@pytest.fixture()
def brand():
    return {"domain_key": "b_com", "organization_name": "Brand",
            "brand_colors": ["#123456"], "_profile": {"name": {"value": "Brand"}}}


def test_the_free_placeholder_says_it_is_a_preview(brand, monkeypatch):
    monkeypatch.delenv("BASEERA_ENABLE_PAID_IMAGES", raising=False)
    out = images.generate_image(brand, headline="Hello")
    assert out["is_preview"] is True
    assert not out.get("trust_violation")
    assert not out.get("degraded_reason"), "nothing degraded — the paid path was never requested"


def test_a_paid_render_that_violates_trust_ships_the_disclosure(brand, monkeypatch):
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")

    class _Res:
        image_base64 = ""
        trust_violation = True
        trust_note_ar = "تنبيه: الصورة فيها عنصر غير مؤكَّد"

    png = images.render_placeholder(brand, headline="x")
    monkeypatch.setattr(images, "_render_real",
                        lambda *a, **k: images.RealRender(png=png, result=_Res()))
    out = images.generate_image(brand, headline="Hello")
    assert out["is_preview"] is False
    assert out["trust_violation"] is True
    assert "غير مؤكَّد" in out["trust_note_ar"], "the client-facing Arabic line was dropped"


def test_a_clean_paid_render_carries_no_false_alarm(brand, monkeypatch):
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")

    class _Res:
        image_base64 = ""
        trust_violation = False
        trust_note_ar = ""

    png = images.render_placeholder(brand, headline="x")
    monkeypatch.setattr(images, "_render_real",
                        lambda *a, **k: images.RealRender(png=png, result=_Res()))
    out = images.generate_image(brand, headline="Hello")
    assert out["is_preview"] is False
    assert out["trust_violation"] is False
    assert not out["degraded_reason"]


def test_a_failed_paid_render_names_the_reason_instead_of_pretending(brand, monkeypatch):
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")

    def boom(*a, **k):
        raise RuntimeError("403 PERMISSION_DENIED")

    monkeypatch.setattr(images, "_render_real", boom)
    out = images.generate_image(brand, headline="Hello")
    assert out["is_preview"] is True, "it fell back, so it must not claim to be a final render"
    assert out["degraded_reason"], "a paid render was requested and failed with no word to the user"
    assert "403" in out["degraded_reason"] or "PERMISSION" in out["degraded_reason"].upper()


def test_a_brand_with_no_profile_says_so_rather_than_failing_silently(brand, monkeypatch):
    monkeypatch.setenv("BASEERA_ENABLE_PAID_IMAGES", "1")
    brand.pop("_profile")
    out = images.generate_image(brand, headline="Hello")
    assert out["is_preview"] is True
    assert "profile" in (out["degraded_reason"] or "").lower()


def test_the_route_passes_the_disclosure_through_to_the_client(brand, monkeypatch, tmp_path):
    """The unit tests above prove the generator stamps it; this proves the ENDPOINT ships it.

    CORRECTED while writing this file: the first version called `gen.generate(...)` as a plain
    Python function. That bypasses FastAPI's dependency injection entirely — `user` arrived as a raw
    `Depends` object — so it was testing a function signature, not a route. Going through TestClient
    exercises the real graph, which is also what makes it able to catch a scoping mistake."""
    import sys
    import types

    _fake = types.ModuleType("playwright")
    _fake_sync = types.ModuleType("playwright.sync_api")
    for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
        setattr(_fake_sync, _n, type("S", (), {}))
    _fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
    _fake_sync.Error = type("Error", (Exception,), {})
    sys.modules.setdefault("playwright", _fake)
    sys.modules.setdefault("playwright.sync_api", _fake_sync)

    from fastapi.testclient import TestClient

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-abc")
    import api.user_auth as ua
    ua._SECRET_CACHE = None

    from api.brand_store import BrandStore
    from api.main import app
    from api.routes import generate as gen

    monkeypatch.setattr(gen.images, "generate_image", lambda *a, **k: {
        "image_b64": "AA==", "filename": "f.png", "headline": "", "subtext": "", "cta": "",
        "is_preview": False, "trust_violation": True, "trust_note_ar": "تنبيه", "degraded_reason": "",
    })
    with TestClient(app) as client:
        token = client.post("/api/auth/signup",
                            json={"email": "d@t.co", "password": "password123",
                                  "full_name": "D"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/api/auth/me", headers=headers).json()
        BrandStore().save(brand, owner_id=me["id"])
        out = client.post("/api/generate", json={"domain_key": "b_com"}, headers=headers).json()

    assert out["trust_violation"] is True
    assert out["trust_note_ar"] == "تنبيه"
    assert out["is_preview"] is False
