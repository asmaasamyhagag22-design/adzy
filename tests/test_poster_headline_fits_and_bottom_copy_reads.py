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


def _size_of(headline: str, treatment: str = "stacked_hero", column_px: int = 900) -> int:
    from poster.schemas import PosterBrief
    from poster.template import _headline_block
    brief = PosterBrief(business_name="Shop", headline=headline)
    html = _headline_block(brief, True, treatment=treatment, column_px=column_px)
    m = re.search(r"font-size:(\d+)px", html)
    return int(m.group(1)) if m else 0


def test_a_long_headline_does_not_eat_the_canvas():
    """Height is a constraint, not an afterthought: n stacked words must fit the block.

    The measured poster used `lockup` (the DNA's typography) with 6 words; `stacked_hero`
    caps at 5 and hands anything longer to the plain wrapping block, which is fine."""
    from poster.template import _CH
    cases = [("اطمني على بشرتك من الشمس والبقع", "lockup"),
             ("اطمني على بشرتك من الشمس", "stacked_hero")]
    for text, treatment in cases:
        n = len(text.split())
        size = _size_of(text, treatment)
        assert size, f"{treatment} produced no sized headline for {n} words"
        assert size * 1.12 * n <= _CH * 0.46, (
            f"{treatment}: {n} words at {size}px = {int(size * 1.12 * n)}px of headline on a "
            f"{_CH}px canvas — that is what covered the product")


def test_a_short_headline_still_gets_a_dramatic_size():
    """The fix must not flatten every poster into timid type."""
    assert _size_of("عرض اليوم") >= 100, _size_of("عرض اليوم")


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

def test_a_near_invisible_badge_is_not_drawn():
    """MEASURED on the La Roche poster: the mark got a light frosted badge whose tone is within
    a hair of the ground it sits on — a visible sticker that adds no contrast. A plate must
    EARN its place: if it cannot separate the logo from the background, draw none."""
    from poster.template import _badge_is_worth_it
    # dark logo (0.16) on a light ground (0.90): the mark already reads -> no badge
    assert _badge_is_worth_it(logo_lum=0.16, bg_lum=0.90) is False
    # dark logo on a mid ground: a light badge genuinely separates it -> badge
    assert _badge_is_worth_it(logo_lum=0.16, bg_lum=0.34) is True
    # light logo on a light ground -> a dark badge separates it -> badge
    assert _badge_is_worth_it(logo_lum=0.88, bg_lum=0.80) is True


def test_the_style_chooser_uses_the_rule():
    import inspect

    import poster.template as T
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(T._adaptive_logo_style).splitlines())
    assert "_badge_is_worth_it" in src
