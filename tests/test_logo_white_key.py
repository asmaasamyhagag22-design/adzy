# -*- coding: utf-8 -*-
"""البند 2 (owner report, nanocare + several brands): the composited logo keeps arriving inside a
WHITE CARD — `_overlay_real_logo` only trims TRANSPARENT margins, and a JPG/white-matted PNG has
alpha=255 everywhere, so the white box rides through alpha_composite untouched. `white_key_logo`
keys near-white to alpha ONLY when the logo has no real transparency; genuinely transparent PNGs
are byte-stable, and a white MARK on a colored ground is never hollowed out. Hermetic (PIL)."""
from __future__ import annotations

import io

from PIL import Image

from reel.image_quality import white_key_logo


def _png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()


def _white_matted_logo() -> bytes:
    """A dark mark centered on an opaque WHITE card (the nanocare failure shape)."""
    img = Image.new("RGB", (200, 120), (255, 255, 255))
    for x in range(70, 130):
        for y in range(40, 80):
            img.putpixel((x, y), (20, 40, 160))
    return _png(img)


def test_white_matte_becomes_transparent_mark_survives():
    out = Image.open(io.BytesIO(white_key_logo(_white_matted_logo()))).convert("RGBA")
    corners = [out.getpixel((0, 0)), out.getpixel((out.width - 1, 0)),
               out.getpixel((0, out.height - 1)), out.getpixel((out.width - 1, out.height - 1))]
    assert all(px[3] == 0 for px in corners)                    # the white card is GONE
    assert out.getpixel((100, 60))[3] == 255                    # the mark itself survives opaque
    # OWNER RULING ("شيل خالص مش تطفية"): the ENTIRE border-connected background is FULLY
    # transparent — zero semi-transparent wash anywhere outside the mark + its 1px feather.
    semi = sum(1 for y in range(out.height) for x in range(out.width)
               if 0 < out.getpixel((x, y))[3] < 255)
    assert semi < out.width * 2 + out.height * 2                # only a thin feather rim allowed
    bg_left = out.getpixel((35, 60))                            # mid-background, far from the mark
    assert bg_left[3] == 0                                      # removed, NOT dimmed


def test_interior_white_is_preserved_not_hollowed():
    # a mark with an enclosed WHITE hole (a letter's eye): the hole is NOT border-connected,
    # so flood-fill must KEEP it opaque — hollowing it out would mangle the logo.
    img = Image.new("RGB", (120, 120), (255, 255, 255))
    for x in range(20, 100):
        for y in range(20, 100):
            img.putpixel((x, y), (10, 30, 120))
    for x in range(50, 70):
        for y in range(50, 70):
            img.putpixel((x, y), (255, 255, 255))               # enclosed white eye
    out = Image.open(io.BytesIO(white_key_logo(_png(img)))).convert("RGBA")
    assert out.getpixel((5, 5))[3] == 0                         # border background gone
    assert out.getpixel((60, 60))[3] == 255                     # the enclosed eye SURVIVES


def test_transparent_logo_is_byte_stable():
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(30, 70):
        for y in range(30, 70):
            img.putpixel((x, y), (200, 30, 30, 255))
    raw = _png(img)
    assert white_key_logo(raw) == raw                           # real transparency -> untouched


def test_white_mark_on_colored_ground_not_hollowed():
    # a WHITE wordmark on a solid brand-colored card: the white here IS the mark — keying it
    # would destroy the logo. The border isn't white, so the keyer must not fire.
    img = Image.new("RGB", (200, 120), (20, 40, 160))
    for x in range(70, 130):
        for y in range(50, 70):
            img.putpixel((x, y), (255, 255, 255))
    raw = _png(img)
    out = Image.open(io.BytesIO(white_key_logo(raw))).convert("RGBA")
    assert out.getpixel((100, 60))[3] == 255                    # the white mark survives
    assert out.getpixel((0, 0))[3] == 255                       # the colored card survives too
