# -*- coding: utf-8 -*-
"""The reel's product is COMPOSITED, like the poster's — a video model must not redraw a label.

MEASURED on the live Nano Care render (2026-07-27). Scene 0's QA verdict, verbatim:

    "The product box is unfaithful, with the brand name and other details replaced by garbled,
     illegible text."

The video model animated the seed and repainted the packaging as it moved, so a real box became gibberish.
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


def test_a_physical_product_is_now_GENERATED_not_pasted():
    """REVERSED 2026-08-01, on the owner's production rule and a live measurement — and the
    reversal is recorded rather than the old test deleted, because the reasoning is the point.

    The rule: «لو بتعملي ريل فيديو، اعتمدي على التوليد بدون أي قلق، لأن حركة الإيد والإضاءة
    هتبطّل العجب… لكن لو بتطلعي بوستر ثابت عالي الدقة، ارجعي لطريقة القص واللزق». A poster is
    zoomed into and read; a two-second clip is not.

    The measurement, from a live Veo run. Scene 0's QA verdict: "The product is not generated
    within the video scene; it is a STATIC IMAGE CRUDELY OVERLAID on top of unrelated footage."
    That scene burned THREE Veo renders at $1.60 each failing on the paste, fell back to Ken
    Burns anyway, and the wasted spend exhausted the per-run budget before three later scenes got
    a clip at all. Meanwhile the frames that came back BEST in the same run were the ones the
    model drew.

    The garbled-label fear this file was written about is now caught EARLIER and cheaper: the
    seed-QA gate rejects an unreadable pack before any Veo spend (measured in the same run,
    repeatedly: "the text on the product packaging is garbled and nonsensical")."""
    from reel.creative import _product_asset
    for overlay in ("", "real_photo", "whatever"):
        got = _product_asset({}, _scene(hero_product=True, product_overlay=overlay),
                             "https://x.test/box.jpg")
        assert got is None, f"a physical product is still pasted into video ({overlay!r})"


def test_a_real_screen_is_still_pasted():
    """Not an exception to the rule but an application of it: an app UI is dense fine print that
    IS the product, motion does not hide it, and no video model renders legible UI."""
    from reel.creative import _product_asset
    prof = {"visual": {"homepage_screenshot_path": "/tmp/shot.png"}}
    assert _product_asset(prof, _scene(hero_product=True, product_overlay="real_screen"),
                          "https://x.test/box.jpg") == "/tmp/shot.png"


def test_a_non_product_scene_composites_nothing():
    from reel.creative import _product_asset
    assert _product_asset({}, _scene(), "https://x.test/box.jpg") is None


def test_the_poster_still_pastes():
    """The other half of the same rule. A static poster IS zoomed into, so its label stays the
    real file — nothing here may leak into that lane."""
    import inspect

    import poster.pipeline as P
    assert "compose_or_background" in inspect.getsource(P)


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
