# -*- coding: utf-8 -*-
"""Two-phase image-byte carve-out (owner footprint ruling), hermetic.

Phase A loads ABOVE-fold images (logo/header/hero -> real logo dims + clean palette), then blocks
BELOW-fold product-image BYTES during the scroll (URLs still collected from the DOM). Logo-like
URLs stay allowed throughout so the logo picker never regresses. `_is_logo_like` is the carve-out.
"""
from __future__ import annotations

from scraper.fetcher import _is_logo_like, _is_tracking_request, two_phase_route_decision


class _Req:
    def __init__(self, resource_type, url):
        self.resource_type, self.url = resource_type, url


class _Route:
    def __init__(self, resource_type, url):
        self.request = _Req(resource_type, url)
        self.aborted = self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


# CS-54/70 closed: this now calls the PRODUCTION decision function (scraper.fetcher.
# two_phase_route_decision) — the closure in _fetch_page_once delegates to the same one,
# so the suite finally covers the code that ships, not a re-implementation of it.
def _decision(handler_block, route):
    req = route.request
    verdict = two_phase_route_decision(url=req.url, resource_type=req.resource_type,
                                       block_below_fold=handler_block)
    route.abort() if verdict == "abort" else route.continue_()


def test_logo_like_matches_brand_chrome_not_products():
    for u in ("https://x.com/images/logo.png", "https://x.com/assets/brand-mark.svg",
              "https://x.com/favicon.ico", "https://x.com/apple-touch-icon.png",
              "https://x.com/img/site-icon.png"):
        assert _is_logo_like(u), u
    for u in ("https://cdn.shopify.com/s/files/1/products/shoe-42.jpg",
              "https://x.com/uploads/2024/hero-banner-model.jpg",
              "https://x.com/media/catalog/product/red-dress.webp"):
        assert not _is_logo_like(u), u


def test_before_block_all_images_load():
    # above-fold phase (block flag False): every image byte loads (logo dims + hero)
    for rt, u in [("image", "https://x.com/logo.png"), ("image", "https://x.com/hero.jpg"),
                  ("image", "https://cdn/products/shoe.jpg")]:
        r = _Route(rt, u); _decision(False, r)
        assert r.continued and not r.aborted, u


def test_after_block_products_dropped_but_logo_and_content_kept():
    block = True
    # product image below fold -> aborted
    r = _Route("image", "https://cdn.shopify.com/s/files/products/shoe.jpg"); _decision(block, r)
    assert r.aborted and not r.continued
    # logo-like image -> STILL loads (carve-out: dims preserved)
    r = _Route("image", "https://x.com/images/logo.png"); _decision(block, r)
    assert r.continued and not r.aborted
    # non-image content (scripts/xhr/css/doc) -> always loads
    for rt in ("script", "xhr", "fetch", "stylesheet", "document"):
        r = _Route(rt, "https://x.com/app.js"); _decision(block, r)
        assert r.continued and not r.aborted, rt
    # trackers -> always dropped
    r = _Route("script", "https://www.googletagmanager.com/gtm.js"); _decision(block, r)
    assert r.aborted


def test_config_default_is_two_phase_on():
    from scraper.config import TWO_PHASE_IMAGE_FETCH
    assert TWO_PHASE_IMAGE_FETCH is True


def test_scrape_thorough_disables_two_phase():
    # scrape(thorough=True) must set two_phase False (old behaviour) — verified via source wiring
    import inspect
    from scraper import crawler
    src = inspect.getsource(crawler.scrape)
    assert "two_phase = TWO_PHASE_IMAGE_FETCH and not thorough" in src
    assert "two_phase=two_phase" in src


def test_capture_screenshots_above_fold_uses_viewport(monkeypatch):
    from scraper.fetcher import _capture_screenshots, FetchResult
    from scraper.time_utils import utc_now

    class _Page:
        def screenshot(self, *, full_page, type):
            return b"FULL" if full_page else b"VIEWPORT"

    r = FetchResult(url="u", final_url="u", http_status=200, fetched_at=utc_now(), duration_ms=1)
    _capture_screenshots(_Page(), r, above_fold_only=True)
    assert r.full_screenshot_bytes == b"VIEWPORT"        # palette source = above-fold viewport
    r2 = FetchResult(url="u", final_url="u", http_status=200, fetched_at=utc_now(), duration_ms=1)
    _capture_screenshots(_Page(), r2, above_fold_only=False)
    assert r2.full_screenshot_bytes == b"FULL"           # thorough keeps the full-page shot
