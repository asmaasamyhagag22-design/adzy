"""Analysis routes: SWOT / strategy / marketing-plan generation (job-based) + SWOT approve (sync).

Each generate endpoint returns `{job_id}` immediately, runs the analysis in a worker thread, patches
the stored brand, and marks the job terminal. The frontend polls GET /api/jobs/{id} then re-fetches
the brand. All gated by require_user (mounted in main.py). Free / degrade-safe under D-5.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import require_user
from ..http_errors import reraise_as_http
from ..persistence import get_persistence
from ..webapp_jobs import run_brand_job as _run  # re-exported for tests
from ..webapp_jobs import start_brand_job
from .. import webapp_analysis as analysis

router = APIRouter()
logger = logging.getLogger(__name__)

__all__ = ["router", "_run"]


class DomainKeyRequest(BaseModel):
    domain_key: str


class ApproveRequest(BaseModel):
    domain_key: str
    # Validate subject AFTER brand authorization in the persistence boundary. Legacy documents
    # have no immutable Run identity; PostgreSQL requires a well-formed reviewed UUID.
    swot_run_id: Any = None


class PlanRequest(BaseModel):
    domain_key: str
    planning_period: str = "1_month"
    campaign_objective: str = ""


class StrategyRequest(BaseModel):
    domain_key: str
    # Parsed inside the handler AFTER ownership/SWOT gates, so an absent objective cannot turn a
    # foreign brand's truthful 404 (or an unapproved SWOT's 409) into a validation side channel.
    objective: dict[str, Any] | None = None
    requested_scope: dict[str, Any] = Field(default_factory=dict)


class StrategyDecisionRequest(BaseModel):
    domain_key: str
    strategy_run_id: str
    decision: str = "approved"
    note: str = ""


def _brand_or_404(domain_key: str, user_id: str = "") -> dict:
    """Resolve a brand the CALLER is entitled to. A brand owned by another account resolves to 404,
    exactly as an absent one does — whether it exists elsewhere is not the caller's information."""
    brand = get_persistence().visible_brand(domain_key=domain_key, user_id=user_id or "")
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    return brand


