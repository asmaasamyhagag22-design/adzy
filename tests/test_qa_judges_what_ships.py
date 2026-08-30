# -*- coding: utf-8 -*-
"""Scene QA must judge the frame that SHIPS — after the real product is composited onto it.

MEASURED across two live Nano Care renders (2026-07-27), the same scene, two verdicts:

  before the composite guard:  "The product box is unfaithful, with the brand name and other
                                details replaced by garbled, illegible text."
  after the composite guard:   "The advertised product, a red and white box, is completely
                                absent from all clip frames."

Both refusals were correct about the clip they were shown, and both were wrong about the film.
The guard tells the generator to leave the product's place EMPTY precisely so the real packshot
can be pasted there — so a raw clip with no product is the INTENDED state, and QA judging it
before the paste can only ever refuse.

The compositor ran QA at the top of the per-scene block and `composite_product_screen` forty
lines below it. Order is the whole defect: judge after the paste and the gate sees what the viewer
sees — which also means a BAD composite gets caught, where before it was never examined at all.
"""
from __future__ import annotations

import inspect
import re


def _qa_and_composite_positions() -> tuple:
    import reel.compositor as rc
    src = inspect.getsource(rc)
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    qa = code.find("check_scene(")
    comp = code.find("composite_product_screen(")
    return qa, comp, code


def test_the_product_is_composited_before_the_scene_is_judged():
    qa, comp, code = _qa_and_composite_positions()
    assert comp != -1, "the compositor never pastes a real product"
    assert qa != -1, "the compositor never runs scene QA"
    assert comp < qa, (
        "scene QA runs BEFORE the real product is composited, so it judges a clip the guard "
        "deliberately emptied — 'the advertised product is completely absent from all clip frames'")


def test_the_retry_also_recomposites():
    """A regenerated clip is a fresh empty plate; judging it without pasting repeats the refusal."""
    _qa, _comp, code = _qa_and_composite_positions()
    m = re.search(r"regenerating once(.{0,1800})", code, re.S)
    assert m, "the retry block moved"
    assert "composite_product_screen" in m.group(1) or "_paste_product" in m.group(1), (
        "the retry re-generates and re-judges without pasting the product again")


def test_a_scene_with_no_real_asset_is_unaffected():
    """Brands with no packshot must keep judging the raw clip exactly as before."""
    from reel.product_overlay import composite_product_screen
    import pathlib
    missing = pathlib.Path("does-not-exist.mp4")
    # returns the original path untouched rather than raising — the no-asset path stays a no-op
    assert composite_product_screen(missing, missing, missing) == missing
