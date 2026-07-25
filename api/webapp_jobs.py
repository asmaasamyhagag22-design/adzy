"""Shared job runner for web-app brand-enrichment jobs (analysis, influencer).

A `work(brand)->patch` function runs in a worker thread; the returned patch is shallow-merged into
the stored brand, then the job is marked terminal. The frontend polls GET /api/jobs/{id} then
re-fetches the brand. These jobs don't stream, so we set the terminal status directly on the loop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import Request

from .brand_store import BrandStore
from .schemas import JobStatus

logger = logging.getLogger(__name__)


def start_brand_job(request: Request, domain_key: str, work, owner_id: str = "") -> dict:
    """Create a job bound to a brand, run `work` in a thread, persist the patch, mark terminal."""
    store = request.app.state.store
    job = store.create()
    job.domain_key = domain_key
    store.attach_loop(job.job_id, asyncio.get_running_loop())
    asyncio.create_task(run_brand_job(job_id=job.job_id, domain_key=domain_key, store=store,
                                      work=work, owner_id=owner_id))
    return {"job_id": job.job_id}


async def run_brand_job(*, job_id: str, domain_key: str, store, work,
                        owner_id: str = "") -> None:
    job = store.get(job_id)
    try:
        patch = await asyncio.to_thread(_do_work, domain_key, work, owner_id)
        if patch is not None:
            # Scoped: an enrichment job must not be able to rewrite another account's analysis.
            BrandStore().update(domain_key, patch, for_user=owner_id or None)
        job.status = JobStatus.SUCCEEDED
        job.current_stage = None
    except Exception as e:  # noqa: BLE001
        logger.exception("brand job %s failed: %s", job_id, e)
        job.status = JobStatus.FAILED
        job.error = "Generation step failed."
    finally:
        job.finished_at = time.monotonic()
        job.updated_at = datetime.now(timezone.utc)


def _do_work(domain_key: str, work, owner_id: str = "") -> dict:
    brand = BrandStore().get(domain_key, for_user=owner_id or None) or {}
    return work(brand)
