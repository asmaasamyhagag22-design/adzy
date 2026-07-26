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
from api.webapp_influencer import selected_face_bytes

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


try:                                         # module seam, so a test can stub the scrape
    from scraper.product_page import scrape_product_page
except Exception:  # noqa: BLE001 — Playwright may be unavailable; the feature degrades, nothing breaks
    scrape_product_page = None  # type: ignore[assignment]

_PRODUCT_NOTES = {
    "unsafe": "الرابط ده مش رابط عام صالح — اتجاهل، والصورة اتعملت من بيانات البراند العامة.",
    "unreachable": ("مقدرناش نفتح صفحة المنتج دي (الموقع رفض الطلب أو الصفحة مش موجودة) — "
                    "الصورة اتعملت من بيانات البراند العامة."),
    "empty": ("فتحنا الصفحة بس مالقيناش فيها بيانات منتج نقدر نستعملها (لا اسم ولا صورة) — "
              "الصورة اتعملت من بيانات البراند العامة."),
    "unavailable": ("قراءة روابط المنتجات مش متاحة في التشغيلة دي — الصورة اتعملت من بيانات "
                    "البراند العامة."),
}


def product_from_link(url: str):
    """(ProductInfo|None, note) for a user-pasted product URL. Never raises.

    THE POINT OF THE NOTE. This project has already been burned by accepting a user's input and
    dropping it: an uploaded logo was read, bounded and discarded, and `/generate/upload` still
    accepts `product_name`/`product_description` form fields that reach nothing. Someone who pastes
    a link and gets a generic poster back has been told, silently, that the link worked. So every
    way this can fail returns a sentence the client can read.

    SSRF: the URL comes from a browser. Same gate as every other user-supplied URL here."""
    u = str(url or "").strip()
    if not u:
        return None, ""
    # REJECT A FOREIGN SCHEME BEFORE NORMALISING, not after. `ensure_scheme` exists to turn
    # "shop.test/p/1" into "https://shop.test/p/1", and handed `file:///etc/passwd` it produces
    # something whose HOST is `file` — which then passes a public-IP check on any resolver that
    # answers for it. Exactly the hole already found and closed in `competitor/manual.py`; the
    # order of these two lines is the whole fix.
    scheme = u.split(":", 1)[0].lower() if ":" in u.split("/", 1)[0] else ""
    if scheme and scheme not in ("http", "https"):
        return None, _PRODUCT_NOTES["unsafe"]
    try:
        from scraper.url_utils import ensure_scheme, is_safe_public_url
        u = ensure_scheme(u)
        if not u.lower().startswith(("http://", "https://")) or not is_safe_public_url(u):
            return None, _PRODUCT_NOTES["unsafe"]
    except Exception:  # noqa: BLE001 — no gate available means we do not fetch, not that we risk it
        return None, _PRODUCT_NOTES["unsafe"]
    if scrape_product_page is None:
        return None, _PRODUCT_NOTES["unavailable"]
    try:
        info = scrape_product_page(u)
    except Exception:  # noqa: BLE001 — a bad link must never cost the client their image
        logger.exception("product link scrape failed: %s", u)
        return None, _PRODUCT_NOTES["unreachable"]
    if info is None:
        return None, _PRODUCT_NOTES["unreachable"]
    if not getattr(info, "usable", False):
        return None, _PRODUCT_NOTES["empty"]
    return info, ""


def _render_real(brand: dict, *, headline: str, language: str, product=None,
                 trend_context: str = "") -> RealRender:
    """Real poster via poster.pipeline.generate_poster.

    Returns a RealRender whose `png` is None when the paid path could not deliver. Raises nothing:
    the CALLER decides what to tell the user, and must be told the reason (see `generate_image`).

    `product`: a `ProductInfo` read from a link the owner pasted. `generate_poster` has taken
    `product_image` / `product_name` all along — the webapp simply never passed them, so "make me
    an ad for THIS item" was not expressible.

    THE CHOSEN FACE rides `identity_image` (owner, 2026-07-26, looking at an Al Fouad poster
    minutes after picking a face from three candidates: «انا عايزة نفس الوش يظهر في البوستر»).
    The picker, the candidates and `selected_face_bytes` all existed; only the influencer's OWN
    poster endpoint passed them on, so the Studio's normal render — the one she actually uses —
    invented a different person every time."""
    profile = brand.get("_profile") or {}
    if not profile:
        raise MissingProfile("no stored _profile for this brand — nothing to ground a render on")
    from business_profile.llm import default_caller
    from poster.pipeline import generate_poster
    caller = default_caller(strong=True)
    face, face_mime = (None, "")
    try:
        face, face_mime = selected_face_bytes(brand)
    except Exception:  # noqa: BLE001 — no face is a normal state, never a failed render
        face, face_mime = None, ""
    # NO ENGINE OVERRIDE. This said engine="oneshot", which forced the model-draws-the-copy
    # path on every render — including Arabic, where it is measured to fail 100% of the time and
    # burn three image calls first. The pipeline picks the engine from the script.
    res = generate_poster(profile, caller=caller, language=language,
                          headline_override=headline or None,
                          trend_context=trend_context or None,
                          identity_image=((face, face_mime or "image/png") if face else None),
                          product_image=(getattr(product, "image", "") or None) if product else None,
                          product_name=(getattr(product, "name", "") or None) if product else None,
                          # The item's OWN facts — its page description and the price exactly as
                          # shown — so the copy can sell the product (SPF, actives, price) and not
                          # just the shop. Empty when the link gave none; nothing is invented.
                          product_description=(getattr(product, "description", "") or "") if product else "",
                          product_price=(getattr(product, "price", "") or "") if product else "",
                          product_currency=(getattr(product, "currency", "") or "") if product else "")
    import base64
    png = base64.b64decode(res.image_base64) if res and res.image_base64 else None
    return RealRender(png=png, result=res)


