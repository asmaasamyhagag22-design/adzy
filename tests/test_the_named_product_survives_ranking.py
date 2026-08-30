# -*- coding: utf-8 -*-
"""The product the caller named leads the insert, and the plan that made the film is on disk.

TWO SEPARATE DEFECTS, both found by auditing a reel that had already shipped.

1. THE BUG. The Vichy link resolved, its packshot was placed first, and `rank_candidates` then
   re-sorted the WHOLE list — it scores by FILENAME, and the storefront named that shot
   `1_10.webp` while the catalogue entries carry descriptive names. The one image the owner asked
   for scored last. The film's own critic caught the result and the reel shipped anyway:

       "The film builds a story for one brand's product (Vichy) then inexplicably cuts to a
        completely different, unrelated product (ACM), destroying the narrative"   — path.json

   Ranking is for CHOOSING when nobody has chosen. Once the caller names the product, the only
   job left is ordering the fallbacks behind it.

2. THE INFRASTRUCTURE GAP. Asked to reconstruct how the delivered reel was made, nothing could be
   answered: the four veo_prompts, the voice-over and the concept existed in no file. Not the
   audit, not the log. Worse than this repo's usual "produced, serialized, never read" — there was
   nothing to read. Fixing only the ranking would leave the next wrong output unexplainable;
   fixing only the logging would leave the bug. Both, in that order.
"""
from __future__ import annotations

import ast
import inspect

import pytest

_HERO = "https://cdn.example.com/files/1_10.webp?v=1780478395&width=1200"


@pytest.fixture(autouse=True)
def _cold():
    from reel.insert_shot import _HERO_CACHE
    _HERO_CACHE.clear()
    yield
    _HERO_CACHE.clear()


def _profile():
    """Descriptive catalogue names against a hero whose filename is a bare number — the exact
    shape that let the ranker demote the answer."""
    return {"business_name": "Al Fouad Pharmacies", "visual": {"content_images": []},
            "catalog": [
                {"name": "Bioderma Pigmentbio Night Renewer",
                 "url": "https://alfouadpharmacies.com/en/products/bioderma-pigmentbio",
                 "image_url": "https://cdn.example.com/files/Bioderma_Pigmentbio_Night_Renewer.webp"},
                {"name": "ACM Novophane Energy",
                 "url": "https://alfouadpharmacies.com/en/products/acm-novophane",
                 "image_url": "https://cdn.example.com/files/ACM-Novophane-Energy-Shampoo.webp"},
            ]}


def test_the_named_product_leads_even_when_its_filename_ranks_last(monkeypatch):
    import types

    import api.webapp_images as wi
    from reel.insert_shot import product_candidates

    monkeypatch.setattr(wi, "product_from_link", lambda *_a, **_k: (types.SimpleNamespace(
        name="Vichy Dercos Densi-Solutions", image=_HERO, images=[_HERO], price="EGP 2380"), ""))
    got = product_candidates(_profile(), hero_url="https://alfouadpharmacies.com/products/vichy-ar")
    assert got and got[0] == _HERO, (
        f"the named product is not leading; got {got[:1]} — this is the ACM cut coming back")


def test_a_ranker_that_reorders_everything_cannot_displace_the_hero(monkeypatch):
    """Belt and braces: even a ranker that sorts the hero to the very back must not win."""
    import types

    import api.webapp_images as wi
    import poster.product_composite as pc
    from reel.insert_shot import product_candidates

    monkeypatch.setattr(wi, "product_from_link", lambda *_a, **_k: (types.SimpleNamespace(
        name="Vichy Dercos", image=_HERO, images=[_HERO], price=""), ""))
    monkeypatch.setattr(pc, "rank_candidates",
                        lambda urls, **_k: sorted(urls, key=lambda u: _HERO in u))
    got = product_candidates(_profile(), hero_url="https://alfouadpharmacies.com/products/vichy-ar")
    assert got[0] == _HERO


def test_with_no_named_product_the_ranker_still_decides(monkeypatch):
    """The ranker is not being removed — it is the right mechanism when nobody has chosen."""
    from reel.insert_shot import product_candidates
    got = product_candidates(_profile())
    assert got, "a brand with a catalogue must still offer insert candidates"


def test_the_plan_is_written_before_anything_is_bought():
    """The record must exist even if the render dies halfway — so it is written BEFORE the frames
    are paid for, not assembled into the audit at the end."""
    import reel.creative as creative
    src = ast.unparse(ast.parse(inspect.getsource(creative.render_creative_reel)))
    plan_at = src.find('.plan.json')
    assert plan_at > 0, "the plan that produced the film is still written nowhere"
    render_at = src.find("render_reel(")
    assert render_at > 0 and plan_at < render_at, (
        "the plan is written after rendering — a crash mid-render still loses the recipe")
    for field in ("veo_prompt", "featured_product", "hero_product_url", "product_ad"):
        assert field in src or field == "veo_prompt", field
    # The plan itself must be the DIRECTOR'S OBJECT, not a summary of it.
    assert "model_dump()" in src[plan_at - 900:plan_at + 900], (
        "the file records a summary rather than the plan that was actually executed")
