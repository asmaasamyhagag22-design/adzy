# -*- coding: utf-8 -*-
"""The Studio must obey the choices the owner already made.

OWNER, looking at the Al Fouad poster the Studio produced (2026-07-26):
  «انا عايزة نفس الوش يظهر في البوستر» — she had just picked a face from three candidates.
  «انا اخترت لاروش، جايب منتجات تانية ليه» — she pasted the La Roche product link.
  «انا ما اخترتش للبراند كله» — a product link means a PRODUCT ad, not a brand ad.
  «مكتوب كريم، خالص هو ماسك سبراي» — the copy named a category the product does not belong to.

MEASURED on that render (poster_a38e8ecf.png + the server log):
  * `api/webapp_images.py` contains NO reference to `identity_image` — the face flow ends at the
    influencer's own poster endpoint, so the Studio's normal render never sees the chosen person.
    The picker was a real feature that stopped one call short of the screen.
  * the log reads `[oneshot] 2 real prop photo(s) attached` and the QA verdict names a THIRD-PARTY
    brand ("Bobai") — the props came from the brand's generic photo pool while the requested
    product was ignored, so an Al Fouad ad advertised somebody else's sunscreen.
  * the headline «الكريم بتاعك خلص فجأة؟» was written brand-wide; nothing tied it to the item.

The law: when the owner has chosen a face, that face is in the frame. When she pasted a product
link, THAT product is the subject — its real name and its real photo — and the copy may not
rename its category. A choice the product cannot honour must be SAID, never silently dropped.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def _brand_with_face(tmp_path):
    face = tmp_path / "face.png"
    face.write_bytes(b"\x89PNG\r\n\x1a\nFACE")
    return {
        "domain_key": "shop_test",
        "organization_name": "Shop",
        "ai_influencer": {"selected_id": "abc123",
                          "candidates": {"abc123": {"url": "/api/generated/face.png"}}},
        "_profile": {"name": {"value": "Shop"}, "category": "ecommerce",
                     "source_url": {"value": "https://shop.test/"}},
    }


@pytest.fixture
def _capture(monkeypatch):
    seen = {}

    class _Res:
        image_base64 = None
        trust_violation = False
        trust_note_ar = ""
        qa = None

    import poster.pipeline as pp
    monkeypatch.setattr(pp, "generate_poster", lambda profile, **kw: (seen.update(kw), _Res())[1])
    import api.webapp_images as wi
    monkeypatch.setattr(wi, "paid_images_enabled", lambda: True)
    return seen


def test_the_chosen_face_reaches_the_studio_render(monkeypatch, _brand_with_face, _capture):
    import api.webapp_images as wi
    monkeypatch.setattr(wi, "selected_face_bytes",
                        lambda brand: (b"FACEBYTES", "image/png"), raising=False)
    wi.generate_image(_brand_with_face, headline="h")
    ident = _capture.get("identity_image")
    assert ident and ident[0] == b"FACEBYTES", (
        "the face the owner picked never reached generate_poster — the picker stops one call "
        f"short of the render: {_capture.keys()}")


def test_no_selected_face_means_no_identity_and_no_crash(monkeypatch, _capture):
    import api.webapp_images as wi
    monkeypatch.setattr(wi, "selected_face_bytes", lambda brand: (None, ""), raising=False)
    brand = {"domain_key": "x", "organization_name": "X",
             "_profile": {"name": {"value": "X"}, "source_url": {"value": "https://x.test/"}}}
    wi.generate_image(brand, headline="h")
    assert not _capture.get("identity_image")


def test_a_product_link_makes_it_a_product_ad_with_that_product(monkeypatch, _capture):
    """«اخترت لاروش جايب منتجات تانية ليه» — the pasted item must be the subject, and the
    brand's generic prop pool must not overwrite it."""
    import api.webapp_images as wi

    from scraper.product_page import ProductInfo
    _P = ProductInfo(
        name="La Roche-Posay Anthelios Anti-Dark Spot Fluid SPF50+ 50ml",
        image="https://cdn.test/laroche.jpg", url="https://x.test/products/la-roche",
        description="Anti-dark-spot sun fluid.", price="1100", currency="EGP")

    monkeypatch.setattr(wi, "product_from_link", lambda url: (_P, ""))
    brand = {"domain_key": "x", "organization_name": "X",
             "_profile": {"name": {"value": "X"}, "source_url": {"value": "https://x.test/"}}}
    wi.generate_image(brand, headline="", product_url="https://x.test/products/la-roche")
    assert _capture.get("product_name") == _P.name, (
        f"the pasted product never became the subject: {_capture.get('product_name')!r}")
    assert _capture.get("product_image"), "the product's own photo was not passed to the render"


def test_an_unreachable_product_link_is_reported_not_silently_swapped(monkeypatch, _capture):
    """A link that cannot be read must NOT quietly become a brand-wide ad about other items —
    that is exactly what produced a Bobai poster for an Al Fouad request."""
    import api.webapp_images as wi
    monkeypatch.setattr(wi, "product_from_link",
                        lambda url: (None, "مقدرناش نفتح صفحة المنتج دي"))
    brand = {"domain_key": "x", "organization_name": "X",
             "_profile": {"name": {"value": "X"}, "source_url": {"value": "https://x.test/"}}}
    out = wi.generate_image(brand, headline="", product_url="https://x.test/products/nope")
    assert out.get("product_note"), "the failed link was swallowed"
    assert not _capture.get("product_name")


def test_the_route_passes_the_product_link_through():
    import inspect

    import api.routes.generate as g
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(g).splitlines())
    assert "product_url" in src
