"""Analysis routes: SWOT / strategy / marketing-plan generation (job-based) + SWOT approve (sync).

Each generate endpoint returns `{job_id}` immediately, runs the analysis in a worker thread, patches
the stored brand, and marks the job terminal. The frontend polls GET /api/jobs/{id} then re-fetches
the brand. All gated by require_user (mounted in main.py). Free / degrade-safe under D-5.
"""
from __future__ import annotations

import logging

from fastapi import Depends, APIRouter, HTTPException, Request
from pydantic import BaseModel

from .auth import require_user
from ..brand_store import BrandStore
from ..webapp_jobs import run_brand_job as _run  # re-exported for tests
from ..webapp_jobs import start_brand_job
from .. import webapp_analysis as analysis

router = APIRouter()
logger = logging.getLogger(__name__)

__all__ = ["router", "_run"]


class DomainKeyRequest(BaseModel):
    domain_key: str


class PlanRequest(BaseModel):
    domain_key: str
    planning_period: str = "1_month"
    campaign_objective: str = ""


def _brand_or_404(domain_key: str, user_id: str = "") -> dict:
    """Resolve a brand the CALLER is entitled to. A brand owned by another account resolves to 404,
    exactly as an absent one does — whether it exists elsewhere is not the caller's information."""
    brand = BrandStore().get(domain_key, for_user=user_id or None)
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    return brand


def _start_job(request: Request, domain_key: str, work, user_id: str) -> dict:
    """404 early if the brand is gone, then run `work(brand)->patch` as a background brand job.

    `user_id` is REQUIRED, deliberately. It was optional for one revision and one of the three call
    sites silently kept the default — `/api/swot/generate` accepted another account's brand and
    started paid work on it (caught by tests/test_idor_over_http.py, not by review). A scoping
    parameter with a permissive default is a hole waiting for the next caller to forget it."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    _brand_or_404(domain_key, user_id)
    return start_brand_job(request, domain_key, work, owner_id=user_id)


# --- SWOT ---
@router.post("/swot/generate")
async def swot_generate(req: DomainKeyRequest, request: Request,
                        user: dict = Depends(require_user)) -> dict:
    return _start_job(request, req.domain_key, analysis.generate_swot, user["id"])


@router.post("/swot/approve")
def swot_approve(req: DomainKeyRequest, user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    if not brand.get("has_swot"):
        raise HTTPException(status_code=409, detail="Generate the SWOT before approving it.")
    BrandStore().update(req.domain_key, {"swot_approved": True}, for_user=user["id"])
    return {"ok": True, "swot_approved": True}


# --- Strategy ---
@router.post("/strategy/generate")
async def strategy_generate(req: DomainKeyRequest, request: Request,
                            user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    if not brand.get("swot_approved"):
        raise HTTPException(status_code=409, detail="Approve the SWOT before generating the strategy.")
    return _start_job(request, req.domain_key, analysis.generate_strategy, user["id"])


# --- Marketing plan + calendar ---
@router.post("/marketing-plan/generate")
async def marketing_plan_generate(req: PlanRequest, request: Request,
                                  user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    if not brand.get("has_strategy"):
        raise HTTPException(status_code=409, detail="Generate the strategy before the marketing plan.")

    def _work(b: dict) -> dict:
        return analysis.generate_plan(b, planning_period=req.planning_period,
                                      campaign_objective=req.campaign_objective)

    return _start_job(request, req.domain_key, _work, user["id"])