def _start_job(request: Request, domain_key: str, work, user_id: str, kind: str) -> dict:
    """404 early if the brand is gone, then run `work(brand)->patch` as a background brand job.

    `user_id` is REQUIRED, deliberately. It was optional for one revision and one of the three call
    sites silently kept the default — `/api/swot/generate` accepted another account's brand and
    started paid work on it (caught by tests/test_idor_over_http.py, not by review). A scoping
    parameter with a permissive default is a hole waiting for the next caller to forget it."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    _brand_or_404(domain_key, user_id)
    return start_brand_job(request, domain_key, work, owner_id=user_id, kind=kind)


# --- SWOT ---
@router.post("/swot/generate")
async def swot_generate(req: DomainKeyRequest, request: Request,
                        user: dict = Depends(require_user)) -> dict:
    return _start_job(request, req.domain_key, analysis.generate_swot, user["id"], kind="swot")


@router.post("/swot/approve")
def swot_approve(req: ApproveRequest, user: dict = Depends(require_user)) -> dict:
    """Approve the SWOT that is current RIGHT NOW — and refuse if that is not the one submitted.

    The stale case is not hypothetical: generation is asynchronous, so a background SWOT can land
    between the moment the panel renders and the moment the human clicks approve. Approving anyway
    would open the strategy gate for a document nobody reviewed.
    """
    _brand_or_404(req.domain_key, user["id"])
    try:
        outcome = get_persistence().approve_swot(
            domain_key=req.domain_key, user_id=user["id"], expected_run_id=req.swot_run_id)
    except Exception as exc:  # noqa: BLE001 — domain errors are translated; anything else re-raises
        reraise_as_http(exc)
        raise
    return {"ok": True, **outcome}


# --- Strategy ---
@router.post("/strategy/generate")
async def strategy_generate(req: StrategyRequest, request: Request,
                            user: dict = Depends(require_user)) -> dict:
    brand = _brand_or_404(req.domain_key, user["id"])
    if not brand.get("swot_approved"):
        raise HTTPException(status_code=409, detail="Approve the SWOT before generating the strategy.")
    persistence = get_persistence()
    if not persistence.supports_canonical_strategy():
        raise HTTPException(
            status_code=503,
            detail="Canonical strategy requires the PostgreSQL persistence backend.")
    try:
        from strategy.positioning import build_positioning_strategy, objective_from_mapping
        objective = objective_from_mapping(req.objective or {})
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid explicit business objective: {exc}") from exc

    swot_run_id = str(brand.get("swot_run_id") or "")
    if not swot_run_id:
        raise HTTPException(
            status_code=409,
            detail="The approved SWOT has no immutable Run identity; regenerate and approve it.")

    def _work(current: dict):
        if (str(current.get("swot_run_id") or "") != swot_run_id
                or not bool(current.get("swot_approved"))):
            # Generation is asynchronous. Never build from a newer/unapproved SWOT while filing
            # the captured approved Run as the dependency.
            raise RuntimeError("the approved SWOT changed before strategy generation started")
        return build_positioning_strategy(
            current, objective=objective, requested_scope=req.requested_scope,
            swot_run_id=swot_run_id)

    return _start_job(request, req.domain_key, _work, user["id"], kind="strategy")


@router.post("/strategy/decide")
def strategy_decide(req: StrategyDecisionRequest,
                    user: dict = Depends(require_user)) -> dict:
    _brand_or_404(req.domain_key, user["id"])
    if req.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="decision must be approved or rejected")
    try:
        return get_persistence().decide_strategy(
            domain_key=req.domain_key, user_id=user["id"], run_id=req.strategy_run_id,
            decision=req.decision, note=req.note)
    except Exception as exc:  # noqa: BLE001
        reraise_as_http(exc)
        raise


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

    # `calendar`: one plan run produces the marketing plan AND the content calendar together, and
    # the schema's vocabulary has one kind for that pair.
    return _start_job(request, req.domain_key, _work, user["id"], kind="calendar")


# --- Social performance: what actually gets the likes ---
#
# OWNER: «أكتر حاجة جايبة لايك». `social_performance_for` has existed, complete, since it was
# written and had ZERO non-test callers — a function that computes a result and returns it to
# nobody, which is this repo's most-repeated defect. Meanwhile four brands' snapshots sit in
# `runs/social_snapshots/` with 15 posts and real like counts each.
#
# SYNCHRONOUS AND UN-GATED FOR SPEND, deliberately: this reads a file off disk and does
# arithmetic. There is no model call and nothing to bill, so putting it behind the paid-job
# machinery would make a free read look expensive and wait on a worker thread for nothing.
@router.get("/social/performance/{domain_key}")
def social_performance(domain_key: str, user: dict = Depends(require_user)) -> dict:
    """Three DISTINCT states, and the caller must be able to tell them apart:

      {}                              — no snapshot: we have never collected this brand's social
      {checked: false, reason}        — collected, but the posts carried no engagement to measure
      {checked: true, own, peers, …}  — measured

    Collapsing the first two into "nothing here" is the same absence-read-as-an-answer defect the
    module itself was written to correct, one layer up in the presentation.
    """
    brand = _brand_or_404(domain_key, user["id"])
    out = analysis.social_performance_for(analysis._profile_of(brand))
    return out if out else {"collected": False}


# --- What the system is about to ask for, before any spend ---
#
# OWNER: «فيتشر ان البرومبت يظهر قبل الجينريشن». There was no such surface: the poster response
# carries its prompt, but only after the render, and the dashboard reads neither field.
#
# Free by construction — `api/prompt_preview.py` returns only what resolves deterministically from
# the brand's own evidence (audience language, locked register, look lock, grade family, tempo,
# derived scene count, whether the last beat can be a real product insert, the estimated Veo cost,
# and the director's full system prompt). No model is called, so a "preview before you spend" is
# not itself the spend. It is also the one creative surface that still answers while the Google
# project is billing-blocked.
@router.get("/prompt-preview/{domain_key}")
def prompt_preview(domain_key: str, kind: str = "reel", language: str = "auto",
                   max_seconds: float = 15.0, n_scenes: int = 5,
                   user: dict = Depends(require_user)) -> dict:
    from ..prompt_preview import preview_for
    brand = _brand_or_404(domain_key, user["id"])
    return preview_for(analysis._profile_of(brand), kind=kind, language=language,
                       max_seconds=max_seconds, n_scenes=n_scenes)


# --- The intelligence the run already produced and nobody could see ---
#
# MEASURED 2026-08-02 on Al Fouad's own stored run: a CLI analysis produces 20 blocks and 9 of them
# reach the dashboard. Two of the missing ones are not diagnostics — they are the analysis the
# client is paying for:
#
#   marketing_intel   the brand's own offers / USPs / pain points / message angles, each quoted
#                     with the post or page it came from
#   competitor_intel  the same, per competitor — what turns "you have 2 competitors" into an
#                     answer about what they promise and where they are weak
#
# They live only under `_full_result`, which the brand projection does not send, so the work was
# computed, serialized and unreadable — this repo's most-repeated defect, and the same one the
# social-performance route above exists to correct.
#
# Free: reads the stored record and reshapes it. No model, no network, nothing billable.
_INTEL_LISTS = ("offers", "usps", "pain_points", "message_angles")


def _intel_block(raw: Any) -> dict:
    """One intel record -> {items{…}, total, rejected, empty} with its evidence kept.

    `rejected` is carried on purpose. A block with nothing in it and `rejected: 4` means the
    grounding gate threw four ungrounded claims away; the same block with `rejected: 0` means
    nothing was ever found. Reporting both as "no data" is the absence-read-as-an-answer defect,
    and it is exactly what a client would ask about first.
    """
    src = raw if isinstance(raw, dict) else {}
    out: dict = {"items": {}, "total": 0, "rejected": int(src.get("rejected") or 0)}
    for key in _INTEL_LISTS:
        rows = []
        for it in (src.get(key) or []):
            if not isinstance(it, dict):
                continue
            value = str(it.get("value") or "").strip()
            if not value:
                continue
            ev = [e for e in (it.get("evidence") or []) if isinstance(e, dict)]
            rows.append({
                "value": value,
                "quote": str((ev[0].get("quote") if ev else "") or "").strip(),
                "source_url": str((ev[0].get("page_url") if ev else "") or "").strip(),
                "sources": len(ev),
            })
        out["items"][key] = rows
        out["total"] += len(rows)
    out["empty"] = out["total"] == 0
    return out


@router.get("/intel/{domain_key}")
def brand_intel(domain_key: str, user: dict = Depends(require_user)) -> dict:
    """What the analysis learned about this brand and about each competitor.

    THREE STATES, kept apart all the way to the client — the run was never done, the run was done
    and found nothing, the run found something:

      {"analysed": false}                          no stored analysis to read
      {"analysed": true, "own": {empty: true, …}}  analysed; nothing survived for this brand
      {"analysed": true, "own": {...}, "peers": …} analysed, with the evidence behind each line
    """
    brand = _brand_or_404(domain_key, user["id"])
    full = brand.get("_full_result") or {}
    if not full:
        return {"analysed": False}

    peers = []
    for key, raw in (full.get("competitor_intel") or {}).items():
        block = _intel_block(raw)
        block["key"] = str(key)
        block["name"] = str((raw or {}).get("brand_ref") or key).replace("_", " ")
        peers.append(block)
    peers.sort(key=lambda b: (-b["total"], b["name"]))

    return {
        "analysed": True,
        "own": _intel_block(full.get("marketing_intel")),
        "peers": peers,
        # A peer list of zero and a peer list of two-that-yielded-nothing are different findings
        # about the market, and the panel must be able to say which.
        "peers_analysed": len(peers),
    }
