# -*- coding: utf-8 -*-
"""When the owner pastes a product link, THAT product is the one in the ad.

OWNER, 2026-08-08, pasting a Vichy Dercos spray URL: «يلا نعمل للمنتج دا».

MEASURED before the fix, on her real link and the real stored catalogue of
alfouadpharmacies.com (24 items):

    product_candidates(profile)                       -> Bioderma_Pigmentbio_Night_Renewer…
    product_candidates(profile, hero_url=<the spray>)  -> Bioderma_Pigmentbio_Night_Renewer…
                                                          IDENTICAL. The link changed nothing.

Two separate defects stacked, and each one alone was enough to ship the wrong product:

1. THE LINK NEVER TRAVELLED. `product_candidates` took a `hero_url`, but not one caller passed
   one — the parameter existed, was documented, and was dead. Same class as this repo's recurring
   "produced, serialized, never read": a correct mechanism nobody wired.

2. NOT IN THE CATALOGUE WAS READ AS NOT AVAILABLE. Her link is the ARABIC storefront
   (`/products/<arabic-slug>`); the catalogue was crawled from the English one
   (`/en/products/<latin-slug>`). Same shop, same item, slugs that can never match. The matcher
   correctly found nothing and then FELL BACK SILENTLY to catalogue order — a night cream leading
   a hair-spray ad, with a log line nobody reads as the only trace. That is "absence read as
   success" exactly.

The fix for (2) is not a better matcher — no slug comparison can bridge Arabic and Latin. It is
that an unmatched hero link gets FETCHED, which is what the poster lane has always done
(`webapp_images.product_from_link`). One brand, one pasted URL, two lanes that disagreed.

These tests are offline: the fetch is stubbed, because what is under test is the WIRING, and the
wiring is where every one of these defects lived.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from reel.insert_shot import product_candidates


@pytest.fixture(autouse=True)
def _no_cached_hero():
    """The resolver memoises by URL so the insert and the director resolve one link ONCE. These
    tests all use the same URL with different stubs, so each starts from cold."""
    from reel.insert_shot import _HERO_CACHE
    _HERO_CACHE.clear()
    yield
    _HERO_CACHE.clear()

_SPRAY = ("https://alfouadpharmacies.com/products/"
          "%D8%B3%D8%A8%D8%B1%D8%A7%D9%8A-%D9%81%D9%8A%D8%B4%D9%8A-100-%D9%85%D9%84")
_SHOT = "https://cdn.example.com/files/vichy_dercos_densi_solutions_spray.webp?width=1200"


def _profile(hero_in_catalog: bool = False):
    """A catalogue whose slugs cannot match an Arabic URL — the production shape."""
    items = [
        {"name": "Bioderma Pigmentbio Night Renewer",
         "url": "https://alfouadpharmacies.com/en/products/bioderma-pigmentbio",
         "image_url": "https://cdn.example.com/files/Bioderma_Pigmentbio_Night_Renewer.webp",
         "price_text": "EGP 1450"},
        {"name": "Uriage Hyseac Spot Control",
         "url": "https://alfouadpharmacies.com/en/products/uriage-hyseac",
         "image_url": "https://cdn.example.com/files/uriage_hyseac.webp", "price_text": "EGP 680"},
    ]
    if hero_in_catalog:
        items.append({"name": "Vichy Dercos Densi-Solutions",
                      "url": _SPRAY,
                      "image_url": _SHOT, "price_text": "EGP 2380"})
    return {"business_name": "Al Fouad Pharmacies", "catalog": items,
            "visual": {"content_images": []}}


def test_a_catalogued_hero_link_leads_without_any_network_call(monkeypatch):
    """The cheap path: the link IS in the catalogue, so nothing is fetched."""
    import api.webapp_images as wi

    def _boom(*_a, **_k):  # pragma: no cover — reached only if the cheap path regressed
        raise AssertionError("a catalogued hero link must not trigger a page fetch")

    monkeypatch.setattr(wi, "product_from_link", _boom)
    got = product_candidates(_profile(hero_in_catalog=True), hero_url=_SPRAY)
    assert got and got[0] == _SHOT, f"the pasted product must lead; got {got[:1]}"


def test_an_uncatalogued_hero_link_is_fetched_rather_than_silently_dropped(monkeypatch):
    """THE REGRESSION THIS FILE EXISTS FOR. Arabic URL, Latin catalogue: zero slug overlap.

    Before the fix this returned the Bioderma cream and logged a line. The ad would have been a
    hair spray narrated over a night cream, and every gate would have passed it — the insert had
    a real product photo of a real product of the right brand.
    """
    import types

    import api.webapp_images as wi

    calls: list = []

    def _fetched(url, *_a, **_k):
        calls.append(url)
        return types.SimpleNamespace(
            name="Vichy Dercos Densi-Solutions Hair Mass Creator - 100ml",
            image=_SHOT, images=[_SHOT], price="EGP 2380"), ""

    monkeypatch.setattr(wi, "product_from_link", _fetched)
    got = product_candidates(_profile(), hero_url=_SPRAY)
    assert calls, "an uncatalogued hero link must be fetched, not dropped for catalogue order"
    assert got and got[0] == _SHOT, (
        f"the fetched packshot must lead the insert; got {got[:1]} — "
        "this is the wrong-product bug back")


def test_a_hero_link_that_cannot_be_fetched_still_leaves_a_usable_insert(monkeypatch):
    """A dead link must cost the HERO, never the reel: the catalogue still leads."""
    import api.webapp_images as wi

    monkeypatch.setattr(wi, "product_from_link",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("404")))
    got = product_candidates(_profile(), hero_url=_SPRAY)
    assert got, "a broken hero link must not empty the candidate list"
    assert "Bioderma" in got[0], "the catalogue should have carried the insert"


def test_no_caller_of_the_reel_engine_drops_the_hero_link_on_the_floor():
    """The parameter was dead for as long as it existed. Every entry point that can receive a
    product URL must hand it down — checked on the SOURCE, with comments stripped, because a
    comment promising the thread is what made the dead parameter look wired."""
    import api.routes.reel as web
    import reel.__main__ as cli
    import reel.creative as creative

    for mod, needle in ((creative, "hero_product_url"),
                        (web, "hero_product_url"),
                        (cli, "hero_product_url")):
        src = inspect.getsource(mod)
        stripped = ast.unparse(ast.parse(src))  # drops comments; keeps docstrings
        body = "\n".join(ln for ln in stripped.splitlines()
                         if not ln.strip().startswith(("'", '"')))
        assert needle in body, (
            f"{mod.__name__} no longer carries {needle} in code — the pasted product link "
            "is being dropped somewhere on the way to the insert")

    # And the engine must actually USE it, not merely accept it.
    sig = inspect.signature(creative.render_creative_reel)
    assert "hero_product_url" in sig.parameters
    inner = ast.unparse(ast.parse(inspect.getsource(creative.build_creative_storyboard)))
    assert "hero_url" in inner, (
        "build_creative_storyboard accepts the hero link but never passes it to the selector")


def test_the_pasted_link_also_tells_the_director_what_the_film_is_about(monkeypatch):
    """THE HALF-WIRED STATE THIS CATCHES: the insert cut to the right bottle while the director
    was briefed on nothing, so the voice never named the product and no scene asked for the one
    sound the picture is about. The packshot and the script must come from the same resolution."""
    import types

    import api.webapp_images as wi
    import reel.creative as creative

    monkeypatch.setattr(wi, "product_from_link", lambda *_a, **_k: (types.SimpleNamespace(
        name="Vichy Dercos Densi-Solutions Hair Mass Creator - 100ml",
        image=_SHOT, images=[_SHOT], price="EGP 2380"), ""))

    seen: dict = {}

    def _planned(profile, photos, **kw):
        seen.update(kw)
        raise RuntimeError("stop here — the brief is all this test needs")

    import reel.coherence as coh
    monkeypatch.setattr(coh, "plan_reel_gated", _planned)
    try:
        creative.render_creative_reel(_profile(), None, [], provider=None,
                                      out_path="x.mp4", hero_product_url=_SPRAY)
    except Exception:  # noqa: BLE001 — the render is not what is under test
        pass
    assert "Vichy" in str(seen.get("featured_product") or ""), (
        f"the director was briefed with {seen.get('featured_product')!r} — the pasted product "
        "reached the insert but not the script")


def test_an_explicit_product_name_still_wins_over_the_link(monkeypatch):
    """A caller who typed a name meant it; the link only fills a gap."""
    import types

    import api.webapp_images as wi
    import reel.creative as creative

    monkeypatch.setattr(wi, "product_from_link", lambda *_a, **_k: (types.SimpleNamespace(
        name="Vichy Dercos", image=_SHOT, images=[_SHOT], price=""), ""))
    seen: dict = {}

    def _planned(profile, photos, **kw):
        seen.update(kw)
        raise RuntimeError("stop")

    import reel.coherence as coh
    monkeypatch.setattr(coh, "plan_reel_gated", _planned)
    try:
        creative.render_creative_reel(_profile(), None, [], provider=None, out_path="x.mp4",
                                      hero_product_url=_SPRAY, featured_product="اسم اختاره العميل")
    except Exception:  # noqa: BLE001
        pass
    assert seen.get("featured_product") == "اسم اختاره العميل"
