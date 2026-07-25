# -*- coding: utf-8 -*-
"""An upload the renderer never receives must be disclosed, not silently dropped.

MEASURED: `/api/generate/upload` and `/api/generate/reference` accept a logo and a reference photo,
`await read_bounded(...)` them — assigning the bytes to nothing — and call `_emit`, which forwards
only headline/subtext/cta/content_type. `webapp_images.generate_image` has no image parameter at
all, and neither does `_render_real` beneath it. So the file is read and discarded on BOTH the free
and the paid path.

Why that is worse here than a missing feature: the UI promises, in words,
"upload the real product photo to preserve its exact identity instead of letting the model imagine
it". The user uploads their actual product, is told the render is anchored to it, and receives an
image the model invented — from a system whose entire claim is that it does not invent. The
response carried no signal either: `degraded_reason` is only set when a PAID path fails, and an
ignored upload is not a failure by that definition.

The fix is the honesty already used by `/api/edit/inpaint` in the same file: say `applied_upload:
false` and return a note in the brand's own language. Wiring the image into a renderer is new work;
telling the truth about it is not."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.brand_store import BrandStore


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
    with TestClient(app) as c:
        yield c


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _setup(client, *, languages=("ar",)):
    token = client.post("/api/auth/signup",
                        json={"email": "u@t.co", "password": "password123",
                              "full_name": "U"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    BrandStore().save({"domain_key": "b_com", "organization_name": "براند",
                       "_profile": {"languages": list(languages)}}, owner_id=me["id"])
    return headers


def test_an_ignored_logo_is_reported_as_not_applied(client):
    headers = _setup(client)
    r = client.post("/api/generate/upload",
                    data={"domain_key": "b_com"},
                    files={"logo": ("logo.png", _png(), "image/png")}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body.get("applied_upload") is False, "the discarded logo is reported as if it were used"
    assert str(body.get("note") or "").strip(), "nothing tells the user their upload was ignored"


def test_an_ignored_reference_photo_is_reported_as_not_applied(client):
    headers = _setup(client)
    r = client.post("/api/generate/reference",
                    data={"domain_key": "b_com"},
                    files={"reference_image": ("p.png", _png(), "image/png")}, headers=headers)
    body = r.json()
    assert body.get("applied_upload") is False
    assert str(body.get("note") or "").strip()


def test_the_note_is_in_the_brands_language(client):
    """D-8: the note follows the brand's measured language, like every other user-facing string."""
    headers = _setup(client, languages=("ar",))
    r = client.post("/api/generate/reference", data={"domain_key": "b_com"},
                    files={"reference_image": ("p.png", _png(), "image/png")}, headers=headers)
    note = r.json().get("note") or ""
    assert any("؀" <= ch <= "ۿ" for ch in note), f"an Arabic brand got: {note!r}"


def test_a_request_with_no_upload_claims_nothing(client):
    """The flag must not appear when there was nothing to ignore."""
    headers = _setup(client)
    r = client.post("/api/generate/upload", data={"domain_key": "b_com"}, headers=headers)
    assert r.status_code == 200
    assert r.json().get("applied_upload") is None


def test_the_plain_json_route_is_unaffected(client):
    headers = _setup(client)
    r = client.post("/api/generate", json={"domain_key": "b_com"}, headers=headers)
    assert r.status_code == 200 and r.json().get("applied_upload") is None


def test_the_interface_no_longer_promises_the_photo_is_used():
    """The label was the loudest part of this defect — it promised identity preservation."""
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "preserve its exact identity" not in html, (
        "the UI still promises the uploaded photo anchors the render")