class MissingProfile(RuntimeError):
    """The brand has no evidenced profile, so no grounded render is possible."""


# ---------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------

def top_trend_context(brand: dict) -> tuple[str, dict, str]:
    """(trend_context, trend_used, note) from the brand's STORED trends — the ones serialized
    into `swot.trends` with their sources. «اضغط قبلها على ترند» works only if pressing it does
    something real: the plumbing (`generate_poster(trend_context=)` -> concept -> research) has
    existed all along and nothing in the webapp ever passed it.

    No stored trends -> ("", {}, an honest note) — a toggle that silently does nothing teaches
    the owner it is decorative."""
    trends = ((brand.get("swot") or {}).get("trends") or [])
    for t in trends:
        title = str((t or {}).get("title") or "").strip()
        if not title:
            continue
        url = str(t.get("url") or "").strip()
        terms = [str(x) for x in (t.get("matched_terms") or []) if x]
        ctx = title + (f" (terms: {', '.join(terms)})" if terms else "")
        return ctx, {"title": title, "url": url}, ""
    return "", {}, ("مفيش ترندات محفوظة للبراند ده لسه — شغّلي التحليل (SWOT) الأول وهيتسحبوا "
                    "بمصادرهم، وبعدها زرار الترند هيشتغل.")


def generate_image(brand: dict, *, headline: str = "", subtext: str = "", cta: str = "",
                   language: str = "auto", content_type: str = "post",
                   product_url: str = "", trend: bool = False) -> dict:
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
    qa_failed = False
    qa_note = ""

    # "Make me an ad for THIS product" — resolved BEFORE the render so its failure is a stated
    # note rather than a silently generic poster.
    product, product_note = product_from_link(product_url)

    # «متأثر بالترند» — the owner pressed Trend. The top stored trend (with its source) shapes
    # the concept; the response names it so she knows exactly what influenced the creative.
    trend_context, trend_used, trend_note = ("", {}, "")
    if trend:
        trend_context, trend_used, trend_note = top_trend_context(brand)

    if paid_images_enabled():
        try:
            real = _render_real(brand, headline=headline, language=language, product=product,
                                trend_context=trend_context)
            png = real.png
            if png is None:
                degraded_reason = "the image model returned no image"
            res = real.result
            trust_violation = bool(getattr(res, "trust_violation", False))
            trust_note_ar = str(getattr(res, "trust_note_ar", "") or "")
            # THE ART CRITIC'S REJECTION, carried. The pipeline's last resort is
            # "no attempt passed; returning best (score=3)" — a poster its own critic turned down
            # for amateur typography, invented products or a misspelled logo — and this response
            # carried only `trust_violation`, so that render reached the client looking exactly
            # like one that scored 9. `overall_pass` is tri-state: only an explicit False is a
            # rejection; None means the critic could not run, which is a different fact and is not
            # reported as failure.
            _qa = getattr(res, "qa", None)
            if _qa is not None and getattr(_qa, "overall_pass", None) is False:
                qa_failed = True
                qa_note = str(getattr(_qa, "reason", "") or "")
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
        "qa_failed": qa_failed,
        "qa_note": qa_note,
        # What we actually read from the pasted link, and why it did not work when it did not.
        "product": ({"name": product.name, "image": product.image, "price": product.price,
                     "currency": product.currency, "url": product.url} if product else None),
        "product_note": product_note,
        "trend_used": trend_used or None,
        "trend_note": trend_note,
        # «فوق الريل والبوست يبقى مكتوب كلام زي السوشيال» — every creative ships with the caption
        # to post it with. Failure degrades to a grounded tagline caption, never breaks the image.
        "social_caption": _caption_for(brand, language=language, trend_context=trend_context),
    }


def _caption_for(brand: dict, *, language: str, trend_context: str = "") -> dict:
    try:
        from api.webapp_social_copy import social_caption
        return social_caption(brand.get("_profile") or {}, language=language,
                              trend_context=trend_context)
    except Exception:  # noqa: BLE001 — a caption must never cost the client their image
        logger.exception("caption generation failed")
        return {}


# ---------------------------------------------------------------------
# on-disk store for URL-served images (calendar day posters)
# ---------------------------------------------------------------------

_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."


def _generated_dir() -> Path:
    d = webapp_dir() / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def unique_asset_name(domain_key: str, kind: str) -> str:
    """A filename no other asset will take.

    MEASURED: influencer portraits were saved as `{domain_key}_influencer_portrait`, a FIXED name,
    so the second portrait overwrote the first — which defeats "generate three, pick one, keep the
    ones you rejected" before a model is ever called. The UI hid it behind a `?t=` cache-buster.

    The random part is not just a counter, because `/api/generated/{name}` is public with no auth by
    design (it serves only already-generated PNGs). A name derived from the brand slug alone would
    let anyone who can guess that slug fetch the brand's faces. Same shape the poster pipeline
    already uses for its renders (`poster_{uuid4().hex[:8]}`)."""
    import re
    import uuid
    safe_key = re.sub(r"[^A-Za-z0-9_-]+", "_", str(domain_key or "brand")).strip("_") or "brand"
    safe_kind = re.sub(r"[^A-Za-z0-9_-]+", "_", str(kind or "asset")).strip("_") or "asset"
    return f"{safe_key}_{safe_kind}_{uuid.uuid4().hex[:10]}"


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
