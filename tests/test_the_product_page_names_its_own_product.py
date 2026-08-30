"""A product page's own <h1> must reach the catalog — as an h1, with its own photo.

MEASURED 2026-08-02 on a live Al Fouad product page, and the two halves compound:

  1. The page has a visible <h1> reading "Eucerin Dermo-Purifyer Triple Serum 40ml". The DOM
     walker returned 174 blocks and ZERO h1. The theme wraps it as <div><h1>name</h1></div>; the
     walker runs in document order, so the WRAPPER was recorded first and the walker's
     "same text as the last block -> skip" rule dropped the semantic child. The name survived
     only as `tag=div`, and `catalog._name_from_page` reads h1/h2/h3/title. It fell through to a
     "Frequently Bought Together" carousel heading — 24 stored items named after the WRONG
     product, at the WRONG price.

  2. `image_url` was None for all 24. `catalog.build_catalog` read `getattr(img, "url", "")`;
     `ImageOfInterest` has no `url` — the field is `src`. The getattr default turned a wrong
     attribute name into "this product has no image", on every product, on every site.

Both defects are silent by construction: neither raised, and both produced a plausible answer.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from scraper.catalog import _same_host_https, build_catalog
from scraper.schemas import ImageOfInterest, ImageRole

PROD = "https://shop.example/en/products/eucerin-dermo-purifyer-triple-serum-40ml"
NAME = "Eucerin Dermo-Purifyer Triple Serum 40ml"


def _img(role, src, page_url=PROD):
    return ImageOfInterest(role=role, src=src, page_url=page_url)


# --- the walker keeps the tag that carries meaning -------------------------------------

def _blocks_for(html: str, url: str):
    """Run the REAL DOM walker over `html`. Skips when playwright is stubbed (conftest) or no
    browser is installed — a stub cannot execute the JS, and asserting against a stub would be
    testing the stub."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - import guard
        pytest.skip("playwright unavailable")
    if not hasattr(sync_playwright, "__call__") or sync_playwright.__module__ == "playwright.sync_api" \
            and type(sync_playwright).__name__ == "type":
        pytest.skip("playwright is stubbed in this run")
    from scraper.extractors.text_blocks import extract_text_blocks
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            try:
                pg = b.new_page(viewport={"width": 1366, "height": 900})
                pg.set_content(html)
                return extract_text_blocks(pg, url)
            finally:
                b.close()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"no usable browser: {type(exc).__name__}")


def test_a_wrapped_heading_is_still_a_heading():
    html = f"""<html><body><main>
        <div class="product-title"><h1>{NAME}</h1></div>
        <h3>Frequently Bought Together</h3>
    </main></body></html>"""
    blocks = _blocks_for(html, PROD)
    tags = {b.tag for b in blocks if b.text.strip() == NAME}
    assert "h1" in tags, f"the wrapper swallowed the h1; recorded as {tags}"


def test_a_wrapped_heading_does_not_double_count():
    """The upgrade must REPLACE the wrapper record, not add a second one — the dedup exists for a
    reason and this fix must not quietly undo it."""
    html = f'<html><body><main><div><h1>{NAME}</h1></div></main></body></html>'
    blocks = _blocks_for(html, PROD)
    assert sum(1 for b in blocks if b.text.strip() == NAME) == 1


def test_a_span_inside_a_heading_does_not_demote_it():
    """One-directional on purpose: <h1><span>x</span></h1> keeps the h1."""
    html = f'<html><body><main><h1><span>{NAME}</span></h1></main></body></html>'
    blocks = _blocks_for(html, PROD)
    tags = {b.tag for b in blocks if b.text.strip() == NAME}
    assert tags == {"h1"}


def test_the_catalog_reads_the_heading_the_walker_kept():
    """The seam. `_name_from_page` scores headings against the URL slug, so the correct name wins
    6/6 and the carousel heading 4/6 — but only if the correct one is tagged as a heading at all."""
    from scraper.catalog import _name_from_page
    page = SimpleNamespace(url=PROD, title="", text_blocks=[
        SimpleNamespace(tag="h3", text="Eucerin Dermo-Purifyer Triple Cleanser 150ml",
                        section=SimpleNamespace(value="main")),
        SimpleNamespace(tag="h1", text=NAME, section=SimpleNamespace(value="main")),
    ])
    assert _name_from_page(page) == NAME


# --- the image reaches the item --------------------------------------------------------

def test_the_image_is_read_from_the_field_that_exists():
    page = SimpleNamespace(url=PROD, title="", text_blocks=[
        SimpleNamespace(tag="h1", text=NAME, section=SimpleNamespace(value="main"))])
    manifest = SimpleNamespace(
        pages=[page],
        images_of_interest=[_img(ImageRole.CONTENT, "https://cdn.example/serum.webp")])
    (item,) = build_catalog(manifest)
    assert item["image_url"] == "https://cdn.example/serum.webp"


def test_the_store_logo_does_not_become_every_products_photo():
    """The logo's page_url IS the product page, and first-wins would have pinned it to every item
    — a catalog that is populated and wrong, worse than the empty one it replaced."""
    page = SimpleNamespace(url=PROD, title="", text_blocks=[
        SimpleNamespace(tag="h1", text=NAME, section=SimpleNamespace(value="main"))])
    manifest = SimpleNamespace(pages=[page], images_of_interest=[
        _img(ImageRole.LOGO, "https://cdn.example/Logo_colors.png"),
        _img(ImageRole.HERO, "https://cdn.example/Logo_colors.png"),
        _img(ImageRole.OG_IMAGE, "https://cdn.example/packshot.webp"),
        _img(ImageRole.CONTENT, "https://cdn.example/hand-holding-it.webp"),
    ])
    (item,) = build_catalog(manifest)
    # og:image leads: it is the page's own declaration, and on the live page the "hero" heuristic
    # returned the LOGO while `content` returned a lifestyle shot with a person in it.
    assert item["image_url"] == "https://cdn.example/packshot.webp"


def test_the_logo_is_a_fallback_not_a_failure():
    """A page whose ONLY image is the logo still gets one — ranked last, never dropped."""
    page = SimpleNamespace(url=PROD, title="", text_blocks=[
        SimpleNamespace(tag="h1", text=NAME, section=SimpleNamespace(value="main"))])
    manifest = SimpleNamespace(pages=[page], images_of_interest=[
        _img(ImageRole.LOGO, "https://cdn.example/Logo_colors.png")])
    (item,) = build_catalog(manifest)
    assert item["image_url"] == "https://cdn.example/Logo_colors.png"


# --- mixed content ---------------------------------------------------------------------

def test_an_http_image_on_an_https_page_is_upgraded():
    """Al Fouad's og:image tag says http:// while the page is served over https. Kept verbatim it
    is blocked as mixed content and the catalog shows broken images. Verified live: the same URL
    over https returns 200 with a byte-identical body."""
    assert _same_host_https("http://shop.example/cdn/x.webp", PROD) == "https://shop.example/cdn/x.webp"


def test_a_third_party_http_image_is_left_alone():
    """The only evidence used is that THIS host just served THIS page over https. A different host
    proved nothing, so guessing there would be inventing a fact about someone else's server."""
    assert _same_host_https("http://other.example/x.webp", PROD) == "http://other.example/x.webp"


def test_an_http_page_is_left_alone():
    assert _same_host_https("http://shop.example/x.webp",
                            "http://shop.example/p/1") == "http://shop.example/x.webp"
