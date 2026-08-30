# -*- coding: utf-8 -*-
"""The lockup treatment must not stroke connected-script glyphs.

MEASURED (nanocare poster, 2026-07-24): the delivered Arabic headline rendered as
"folded pipes" — double seam-lines crossing the strokes at every letter joint and
fold-like triangles at terminals. Reproduced pixel-for-pixel on a second machine with
the SAME font (Changa) by applying the lockup CSS alone, and it disappears by removing
one property: `-webkit-text-stroke` outlines every GLYPH separately, and Arabic glyphs
join — so each join gets a visible seam drawn across it. The gradient fill
(background-clip:text) and the drop-shadows render connected text cleanly.

So the fix keeps the DESIGNED look (gradient + halo) for RTL and drops only the
per-glyph stroke. The template already treats `[dir="rtl"]` this way for negative
letter-spacing (same failure family: per-glyph typography on a connected script).

The pipeline enables the treatment via `model_copy(update={"headline_treatment":
"lockup"})` (poster/pipeline.py) — these tests mimic that exactly. Hermetic: pure
HTML assembly, no browser, no network."""
from __future__ import annotations

import re

from poster.schemas import PosterBrief
from poster.template import default_design_spec, render_poster_html


def _arabic_brief(**kw) -> PosterBrief:
    base = dict(
        business_name="Nano Care Therapeutics",
        category="beauty",
        headline="كل حباية بتوصل لمكانها.",
        cta_text="اسألي الصيدلي عن نانو كير.",
        palette_hex=["#0c0c3c", "#547cf6"],
        primary_color="#0c0c3c",
        heading_font="Changa",
        body_font="Changa",
    )
    base.update(kw)
    return PosterBrief(**base)


def _lockup_html(brief: PosterBrief) -> str:
    spec = default_design_spec(brief).model_copy(update={"headline_treatment": "lockup"})
    return render_poster_html(brief, spec=spec)


def _rtl_lockup_rules(html: str) -> list[str]:
    """The declaration blocks of every `[dir="rtl"] …lockup…` CSS rule in the emitted page."""
    return [m.group(1) for m in
            re.finditer(r'\[dir="rtl"\][^{]*lockup[^{]*\{([^}]*)\}', html)]


def test_rtl_zeroes_the_per_glyph_stroke_on_lockup_words():
    html = _lockup_html(_arabic_brief())
    assert 'dir="rtl"' in html                      # the override can actually activate
    rules = _rtl_lockup_rules(html)
    assert any("-webkit-text-stroke:0" in r.replace(" ", "") for r in rules), (
        "no [dir=rtl] lockup rule zeroes -webkit-text-stroke — every Arabic letter joint "
        "gets a seam drawn across it (the 'folded pipes' corruption)")


def test_the_flat_lockup_variant_is_covered_too():
    # A long headline takes the `lockup-flat` fallback (its own stroked class).
    long_head = "كل حباية بتوصل لمكانها في الجسم من غير ما تتكسر في المعدة أبداً"
    html = _lockup_html(_arabic_brief(headline=long_head))
    joined = " ".join(_rtl_lockup_rules(html)).replace(" ", "")
    assert "-webkit-text-stroke:0" in joined
    flat_covered = any("lockup-flat" in m.group(0) for m in
                       re.finditer(r'\[dir="rtl"\][^{]*lockup[^{]*\{[^}]*\}', html))
    assert flat_covered, "lockup-flat keeps its per-glyph stroke on Arabic"


def test_the_designed_look_survives_the_fix():
    """The corruption is the STROKE alone — the gradient and halo were measured clean,
    and removing them would degrade every RTL lockup back to plain text."""
    html = _lockup_html(_arabic_brief())
    assert "background-clip:text" in html            # gradient fill still there
    assert "drop-shadow" in html                     # legibility halo still there
    for r in _rtl_lockup_rules(html):
        flat = r.replace(" ", "")
        assert "background:none" not in flat and "filter:none" not in flat, (
            "the RTL override must remove only the stroke, not the design")


def test_latin_lockup_keeps_its_stroke():
    """Latin glyphs are separate shapes — the outline stroke is safe and is part of the
    designed look. The fix must not strip it from LTR posters."""
    brief = PosterBrief(business_name="Acme", category="business",
                        headline="Every pill lands", cta_text="Learn more",
                        palette_hex=["#e60000"], primary_color="#e60000")
    spec = default_design_spec(brief).model_copy(update={"headline_treatment": "lockup"})
    html = render_poster_html(brief, spec=spec)
    base_rules = [m.group(1) for m in
                  re.finditer(r'(?<!\])\s\.lockup-headline\s+\.lw\s*\{([^}]*)\}', html)]
    assert any("-webkit-text-stroke:1" in r.replace(" ", "") for r in base_rules), (
        "the base lockup stroke vanished — LTR posters lost their designed outline")
