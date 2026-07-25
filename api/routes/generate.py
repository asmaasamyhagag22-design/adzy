"""Image generation routes (gated by require_user at mount time).

  POST /api/generate            (JSON)      text->image
  POST /api/generate/upload     (multipart) + a logo file
  POST /api/generate/reference  (multipart) + a reference photo
  POST /api/edit/inpaint        (multipart) image + mask  (stub: returns the image unchanged)
  POST /api/calendar/generate-image (JSON)  -> {image_url} (persisted on the calendar entry)

All return the frontend's shape. Degrade-safe: a free branded placeholder unless
BASEERA_ENABLE_PAID_IMAGES=1 (see api/webapp_images.py). Never touches reel.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

from fastapi import Depends, APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .auth import require_user
from ..brand_store import BrandStore, record_history
from ..upload_limits import read_bounded
from .. import webapp_images as images

router = APIRouter()
logger = logging.getLogger(__name__)


def _brand_or_404(domain_key: str, user_id: str = "") -> dict:
    """Scoped like every other brand read — another account's brand resolves to 404."""
    brand = BrandStore().get(domain_key, for_user=user_id or None)
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    return brand


# An uploaded logo or product photo is bounded, read — and then reaches no renderer, because
# neither `generate_image` nor `_render_real` takes an image argument. Wiring it in is new work.
# Saying so is not, and the alternative is a user who uploaded their real product being handed one
# the model imagined, by a system whose whole claim is that it does not imagine. Same shape as
# `_INPAINT_NOTE` below: the brand's own language, chosen with `images.brand_language`.
_IGNORED_UPLOAD_NOTE = {
    "ar": "الصورة اللي رفعتيها لسه مش داخلة في التوليد — البوستر اتعمل من بروفايل البراند بس.",
    "fr": "L'image envoyée n'alimente pas encore la génération — le visuel vient du profil de marque.",
    "es": "La imagen subida aún no alimenta la generación — el visual viene del perfil de marca.",
}
_IGNORED_UPLOAD_DEFAULT = ("Your uploaded image does not feed the generation yet — this visual was "
                           "built from the brand profile alone.")


def _emit(brand: dict, *, headline: str, subtext: str, cta: str, style: str,
          content_type: str, owner_id: str = "", ignored_upload: str = "",
          product_url: str = "", language: str = "auto", trend: bool = False) -> dict:
    # `product_url` is the owner's «بحط اللينك بتاعه»: an ad for ONE named item rather than for the
    # brand in general. `generate_poster` has accepted `product_image`/`product_name` all along —
    # this function was simply the wall they never got past, which is also why the
    # `product_name`/`product_description` form fields below reached nothing.
    out = images.generate_image(brand, headline=headline, subtext=subtext, cta=cta,
                                content_type=content_type, product_url=product_url,
                                language=(language if language in ("ar", "en") else "auto"),
                                trend=trend)
    if ignored_upload:
        lang = images.brand_language(brand)
        out = {**out, "applied_upload": False,
               "note": _IGNORED_UPLOAD_NOTE.get(lang, _IGNORED_UPLOAD_DEFAULT)}
    record_history({
        "image_b64": out["image_b64"], "filename": out["filename"],
        "brand_name": brand.get("organization_name", ""), "domain_key": brand.get("domain_key", ""),
        "content_type": content_type, "style": style,
        # provenance travels with the gallery entry, so a disclosed image never loses its disclosure
        "is_preview": out.get("is_preview", True),
        "trust_violation": out.get("trust_violation", False),
    }, owner_id=owner_id)
    return out


# ---- JSON: text -> image ----
class GenerateRequest(BaseModel):
    domain_key: str
    content_type: str = "post"
    style: str = ""
    headline: str = ""
    subtext: str = ""
    cta: str = ""
    user_context: str = ""
    mode: str = "text_to_image"
    language: str = "auto"         # "ar" | "en" | "auto" — the owner's choice, not an inference
    trend: bool = False            # «اضغط قبلها على ترند» — shape the creative with the top stored trend
    content_only: bool = False     # «الكونتنت بس» — caption alone, no image, zero render spend
    product_url: str = ""          # paste a product page; the poster is built around THAT item
    product_name: str = ""
    product_description: str = ""


