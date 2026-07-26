# -*- coding: utf-8 -*-
"""Round-2 reviewer findings on the v7 render (2026-07-26) — measured in pixels, fixed at roots.

  1. «البانل الملزوق باين كمستطيل… الحد السفلي فرق 177 مستوى سطوع» — the product photo from the
     store is a studio SHOT (its own ground, its own heart, its own reflection), not a cut-out.
     White-keying only strips FLAT borders, so the whole rectangle shipped as a card.
  2. «قلبين مختلفين في نفس الكادر» — the panel's own heart + the background's ghost heart.
     Dies with (1): key the asset properly or do not paste it at all.
  3+5. «الكونتراست لسه راسب… التلت العلوي فاضي 99%» — the copy defaults to the bottom band even
     when the top third is empty and clean. The clearest region should HOST the copy.
  4. «تلات حدود شمال في 20 بكسل» — logo x=116, headline x=106, CTA x=96. One gutter, everything
     hangs on it.
  6. «بيعالج claim علاجي» — a therapeutic verb on a cosmetic, plus SPF 50+ absent from the
     Arabic copy. Soften the verb unless verified; surface the spec as a chip.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image


# --- 1+2: a photographic panel must never ship as a card -------------------------------------

def _studio_shot() -> bytes:
    """A product photo the way stores actually shoot them: gradient ground + reflection."""
    im = Image.new("RGB", (400, 700))
    for y in range(700):
        g = 200 + int(40 * y / 700)
        for x in range(400):
            im.putpixel((x, y), (g, g, g + 2))
    for y in range(120, 520):                       # the bottle
        for x in range(140, 260):
            im.putpixel((x, y), (245, 244, 248))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_a_studio_shot_with_a_gradient_ground_is_keyed_or_refused(tmp_path):
    from poster.product_composite import composite_product
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1350), (60, 62, 70)).save(bg)
    out = composite_product(bg, _studio_shot(), zone=(0.52, 0.34, 0.44))
    if out is None:
        return                                     # refusing is honest
    res = Image.open(out).convert("RGB")
    # no hard rectangle: the pasted region's corners must not be the shot's grey ground
    for fx, fy in ((0.53, 0.35), (0.90, 0.35), (0.53, 0.76), (0.90, 0.76)):
        px = res.getpixel((int(1080 * fx), int(1350 * fy)))
        assert not (195 <= px[0] <= 250 and abs(px[0] - px[1]) < 6), (
            f"the studio ground shipped as a card corner at {(fx, fy)}: {px}")


def test_a_true_cutout_still_pastes():
    import tempfile

    from poster.product_composite import composite_product
    im = Image.new("RGBA", (300, 600), (0, 0, 0, 0))
    for y in range(60, 540):
        for x in range(80, 220):
            im.putpixel((x, y), (240, 240, 245, 255))
    buf = io.BytesIO(); im.save(buf, format="PNG")
    with tempfile.TemporaryDirectory() as td:
        bg = Path(td) / "bg.png"
        Image.new("RGB", (1080, 1350), (60, 62, 70)).save(bg)
        assert composite_product(bg, buf.getvalue(), zone=(0.52, 0.34, 0.44)) is not None


# --- 3+5: the copy goes where the canvas is clear ---------------------------------------------

def test_the_clearest_third_is_found(tmp_path):
    from poster.placement import clearest_copy_zone
    im = Image.new("RGB", (1080, 1350), (128, 128, 128))
    for y in range(450, 1350):                      # busy bottom two thirds
        for x in range(0, 1080, 3):
            im.putpixel((x, y), ((x * 7) % 255, (y * 5) % 255, 90))
    p = tmp_path / "bg.png"; im.save(p)
    zone = clearest_copy_zone(p)
    assert zone is not None and zone[1] < 0.33, zone   # the empty TOP hosts the copy


def test_a_uniform_canvas_returns_none_and_keeps_the_designed_layout(tmp_path):
    from poster.placement import clearest_copy_zone
    p = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1350), (128, 128, 128)).save(p)
    assert clearest_copy_zone(p, min_advantage=0.15) is None


def test_the_pipeline_relocates_failing_copy():
    import inspect

    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp).splitlines())
    assert "clearest_copy_zone" in src, "failing contrast still retries in the same busy spot"


# --- 4: one gutter -----------------------------------------------------------------------------

def test_the_template_hangs_everything_on_one_gutter():
    import inspect

    import poster.template as T
    src = inspect.getsource(T)
    assert "_GUTTER_PX" in src
    import re
    vals = set(re.findall(r"_GUTTER_PX\s*=\s*(\d+)", src))
    assert len(vals) == 1, vals


# --- 6: therapeutic verbs + the spec chip ------------------------------------------------------

def test_a_therapeutic_verb_is_softened():
    from poster.copy_rules import soften_therapeutic_claims
    out, changed = soften_therapeutic_claims("صن سكرين بيعالج البقع الداكنة مش بس بيحمي منها")
    assert changed and "بيعالج" not in out
    assert "يقلل" in out or "ظهور" in out, out
    out2, changed2 = soften_therapeutic_claims("This fluid treats dark spots fast")
    assert changed2 and "treats" not in out2.lower()


def test_clean_copy_is_untouched():
    from poster.copy_rules import soften_therapeutic_claims
    text = "بيحمي بشرتك ويقلل ظهور البقع"
    out, changed = soften_therapeutic_claims(text)
    assert out == text and changed is False


def test_the_facts_block_demands_the_spec_chip():
    from poster.concept import product_facts_block
    b = product_facts_block(name="Anthelios SPF50+ 50ml", description="UVMUNE 400",
                            price="1100", currency="EGP")
    low = b.lower()
    assert "chip" in low or "proof" in low, b
    assert "spf" in low


def test_the_concept_copy_passes_through_the_softener():
    import inspect

    import poster.concept as c
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(c).splitlines())
    assert "soften_therapeutic_claims" in src
