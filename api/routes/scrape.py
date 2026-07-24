"""POST /api/scrape — scrape a URL, build a BusinessProfile, store it as a brand.

Returns `{job_id}` immediately; the frontend polls GET /api/jobs/{id} until status == "done", then
fetches GET /api/brands/{domain_key}. Reuses the existing pipeline (`run_pipeline_job`) verbatim and
translates its BusinessProfile into the brand object via `profile_to_brand`.

Free under D-5: with no LLM creds, `has_llm()` is False so we run rules-only (skip_llm=True). With
creds present the LLM enriches the profile; if the call 403s, the runner degrades to a rules profile
and we surface a soft `warning` — never a hard failure.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from scraper.url_utils import is_safe_public_url

from ..brand_store import BrandStore
from ..brand_translate import domain_key_from_url, profile_to_brand
from ..jobs.runner import has_llm, run_pipeline_job

router = APIRouter()
logger = logging.getLogger(__name__)


class ScrapeRequest(BaseModel):
    url: str
    max_pages: int = 20
    max_depth: int = 3
    delay: float = 1.0


@router.post("/scrape")
async def scrape(req: ScrapeRequest, request: Request) -> dict:
    if not is_safe_public_url(req.url):
        raise HTTPException(
            status_code=400,
            detail="URL not allowed: must be a public http(s) address (no localhost/internal hosts).")
    store = request.app.state.store
    job = store.create()
    # domain_key is a deterministic function of the URL — set it now so a fast poll after `done`
    # never races the brand write.
    job.domain_key = domain_key_from_url(req.url)
    store.attach_loop(job.job_id, asyncio.get_running_loop())
    asyncio.create_task(_run_scrape_job(job_id=job.job_id, url=req.url, store=store))
    return {"job_id": job.job_id}


async def _run_scrape_job(*, job_id: str, url: str, store) -> None:
    try:
        # The pipeline runs in a worker thread; it lands the final profile on `job.result` via a
        # loop callback (set_result_threadsafe). The `sleep(0)` flushes that callback onto THIS loop
        # so `job.result` is populated before we translate it into a brand.
        await asyncio.to_thread(
            run_pipeline_job, job_id, url, store, skip_llm=not has_llm(), persist_to_disk=False)
        await asyncio.sleep(0)
        _finish_scrape(job_id, url, store)
    except Exception as e:  # noqa: BLE001 — last-resort guard; the runner emits its own error events
        logger.exception("scrape job %s failed outside the pipeline: %s", job_id, e)


def _finish_scrape(job_id: str, url: str, store) -> None:
    """Translate the finished profile into a brand and persist it (runs on the loop; fast file I/O)."""
    job = store.get(job_id)
    if job is None or job.result is None:
        return  # the runner already emitted an ERROR event; nothing to store
    brand = profile_to_brand(job.result, source_url=url, domain_key=job.domain_key)
    # Stash the raw BusinessProfile (frontend ignores unknown keys) so the analysis stages
    # (swot/strategy/plan) can feed the real pipeline instead of the lossy brand projection.
    brand["_profile"] = job.result
    if job.result.get("llm_silent_failure"):
        job.warning = ("Brand understanding degraded (the AI step was unavailable); "
                       "showing rules-based facts.")
    try:
        BrandStore().save(brand)
    except Exception:  # noqa: BLE001
        logger.exception("failed to persist brand for job %s", job_id)
