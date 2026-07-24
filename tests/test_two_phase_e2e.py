# -*- coding: utf-8 -*-
"""Two-phase image carve-out — END-TO-END regression proof on a CONTROLLED local page (no
external hits). This is the owner-mandated proof: two-phase must cut the below-fold product-image
BYTES to near-zero while loading the logo and preserving ALL content (text/html/image-URLs).

Deterministic: a local http.server serves a page with a logo (above-fold, eager) + 20 lazy
product images (below-fold) + real text. Skips gracefully if Chromium can't launch.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

_PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
                     "890000000d49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082")
_PRODUCTS = "".join(f'<img loading="lazy" src="/product{i}.jpg" width="200" height="200">'
                    for i in range(20))
_HTML = (f'<!doctype html><html><head><title>Shop</title></head><body>'
         f'<header><img src="/logo.png" width="120" height="40" alt="logo"></header>'
         f'<h1>Welcome to the store</h1>'
         f'<p>This is real page text with offerings and prices EGP 250. Contact us.</p>'
         f'<div style="margin-top:4000px">{_PRODUCTS}</div></body></html>')


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            b = _HTML.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        else:
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(_PNG))); self.end_headers(); self.wfile.write(_PNG)


@pytest.fixture()
def local_site():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/"
    finally:
        srv.shutdown()


def _fetch(url, two_phase):
    import playwright.sync_api as _pw
    # Several other test modules install a FAKE playwright.sync_api via sys.modules.setdefault at
    # IMPORT time (to avoid real Chromium), which pollutes the global module registry for the whole
    # session — so in the FULL suite this proof always sees the stub and skips. It exercises the real
    # browser only when this file runs on its own AND a working Chromium is installed (CS-45: the old
    # comment claimed it "runs standalone" unconditionally, but on a box whose Chromium loads no
    # images it hard-FAILED with a misleading assertion instead of skipping honestly).
    if getattr(_pw, "__file__", None) is None:
        pytest.skip("playwright.sync_api is stubbed by another test module in the full suite")
    from playwright.sync_api import sync_playwright
    from scraper.fetcher import make_browser_context, _fetch_page_once
    with sync_playwright() as pw:
        try:
            br = pw.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"chromium unavailable: {type(e).__name__}")
        ctx = make_browser_context(br)
        loaded, blocked = [], []
        ctx.on("requestfinished", lambda r: loaded.append(r.url) if r.resource_type == "image" else None)
        ctx.on("requestfailed", lambda r: blocked.append(r.url) if r.resource_type == "image" else None)
        res = _fetch_page_once(ctx, url, keep_page=False, two_phase=two_phase)
        br.close()
    return {"ok": res.ok, "text": res.rendered_text, "html": res.html,
            "img_loaded": len(loaded), "img_blocked": len(blocked),
            "logo_loaded": any("logo" in u for u in loaded)}


def test_two_phase_blocks_products_keeps_logo_and_content(local_site):
    thorough = _fetch(local_site, two_phase=False)
    two_phase = _fetch(local_site, two_phase=True)

    # CS-45: if the CONTROL fetch loaded ZERO images, this Chromium build cannot exercise image
    # loading at all here (no browser binaries fully installed / sandboxed net to 127.0.0.1) — that
    # is an ENVIRONMENT gap, not a two-phase regression, so skip HONESTLY instead of asserting a
    # false failure. The proof still runs (and can fail for real) wherever the browser loads images.
    if thorough["img_loaded"] == 0:
        pytest.skip("browser loaded no images in this environment — cannot exercise the carve-out")

    # THOROUGH loads everything (logo + 20 products); two-phase loads ~only the logo
    assert thorough["img_loaded"] >= 21 and thorough["img_blocked"] == 0
    assert two_phase["img_loaded"] <= 2 and two_phase["img_blocked"] >= 18   # ~95% byte cut
    assert two_phase["logo_loaded"] is True                                  # logo dims preserved

    # ZERO content regression: text + html identical, real text present in BOTH
    assert "real page text" in thorough["text"] and "real page text" in two_phase["text"]
    assert two_phase["text"] == thorough["text"]
    assert two_phase["html"] == thorough["html"]
    assert two_phase["ok"] and thorough["ok"]
