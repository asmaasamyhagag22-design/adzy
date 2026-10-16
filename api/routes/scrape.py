"""POST /api/scrape — scrape a URL, build a BusinessProfile, store it as a brand.

Returns `{job_id}` immediately; the frontend polls GET /api/jobs/{id} until status == "done", then
fetches GET /api/brands/{domain_key}. Reuses the existing pipeline (`run_pipeline_job`) verbatim and
translates its BusinessProfile into the brand object via `profile_to_brand`.

Free under D-5: with no LLM creds, `has_llm()` is False so we run rules-only (skip_llm=True). With
creds present the LLM enriches the profile; if the call 403s, the runner degrades to a rules profile
and we surface a soft `warning` — never a hard failure.

ONE JOB ID, NOT TWO. This route used to create an in-memory job for the SPA to poll AND a separate
durable "ticket" for PostgreSQL. Only one of them was ever polled, so under the durable backend the
client watched a handle that knew nothing about the work. `begin_scrape` now returns the id the
caller will actually poll, on whichever backend is selected.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import Depends, APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from scraper.config import (
    ECOMMERCE_BUDGET_SECONDS,
    ECOMMERCE_MAX_INTERNAL_PAGES,
    INTER_PAGE_DELAY_SECONDS,
    MAX_CRAWL_DEPTH,
)
from scraper.url_utils import is_safe_public_url

from .auth import require_user
from ..persistence import get_persistence
from ..brand_translate import domain_key_from_url, profile_to_brand
from ..jobs.recorder import JobRecorder
from ..jobs.runner import has_llm, run_pipeline_job

router = APIRouter()
logger = logging.getLogger(__name__)

# profile_to_brand emits these empty run-state placeholders for a NEW brand.  During a re-scrape
# they are absence, not an instruction to erase analysis/creative work that may have committed
# while crawling.  Base profile fields still replace from the fresh scrape.
_PRESERVE_ON_RESCRAPE = {
    "has_swot", "swot_approved", "has_strategy", "swot", "marketing_strategy",
    "marketing_plan", "content_calendar", "ai_influencer",
}


class ScrapeRequest(BaseModel):
    url: str
    # The crawler's page cap counts internal frontier slots; the homepage is always depth 0 and is
    # not charged to this value.  The web product has historically advertised a smaller 20-page
    # default than the direct scraper/CLI, so preserve that API contract rather than changing the
    # crawler's independent default.
    max_pages: int = Field(default=20, ge=0, le=ECOMMERCE_MAX_INTERNAL_PAGES)
    max_depth: int = Field(default=MAX_CRAWL_DEPTH, ge=0, le=MAX_CRAWL_DEPTH)
    delay: float = Field(
        default=INTER_PAGE_DELAY_SECONDS,
        ge=0,
        le=ECOMMERCE_BUDGET_SECONDS,
        allow_inf_nan=False,
    )


@router.post("/scrape")
async def scrape(req: ScrapeRequest, request: Request,
                 user: dict = Depends(require_user)) -> dict:
    if not is_safe_public_url(req.url):
        raise HTTPException(
            status_code=400,
            detail="URL not allowed: must be a public http(s) address (no localhost/internal hosts).")
    # OWNERSHIP PREFLIGHT — a COST gate, and only that.
    #
    # Without it the crawl and the LLM enrichment are paid for in full and the request is refused at
    # the last step, by `upsert`, with the work already spent. Absent / unclaimed / mine all
    # proceed; only a brand ANOTHER account manages is turned away, and the refusal names no
    # account and returns none of that brand's data.
    #
    # This is deliberately NOT the concurrency authority. Two callers racing an unclaimed brand can
    # both pass here; the commit re-reads the owner under the storage lock at the final write and is
    # what decides the winner. Never treat passing this check as permission to write.
    domain_key = domain_key_from_url(req.url)
    persistence = get_persistence()
    current_owner = persistence.owner_of_domain(domain_key)
    if current_owner and current_owner != str(user["id"]):
        raise HTTPException(
            status_code=409,
            detail="This website is already managed by another account.")

    # JOB FIRST, BEFORE THE CRAWL (§2/§8). For a first-ever domain there is no Brand and there must
    # not be one: a Brand created up front to hold the job would outlive a failed scrape as a
    # namespace nobody asked for, indistinguishable later from a real but empty brand. The job
    # carries `requested_domain_key` instead, and the Brand is created only inside the success
    # transaction. `domain_key` is a deterministic function of the URL, so it is known now and a
    # fast poll after `done` never races the brand write.
    job_id = persistence.begin_scrape(domain_key=domain_key, user_id=user["id"])
    recorder = JobRecorder(job_id=job_id, provider=persistence)
    asyncio.create_task(_run_scrape_job(job_id=job_id, url=req.url, recorder=recorder,
                                        domain_key=domain_key,
                                        max_pages=req.max_pages, max_depth=req.max_depth,
                                        delay=req.delay, owner_id=user["id"]))
    return {"job_id": job_id}


async def _run_scrape_job(
    *, job_id: str, url: str, recorder: JobRecorder, domain_key: str,
    max_pages: int, max_depth: int, delay: float, owner_id: str = "",
) -> None:
    persistence = get_persistence()
    try:
        final_payload = await asyncio.to_thread(
            run_pipeline_job, job_id, url, recorder, skip_llm=not has_llm(),
            persist_to_disk=False, defer_final_event=True,
            max_pages=max_pages, max_depth=max_depth, delay=delay)
        if final_payload is None:
            # The pipeline emitted its own terminal ERROR event, which IS the failure transition on
            # both backends. This call is the belt: if that event never landed, the job must not be
            # left running forever (§11); if it did, terminal state is immutable and this is a
            # no-op that cannot turn a failure into anything else.
            await asyncio.to_thread(
                persistence.finish_job_failure, job_id=job_id,
                error="Scrape processing failed.",
                payload={"error_type": "PipelineFailure", "persistence": False})
            return
        try:
            warning = await _finish_scrape(job_id, url, recorder, domain_key=domain_key,
                                           owner_id=owner_id)
        except Exception as e:  # noqa: BLE001 — detailed internally, safe at the public boundary
            logger.exception("failed to persist brand for job %s: %s", job_id, e)
            await asyncio.to_thread(
                persistence.finish_job_failure, job_id=job_id,
                error="Brand profile could not be saved.",
                payload={"error_type": type(e).__name__, "persistence": True})
            return
        # TERMINAL STATE. Under PostgreSQL `commit_scrape` already committed the Brand, the Run, the
        # job's terminal status AND its terminal event in ONE transaction — so this verifies rather
        # than publishes, and adds no second terminal event. Under legacy it IS the publication.
        # Either way exactly one terminal event exists when this returns.
        published = await asyncio.to_thread(
            persistence.finish_job_success, job_id=job_id, payload=final_payload,
            warning=warning)
        if not published:
            # The brand committed, so this must NOT be turned into a failure. It is logged because
            # a succeeded business write beside a job that never said so is the Class-A shape, and
            # a silent one is worse than a loud one.
            logger.error("scrape job %s committed its brand but published no terminal state",
                         job_id)
    except Exception as e:  # noqa: BLE001 — last-resort guard; the runner emits its own error events
        logger.exception("scrape job %s failed outside the pipeline: %s", job_id, e)
        try:
            await asyncio.to_thread(
                persistence.finish_job_failure, job_id=job_id,
                error="Scrape processing failed.",
                payload={"error_type": type(e).__name__, "persistence": False})
        except Exception:  # noqa: BLE001 — logged; the original failure is what matters
            logger.exception("could not terminalize scrape job %s", job_id)


async def _finish_scrape(job_id: str, url: str, recorder: JobRecorder, *,
                         domain_key: str, owner_id: str = "") -> str:
    """Translate the finished profile into a brand and persist it. Returns any soft warning.

    The profile comes off the RECORDER, not out of a job store: it is the pipeline's return value
    being handed to the finalizer in the same process, and reading it back out of a store would
    mean the durable backend had to have written it before the job succeeded — which is exactly the
    committed-result-beside-a-running-job window this design closes.
    """
    profile = recorder.result
    if not profile:
        raise RuntimeError("pipeline completed without a profile result")
    brand = profile_to_brand(profile, source_url=url, domain_key=domain_key)
    # Stash the raw BusinessProfile (frontend ignores unknown keys) so the analysis stages
    # (swot/strategy/plan) can feed the real pipeline instead of the lossy brand projection.
    brand["_profile"] = profile
    warning = ""
    if profile.get("llm_silent_failure"):
        warning = ("Brand understanding degraded (the AI step was unavailable); "
                   "showing rules-based facts.")
    # The account that requested the scrape owns the result. Brands stored before ownership
    # existed carry no owner and are surfaced as `unclaimed` rather than assigned to a guess.
    # OFF THE LOOP. The write takes a process RLock and a cross-process file lock (legacy) or a
    # brand row lock (PostgreSQL), so a competing writer holds this for as long as ITS write takes.
    # On the event-loop thread that is not one slow request — it is every request, every SSE stream
    # and every background job on the process, stopped for the duration. MEASURED: a 1.5s competing
    # write froze the loop for 1.468s.
    #
    # This moves WHERE the write runs, never WHAT it decides: C2's ownership re-check and the
    # claim-on-success still happen inside the commit, under the same lock, against the same re-read
    # latest state. A worker thread is still one caller among many to that lock.
    persisted = await asyncio.to_thread(
        get_persistence().commit_scrape,
        domain_key=domain_key, user_id=owner_id or "", ticket=job_id,
        brand=brand, profile=profile, source_url=url,
        display_name=str(brand.get("name") or ""),
        warning=warning)
    if persisted is None:
        raise OSError("brand changed ownership or the update was not applied")
    return warning
