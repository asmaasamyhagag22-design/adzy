# -*- coding: utf-8 -*-
"""The classic overlay must not ship a logo's baked white canvas — or design around it.

MEASURED (nanocare poster, 2026-07-24): the brand's logo is a JPG (zid.store) — a navy
mark on a baked WHITE canvas, alpha=255 everywhere. The classic path inlined it as-is, so:
  1. the white rectangle shipped onto the poster — the owner's dead white plate, resurrected
     from inside the logo file itself;
  2. `_logo_luminance` averaged mostly-white pixels -> judged "light logo" -> picked the DARK
     frosted badge behind it -> the gray double-frame the owner flagged as "اللوجو باظ تاني".

The one-shot compositor already solved exactly this (`_overlay_real_logo` -> `white_key_logo`,
owner ruling "شيل خالص مش تطفية"): flood-fill the near-white region CONNECTED TO THE BORDERS
to alpha-0, guarded so real transparency and colored cards stay byte-stable. These tests make
the CLASSIC path reuse that primitive at both of its logo entry points (remote fetch + inline
data URI). Hermetic: PIL-built fixtures, no network, no browser."""
from __future__ import annotations

import base64
import io
import re

from PIL import Image

from poster.schemas import PosterBrief
from poster.template import _trim_logo_margins, render_poster_html


def _white_matted_jpg(size: int = 120) -> bytes:
    """A navy mark on a white canvas, saved as JPEG — the nanocare logo's shape."""
    im = Image.new("RGB", (size, size), (255, 255, 255))
    for x in range(30, 90):
        for y in range(40, 80):
            im.putpixel((x, y), (12, 12, 60))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _transparent_png_logo(size: int = 120) -> bytes:
    """A mark with REAL transparency — the guard case that must stay untouched."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for x in range(30, 90):
        for y in range(40, 80):
            im.putpixel((x, y), (12, 12, 60, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _logo_img_uri(html: str) -> str:
    m = re.search(r'class="brand-logo"[^>]*src="(data:image/[^"]+)"', html)
    assert m, "no inlined brand-logo <img> in the rendered html"
    return m.group(1)


def _decode(uri: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(uri.partition(",")[2]))).convert("RGBA")


def _brief(logo_uri: str) -> PosterBrief:
    return PosterBrief(business_name="Nano Care Therapeutics", category="beauty",
                       headline="كل حباية بتوصل لمكانها.", cta_text="اسألي الصيدلي.",
                       palette_hex=["#0c0c3c"], primary_color="#0c0c3c",
                       logo_url=logo_uri)


def test_trim_keys_a_baked_white_canvas_to_alpha():
    """CORRECTED while writing: key-then-trim crops to the mark's bbox, so the shipped
    image's border pixels are the MARK itself (opaque navy) — correct, the mark fills its
    chip. The measurable defect is white-card pixels surviving, so that is what's asserted."""
    data, mime = _trim_logo_margins(_white_matted_jpg(), "image/jpeg")
    assert mime == "image/png", "a keyed logo must ship with an alpha channel"
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    white_opaque = [p for p in im.getdata() if p[3] > 40 and min(p[:3]) > 230]
    assert not white_opaque, f"{len(white_opaque)}px of the white canvas still ship"
    assert any(p[3] == 255 for p in im.getdata()), "the mark itself must survive opaque"


def test_trim_leaves_real_transparency_alone():
    """CORRECTED while writing: after the (pre-existing) bbox trim, a clean mark IS fully
    opaque — that's the trim doing its job, not flattening. The real guard: no near-white
    OPAQUE pixel may appear (nothing became a white card)."""
    src = _transparent_png_logo()
    data, _mime = _trim_logo_margins(src, "image/png")
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    white_opaque = [p for p in im.getdata() if p[3] > 40 and min(p[:3]) > 230]
    assert not white_opaque, "a white card appeared inside a genuinely transparent logo"
    assert any(p[3] == 255 for p in im.getdata()), "the mark itself vanished"


def test_an_inline_data_uri_logo_is_keyed_in_the_rendered_page():
    """The webapp hands the brief an ALREADY-INLINE logo, skipping the remote fetch —
    the same white-matte must not sneak through that door."""
    jpg_uri = "data:image/jpeg;base64," + base64.b64encode(_white_matted_jpg()).decode()
    html = render_poster_html(_brief(jpg_uri))
    im = _decode(_logo_img_uri(html))
    white_opaque = [p for p in im.getdata() if p[3] > 40 and min(p[:3]) > 230]
    assert not white_opaque, "the baked white canvas shipped to the poster"


def test_a_keyed_dark_logo_gets_no_dark_badge(tmp_path):
    """The gray double-frame: white matte -> 'light logo' -> dark frosted badge. With the
    matte keyed, the luminance judge sees the NAVY mark on a light background -> high
    contrast -> no plate at all."""
    bg = tmp_path / "light_bg.png"
    Image.new("RGB", (1080, 1350), (238, 238, 235)).save(bg)
    jpg_uri = "data:image/jpeg;base64," + base64.b64encode(_white_matted_jpg()).decode()
    html = render_poster_html(_brief(jpg_uri), background_path=str(bg))
    m = re.search(r'class="brand-logo" style="([^"]*)"', html)
    style = m.group(1) if m else ""
    assert "rgba(12,15,22" not in style, f"dark frosted badge on a dark logo: {style}"
    assert "background:transparent" in style, f"expected the no-plate style, got: {style}"
