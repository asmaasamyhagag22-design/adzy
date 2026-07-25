# -*- coding: utf-8 -*-
"""Adding a competitor over HTTP must actually change the analysis.

The store-level rules live in tests/test_user_added_competitor.py. This file locks the thing that
makes the feature worth having: a competitor typed in the UI has to reach `synthesize_swot` and
turn a page of un-compared site readings into a relative analysis. A route that stores a name
nobody reads would pass every unit test and deliver nothing.

The path that matters most is the FAILING one. When discovery finds nothing the webapp takes
`synthesize_swot_standalone`, which used to build its matrix with a hard-coded empty peer list —
so the client would be told "add a competitor to get a real analysis", add one, and watch the next
run ignore it. That is the exact shape of a feature that is true in the code and false in use.

Hermetic — tmp registry and tmp webapp dir, no network."""
from __future__ import annotations

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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.brand_store import BrandStore  # noqa: E402

_PROFILE = {
    "name": {"value": "Nano Care"},
    "source_url": {"value": "https://brand.example/"},
    "languages": ["ar"],
    "contact_channels": {"whatsapp": {"value": ""}},
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_REGISTRY_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-abc")
    import api.user_auth as ua
    ua._SECRET_CACHE = None
    import competitor.registry as reg
    monkeypatch.setattr(reg, "_STORE", str(tmp_path / "registry"))
    yield


@pytest.fixture
def client():
    from api.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth(client):
    token = client.post("/api/auth/signup",
                        json={"email": "o@t.co", "password": "password123",
                              "full_name": "O"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    BrandStore().save({"domain_key": "brand_example", "organization_name": "Nano Care",
                       "_profile": _PROFILE}, owner_id=me["id"])
    return headers


def test_a_competitor_can_be_added_listed_and_removed(client, auth):
    r = client.post("/api/brands/brand_example/competitors", headers=auth,
                    json={"domain_key": "brand_example", "name": "Rival Co",
                          "website": "https://rival.example/"})
    assert r.status_code == 200, r.text
    key = r.json()["competitor"]["key"]

    listed = client.get("/api/brands/brand_example/competitors", headers=auth).json()["competitors"]
    assert [c["name"] for c in listed] == ["Rival Co"]
    assert listed[0]["source"] == "user", "provenance lost between the store and the client"

    gone = client.delete(f"/api/brands/brand_example/competitors/{key}", headers=auth)
    assert gone.status_code == 200 and gone.json()["removed"] is True
    assert client.get("/api/brands/brand_example/competitors",
                      headers=auth).json()["competitors"] == []


def test_a_nameless_competitor_is_a_400_not_a_silent_success(client, auth):
    r = client.post("/api/brands/brand_example/competitors", headers=auth,
                    json={"domain_key": "brand_example", "name": "  ", "website": ""})
    assert r.status_code == 400


def test_an_internal_url_is_refused(client, auth):
    r = client.post("/api/brands/brand_example/competitors", headers=auth,
                    json={"domain_key": "brand_example", "name": "X",
                          "website": "http://127.0.0.1:8000/"})
    assert r.status_code == 400, "a competitor URL is a URL we fetch — SSRF gate applies"


def test_another_account_cannot_read_or_add_competitors(client, auth):
    """Same scoping as every other brand route: the brand resolves to 404 for a stranger."""
    other = client.post("/api/auth/signup",
                        json={"email": "b@t.co", "password": "password123",
                              "full_name": "B"}).json()["access_token"]
    h = {"Authorization": f"Bearer {other}"}
    assert client.get("/api/brands/brand_example/competitors", headers=h).status_code == 404
    assert client.post("/api/brands/brand_example/competitors", headers=h,
                       json={"domain_key": "brand_example", "name": "X",
                             "website": ""}).status_code == 404


def test_adding_a_competitor_makes_the_standalone_swot_comparative(client, auth):
    """THE POINT OF THE FEATURE. With no peers the SWOT has no strategic quadrants and says so;
    after the owner names one, the same profile produces a compared analysis."""
    from api.webapp_analysis import synthesize_swot_standalone

    before, peers_before = synthesize_swot_standalone(_PROFILE)
    assert peers_before == []
    assert any("أضيفي منافساً" in n for n in before.notes), before.notes

    client.post("/api/brands/brand_example/competitors", headers=auth,
                json={"domain_key": "brand_example", "name": "Rival Co",
                      "website": "https://rival.example/"})

    after, peers_after = synthesize_swot_standalone(_PROFILE)
    assert [p.candidate.name for p in peers_after] == ["Rival Co"], \
        "the typed competitor never reached the analysis"
    assert not any("أضيفي منافساً" in n for n in after.notes), \
        "still telling the owner to do what she just did"
