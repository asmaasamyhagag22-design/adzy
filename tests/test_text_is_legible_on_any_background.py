# -*- coding: utf-8 -*-
"""«في كلام تحت مش باين اصلا» — near-white copy on a near-white background.

MEASURED on the Al Fouad poster (2026-07-25). The subheadline

    «اسألي صيادلة الفؤاد عن روتينك مجاناً. توصيل في نفس اليوم بالقاهرة والجيزة.»

is rendered at `color:#e6ecf2` over a bright white bathroom, and is effectively invisible. The
headline above it survives only because it is much heavier.

THE CAUSE IS A CONSTANT WHERE A MEASUREMENT BELONGS. `scrim_strength` defaults to 0.82 and is
applied blindly: the legibility scrim behind the text is always the same darkness regardless of
what is actually behind it. On a dark scene 0.82 is more than enough; on a white one it is not
close, and near-white type over near-white pixels is the result.

The measurement already exists in this very module — `_bg_region_luminance` reads the mean
luminance of the background inside a box, and `_ink_on` picks near-black or white for a given
backing. Both are used for the LOGO, which therefore always reads. The body copy, which carries
the actual message, was never given the same treatment.

D-8 / determinism: this is arithmetic on pixels the renderer already has. No model, no network, no
language or market assumption — a bright region is bright in every country.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from poster.template import effective_scrim_strength


def _bg(tmp_path: Path, rgb: tuple, name="bg.png") -> str:
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (400, 500), rgb).save(p)
    return str(p)


# --- the scrim answers the background it is actually on ------------------------------------------

def test_a_bright_background_gets_a_much_stronger_scrim(tmp_path):
    """The Al Fouad case: white bathroom, near-white copy."""
    white = _bg(tmp_path, (245, 246, 248))
    s = effective_scrim_strength(0.82, white, text_box=[0.05, 0.62, 0.9])
    assert s > 0.82, f"a white background kept the default scrim ({s})"


def test_a_dark_background_is_left_alone(tmp_path):
    """A scene that already carries the type must not be crushed under an opaque slab."""
    dark = _bg(tmp_path, (18, 20, 26))
    s = effective_scrim_strength(0.82, dark, text_box=[0.05, 0.62, 0.9])
    assert s <= 0.82 + 1e-9


def test_the_scrim_never_becomes_fully_opaque(tmp_path):
    """A solid black box would be legible and would also destroy the photograph."""
    white = _bg(tmp_path, (255, 255, 255))
    s = effective_scrim_strength(0.82, white, text_box=[0.05, 0.62, 0.9])
    assert s < 1.0


def test_a_mid_background_lands_between(tmp_path):
    mid = _bg(tmp_path, (128, 128, 128))
    white = _bg(tmp_path, (250, 250, 250), name="w.png")
    dark = _bg(tmp_path, (20, 20, 20), name="d.png")
    box = [0.05, 0.62, 0.9]
    assert (effective_scrim_strength(0.82, dark, text_box=box)
            <= effective_scrim_strength(0.82, mid, text_box=box)
            <= effective_scrim_strength(0.82, white, text_box=box))


# --- it degrades to today's behaviour rather than failing -----------------------------------------

def test_no_background_leaves_the_requested_strength(tmp_path):
    assert effective_scrim_strength(0.82, None, text_box=[0.05, 0.62, 0.9]) == 0.82
    assert effective_scrim_strength(0.82, "/nope/missing.png", text_box=None) == 0.82


def test_an_unreadable_file_never_raises(tmp_path):
    p = tmp_path / "broken.png"
    p.write_bytes(b"not an image")
    assert effective_scrim_strength(0.82, str(p), text_box=[0.1, 0.7, 0.8]) == 0.82


# --- and the renderer uses it ---------------------------------------------------------------------

def test_the_renderer_measures_before_it_draws():
    import inspect
    import poster.template as t
    src = inspect.getsource(t.render_poster_html)
    assert "effective_scrim_strength" in src, (
        "the poster still draws a constant scrim without looking at the background")


def test_the_measured_scrim_actually_reaches_the_css(tmp_path):
    """END-TO-END on the HTML: the same brief over a white background must produce a darker
    scrim than over a dark one. Asserted on the output, not on the helper."""
    from poster.template import PosterBrief, default_design_spec, render_poster_html
    brief = PosterBrief(business_name="Example", headline="عنوان طويل",
                        subheadline="سطر تحت العنوان", cta_text="اطلب")
    spec = default_design_spec(brief)
    white_css = render_poster_html(brief, _bg(tmp_path, (250, 250, 250), "w.png"), spec=spec)
    dark_css = render_poster_html(brief, _bg(tmp_path, (16, 16, 20), "d.png"), spec=spec)
    import re

    def _max_alpha(html: str) -> float:
        vals = [float(m) for m in re.findall(r"rgba\(8,\s*12,\s*18,\s*([0-9.]+)\)", html)]
        return max(vals) if vals else 0.0
    assert _max_alpha(white_css) > _max_alpha(dark_css), (
        "the background made no difference to the scrim that has to make the text readable")
