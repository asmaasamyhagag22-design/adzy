# -*- coding: utf-8 -*-
"""«لما بحتاج اعمل لمنتج معين بحط اللينك بتاعه» — the product link the dashboard never had.

WHAT WAS ALREADY THERE, unconnected:

    scraper/product_page.py:177  scrape_product_page(url) -> ProductInfo(name, image, price, …)
    poster/pipeline.py:909       generate_poster(..., product_image=..., product_name=...)

Both ends built, tested, and never joined — the same shape as the ads pull and the geocode branch
before it. The webapp meanwhile ACCEPTED `product_name` and `product_description` as form fields
on `/generate/upload` and `/generate/reference` and passed them to nothing at all: `_emit` has no
such parameters. A field a client fills in and the system discards is worse than a missing one.

THE HONESTY REQUIREMENT, which is why this is not a two-line patch. This project has already been
burned by silently ignoring a user's input — an uploaded logo was read, bounded, and dropped, and
the fix was to SAY so (`_IGNORED_UPLOAD_NOTE`). A product URL that cannot be fetched, or that
yields no usable product, must therefore reach the client as a stated reason, never as a poster
that quietly ignored the link they pasted.

SSRF: a URL from the browser is attacker-controlled. It goes through the same
`is_safe_public_url` gate every other user-supplied URL in this codebase does.

Hermetic: the scraper and the renderer are both stubbed at their seams.
"""
from __future__ import annotations

import pytest

import api.webapp_images as wi


class _Res:
    """Minimal stand-in for PosterGenResult."""
    image_base64 = None
    trust_violation = False
    trust_note_ar = ""


@pytest.fixture
def _brand():
    return {"domain_key": "shop_test", "organization_name": "Shop",
            "_profile": {"name": "Shop", "category": "ecommerce",
                         "source_url": "https://shop.test/"}}


@pytest.fixture
def _capture(monkeypatch):
    """Record the kwargs the poster pipeline is actually called with."""
    seen = {}

    def _fake_generate_poster(profile, **kw):
        seen.update(kw)
        return _Res()

    import poster.pipeline as pp
    monkeypatch.setattr(pp, "generate_poster", _fake_generate_poster)
    monkeypatch.setattr(wi, "paid_images_enabled", lambda: True)
    import business_profile.llm as llm
    monkeypatch.setattr(llm, "default_caller", lambda **k: object(), raising=False)
    return seen


def _product(**over):
    from scraper.product_page import ProductInfo
    base = {"name": "Vitamin C Serum", "image": "https://shop.test/img/serum.jpg",
            "url": "https://shop.test/p/serum", "description": "Brightening serum",
            "price": "450", "currency": "EGP"}
    base.update(over)
    return ProductInfo(**base)


# --- the link reaches the renderer --------------------------------------------------------------

def test_a_product_link_puts_that_product_in_the_poster(monkeypatch, _brand, _capture):
    monkeypatch.setattr(wi, "scrape_product_page", lambda u: _product(), raising=False)
    wi.generate_image(_brand, headline="عرض", product_url="https://shop.test/p/serum")
    assert _capture.get("product_name") == "Vitamin C Serum", _capture
    assert _capture.get("product_image") == "https://shop.test/img/serum.jpg", _capture


def test_no_link_leaves_the_normal_path_untouched(monkeypatch, _brand, _capture):
    """The ordinary poster must not change shape because a new parameter exists."""
    wi.generate_image(_brand, headline="عرض")
    assert not _capture.get("product_name")
    assert not _capture.get("product_image")


def test_the_scraped_product_facts_are_reported_back(monkeypatch, _brand, _capture):
    """The client pasted a link; they should see WHAT we read from it, not just a picture."""
    monkeypatch.setattr(wi, "scrape_product_page", lambda u: _product(), raising=False)
    out = wi.generate_image(_brand, headline="عرض", product_url="https://shop.test/p/serum")
    prod = out.get("product") or {}
    assert prod.get("name") == "Vitamin C Serum"
    assert prod.get("price") == "450"


# --- a link that does not work must SAY so -------------------------------------------------------

def test_an_unreachable_product_link_is_disclosed_not_swallowed(monkeypatch, _brand, _capture):
    monkeypatch.setattr(wi, "scrape_product_page", lambda u: None, raising=False)
    out = wi.generate_image(_brand, headline="عرض", product_url="https://shop.test/p/gone")
    assert out.get("product_note"), "the link was ignored in silence"
    assert not _capture.get("product_name")


def test_a_product_page_with_nothing_usable_is_disclosed(monkeypatch, _brand, _capture):
    monkeypatch.setattr(wi, "scrape_product_page",
                        lambda u: _product(name="", image=""), raising=False)
    out = wi.generate_image(_brand, headline="عرض", product_url="https://shop.test/p/empty")
    assert out.get("product_note")


def test_a_scraper_crash_never_breaks_the_render(monkeypatch, _brand, _capture):
    def _boom(_u):
        raise RuntimeError("playwright died")
    monkeypatch.setattr(wi, "scrape_product_page", _boom, raising=False)
    out = wi.generate_image(_brand, headline="عرض", product_url="https://shop.test/p/x")
    assert out.get("image_b64"), "a bad product link killed the whole image"
    assert out.get("product_note")


# --- SSRF ------------------------------------------------------------------------------------

def test_a_non_public_url_is_refused_before_any_fetch(monkeypatch, _brand, _capture):
    called = {"n": 0}

    def _spy(_u):
        called["n"] += 1
        return _product()
    monkeypatch.setattr(wi, "scrape_product_page", _spy, raising=False)
    for bad in ("http://127.0.0.1/admin", "file:///etc/passwd",
                "http://169.254.169.254/latest/meta-data/"):
        out = wi.generate_image(_brand, headline="x", product_url=bad)
        assert out.get("product_note"), bad
    assert called["n"] == 0, "a private/loopback URL was fetched"


# --- the route accepts it ------------------------------------------------------------------------

def test_the_generate_route_accepts_a_product_url():
    import inspect
    import api.routes.generate as g
    assert "product_url" in inspect.getsource(g), "no way for the client to send the link"


def test_the_frontend_has_a_field_for_it():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "product_url" in html or "productUrl" in html, "the field exists nowhere on screen"
