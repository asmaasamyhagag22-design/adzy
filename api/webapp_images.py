"""Image generation for the web app — degrade-safe by design.

Under D-5 (paid Gemini/Vertex/Imagen calls 403 here), real generation can't run, so the DEFAULT is a
free, deterministic, on-brand PLACEHOLDER rendered with Pillow (brand colors + the copy). Real
generation via the existing `poster.pipeline.generate_poster` is opt-in behind
`BASEERA_ENABLE_PAID_IMAGES=1` (so nothing spends by accident); any failure there falls back to the
placeholder. Never touches reel/Veo.

Every generator returns `{image_b64, filename, headline, subtext, cta}` (what the frontend reads).
Calendar images are also saved to disk and served by URL (see api/routes/generate.py).
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import textwrap
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from api.user_auth import webapp_dir

logger = logging.getLogger(__name__)


def paid_images_enabled() -> bool:
    return os.environ.get("BASEERA_ENABLE_PAID_IMAGES") == "1"


# ---------------------------------------------------------------------
# Pillow placeholder (free, deterministic, on-brand)
# ---------------------------------------------------------------------

def _hex_to_rgb(h: str, default=(42, 26, 31)) -> tuple:
    h = (h or "").lstrip("#")
    if len(h) == 6:
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


def _font(size: int):
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _luminance(rgb) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0


def render_placeholder(brand: dict, *, headline: str = "", subtext: str = "", cta: str = "",
                       size: tuple = (1080, 1350)) -> bytes:
    """A clean branded card. Deterministic, no network, no paid call."""
    W, H = size
    colors = brand.get("brand_colors") or ["#2A1A1F"]
    bg = _hex_to_rgb(colors[0])
    accent = _hex_to_rgb(colors[1]) if len(colors) > 1 else (255, 255, 255)
    ink = (255, 255, 255) if _luminance(bg) < 0.55 else (20, 20, 20)

    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    margin = int(W * 0.09)

    # brand name (top)
    name = (brand.get("organization_name") or "").upper()
    d.text((margin, int(H * 0.07)), name[:40], font=_font(38), fill=ink)

    # headline (center, wrapped)
    hl = (headline or brand.get("tagline") or "").strip()
    hf = _font(88)
    y = int(H * 0.30)
    for line in textwrap.wrap(hl, width=18)[:4]:
        d.text((margin, y), line, font=hf, fill=ink)
        y += 96

    # subtext
    st = (subtext or brand.get("description") or "").strip()
    if st:
        y += 20
        for line in textwrap.wrap(st, width=42)[:3]:
            d.text((margin, y), line, font=_font(34), fill=ink)
            y += 46

    # CTA pill (bottom)
    cta = (cta or "Learn more").strip()
    cf = _font(38)
    tb = d.textbbox((0, 0), cta, font=cf)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = 28
    bx0, by0 = margin, int(H * 0.85)
    d.rounded_rectangle([bx0, by0, bx0 + tw + pad * 2, by0 + th + pad * 2], radius=16, fill=accent)
    d.text((bx0 + pad, by0 + pad - tb[1]), cta,
           font=cf, fill=(255, 255, 255) if _luminance(accent) < 0.55 else (20, 20, 20))

    # tiny "placeholder" watermark so it's never mistaken for a finished paid render
    d.text((margin, H - 46), "preview · enable image generation for the final render",
           font=_font(22), fill=ink)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------
# real generation (opt-in, paid) — reuses the existing poster pipeline
# ---------------------------------------------------------------------

def _render_real(brand: dict, *, headline: str, language: str) -> Optional[bytes]:
    """Real poster via poster.pipeline.generate_poster. Returns PNG bytes, or None to fall back."""
    profile = brand.get("_profile") or {}
    if not profile:
        return None
    try:
        from business_profile.llm import default_caller
        from poster.pipeline import generate_poster
        caller = default_caller(strong=True)
        res = generate_poster(profile, caller=caller, engine="oneshot", language=language,
                              headline_override=headline or None)
        import base64
        return base64.b64decode(res.image_base64) if res and res.image_base64 else None
    except Exception:  # noqa: BLE001 — any failure (incl. 403) -> caller falls back to placeholder
        logger.exception("real image generation failed; falling back to placeholder")
        return None


# ---------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------

def generate_image(brand: dict, *, headline: str = "", subtext: str = "", cta: str = "",
                   language: str = "auto", content_type: str = "post") -> dict:
    """Return {image_b64, filename, headline, subtext, cta}. Real if enabled+available, else the
    branded placeholder. Always succeeds."""
    import base64

    png: Optional[bytes] = None
    if paid_images_enabled():
        png = _render_real(brand, headline=headline, language=language)
    if png is None:
        png = render_placeholder(brand, headline=headline, subtext=subtext, cta=cta)

    key = (brand.get("domain_key") or "brand")
    digest = hashlib.md5(png).hexdigest()[:10]
    return {
        "image_b64": base64.b64encode(png).decode("ascii"),
        "filename": f"{key}_{content_type}_{digest}.png",
        "headline": headline,
        "subtext": subtext,
        "cta": cta,
    }


# ---------------------------------------------------------------------
# on-disk store for URL-served images (calendar day posters)
# ---------------------------------------------------------------------

_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."


def _generated_dir() -> Path:
    d = webapp_dir() / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_generated_png(png: bytes, name: str) -> str:
    """Persist a PNG and return its public URL path (/api/generated/<name>)."""
    safe = "".join(c if c in _SAFE else "_" for c in name)
    if not safe.endswith(".png"):
        safe += ".png"
    (_generated_dir() / safe).write_bytes(png)
    return f"/api/generated/{safe}"


def read_generated_png(name: str) -> Optional[bytes]:
    safe = "".join(c if c in _SAFE else "_" for c in name)
    p = _generated_dir() / safe
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None
