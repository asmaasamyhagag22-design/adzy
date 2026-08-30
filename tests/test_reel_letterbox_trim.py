# -*- coding: utf-8 -*-
"""P-38 (2026-07-18): the reel end-card could ship with a near-black letterbox band (frame
inspection, nti_render3). reel/ had NO letterbox trimmer — only the poster did (poster.pipeline).
Add a deterministic row-mean trimmer to reel.image_quality (reel's own leaf image module, no
poster dependency / import cycle) and apply it to the rendered end-card PNG. Hermetic — synthetic
PNGs, no Chromium/render."""
from __future__ import annotations

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def test_trim_letterbox_png_crops_near_black_bars(tmp_path):
    from reel.image_quality import trim_letterbox_png
    im = Image.new("RGB", (120, 300), (6, 6, 6))                 # near-black top+bottom bars
    for y in range(100, 205):                                    # a bright content band in the middle
        for x in range(120):
            im.putpixel((x, y), (200, 180, 150))
    p = tmp_path / "endcard.png"
    im.save(p)
    trim_letterbox_png(str(p))
    h = Image.open(p).size[1]
    assert 90 <= h <= 130, f"bars should be trimmed to ~the content band, got {h}"


def test_trim_letterbox_png_is_a_noop_on_a_full_frame(tmp_path):
    from reel.image_quality import trim_letterbox_png
    im = Image.new("RGB", (120, 300), (150, 150, 150))           # full-content, no bars
    p = tmp_path / "full.png"
    im.save(p)
    trim_letterbox_png(str(p))
    assert Image.open(p).size == (120, 300)                      # untouched


def test_trim_letterbox_png_does_not_gut_a_legitimately_dark_frame(tmp_path):
    from reel.image_quality import trim_letterbox_png
    im = Image.new("RGB", (120, 300), (8, 8, 8))                 # almost all dark (a dark scene)
    for y in range(140, 160):                                    # only a thin bright sliver
        for x in range(120):
            im.putpixel((x, y), (220, 220, 220))
    p = tmp_path / "dark.png"
    im.save(p)
    trim_letterbox_png(str(p))
    assert Image.open(p).size == (120, 300)                      # guarded: not gutted
