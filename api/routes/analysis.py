"""Analysis routes: SWOT / strategy / marketing-plan generation (job-based) + SWOT approve (sync).

Each generate endpoint returns `{job_id}` immediately, runs the analysis in a worker thread, patches
the stored brand, and marks the job terminal. The frontend polls GET /api/jobs/{id} then re-fetches
the brand. All gated by require_user (mounted in main.py). Free / degrade-safe under D-5.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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


def _brand_or_404(domain_key: str) -> dict:
    brand = BrandStore().get(domain_key)
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    return brand


def _start_job(request: Request, domain_key: str, work) -> dict:
    """404 early if the brand is gone, then run `work(brand)->patch` as a background brand job."""
    _brand_or_404(domain_key)
    return start_brand_job(request, domain_key, work)


# --- SWOT ---
@router.post("/swot/generate")
async def swot_generate(req: DomainKeyRequest, request: Request) -> dict:
    return _start_job(request, req.domain_key, analysis.generate_swot)


@router.post("/swot/approve")
def swot_approve(req: DomainKeyRequest) -> dict:
    brand = _brand_or_404(req.domain_key)
    if not brand.get("has_swot"):
        raise HTTPException(status_code=409, detail="Generate the SWOT before approving it.")
    BrandStore().update(req.domain_key, {"swot_approved": True})
    return {"ok": True, "swot_approved": True}


# --- Strategy ---
@router.post("/strategy/generate")
async def strategy_generate(req: DomainKeyRequest, request: Request) -> dict:
    brand = _brand_or_404(req.domain_key)
    if not brand.get("swot_approved"):
        raise HTTPException(status_code=409, detail="Approve the SWOT before generating the strategy.")
    return _start_job(request, req.domain_key, analysis.generate_strategy)


# --- Marketing plan + calendar ---
@router.post("/marketing-plan/generate")
async def marketing_plan_generate(req: PlanRequest, request: Request) -> dict:
    brand = _brand_or_404(req.domain_key)
    if not brand.get("has_strategy"):
        raise HTTPException(status_code=409, detail="Generate the strategy before the marketing plan.")

    def _work(b: dict) -> dict:
        return analysis.generate_plan(b, planning_period=req.planning_period,
                                      campaign_objective=req.campaign_objective)

    return _start_job(request, req.domain_key, _work)
