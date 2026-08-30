# -*- coding: utf-8 -*-
"""The composite must SHIP A PRODUCT — the right asset, softly blended.

MEASURED on the v8 render (2026-07-26): the refusal rule fired and the poster shipped with NO
product at all — an empty hand reaching for nothing, which is worse for the owner than any of
the defects it was protecting her from. Two roots:

  1. ONE CANDIDATE. `parse_product` returns the page's og:image, and for this product that is
     `Group_1.webp` — a composed marketing BANNER (its own gradient, its own heart prop). The
     product page's gallery also carries clean pack shots; nothing ever looked at them.
  2. ALL-OR-NOTHING. When a border cannot be keyed the only options were "paste a hard-edged
     card" or "paste nothing". A feathered blend is the third state: the label stays
     pixel-exact and there is no 177-level cliff.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image


def _shot(kind: str) -> bytes:
    im = Image.new("RGB", (300, 600), (255, 255, 255))
    if kind == "banner":                       # gradient ground = not keyable
        for y in range(600):
            g = 190 + int(50 * y / 600)
            for x in range(300):
                im.putpixel((x, y), (g, g, g + 3))
    for y in range(150, 460):                  # the bottle
        for x in range(110, 200):
            im.putpixel((x, y), (20, 80, 190))
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return buf.getvalue()


def test_the_parser_collects_the_gallery_not_just_the_og_image():
    from scraper.product_page import ProductInfo
    assert "images" in ProductInfo.model_fields, "only one candidate image is ever considered"


def test_the_first_keyable_candidate_wins(tmp_path, monkeypatch):
    """Given a banner and a clean pack shot, the clean one is composited."""
    import poster.product_composite as pc

    bg = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1350), (55, 58, 66)).save(bg)
    assets = {"https://x/banner.png": _shot("banner"), "https://x/clean.png": _shot("clean")}
    monkeypatch.setattr(pc, "_fetch", lambda u: assets.get(u))
    out = pc.composite_product(bg, ["https://x/banner.png", "https://x/clean.png"])
    assert out, "no candidate was composited"
    res = Image.open(out).convert("RGB")
    # the bottle is on the poster
    blues = sum(1 for px in res.getdata() if px[2] > 150 and px[2] - px[0] > 80)
    assert blues > 2000, blues


def test_an_unkeyable_asset_is_feathered_not_refused(tmp_path, monkeypatch):
    """The third state: soft-edged blend, no hard rectangle, product still present."""
    import poster.product_composite as pc

    bg = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1350), (55, 58, 66)).save(bg)
    monkeypatch.setattr(pc, "_fetch", lambda u: _shot("banner"))
    out = pc.composite_product(bg, ["https://x/banner.png"])
    assert out, "an unkeyable asset shipped NO product — worse than a soft blend"
    res = Image.open(out).convert("L")
    W, H = res.size
    # walk the pasted region's left edge: no single-pixel cliff
    xs = [int(W * f) for f in (0.50, 0.51, 0.52, 0.53, 0.54)]
    y = int(H * 0.5)
    jumps = [abs(res.getpixel((xs[i + 1], y)) - res.getpixel((xs[i], y))) for i in range(4)]
    assert max(jumps) < 90, f"a hard card edge survived: {jumps}"


def test_a_single_url_still_works(tmp_path, monkeypatch):
    import poster.product_composite as pc
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1350), (55, 58, 66)).save(bg)
    monkeypatch.setattr(pc, "_fetch", lambda u: _shot("clean"))
    assert pc.composite_product(bg, "https://x/clean.png")


def test_the_cta_is_never_cut_mid_phrase():
    """v8 shipped «اطلبيه دلوقتي من صيدليات» — the 4-word cap sliced the brand in half."""
    from poster.copy_rules import tidy_cta
    out = tidy_cta("اطلبيه دلوقتي من صيدليات الفؤاد", brand_name="صيدليات الفؤاد")
    assert "صيدليات" not in out, out
    assert out.strip() and not out.endswith(("من", "في", "على")), out