@router.post("/generate")
def generate(req: GenerateRequest, user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    if req.content_only:
        # «الكونتنت بس من غير ريل ولا صورة» — the caption alone. No renderer in the call path,
        # so zero image spend by construction; the trend toggle still shapes the words.
        from api.webapp_images import top_trend_context
        from api.webapp_social_copy import content_only as _content_only
        ctx, used, note = top_trend_context(brand) if req.trend else ("", {}, "")
        out = _content_only(brand.get("_profile") or {},
                            language=(req.language if req.language in ("ar", "en") else "auto"),
                            trend_context=ctx)
        out["trend_used"] = used or None
        out["trend_note"] = note
        return out
    return _emit(brand, headline=req.headline, subtext=req.subtext, cta=req.cta,
                 style=req.style, content_type=req.content_type, owner_id=user["id"],
                 product_url=req.product_url, language=req.language, trend=req.trend)


# ---- multipart: + a logo ----
@router.post("/generate/upload")
async def generate_with_logo(
    domain_key: str = Form(...),
    content_type: str = Form("post"),
    style: str = Form(""),
    headline: str = Form(""),
    subtext: str = Form(""),
    cta: str = Form(""),
    ai_text: str = Form("false"),
    product_url: str = Form(""),
    product_name: str = Form(""),
    product_description: str = Form(""),
    logo: Optional[UploadFile] = File(None),
    user: dict = Depends(require_user),
) -> dict:
    brand = _brand_or_404(domain_key, user["id"])
    if logo is not None:
        # Consumed and bounded. NOT forwarded anywhere: `generate_image` renders from the stored
        # profile and takes no image argument, so an uploaded logo reaches neither the placeholder
        # nor the paid path. The response now says so — see `_IGNORED_UPLOAD_NOTE`.
        await read_bounded(logo, field="logo")
    return _emit(brand, headline=headline, subtext=subtext, cta=cta, style=style,
                 content_type=content_type, owner_id=user["id"], product_url=product_url,
                 ignored_upload="logo" if logo is not None else "")


# ---- multipart: + a reference photo ----
@router.post("/generate/reference")
async def generate_with_reference(
    domain_key: str = Form(...),
    content_type: str = Form("post"),
    style: str = Form(""),
    headline: str = Form(""),
    subtext: str = Form(""),
    cta: str = Form(""),
    ai_text: str = Form("false"),
    reference_strength: str = Form("0.55"),
    product_url: str = Form(""),
    product_name: str = Form(""),
    product_description: str = Form(""),
    reference_image: Optional[UploadFile] = File(None),
    user: dict = Depends(require_user),
) -> dict:
    brand = _brand_or_404(domain_key, user["id"])
    if reference_image is not None:
        # Same as the logo above: bounded and consumed, but not plumbed into any renderer.
        await read_bounded(reference_image, field="reference_image")
    return _emit(brand, headline=headline, subtext=subtext, cta=cta, style=style,
                 content_type=content_type, owner_id=user["id"], product_url=product_url,
                 ignored_upload="reference_image" if reference_image is not None else "")


# ---- multipart: inpaint (NOT IMPLEMENTED — returns the input image and says so) ----
# Region editing needs a paid Imagen edit call (poster/imagen_edit_provider.py) and every Imagen
# model 404s on this project, so the request cannot be served. It used to return 200 with the
# original bytes and no other signal: the comment called that "an honest stub", but the honesty
# reached the developer reading the file, not the user who painted a mask and got their own picture
# back. `applied: false` + a note in their language is what makes it honest at the client.
_INPAINT_NOTE = {
    "ar": "تحرير جزء من الصورة لسه مش متاح — رجّعنا الصورة زي ما هي من غير تعديل.",
    "fr": "L'édition de zone n'est pas encore disponible — l'image est renvoyée inchangée.",
    "es": "La edición por región aún no está disponible — la imagen se devuelve sin cambios.",
}
_INPAINT_NOTE_DEFAULT = ("Region editing is not available yet — the image is returned unchanged, "
                         "with nothing applied.")


@router.post("/edit/inpaint")
async def inpaint(
    domain_key: str = Form(...),
    edit_prompt: str = Form(""),
    seed: str = Form("-1"),
    strength: str = Form("0.85"),
    language: str = Form(""),
    image: UploadFile = File(...),
    mask: Optional[UploadFile] = File(None),
    user: dict = Depends(require_user),
) -> dict:
    brand = _brand_or_404(domain_key, user["id"])
    src = await read_bounded(image, field="image")
    if mask is not None:
        await read_bounded(mask, field="mask")
    lang = (language or "").strip().lower()[:2] or images.brand_language(brand)
    return {
        "image_b64": base64.b64encode(src).decode("ascii"),
        "filename": f"{domain_key}_edit.png",
        "applied": False,
        "note": _INPAINT_NOTE.get(lang, _INPAINT_NOTE_DEFAULT),
    }


# ---- JSON: a poster for one calendar day -> {image_url} ----
class CalendarImageRequest(BaseModel):
    domain_key: str
    date: str = ""
    seed: int = -1


@router.post("/calendar/generate-image")
def calendar_generate_image(req: CalendarImageRequest, user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    # find the calendar entry for this date to theme the image
    entry = None
    for e in ((brand.get("content_calendar") or {}).get("calendar") or []):
        if e.get("date") == req.date:
            entry = e
            break
    headline = (entry or {}).get("topic") or brand.get("tagline", "")
    # D-8: the fallback CTA follows the brand's MEASURED language; "Learn more" was a hardcoded
    # English button on every Arabic brand's calendar poster.
    cta = (entry or {}).get("cta") or ""

    # Same honest gate as /api/generate — this used to call `_render_real` directly, which both
    # bypassed the disclosure and would now hand back a RealRender where bytes were expected.
    out = images.generate_image(brand, headline=headline, cta=cta, content_type="calendar")
    png = base64.b64decode(out["image_b64"])
    url = images.save_generated_png(png, f"{req.domain_key}_{(req.date or 'day')}")

    # persist the poster URL on the matching calendar entry so it survives a reload
    if entry is not None:
        entry["poster"] = {"url": url, "is_preview": out["is_preview"],
                           "trust_violation": out["trust_violation"]}
        BrandStore().save(brand)
    return {"image_url": url, "is_preview": out["is_preview"],
            "degraded_reason": out["degraded_reason"],
            "trust_violation": out["trust_violation"], "trust_note_ar": out["trust_note_ar"]}
