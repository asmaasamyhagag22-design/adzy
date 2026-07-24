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

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..brand_store import BrandStore, record_history
from .. import webapp_images as images

router = APIRouter()
logger = logging.getLogger(__name__)


def _brand_or_404(domain_key: str) -> dict:
    brand = BrandStore().get(domain_key)
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    return brand


def _emit(brand: dict, *, headline: str, subtext: str, cta: str, style: str,
          content_type: str) -> dict:
    out = images.generate_image(brand, headline=headline, subtext=subtext, cta=cta,
                                content_type=content_type)
    record_history({
        "image_b64": out["image_b64"], "filename": out["filename"],
        "brand_name": brand.get("organization_name", ""), "domain_key": brand.get("domain_key", ""),
        "content_type": content_type, "style": style,
    })
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
    product_name: str = ""
    product_description: str = ""


@router.post("/generate")
def generate(req: GenerateRequest) -> dict:
    brand = _brand_or_404(req.domain_key)
    return _emit(brand, headline=req.headline, subtext=req.subtext, cta=req.cta,
                 style=req.style, content_type=req.content_type)


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
    product_name: str = Form(""),
    product_description: str = Form(""),
    logo: Optional[UploadFile] = File(None),
) -> dict:
    brand = _brand_or_404(domain_key)
    if logo is not None:
        await logo.read()  # consume the upload (used by the paid path; placeholder ignores it)
    return _emit(brand, headline=headline, subtext=subtext, cta=cta, style=style,
                 content_type=content_type)


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
    product_name: str = Form(""),
    product_description: str = Form(""),
    reference_image: Optional[UploadFile] = File(None),
) -> dict:
    brand = _brand_or_404(domain_key)
    if reference_image is not None:
        await reference_image.read()
    return _emit(brand, headline=headline, subtext=subtext, cta=cta, style=style,
                 content_type=content_type)


# ---- multipart: inpaint (stub returns the input image unchanged) ----
@router.post("/edit/inpaint")
async def inpaint(
    domain_key: str = Form(...),
    edit_prompt: str = Form(""),
    seed: str = Form("-1"),
    strength: str = Form("0.85"),
    image: UploadFile = File(...),
    mask: Optional[UploadFile] = File(None),
) -> dict:
    _brand_or_404(domain_key)
    src = await image.read()
    if mask is not None:
        await mask.read()
    # Real region-editing needs a paid Imagen edit call (poster/imagen_edit_provider.py), disabled
    # under D-5. The honest stub returns the image UNCHANGED so the plumbing works end to end.
    return {
        "image_b64": base64.b64encode(src).decode("ascii"),
        "filename": f"{domain_key}_edit.png",
    }


# ---- JSON: a poster for one calendar day -> {image_url} ----
class CalendarImageRequest(BaseModel):
    domain_key: str
    date: str = ""
    seed: int = -1


@router.post("/calendar/generate-image")
def calendar_generate_image(req: CalendarImageRequest) -> dict:
    brand = _brand_or_404(req.domain_key)
    # find the calendar entry for this date to theme the image
    entry = None
    for e in ((brand.get("content_calendar") or {}).get("calendar") or []):
        if e.get("date") == req.date:
            entry = e
            break
    headline = (entry or {}).get("topic") or brand.get("tagline", "")
    cta = (entry or {}).get("cta") or "Learn more"

    png = images.render_placeholder(brand, headline=headline, cta=cta)
    if images.paid_images_enabled():
        real = images._render_real(brand, headline=headline, language="auto")
        if real:
            png = real
    url = images.save_generated_png(png, f"{req.domain_key}_{(req.date or 'day')}")

    # persist the poster URL on the matching calendar entry so it survives a reload
    if entry is not None:
        entry["poster"] = {"url": url}
        BrandStore().save(brand)
    return {"image_url": url}
