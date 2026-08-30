# -*- coding: utf-8 -*-
"""The app ships security headers, and its CSP is compatible with the page it actually serves.

`api/main.py` installed only CORSMiddleware. Meanwhile the SPA holds a signed JWT in the browser
and renders scraped, third-party brand content — names, taglines, offering text pulled off other
people's websites — into the same document. That is the exact shape where a missing
Content-Security-Policy turns one bad string into a token theft.

The headers are asserted on a REAL response through the app, not on the middleware list: a header
set on a middleware that never runs for a given path is the "true statement answering a different
question" this project keeps catching. The CSP is asserted against the page's real needs too —
`frontend/index.html` uses inline `<script>` and inline `style=` attributes and loads Google Fonts,
so a policy that forbade those would look strict in a test and produce a blank page in a browser.
Locking that here is what stops the next tightening from silently breaking the product."""
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


def test_the_app_page_carries_the_headers(client):
    h = client.get("/app").headers
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") in ("DENY", "SAMEORIGIN")
    assert "referrer-policy" in h
    assert "content-security-policy" in h


def test_an_api_response_carries_them_too(client):
    """A JSON error is a response the browser parses; the headers must not be page-only."""
    h = client.get("/api/brands").headers          # 401, still a real response
    assert h.get("x-content-type-options") == "nosniff"
    assert "content-security-policy" in h


def test_the_policy_forbids_framing_and_object_embeds(client):
    csp = client.get("/app").headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_the_policy_still_allows_what_the_real_page_needs(client):
    """Measured against frontend/index.html: inline script, inline styles, Google Fonts, data: images."""
    csp = client.get("/app").headers["content-security-policy"]
    assert "'unsafe-inline'" in csp, "the SPA's inline <script> would be blocked — blank page"
    assert "data:" in csp, "generated images arrive as data: URIs and would not render"
    assert "fonts.googleapis.com" in csp and "fonts.gstatic.com" in csp, \
        "the poster/UI webfonts would silently fall back"


def test_the_page_really_does_rely_on_those_allowances():
    """Guards the test above from becoming a rubber stamp: if the page stops using inline script,
    the CSP should be tightened rather than this allowance kept forever."""
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "<script>" in html, "no inline script any more — tighten the CSP and update this test"
    assert "fonts.googleapis.com" in html or "fonts.gstatic.com" in html


def test_headers_do_not_replace_existing_ones(client):
    """The middleware must not clobber CORS or content-type."""
    r = client.get("/app")
    assert r.headers.get("content-type", "").startswith("text/html")
    assert r.status_code == 200
