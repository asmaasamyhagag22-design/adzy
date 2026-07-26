# -*- coding: utf-8 -*-
"""Two measured defects in the Al Fouad La Roche poster (2026-07-26).

OWNER: «طريقة الكلام اللي في نص الصفحة دي مش حلو بس الباكجراوند كويسة»
       «الكلام اللي تحت لسه فاتح — المفروض اللي بيكتب البرومبت يكون عامل حساب التفاصيل دي»

MEASURED on that render:
  1. THE HERO HEADLINE IS SIZED BY ITS LONGEST WORD ONLY. Each word becomes its own line, so a
     5-word Arabic headline at ~110px with 1.12 leading is ~615px of type — it swallowed the
     middle of the poster and covered the hero product. Fitting the WIDTH while ignoring the
     HEIGHT is half a fit.
  2. THE FREE-FORM UPPER SCRIM FADES TO ZERO AT ITS EDGES. The cluster carries the headline AND
     the subline AND the CTA; the radial wash is centred at 42%, so the bottom copy sat on bare
     bright photo — the same invisible-copy complaint as the fixed-layout path, in the branch
     that path's fix did not touch.
"""
from __future__ import annotations

import re


def _stops(css: str):
    return [(float(a), float(pos)) for a, pos in
            re.findall(r"rgba\(\s*\d+,\s*\d+,\s*\d+,\s*([\d.]+)\s*\)\s+([\d.]+)%", css)]


def _size_of(headline: str, treatment: str = "stacked_hero", column_px: int = 900,
             rtl: bool = False) -> int:
    """Latin by default: Arabic no longer stacks one word per line at all (see
    tests/test_arabic_copy_is_typeset_not_stacked.py), so the HEIGHT fit is a Latin rule."""
    from poster.schemas import PosterBrief
    from poster.template import _headline_block
    brief = PosterBrief(business_name="Shop", headline=headline)
    html = _headline_block(brief, rtl, treatment=treatment, column_px=column_px)
    m = re.search(r"font-size:(\d+)px", html)
    return int(m.group(1)) if m else 0


def test_a_long_headline_does_not_eat_the_canvas():
    """Height is a constraint, not an afterthought: n stacked words must fit the block.

    The measured poster used `lockup` (the DNA's typography) with 6 words; `stacked_hero`
    caps at 5 and hands anything longer to the plain wrapping block, which is fine."""
    from poster.template import _CH
    cases = [("PROTECT YOUR SKIN FROM SUN SPOTS", "lockup"),
             ("PROTECT YOUR SKIN TODAY", "stacked_hero")]
    for text, treatment in cases:
        n = len(text.split())
        size = _size_of(text, treatment)
        assert size, f"{treatment} produced no sized headline for {n} words"
        assert size * 1.12 * n <= _CH * 0.46, (
            f"{treatment}: {n} words at {size}px = {int(size * 1.12 * n)}px of headline on a "
            f"{_CH}px canvas — that is what covered the product")


def test_a_short_headline_still_gets_a_dramatic_size():
    """The fix must not flatten every poster into timid type."""
    assert _size_of("BIG SALE") >= 100, _size_of("BIG SALE")


def test_the_freeform_upper_scrim_never_fades_to_nothing_under_the_copy():
    from poster.template import _freeform_lower_css
    css = _freeform_lower_css([0.06, 0.22, 0.6], 0.82, "right")
    stops = _stops(css)
    assert stops, css
    assert min(a for a, _ in stops) >= 0.25, (
        f"the wash reaches zero inside the text cluster — the bottom copy gets no backing: "
        f"{stops}")


def test_the_freeform_low_placement_keeps_its_strong_base():
    from poster.template import _freeform_lower_css
    stops = _stops(_freeform_lower_css([0.06, 0.62, 0.6], 0.82, "right"))
    assert max(a for a, _ in stops) >= 0.8, stops


# --- «مشكلة اللوجو تاني» -------------------------------------------------------------------

def test_the_logo_never_gets_a_plate_behind_it():
    """OWNER RULING (2026-07-26): «انا عايزة اللوجو من غير خلفية وراه». No badge, no chip, no
    frosted panel — on ANY background. The mark sits directly on the image; legibility comes
    from a shadow sized to what is behind it, never from a box."""
    from poster.template import _logo_style_for
    for logo_lum, bg_lum in ((0.16, 0.90), (0.16, 0.20), (0.88, 0.85), (0.50, 0.50)):
        style = _logo_style_for(logo_lum=logo_lum, bg_lum=bg_lum)
        assert "background:transparent" in style, (logo_lum, bg_lum, style)
        assert "backdrop-filter" not in style and "border:" not in style, style
        assert "drop-shadow" in style, style


def test_a_logo_on_a_matching_ground_gets_a_stronger_shadow():
    """No plate does not mean no legibility: the harder the mark is to separate, the stronger
    the shadow that separates it."""
    from poster.template import _logo_style_for
    easy = _logo_style_for(logo_lum=0.16, bg_lum=0.92)
    hard = _logo_style_for(logo_lum=0.50, bg_lum=0.52)

    def _blur(style: str) -> float:
        import re
        return float(re.search(r"drop-shadow\([^)]*?([\d.]+)px\s+rgba", style).group(1))
    assert _blur(hard) > _blur(easy), (easy, hard)


def test_the_unmeasurable_logo_path_does_not_fall_back_to_a_box():
    """An SVG logo cannot be measured — that used to mean a dark chip. A box drawn because we
    could not measure is the worst version of the box the owner does not want."""
    import inspect

    import poster.template as T
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(T).splitlines())
    assert "background:rgba(18,20,26,.92)" not in src, "the fallback chip is still drawn"
