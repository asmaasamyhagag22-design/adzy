# -*- coding: utf-8 -*-
"""A feature that is not implemented must say so in the RESPONSE, not only in a code comment.

MEASURED (api/routes/generate.py): `/api/edit/inpaint` reads the uploaded image, reads and discards
the mask and the edit prompt, and returns HTTP 200 with the ORIGINAL bytes. Its comment calls this
"the honest stub" — but the comment is addressed to a developer, while the person who painted a mask
and typed «شيل الخلفية» receives a 200 and an unchanged picture. They conclude the model
misunderstood them and try again, spending their time on a feature that never ran. That is the
project's #1 failure class: "an image came back" is true, and answers a different question from
"was my edit applied?".

Also locked here: uploads are bounded. `await image.read()` pulled an arbitrarily large body fully
into memory, and inpaint then base64-expanded it (×1.33) into the JSON response.

Hermetic — no network, no paid call."""
from __future__ import annotations

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
from fastapi.testclient import TestClient  # noqa: E402


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
    return TestClient(app)


@pytest.fixture
def auth(client):
    r = client.post("/api/auth/signup", json={"email": "a@b.co", "password": "password123",
                                              "full_name": "A"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def brand():
    from api.brand_store import BrandStore
    b = {"domain_key": "b_com", "organization_name": "B", "brand_colors": ["#123456"]}
    BrandStore().save(b)
    return b


def _png(size=(8, 8)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_inpaint_declares_that_nothing_was_edited(client, auth, brand):
    src = _png()
    r = client.post("/api/edit/inpaint",
                    data={"domain_key": "b_com", "edit_prompt": "شيل الخلفية"},
                    files={"image": ("i.png", src, "image/png")}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body.get("applied") is False, "an unedited image was returned as a successful edit"
    assert body.get("note"), "no word to the user that region editing is not available"


def test_the_note_is_in_the_users_language_not_only_english(client, auth, brand):
    r = client.post("/api/edit/inpaint",
                    data={"domain_key": "b_com", "edit_prompt": "x", "language": "ar"},
                    files={"image": ("i.png", _png(), "image/png")}, headers=auth)
    assert any("؀" <= ch <= "ۿ" for ch in r.json().get("note", "")), \
        "an Arabic-speaking user is told in English that their edit did not run"


def test_the_image_still_round_trips_so_the_plumbing_keeps_working(client, auth, brand):
    import base64
    src = _png()
    r = client.post("/api/edit/inpaint", data={"domain_key": "b_com"},
                    files={"image": ("i.png", src, "image/png")}, headers=auth)
    assert base64.b64decode(r.json()["image_b64"]) == src


def test_an_oversized_upload_is_refused_instead_of_buffered(client, auth, brand, monkeypatch):
    from api import upload_limits

    monkeypatch.setattr(upload_limits, "_MAX_UPLOAD_BYTES", 1024)
    big = b"\x89PNG\r\n\x1a\n" + b"0" * 4096
    r = client.post("/api/edit/inpaint", data={"domain_key": "b_com"},
                    files={"image": ("i.png", big, "image/png")}, headers=auth)
    assert r.status_code == 413, f"a {len(big)}-byte body was accepted whole into memory"


def test_a_normal_upload_is_unaffected_by_the_limit(client, auth, brand):
    r = client.post("/api/edit/inpaint", data={"domain_key": "b_com"},
                    files={"image": ("i.png", _png(), "image/png")}, headers=auth)
    assert r.status_code == 200


def test_the_other_upload_routes_are_bounded_too(client, auth, brand, monkeypatch):
    from api import upload_limits

    monkeypatch.setattr(upload_limits, "_MAX_UPLOAD_BYTES", 1024)
    big = b"0" * 4096
    for path, field in (("/api/generate/upload", "logo"),
                        ("/api/generate/reference", "reference_image")):
        r = client.post(path, data={"domain_key": "b_com"},
                        files={field: ("f.png", big, "image/png")}, headers=auth)
        assert r.status_code == 413, f"{path} accepted an unbounded upload"
