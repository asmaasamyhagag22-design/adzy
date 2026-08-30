# -*- coding: utf-8 -*-
"""The app's HTML must be revalidated, or a fix ships to a browser that never asks for it.

MEASURED 2026-07-26, minutes after correcting three false stack claims on the landing page:

    file on disk        0 hits for ollama / z-image / local gpu / brandtwyn
    curl over HTTP      0 hits
    browser fetch('/')  ALL FOUR still present

The server was serving the corrected page and the browser was showing the old one. The response
carried an `etag` and `last-modified` but no `Cache-Control`, so the browser applied its own
heuristic freshness and never asked. `fetch('/', {cache: 'reload'})` returned the corrected page
immediately, which is what isolated it to caching rather than to the deploy.

For a demo this is the difference between showing the product and showing last week's copy of it.
`no-cache` does NOT mean "do not store" — it means "revalidate before use", so the ETag already
present turns almost every reload into a 304 with no body. The cost is one conditional request.

Static assets under /assets are deliberately NOT covered: they are content-addressed by filename
and benefit from being cached hard.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from api.main import app
    return TestClient(app)


@pytest.mark.parametrize("path", ["/", "/app"])
def test_html_pages_must_be_revalidated(client, path):
    r = client.get(path)
    assert r.status_code == 200
    cc = r.headers.get("Cache-Control", "")
    assert cc, f"{path} ships no Cache-Control — the browser invents its own freshness window"
    assert "no-cache" in cc or "max-age=0" in cc, (
        f"{path} sends Cache-Control: {cc!r}; an edit to the page would not reach a returning "
        "visitor until their browser decided on its own to ask")


def test_the_etag_still_makes_revalidation_cheap(client):
    """no-cache is only affordable because the conditional request can answer 304."""
    first = client.get("/")
    etag = first.headers.get("etag")
    assert etag, "no ETag — every revalidation would re-send the whole page"
    again = client.get("/", headers={"If-None-Match": etag})
    assert again.status_code == 304, (
        f"conditional GET returned {again.status_code}; revalidation costs a full body every time")


def test_api_responses_are_not_made_cacheable_by_this(client):
    """The rule is for documents. A brand payload must never be served from a stale copy."""
    r = client.get("/api/health")
    cc = r.headers.get("Cache-Control", "")
    assert "max-age=" not in cc or "max-age=0" in cc, (
        f"/api/health advertises {cc!r} — API data must not be cached")
