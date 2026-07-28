# -*- coding: utf-8 -*-
"""A composited product must read as STANDING, not hovering.

MEASURED on a live Nano Care render (2026-07-27). The critic rejected it three times with
"the product boxes are illogically floating over a generic stock photo", and the geometry says why:

    DEFAULT_ZONE = (0.52, 0.34, 0.44)
      height 44% of the canvas  -> on a 2700 px poster the box is ~1190 px, the size of the
                                   park bench beside it
      top at 34%, so the BASE lands at 34 + 44 = 78% of the frame — a fifth of the way up from
      the bottom, with nothing under it

The contact shadow was already there — the code even quotes the earlier note «العبوة طايرة مش
واقفة على حاجة» — but a shadow cannot rescue a base drawn in mid-air. The zone was expressed as a
TOP edge plus a height, so where the product's feet ended up was arithmetic nobody looked at.

So the base is what gets specified, and it is pinned here: a hero product's feet belong in the
lower band of the frame, where a generated scene puts its floor.

UPDATE (2026-07-28). The constant this file used to read is gone. The owner's ruling — «مينفعش
يكون مكان ثابت — لازم نفهم المنتج وعلى أساسه نقرر مساحته وحجمه» — moved the answer from a
module-level tuple to a per-product DECISION, so both guarantees are now asserted where they
actually live: on what `decide_placement` returns, for every product it can be handed.
"""
from __future__ import annotations


def _geo(**kw):
    from poster.product_geometry import ProductGeometry
    base = dict(camera_elevation="eye_level", has_flat_base=True, form_factor="carton",
                real_scale_hint="two_hands", aspect=0.8, checked=True)
    base.update(kw)
    return ProductGeometry(**base)


def test_a_standing_product_puts_its_base_in_the_lower_band():
    from poster.product_placement import MODE_STANDING, decide_placement
    d = decide_placement(_geo(), text_box=[0.06, 0.07, 0.88])   # copy up top, floor free below
    assert d.mode == MODE_STANDING
    base = d.zone[1] + d.zone[2]
    assert 0.78 < base <= 0.99, (
        f"the product's base lands at {base:.0%} of the frame — a hero product's feet belong in "
        "the lower band, where the scene's floor is")


def test_no_product_is_ever_bench_sized():
    """The 44% defect, pinned at the ceiling every decision passes through."""
    from poster.product_placement import MAX_HEIGHT_FRAC, decide_placement
    assert MAX_HEIGHT_FRAC <= 0.38, (
        f"the ceiling allows {MAX_HEIGHT_FRAC:.0%} of the canvas height; at 44% the product read "
        "as the same size as the furniture next to it")
    for form in ("carton", "bottle", "apparel", "device", "other"):
        for hint in ("palm", "two_hands", "large", "unknown"):
            d = decide_placement(_geo(form_factor=form, real_scale_hint=hint),
                                 text_box=[0.06, 0.07, 0.88])
            assert d.height_frac <= MAX_HEIGHT_FRAC, f"{form}/{hint} -> {d.height_frac:.0%}"


def test_the_background_is_told_to_provide_a_surface_low_in_frame():
    """The paste can only land on a floor if one was asked for, in the band it will land in."""
    from poster.pipeline import _product_scene_guard
    block = _product_scene_guard("نانوكير ليبوسومال حديد", composited=True).lower()
    assert "surface" in block
    assert "lower" in block or "bottom" in block, (
        "nothing tells the model WHERE the surface has to be, so it puts the floor wherever it "
        "likes and the paste lands above it")


def test_a_pasted_product_actually_sits_low(tmp_path):
    """End to end on pixels: the opaque product must occupy the lower half, not the middle."""
    from PIL import Image

    from poster.product_composite import composite_product

    bg = tmp_path / "bg.png"
    Image.new("RGB", (800, 1000), (140, 150, 160)).save(bg)
    # a red block on pure white — keys out cleanly
    asset = Image.new("RGB", (300, 400), (255, 255, 255))
    for x in range(60, 240):
        for y in range(40, 360):
            asset.putpixel((x, y), (200, 30, 40))
    ap = tmp_path / "prod.png"
    asset.save(ap)

    out = composite_product(bg, str(ap), out_path=str(tmp_path / "out.png"))
    assert out, "nothing was composited"

    im = Image.open(out).convert("RGB")
    W, H = im.size
    reds = [(x, y) for y in range(H) for x in range(0, W, 3)
            if im.getpixel((x, y))[0] > 150 and im.getpixel((x, y))[1] < 90]
    assert reds, "the product is not on the canvas"
    lowest = max(y for _x, y in reds)
    assert lowest / H > 0.72, (
        f"the product's lowest pixel is at {lowest / H:.0%} of the frame — it is floating")
