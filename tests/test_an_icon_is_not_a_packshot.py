# -*- coding: utf-8 -*-
"""The shot before the logo must be a photographed product, not a 120-pixel decoration.

MEASURED 2026-08-06 on NTI — a government training institute, i.e. a brand with NO packshot at
all, which is exactly the case where this goes wrong. Its best-ranked insert candidate was
`linkchain3.png`, a **120x120 decorative chain icon**. It keys perfectly off its flat background
and its aspect ratio is 1.00, so every existing test passed it, and it would have been composited
as the HERO PRODUCT SHOT in the beat immediately before the brand's logo.

This is the failure `poster/product_composite.rank_candidates` was written for — "a site logo once
won the 'first thing that keys cleanly' race and was composited at 44% of a poster" — wearing a
filename its keyword filter does not know.

THE MISSING SIGNAL WAS SIZE, and it separates the two classes with an enormous margin. Trimmed
heights, measured across three real brands:

    real packshots (Al Fouad, Nano Care)    664 - 1615 px
    NTI decorations                         108 -  219 px

The floor is DERIVED from what the insert draws — the pack fills `_HEIGHT_FRAC` of a 1920-tall
frame (883px), and a source needing more than 2x upscaling to get there is an icon, not a
photograph. Nothing is keyed to a vertical: a brand with real product photography sails through,
and a brand with none correctly gets no insert.
"""
from __future__ import annotations

import io

import pytest

from reel.insert_shot import _HEIGHT_FRAC, _MAX_UPSCALE, _MIN_PRODUCT_PX, _keyed_products
from reel.schemas import REEL_H


def _png(w: int, h: int, *, subject_frac: float = 0.8) -> bytes:
    """A studio shot: a solid subject centred on flat white, so it keys cleanly."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    sw, sh = int(w * subject_frac), int(h * subject_frac)
    d.rectangle([(w - sw) // 2, (h - sh) // 2, (w + sw) // 2, (h + sh) // 2], fill=(30, 90, 160))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_the_floor_is_derived_from_what_the_insert_draws():
    """A magic number would drift the moment the insert's framing is tuned."""
    assert _MIN_PRODUCT_PX == int(REEL_H * _HEIGHT_FRAC / _MAX_UPSCALE)
    assert _MAX_UPSCALE <= 2.5, "past this the pack is visibly soft at the size it is drawn"
    assert 300 < _MIN_PRODUCT_PX < 660, (
        "the floor must sit inside the measured gap: decorations top out at 219px and the "
        "smallest real packshot measured 664px")


def test_a_small_decorative_icon_is_refused():
    """The measured case: 120x120, keys cleanly, aspect 1.00 — and it is not a product."""
    assert _keyed_products([_png(120, 120)]) == []


@pytest.mark.parametrize("size", [(120, 120), (200, 200), (300, 300), (750, 500)])
def test_nothing_under_the_floor_survives(size):
    """750x500 is included deliberately: NTI's `dey2.jpg` is that size and still trims to 219px,
    because the subject is a small part of the frame. The gate measures the SUBJECT, not the file."""
    w, h = size
    out = _keyed_products([_png(w, h, subject_frac=0.44)])
    assert out == [], f"a {w}x{h} image with a small subject was accepted as a packshot"


@pytest.mark.parametrize("size", [(720, 720), (1000, 1200), (1615, 1615)])
def test_a_real_packshot_still_passes(size):
    """The fix must not buy NTI by losing every brand that has real product photography — these
    are the measured sizes of Al Fouad's and Nano Care's own catalogue images."""
    assert len(_keyed_products([_png(*size)])) == 1


def test_a_brand_with_no_packshot_gets_no_insert_rather_than_a_bad_one():
    """`insert_clip` returns None on an empty set, and the compositor then GENERATES that beat —
    which is the honest outcome for a service brand. A beat that says nothing while looking
    deliberate is worse than a generated one."""
    assert _keyed_products([_png(120, 120), _png(96, 96)]) == []
