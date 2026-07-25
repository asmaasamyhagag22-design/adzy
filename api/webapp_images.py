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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from api.text_shaping import has_rtl, shape_for_pillow, wrap_shaped
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


# Fallback copy, keyed on the brand's MEASURED language with a universal default (D-8 — the same
# config-with-universal-default pattern as competitor/market_definition._BEST_SEED). A hardcoded
# English "Learn more" on an Arabic brand's card is exactly the country/language hardcode D-8 bans.
_DEFAULT_CTA = {
    "ar": "اعرف المزيد", "fa": "بیشتر بدانید", "ur": "مزید جانیں", "he": "מידע נוסף",
    "fr": "En savoir plus", "es": "Más información", "de": "Mehr erfahren", "tr": "Daha fazla",
}
_PREVIEW_NOTE = {
    "ar": "معاينة · فعّلي توليد الصور للنسخة النهائية",
    "fr": "aperçu · activez la génération d'images pour le rendu final",
    "es": "vista previa · activa la generación de imágenes para el render final",
}
_PREVIEW_NOTE_DEFAULT = "preview · enable image generation for the final render"


def brand_language(brand: dict) -> str:
    """The brand's own leading language code, measured — never assumed."""
    prof = brand.get("_profile") or {}
    for langs in (prof.get("languages"), brand.get("languages")):
        for entry in (langs or []):
            code = entry.get("code") if isinstance(entry, dict) else entry
            code = str(code or "").strip().lower()[:2]
            if code:
                return code
    return ""


def render_placeholder(brand: dict, *, headline: str = "", subtext: str = "", cta: str = "",
                       size: tuple = (1080, 1350)) -> bytes:
    """A clean branded card. Deterministic, no network, no paid call.

    Every string is routed through `shape_for_pillow` before it is drawn: Pillow here has no raqm
    backend, so unshaped Arabic/Hebrew comes out isolated and reversed (measured — see
    api/text_shaping). RTL copy is also right-aligned, because a right-to-left card flush to the
    left margin reads as broken even when the glyphs are correct."""
    W, H = size
    colors = brand.get("brand_colors") or ["#2A1A1F"]
    bg = _hex_to_rgb(colors[0])
    accent = _hex_to_rgb(colors[1]) if len(colors) > 1 else (255, 255, 255)
    ink = (255, 255, 255) if _luminance(bg) < 0.55 else (20, 20, 20)

    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    margin = int(W * 0.09)

    lang = brand_language(brand)
    name_raw = (brand.get("organization_name") or "")
    hl = (headline or brand.get("tagline") or "").strip()
    st = (subtext or brand.get("description") or "").strip()
    cta = (cta or _DEFAULT_CTA.get(lang, "Learn more")).strip()
    # Direction is decided by the COPY that will actually be drawn, not by a country guess.
    rtl = has_rtl(f"{hl} {st} {name_raw} {cta}")

    def line(text: str, font, y: int, fill) -> None:
        """Draw one already-shaped line, flush to the reading-side margin."""
        x = (W - margin - d.textlength(text, font=font)) if rtl else margin
        d.text((x, y), text, font=font, fill=fill)

    # brand name (top). .upper() is a no-op for Arabic and would mangle nothing, but it is applied
    # only to the Latin case so an RTL wordmark keeps the shape the reshaper produced.
    name = name_raw if rtl else name_raw.upper()
    line(shape_for_pillow(name[:40]), _font(38), int(H * 0.07), ink)

    # headline (center, wrapped). Wrap BEFORE shaping — see wrap_shaped.
    hf = _font(88)
    y = int(H * 0.30)
    for text in wrap_shaped(hl, 18, 4):
        line(text, hf, y, ink)
        y += 96

    # subtext
    if st:
        y += 20
        sf = _font(34)
        for text in wrap_shaped(st, 42, 3):
            line(text, sf, y, ink)
            y += 46

    # CTA pill (bottom) — the pill hugs the shaped text, so it must be measured after shaping.
    cta_shaped = shape_for_pillow(cta)
    cf = _font(38)
    tb = d.textbbox((0, 0), cta_shaped, font=cf)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = 28
    by0 = int(H * 0.85)
    bx0 = (W - margin - tw - pad * 2) if rtl else margin
    d.rounded_rectangle([bx0, by0, bx0 + tw + pad * 2, by0 + th + pad * 2], radius=16, fill=accent)
    d.text((bx0 + pad, by0 + pad - tb[1]), cta_shaped,
           font=cf, fill=(255, 255, 255) if _luminance(accent) < 0.55 else (20, 20, 20))

    # tiny "placeholder" watermark so it's never mistaken for a finished paid render
    note = _PREVIEW_NOTE.get(lang, _PREVIEW_NOTE_DEFAULT)
    line(shape_for_pillow(note), _font(22), H - 46, ink)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------
