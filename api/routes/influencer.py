"""AI influencer routes (gated by require_user at mount time).

  POST /api/influencer/create   {domain_key, ...persona knobs} -> {job_id}  (first candidate)
  POST /api/influencer/another  {domain_key, ...persona knobs} -> {job_id}  (one MORE candidate)
  POST /api/influencer/select   {domain_key, candidate_id}     -> sync, locks the brand's face
  POST /api/influencer/validate {domain_key, approved}         -> legacy alias for select
  POST /api/influencer/poster   {domain_key, product_hint, scene_description} -> {job_id}

The owner's flow: describe the person, see one, ask for a second and third if she wants, pick one.
Faces are paid ONE AT A TIME by her ruling — `create` starts over, `another` appends (max 3), so no
route ever generates a set. `select` records WHICH face, because the old `validated` bool knew only
that something had been approved, not which portrait, and a later candidate inherited the approval.
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


class SelectRequest(BaseModel):
    domain_key: str
    candidate_id: str


class PosterRequest(BaseModel):
    domain_key: str
    product_hint: str = ""
    scene_description: str = ""


def _candidate_job(request: Request, req: "CreateRequest", user: dict, *, reset: bool) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    cfg: dict[str, Any] = req.model_dump()
    if not reset:
        existing = ((brand.get("ai_influencer") or {}).get("candidates") or {})
        if len(existing) >= influencer.MAX_CANDIDATES:
            raise HTTPException(
                status_code=409,
                detail=(f"You already have {influencer.MAX_CANDIDATES} looks to choose from. "
                        "Pick one, or start over to replace them."))
    if reset:
        # Starting over discards the old set deliberately — the owner asked for a fresh three.
        # REPLACE, not update: `_merge_one_level` recurses into dicts, so the empty `candidates`
        # dict this passes merged into the old one and removed nothing. MEASURED — the reset was a
        # no-op, the second click left two candidates, and the fourth hit MAX_CANDIDATES and
        # reached the user as "Generation step failed."
        BrandStore().replace_block(req.domain_key, "ai_influencer",
                                   {"candidates": {}, "selected_id": "",
                                    "has_portrait": False, "validated": False,
                                    "portrait_url": None},
                                   for_user=user["id"])
    return start_brand_job(request, req.domain_key,
                           lambda b: influencer.generate_candidate(b, cfg),
                           owner_id=user["id"])


@router.post("/influencer/create")
async def create(req: CreateRequest, request: Request, user: dict = Depends(require_user)) -> dict:
    """Start fresh: clear any previous looks and generate the FIRST candidate."""
    return _candidate_job(request, req, user, reset=True)


@router.post("/influencer/another")
async def another(req: CreateRequest, request: Request,
                  user: dict = Depends(require_user)) -> dict:
    """One MORE candidate — "التاني والتالت بطلب". Refuses past the third rather than spending."""
    return _candidate_job(request, req, user, reset=False)


@router.post("/influencer/select")
def select(req: SelectRequest, user: dict = Depends(require_user)) -> dict:
    """Lock ONE candidate in as the brand's face."""
    brand = _brand_or_404(req.domain_key, user["id"])
    try:
        patch = influencer.select_candidate(brand, req.candidate_id)
    except influencer.UnknownCandidate as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    BrandStore().update(req.domain_key, patch, for_user=user["id"])
    return {"ok": True, "selected_id": req.candidate_id}


@router.post("/influencer/validate")
def validate(req: ValidateRequest, user: dict = Depends(require_user)) -> dict:
    """Legacy shape kept for the existing UI: approving with only ONE candidate selects it.

    With several candidates there is no such thing as "approve" without saying WHICH — that
    ambiguity is exactly what let an approval slide onto a later face — so the caller is told to
    use /influencer/select instead of being given a silent guess."""
    brand = _brand_or_404(req.domain_key, user["id"])
    cands = (brand.get("ai_influencer") or {}).get("candidates") or {}
    if not cands:
        raise HTTPException(status_code=409, detail="Create a portrait before validating it.")
    if len(cands) > 1:
        raise HTTPException(
            status_code=409,
            detail="Several looks exist — choose one with /api/influencer/select {candidate_id}.")
    BrandStore().update(req.domain_key,
                        influencer.select_candidate(brand, next(iter(cands))),
                        for_user=user["id"])
    return {"ok": True, "validated": bool(req.approved)}


@router.post("/influencer/poster")
async def poster(req: PosterRequest, request: Request, user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    ai = brand.get("ai_influencer") or {}
    if not ai.get("selected_id"):
        raise HTTPException(status_code=409,
                            detail="Pick one of the looks before generating a poster.")
    return start_brand_job(request, req.domain_key,
                           lambda brand: influencer.generate_influencer_poster(
                               brand, product_hint=req.product_hint,
                               scene_description=req.scene_description),
                           owner_id=user["id"])
