# -*- coding: utf-8 -*-
"""The reel's product is COMPOSITED, like the poster's — a video model must not redraw a label.

MEASURED on the live Nano Care render (2026-07-27). Scene 0's QA verdict, verbatim:

    "The product box is unfaithful, with the brand name and other details replaced by garbled,
     illegible text."

Kling animated the seed and repainted the packaging as it moved, so a real box became gibberish.
The gate refused it — correctly — regenerated once, failed again, and dropped to the Ken Burns
fallback: a slow zoom on a still. Which is the exact slideshow the whole cheat-sheet exists to kill.

THE CURE IS THE POSTER'S, AND IT IS ALREADY HERE. `reel/product_overlay.py::composite_product_screen`
pastes the real asset onto the moving footage, and the compositor calls it whenever a scene carries
`product_asset_url`. But `_product_asset` only returns one when the DIRECTOR set BOTH `hero_product`
AND `product_overlay ∈ {real_screen, real_photo}` — two fields an LLM must remember. Nano Care's plan
did not, so the product went through the generator and came out garbled.

A cure that depends on a model remembering to opt in is not a cure. When a scene is about the
product and a real photograph of it exists, the photograph is used — and the generation prompt is
told to leave its place empty, or the frame ends up with a garbled invented box AND a pasted real
one.

"Prose loses to pixels", third time this session: do not ask a model to preserve a label. Put the
real pixels on top of it.
"""
from __future__ import annotations

import types


def _scene(**kw):
    s = types.SimpleNamespace(hero_product=False, product_overlay="", veo_prompt="x")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_a_hero_product_scene_uses_the_real_photo_even_with_no_overlay_mode_set():
    from reel.creative import _product_asset
    got = _product_asset({}, _scene(hero_product=True), "https://x.test/box.jpg")
    assert got == "https://x.test/box.jpg", (
        "the director forgot `product_overlay`, so the real packshot was not used and the label "
        "went through the video model")


def test_an_explicit_overlay_mode_still_wins():
    from reel.creative import _product_asset
    prof = {"visual": {"homepage_screenshot_path": "/tmp/shot.png"}}
    assert _product_asset(prof, _scene(hero_product=True, product_overlay="real_screen"),
                          "https://x.test/box.jpg") == "/tmp/shot.png"


def test_a_non_product_scene_composites_nothing():
    from reel.creative import _product_asset
    assert _product_asset({}, _scene(), "https://x.test/box.jpg") is None


def test_no_real_asset_means_no_composite():
    from reel.creative import _product_asset
    assert _product_asset({}, _scene(hero_product=True), None) is None


def test_the_prompt_tells_the_model_to_leave_the_products_place_empty():
    """Otherwise the frame carries a garbled invented box AND the pasted real one."""
    from reel.creative import _composited_product_guard
    g = _composited_product_guard(True).lower()
    assert "do not" in g and "product" in g
    assert "empty" in g or "leave" in g
    assert _composited_product_guard(False) == ""


def test_the_storyboard_applies_the_guard_to_a_composited_scene():
    import inspect

    import reel.creative as rc
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(rc).splitlines())
    assert "_composited_product_guard" in src.replace("def _composited_product_guard", "", 1), (
        "the guard is defined but never appended to a scene's visual prompt")
