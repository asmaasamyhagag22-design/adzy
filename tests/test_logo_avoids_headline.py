# -*- coding: utf-8 -*-
"""The logo must not land on the headline.

OWNER-FACING FAILURE (Volume Qatar, 2026-07-21): the rendered poster put its headline in the TOP-
LEFT, and the logo compositor — which picked its corner purely from reading direction — stamped the
wordmark straight over it. "Feed your mind" read as "eed your mind" on a poster meant to win a
client.

The corner is now MEASURED: whichever top corner carries less rendered detail wins, with the
reading-side corner kept as the tie-breaker so a clean poster's layout is unchanged. Hermetic (PIL
only, no model)."""
from __future__ import annotations

from PIL import Image, ImageDraw

from poster.pipeline import _pick_logo_corner


def _poster(busy: str) -> Image.Image:
    """A dark poster with dense text-like strokes in ONE top corner."""
    im = Image.new("RGB", (1000, 1000), (18, 18, 22))
    d = ImageDraw.Draw(im)
    x0 = 40 if busy == "left" else 560
    for i in range(14):                      # stripes = high local edge energy, like a headline
        y = 40 + i * 9
        d.line([(x0, y), (x0 + 380, y)], fill=(245, 245, 245), width=3)
    return im


def test_logo_moves_away_from_a_busy_left_corner():
    assert _pick_logo_corner(_poster("left"), 220, 90, margin=45, prefer_left=True) == "tr"


def test_logo_moves_away_from_a_busy_right_corner():
    assert _pick_logo_corner(_poster("right"), 220, 90, margin=45, prefer_left=False) == "tl"


def test_an_empty_poster_keeps_the_reading_side():
    empty = Image.new("RGB", (1000, 1000), (18, 18, 22))
    assert _pick_logo_corner(empty, 220, 90, margin=45, prefer_left=True) == "tl"
    assert _pick_logo_corner(empty, 220, 90, margin=45, prefer_left=False) == "tr"


def test_a_full_width_headline_sends_the_logo_to_the_bottom():
    """The real Volume Qatar failure: a headline spanning the whole top row leaves NEITHER top
    corner usable, and choosing 'the less busy of two bad corners' still lands on the text."""
    im = Image.new("RGB", (1000, 1000), (18, 18, 22))
    d = ImageDraw.Draw(im)
    for i in range(14):
        y = 40 + i * 9
        d.line([(40, y), (960, y)], fill=(245, 245, 245), width=3)   # full-width headline band
    assert _pick_logo_corner(im, 220, 90, margin=45, prefer_left=True) in ("bl", "br")


def test_a_marginally_busier_preferred_corner_is_not_abandoned():
    # Only a CLEAR difference should override the design's reading side — otherwise ordinary
    # background texture would flip the layout at random between renders.
    im = Image.new("RGB", (1000, 1000), (18, 18, 22))
    d = ImageDraw.Draw(im)
    d.line([(40, 60), (200, 60)], fill=(60, 60, 66), width=2)      # faint, not a headline
    assert _pick_logo_corner(im, 220, 90, margin=45, prefer_left=True) == "tl"