# real generation (opt-in, paid) — reuses the existing poster pipeline
# ---------------------------------------------------------------------

@dataclass
class RealRender:
    """A finished paid render together with the pipeline's own verdict about it.

    `_render_real` used to return bare PNG bytes, which threw away `PosterGenResult.trust_violation`
    and `trust_note_ar` — the two fields the pipeline added *because* "the qa verdict alone was
    MEASURED being ignored downstream" (poster/pipeline.py, after nanocare shipped a fabricated
    product label silently). The web app was the first downstream to repeat that exact drop."""
    png: Optional[bytes]
    result: object = None


def _render_real(brand: dict, *, headline: str, language: str) -> RealRender:
    """Real poster via poster.pipeline.generate_poster.

    Returns a RealRender whose `png` is None when the paid path could not deliver. Raises nothing:
    the CALLER decides what to tell the user, and must be told the reason (see `generate_image`)."""
    profile = brand.get("_profile") or {}
    if not profile:
        raise MissingProfile("no stored _profile for this brand — nothing to ground a render on")
    from business_profile.llm import default_caller
    from poster.pipeline import generate_poster
    caller = default_caller(strong=True)
    res = generate_poster(profile, caller=caller, engine="oneshot", language=language,
                          headline_override=headline or None)
    import base64
    png = base64.b64decode(res.image_base64) if res and res.image_base64 else None
    return RealRender(png=png, result=res)


class MissingProfile(RuntimeError):
    """The brand has no evidenced profile, so no grounded render is possible."""


# ---------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------

def generate_image(brand: dict, *, headline: str = "", subtext: str = "", cta: str = "",
                   language: str = "auto", content_type: str = "post") -> dict:
    """Return the image plus an honest account of HOW it was produced. Always succeeds.

    Beyond {image_b64, filename, headline, subtext, cta}:
      * `is_preview`    — True when this is the free placeholder, False for a real paid render. The
                          watermark used to be the only signal, and it told a user who had already
                          set BASEERA_ENABLE_PAID_IMAGES=1 to go and enable image generation.
      * `degraded_reason` — set ONLY when a paid render was requested and did not deliver. A silent
                          fallback answers "was an image produced?" while the user asked "was the
                          image I asked for produced?".
      * `trust_violation` / `trust_note_ar` — the poster pipeline's own top-level disclosure,
                          carried through instead of dropped (see RealRender)."""
    import base64

    png: Optional[bytes] = None
    degraded_reason = ""
    trust_violation = False
    trust_note_ar = ""

    if paid_images_enabled():
        try:
            real = _render_real(brand, headline=headline, language=language)
            png = real.png
            if png is None:
                degraded_reason = "the image model returned no image"
            res = real.result
            trust_violation = bool(getattr(res, "trust_violation", False))
            trust_note_ar = str(getattr(res, "trust_note_ar", "") or "")
        except MissingProfile as exc:
            degraded_reason = str(exc)
            logger.warning("paid render skipped: %s", exc)
        except Exception as exc:  # noqa: BLE001 — degrade, but never in silence
            degraded_reason = f"{type(exc).__name__}: {exc}"[:200]
            logger.exception("real image generation failed; falling back to placeholder")

    if png is None:
        png = render_placeholder(brand, headline=headline, subtext=subtext, cta=cta)
        is_preview = True
        trust_violation, trust_note_ar = False, ""   # the placeholder makes no claims to violate
    else:
        is_preview = False

    key = (brand.get("domain_key") or "brand")
    digest = hashlib.md5(png).hexdigest()[:10]
    return {
        "image_b64": base64.b64encode(png).decode("ascii"),
        "filename": f"{key}_{content_type}_{digest}.png",
        "headline": headline,
        "subtext": subtext,
        "cta": cta,
        "is_preview": is_preview,
        "degraded_reason": degraded_reason,
        "trust_violation": trust_violation,
        "trust_note_ar": trust_note_ar,
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
