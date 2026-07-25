"""AI influencer routes (gated by require_user at mount time).

  POST /api/influencer/create   {domain_key, ...persona knobs} -> {job_id}  (portrait)
  POST /api/influencer/validate {domain_key, approved}         -> sync flag flip
  POST /api/influencer/poster   {domain_key, product_hint, scene_description} -> {job_id}

Gated by the flow: poster requires an APPROVED portrait. Degrade-safe placeholders (see
api/webapp_influencer.py). Never touches reel.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, APIRouter, HTTPException, Request
from pydantic import BaseModel

from .auth import require_user
from ..brand_store import BrandStore
from ..webapp_jobs import start_brand_job
from .. import webapp_influencer as influencer

router = APIRouter()


def _brand_or_404(domain_key: str, user_id: str = "") -> dict:
    """Scoped like every other brand read — another account's brand resolves to 404."""
    brand = BrandStore().get(domain_key, for_user=user_id or None)
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    return brand


class CreateRequest(BaseModel):
    domain_key: str
    gender: str = ""
    beard: str = ""
    hair_length: str = ""
    hair_style: str = ""
    hair_color: str = ""
    skin: str = ""
    age: str = ""
    glasses: str = ""
    expression: str = ""
    persona: str = ""
    seed: Optional[int] = None


class ValidateRequest(BaseModel):
    domain_key: str
    approved: bool = True


class PosterRequest(BaseModel):
    domain_key: str
    product_hint: str = ""
    scene_description: str = ""


@router.post("/influencer/create")
async def create(req: CreateRequest, request: Request, user: dict = Depends(require_user)) -> dict:
    _brand_or_404(req.domain_key, user["id"])
    cfg: dict[str, Any] = req.model_dump()
    return start_brand_job(request, req.domain_key,
                           lambda brand: influencer.generate_portrait(brand, cfg),
                           owner_id=user["id"])


@router.post("/influencer/validate")
def validate(req: ValidateRequest, user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    if not (brand.get("ai_influencer") or {}).get("has_portrait"):
        raise HTTPException(status_code=409, detail="Create a portrait before validating it.")
    BrandStore().update(req.domain_key, influencer.validate_portrait(brand, req.approved),
                        for_user=user["id"])
    return {"ok": True, "validated": bool(req.approved)}


@router.post("/influencer/poster")
async def poster(req: PosterRequest, request: Request, user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    ai = brand.get("ai_influencer") or {}
    if not (ai.get("has_portrait") and ai.get("validated")):
        raise HTTPException(status_code=409,
                            detail="Approve the influencer portrait before generating a poster.")
    return start_brand_job(request, req.domain_key,
                           lambda brand: influencer.generate_influencer_poster(
                               brand, product_hint=req.product_hint,
                               scene_description=req.scene_description),
                           owner_id=user["id"])
