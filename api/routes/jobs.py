"""Job-status routes:

- GET /api/jobs/{job_id}          → snapshot (status + progress + step)
- GET /api/jobs/{job_id}/stream   → SSE live event stream
- GET /api/jobs/{job_id}/result   → final BusinessProfile JSON

ALL THREE READ THROUGH THE PROVIDER, AND THEY CROSSED WITH THE WRITERS
----------------------------------------------------------------------
Moving these reads on their own was tried, measured and reverted: the write side still created
16-hex in-memory ids, so a provider-backed read looked for them in PostgreSQL, found nothing, and
polled a job that never reached a terminal state — 10 integration tests failed on a change whose own
targeted suite was green. Reads and writes are one lifecycle; they cross together or not at all.

WHAT STAYED IN THE ROUTE. Persistence answers "which durable events exist after seq N" and nothing
else. Replay, the bounded poll, keepalive, disconnect, SSE framing and the legacy presentation dedup
are HTTP transport and belong here — which is also why `PersistenceProvider` has no streaming
method: a provider that returned an async generator would be a provider that owns the connection.

THE DEDUP KEY IS PRESENTATION-ONLY. `stage|status|timestamp` is what the existing contract pins, and
it is applied to what this connection has SENT — never to the durable log. Two identical events at
two valid sequence positions are two events, and `JobEventRepository` keeps both: distorting the
record to imitate a queue would make the audit trail lie to protect a rendering detail.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Sequence

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ..job_access import bearer_authentication_failed, request_job_principals
from ..persistence import get_persistence
from ..schemas import JobResultResponse, JobStatus

router = APIRouter()
logger = logging.getLogger(__name__)
_NOT_FOUND = "Job not found."

#: How long the stream waits before asking for new events again. Zero events means one query per
#: interval per connection; a burst re-reads immediately (the loop only sleeps when nothing came
#: back), so this is the IDLE cost, not the streaming latency.
_POLL_SECONDS = 0.25
#: Silence after which a comment-shaped ping keeps proxies from closing the connection. Matches the
#: 15s the queue-based implementation used, so nothing downstream sees a different heartbeat.
_KEEPALIVE_SECONDS = 15.0


def _principals(job_id: str, request: Request) -> Sequence[str]:
    """The credentials this request actually presented, once the job is known to be theirs.

    Resolution and authorization are ONE step on purpose: the provider answers None for absent and
    for foreign-owned alike, so a job id cannot be probed for existence. 401 is reserved for a
    caller who supplied a Bearer token that is no longer valid — the SPA reads that as session
    expiry — and it is independent of the job id, so it cannot be used to distinguish either.
    """
    if bearer_authentication_failed(request):
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    principals = request_job_principals(request)
    if not principals:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return principals


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict:
    """The shape the frontend expects: status ∈ {running,done,error} + progress/step/domain_key.

    The projection lives in `api/job_projection.py` and both backends answer through it, so a
    durable job and an in-memory one cannot disagree about when the bar reaches 100.
    """
    principals = _principals(job_id, request)
    snapshot = get_persistence().job_snapshot(job_id=job_id, principals=principals)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return snapshot


@router.get("/jobs/{job_id}/result", response_model=JobResultResponse)
async def get_job_result(job_id: str, request: Request) -> JobResultResponse:
    """The job's business result, whatever shape holds it.

    The route never inspects `run_id`, never asks which backend answered and never looks in a
    result table: it asks the provider for THE result and the provider decides whether that is a
    Run's output or a direct `job_results` row. A route that branched on the shape would have to be
    changed every time a new kind of job acquired a new kind of result.
    """
    principals = _principals(job_id, request)
    result = get_persistence().job_result(job_id=job_id, principals=principals)
    if result is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    status = str(result.get("status") or "")
    if status == JobStatus.FAILED.value:
        raise HTTPException(
            status_code=500,
            detail=f"job '{job_id}' failed: {result.get('error') or 'unknown error'}",
        )
    if status != JobStatus.SUCCEEDED.value:
        raise HTTPException(
            status_code=409,
            detail=f"job '{job_id}' is not finished yet (status={status})",
        )
    if result.get("profile") is None:
        # Succeeded, and the durable result is not there. NOT a 409: "not finished yet" would be a
        # false statement about a job that IS finished, and it would make a repair case look like a
        # timing one — the reader would poll forever waiting for an answer that already happened.
        raise HTTPException(
            status_code=500,
            detail=f"job '{job_id}' succeeded but its stored result is missing",
        )
    return JobResultResponse(
        job_id=str(result.get("job_id") or job_id),
        status=JobStatus.SUCCEEDED,
        profile=result.get("profile"),
        manifest_path=result.get("manifest_path"),
        profile_path=result.get("profile_path"),
    )


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    """Stream stage events via Server-Sent Events.

    Replay + bounded-poll strategy, resumed by `seq`:
    - every durable event carries a per-job sequence number, allocated under the job's row lock, so
      "what have I not seen yet" is a cursor rather than a guess. Ordering by timestamp would be a
      coin flip for two events committed in one transaction — they share a microsecond.
    - a burst re-reads immediately; only an empty read sleeps. So the poll interval is the idle
      cost, not the latency of a live stage.
    - a late subscriber (job already finished) still gets the full history: the replay is just the
      first read from seq 0.

    No Redis, no Kafka, no long-lived transaction, no row lock held while waiting, no busy-spin.
    """
    principals = _principals(job_id, request)
    persistence = get_persistence()
    if persistence.job_snapshot(job_id=job_id, principals=principals) is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    def _event_key(event: dict) -> str:
        # Presentation dedup only — see the module docstring. Unique per emission; timestamps are
        # microsecond-precise UTC.
        return f"{event.get('stage')}|{event.get('status')}|{event.get('timestamp')}"

    async def event_gen() -> AsyncIterator[dict]:
        sent_keys: set[str] = set()
        cursor = 0
        silent_for = 0.0
        while True:
            if await request.is_disconnected():
                logger.debug("Client disconnected from stream %s", job_id)
                break
            # ASKED BEFORE THE READ, not after. A job that terminalises between the read and the
            # check would otherwise end the stream one event short of its own ending: the check
            # would see terminal, and the event that made it terminal would never be read.
            was_terminal = persistence.job_is_terminal(job_id=job_id, principals=principals)
            events = persistence.job_events_since(
                job_id=job_id, principals=principals, after_seq=cursor) or []
            for event in events:
                cursor = max(cursor, int(event.get("seq") or 0))
                key = _event_key(event)
                if key in sent_keys:
                    continue
                sent_keys.add(key)
                try:
                    data = json.dumps({"job_id": job_id, **event})
                except (TypeError, ValueError) as e:
                    logger.exception("Stream serialization failed: %s", e)
                    return
                yield {"event": "stage", "data": data}
            if events:
                silent_for = 0.0
                continue
            if was_terminal:
                break
            await asyncio.sleep(_POLL_SECONDS)
            silent_for += _POLL_SECONDS
            if silent_for >= _KEEPALIVE_SECONDS:
                silent_for = 0.0
                yield {"event": "ping", "data": "{}"}

    return EventSourceResponse(event_gen())
