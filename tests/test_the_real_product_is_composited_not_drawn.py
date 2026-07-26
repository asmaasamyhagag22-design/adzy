# -*- coding: utf-8 -*-
"""THE PRODUCT IS COMPOSITED, NEVER GENERATED.

The blocker in the reviewer's list (2026-07-26), and the only one that is a legal problem
rather than a taste problem: the model redrew a REAL, REGISTERED product and got its label
wrong.

    on the poster                          the truth
    LABORATONE DERHATOLOGIQUE              LABORATOIRE DERMATOLOGIQUE
    ANTFLICHES / AXTI-DARC SPOIS FLUID     ANTI-TACHES / ANTI-DARK SPOTS FLUID

That is not an aesthetic defect, it is a trademark distortion on an ad for a pharmacy that
sells the genuine article. No prompt wording fixes it, because the failure is asking a
generative model to reproduce text at all — measured across every attempt of every run this
week, in both engines, with the real photo attached as a reference.

So the rule becomes structural: the scene model renders the BACKGROUND and the PEOPLE; the
product arrives as its own real asset and is pasted in. The label is then pixel-identical by
construction — «عايزة المنتج يجي زي ما هو بالظبط».

Hermetic: no network, no model.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image


def _bg(tmp: Path, size=(1080, 1350), color=(210, 210, 214)) -> Path:
    p = tmp / "bg.png"
    Image.new("RGB", size, color).save(p)
    return p


def _product_png(tmp: Path, size=(400, 800)) -> bytes:
    """A product cut-out: opaque bottle on transparent ground."""
    import io
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    for y in range(60, size[1] - 60):
        for x in range(80, size[0] - 80):
            im.putpixel((x, y), (240, 240, 245, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_the_product_lands_on_the_background_unchanged(tmp_path):
    from poster.product_composite import composite_product

    bg = _bg(tmp_path)
    out = composite_product(bg, _product_png(tmp_path), zone=(0.55, 0.30, 0.40))
    assert out and Path(out).is_file()
    im = Image.open(out).convert("RGB")
    assert im.size == (1080, 1350), im.size
    # the product's own pixels are present, unaltered in hue
    px = im.getpixel((int(1080 * 0.75), int(1350 * 0.6)))
    assert px[0] > 200 and abs(px[0] - px[2]) < 20, px


def test_a_contact_shadow_is_drawn_so_it_is_not_floating(tmp_path):
    """The reviewer: «العبوة مش mounted — مفيش contact shadow، طايرة مش واقفة»."""
    from poster.product_composite import composite_product

    bg = _bg(tmp_path, color=(220, 220, 224))
    out = composite_product(bg, _product_png(tmp_path), zone=(0.55, 0.30, 0.40),
                            contact_shadow=True)
    im = Image.open(out).convert("L")
    w, h = im.size
    # a band just under the product must be darker than the untouched background
    under = [im.getpixel((x, int(h * 0.30 + h * 0.42))) for x in range(int(w * 0.6), int(w * 0.8), 7)]
    far = [im.getpixel((x, int(h * 0.05))) for x in range(int(w * 0.6), int(w * 0.8), 7)]
    assert min(under) < min(far) - 5, (min(under), min(far))


def test_a_flat_white_studio_shot_is_keyed_out(tmp_path):
    """Most store photos are a bottle on white, not a cut-out. Pasting the white rectangle is
    the sticker the owner has already rejected twice."""
    import io

    from poster.product_composite import composite_product
    im = Image.new("RGB", (300, 600), (255, 255, 255))
    for y in range(80, 520):
        for x in range(90, 210):
            im.putpixel((x, y), (30, 90, 200))
    buf = io.BytesIO()
    im.save(buf, format="PNG")

    bg = _bg(tmp_path, color=(40, 44, 52))          # dark ground: a white box would scream
    out = composite_product(bg, buf.getvalue(), zone=(0.55, 0.30, 0.40))
    res = Image.open(out).convert("RGB")
    # The ground is dark, the product is blue: ANY near-white area is the studio plate.
    white = sum(1 for px in res.getdata() if px[0] > 235 and px[1] > 235 and px[2] > 235)
    assert white < 500, f"the studio white was pasted as a plate ({white} near-white px)"


def test_an_unusable_asset_returns_none_rather_than_a_wrong_poster(tmp_path):
    from poster.product_composite import composite_product
    assert composite_product(_bg(tmp_path), b"", zone=(0.5, 0.3, 0.4)) is None
    assert composite_product(_bg(tmp_path), b"not an image", zone=(0.5, 0.3, 0.4)) is None


def test_the_scene_prompt_asks_for_an_empty_product_zone():
    """The model must LEAVE ROOM instead of drawing its own bottle there."""
    from poster.pipeline import _product_scene_guard
    g = _product_scene_guard("La Roche-Posay Anthelios Fluid", composited=True)
    low = g.lower()
    assert "do not draw" in low or "no product" in low or "leave" in low, g
    assert "composit" in low or "placed" in low or "added" in low, g


def test_the_pipeline_composites_when_it_has_a_real_asset():
    import inspect

    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp).splitlines())
    assert "composite_product" in src, (
        "the pipeline still lets the model draw the product it was handed")
