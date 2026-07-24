"""Job-status routes:

- GET /api/jobs/{job_id}          → snapshot (status + event history)
- GET /api/jobs/{job_id}/stream   → SSE live event stream
- GET /api/jobs/{job_id}/result   → final BusinessProfile JSON
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ..jobs.store import STREAM_CLOSE, Job
from ..schemas import (
    JobResultResponse, JobStatus, Stage, StageEvent,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Progress % + human step label per pipeline stage — the AdArchitect frontend renders a progress bar
# and a rotating status line off these (its 7 cosmetic stages are driven purely by `progress`).
_STAGE_PROGRESS: dict[Stage, tuple[int, str]] = {
    Stage.SCRAPE: (15, "Crawling the website"),
    Stage.RULES: (35, "Extracting brand facts"),
    Stage.EVIDENCE_PACK: (50, "Assembling evidence"),
    Stage.LLM: (75, "Understanding the brand"),
    Stage.VALIDATE: (85, "Verifying against sources"),
    Stage.MERGE: (92, "Building the profile"),
    Stage.FINAL: (100, "Done"),
}

# The frontend polls until status is one of these; anything else means "keep polling".
_STATUS_MAP = {
    JobStatus.QUEUED: "running",
    JobStatus.RUNNING: "running",
    JobStatus.SUCCEEDED: "done",
    JobStatus.FAILED: "error",
}


def _webapp_job_view(job: Job) -> dict:
    """The shape the frontend expects: status ∈ {running,done,error} + progress/step/domain_key."""
    status = _STATUS_MAP.get(job.status, "running")
    progress, step = _STAGE_PROGRESS.get(job.current_stage or Stage.SCRAPE, (5, "Starting"))
    if status == "done":
        progress, step = 100, "Done"
    elif status == "error":
        step = "Failed"
    return {
        "job_id": job.job_id,
        "status": status,
        "progress": progress,
        "step": step,
        "domain_key": job.domain_key,
        "warning": job.warning,
        "error": job.error,
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict:
    job = request.app.state.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    return _webapp_job_view(job)


@router.get("/jobs/{job_id}/result", response_model=JobResultResponse)
async def get_job_result(job_id: str, request: Request) -> JobResultResponse:
    job = request.app.state.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    if job.status == JobStatus.FAILED:
        raise HTTPException(
            status_code=500,
            detail=f"job '{job_id}' failed: {job.error or 'unknown error'}",
        )
    if job.status != JobStatus.SUCCEEDED or job.result is None:
        raise HTTPException(
            status_code=409,
            detail=f"job '{job_id}' is not finished yet (status={job.status.value})",
        )
    return JobResultResponse(
        job_id=job.job_id,
        status=job.status,
        profile=job.result,
        manifest_path=job.manifest_path,
        profile_path=job.profile_path,
    )


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    """Stream stage events via Server-Sent Events.

    Replay + live-tail strategy:
    - Each event the runner emits is appended to job.events AND pushed
      to job.queue. The replay reads from events; the live tail reads
      from the queue. Without dedup, an event that arrives between
      "snapshot the replay" and "start awaiting the queue" gets yielded
      twice (once as part of the snapshot, once via the queue).
    - We dedupe per-subscriber by tracking which event ids we've already
      sent. The id is derived from (job_id, stage, status, timestamp) —
      stable for a single emission, distinct across emissions.

    A late subscriber (job already finished) still gets the full event
    history via the replay; the queue is drained but a close sentinel
    or terminal status ends the stream immediately.
    """
    store = request.app.state.store
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")

    def _event_key(ev: StageEvent) -> str:
        # Unique enough per emission; timestamps are ms-precise UTC datetimes.
        return f"{ev.stage.value}|{ev.status.value}|{ev.timestamp.isoformat()}"

    async def event_gen() -> AsyncIterator[dict]:
        sent_keys: set[str] = set()

        # CS-82: SUBSCRIBE before replaying the snapshot so an event emitted in the gap between
        # snapshot and live-tail lands on THIS connection's OWN queue (dedup below drops any overlap
        # with the replay). Every concurrent viewer gets its own queue — no split, no orphaned close.
        queue = store.subscribe(job_id)
        try:
            # Replay snapshot of events known at connect time.
            for ev in list(job.events):
                key = _event_key(ev)
                if key in sent_keys:
                    continue
                sent_keys.add(key)
                yield {"event": "stage", "data": ev.model_dump_json()}

            if job.is_terminal() or queue is None:
                return

            # Live tail — reads THIS connection's own queue.
            while True:
                if await request.is_disconnected():
                    logger.debug("Client disconnected from stream %s", job_id)
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat to keep the connection open across proxies.
                    yield {"event": "ping", "data": "{}"}
                    continue
                if item is STREAM_CLOSE:
                    break
                # item is a StageEvent
                try:
                    key = _event_key(item)
                    if key in sent_keys:
                        # Already replayed; skip to avoid duplication.
                        continue
                    sent_keys.add(key)
                    yield {"event": "stage", "data": item.model_dump_json()}
                except Exception as e:  # noqa: BLE001
                    logger.exception("Stream serialization failed: %s", e)
                    break
        finally:
            if queue is not None:
                store.unsubscribe(job_id, queue)

    return EventSourceResponse(event_gen())
