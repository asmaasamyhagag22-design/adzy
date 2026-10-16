"""In-memory job store.

Each Job carries:
- a JobStatus
- a history of StageEvents (for snapshot + late-subscriber catch-up)
- an asyncio.Queue used by the SSE endpoint to stream events live
- a reference to the asyncio loop that owns the queue (so the worker
  thread can push events safely via call_soon_threadsafe)
- the final BusinessProfile JSON when the job succeeds
- audit paths: where the manifest and profile were written

Design notes:
- One asyncio.Queue per job; the SSE stream reads from it; the runner
  thread writes to it via call_soon_threadsafe(queue.put_nowait, event).
- Events go to BOTH the queue (for live streamers) AND `events` list
  (for snapshots / catch-up). When the SSE endpoint starts streaming,
  it first replays the events list, then awaits the queue.
- TTL cleanup runs in a background task; defaults to 1 hour after the
  job reaches a terminal state. Old jobs are evicted to keep memory bounded.

Thread-safety:
- All mutations of Job fields happen either on the event loop OR
  inside the runner's emit() helper which marshals back to the loop.
- The internal dict is guarded by an asyncio.Lock when iterated, but
  reads of individual jobs by id don't need a lock — the dict reference
  is stable for the job's lifetime.
"""
from __future__ import annotations

import asyncio
import concurrent.futures as _futures
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..schemas import JobStatus, Stage, StageEvent, StageStatus

logger = logging.getLogger(__name__)


# Sentinel event meaning "stream is closing; stop listening".
_STREAM_CLOSE = object()
_KEEP_RESULT = object()


@dataclass
class Job:
    job_id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    # Authorization identity fixed at creation.  A web-app job stores ``user:<id>``; a legacy
    # no-user job stores only a one-way capability digest.  Never included in HTTP projections.
    owner_principal: str = field(default="", repr=False)
    current_stage: Optional[Stage] = None
    error: Optional[str] = None
    events: list[StageEvent] = field(default_factory=list)
    # CS-82: one queue PER SSE subscriber (fan-out), not a single shared queue. N concurrent
    # viewers used to consume-split ONE queue — each event reached only one of them and the losers
    # never got the terminal STREAM_CLOSE, so their connections hung open. append_event now pushes
    # every event to EVERY subscriber's own queue.
    subscribers: set = field(default_factory=set)
    # The loop that owns the subscriber queues. Cross-thread pushes use call_soon_threadsafe.
    loop: Optional[asyncio.AbstractEventLoop] = None
    # Audit paths
    manifest_path: Optional[str] = None
    profile_path: Optional[str] = None
    # Final result (the BusinessProfile as a plain dict for HTTP serialization)
    result: Optional[dict[str, Any]] = None
    # When the job finished (terminal state). Used for TTL.
    finished_at: Optional[float] = None  # time.monotonic()
    # Web-app fields: the brand a scrape/generate job produced, and a soft warning to surface
    # (e.g. "scrape ok but the analysis chain degraded"). Read by GET /api/jobs/{id}.
    domain_key: Optional[str] = None
    warning: Optional[str] = None

    def is_terminal(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED)


class JobStore:
    """Process-local job registry.

    Single instance per app, mounted on `app.state.store`. Not safe
    for multi-process deployment; for that we'd swap to Redis-backed
    storage. Fine for single-container demo deploys (Railway).
    """

    def __init__(self, ttl_seconds: int = 3600):
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds
        self._cleanup_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_cleanup(self) -> None:
        """Start the periodic TTL cleanup task. Called on app startup."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    def _sweep_interval(self) -> int:
        """CS-127: the poll interval has a hard 30s FLOOR so a JOB_TTL_SECONDS of 0 (or a tiny
        value) can never turn the cleanup loop into a busy-spin that pegs the event loop."""
        return max(30, min(self._ttl or 0, 300))

    def _stuck_grace_seconds(self) -> float:
        """CS-83: how long a NON-terminal job may sit un-updated before it's reclaimed. A generous
        multiple of the TTL (>=1h) so an in-flight job is never killed, but a wedged QUEUED/RUNNING
        job can't leak for the whole process lifetime."""
        return max(self._ttl * 4, 3600)

    def _expired_ids(self, now: float, wall: float) -> list[str]:
        """IDs to evict: terminal jobs older than the TTL, PLUS stuck non-terminal jobs whose last
        update is older than the stuck-grace window (CS-83). Pure over the current snapshot."""
        grace = self._stuck_grace_seconds()
        out: list[str] = []
        for jid, j in self._jobs.items():
            if j.is_terminal():
                if j.finished_at and (now - j.finished_at) > self._ttl:
                    out.append(jid)
            elif (wall - j.updated_at.timestamp()) > grace:
                out.append(jid)                         # stuck QUEUED/RUNNING — reclaim it
        return out

    async def _cleanup_loop(self) -> None:
        """Evict terminal jobs older than TTL and stuck non-terminal jobs. Loops until cancelled."""
        interval = self._sweep_interval()
        while True:
            await asyncio.sleep(interval)
            # CS-128: the try/except is INSIDE the loop, so ONE failed sweep is logged and the loop
            # CONTINUES — a single exception no longer permanently disables cleanup (which used to
            # let memory grow unbounded for the whole process lifetime with only one log line).
            try:
                now, wall = time.monotonic(), time.time()
                async with self._lock:
                    expired = self._expired_ids(now, wall)
                    for jid in expired:
                        del self._jobs[jid]
                if expired:
                    logger.info("Evicted %d expired/stuck jobs", len(expired))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — one bad sweep must not kill cleanup (CS-128)
                logger.exception("Cleanup sweep failed (continuing): %s", e)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, *, owner_principal: str = "") -> Job:
        """Create a new job in QUEUED state. Caller must call attach_loop()
        before the runner starts so cross-thread event pushes work.

        Public creation routes always pass an explicit principal.  An omitted principal remains
        useful for internal runner tests, but is deliberately unreadable through public routes.
        """
        job_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc)
        job = Job(
            job_id=job_id,
            status=JobStatus.QUEUED,
            created_at=now,
            updated_at=now,
            owner_principal=str(owner_principal or ""),
        )
        self._jobs[job_id] = job
        return job

    def attach_loop(self, job_id: str, loop: asyncio.AbstractEventLoop) -> None:
        """Record the asyncio loop that owns this job's queue. The runner
        thread uses this loop to push events safely."""
        job = self._jobs.get(job_id)
        if job is not None:
            job.loop = loop

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    # ------------------------------------------------------------------
    # Mutations — called either by the runner (via emit_threadsafe) or
    # by HTTP handlers on the event loop.
    # ------------------------------------------------------------------

    def append_event(self, job_id: str, event: StageEvent) -> bool:
        """Record an event, update job status, and push to the queue.

        Must be called on the asyncio loop. The runner thread uses
        emit_threadsafe() which schedules this method on the loop.
        """
        job = self._jobs.get(job_id)
        if job is None:
            logger.warning("append_event: unknown job_id=%s", job_id)
            return False
        if job.is_terminal():
            # Terminal state is immutable.  A delayed callback must not turn a durable-commit
            # failure into success (or vice versa), and subscribers have already received CLOSE.
            logger.warning("append_event: ignored event for terminal job_id=%s", job_id)
            return False
        job.events.append(event)
        job.updated_at = event.timestamp
        # Status transitions driven by the event stream:
        if event.status == StageStatus.STARTED:
            job.status = JobStatus.RUNNING
            job.current_stage = event.stage
        elif event.status == StageStatus.ERROR:
            job.status = JobStatus.FAILED
            job.error = event.error
            job.current_stage = None
            job.finished_at = time.monotonic()
        elif event.stage == Stage.FINAL and event.status == StageStatus.DONE:
            job.status = JobStatus.SUCCEEDED
            job.error = None
            job.current_stage = None
            job.finished_at = time.monotonic()
        # Fan out to EVERY live subscriber's own queue (CS-82) — non-blocking (queues are unbounded).
        for q in list(job.subscribers):
            try:
                q.put_nowait(event)
            except Exception as e:  # noqa: BLE001
                logger.exception("Queue put failed for job %s: %s", job_id, e)
        # If terminal, push a close sentinel to EACH subscriber so every SSE stream exits cleanly.
        if job.is_terminal():
            for q in list(job.subscribers):
                try:
                    q.put_nowait(_STREAM_CLOSE)
                except Exception:
                    pass
        return True

    def finish_success(self, job_id: str, *, result: Any = _KEEP_RESULT,
                       payload: Optional[dict[str, Any]] = None,
                       warning: str = "") -> bool:
        """Publish one authoritative success transition on the event loop.

        ``result`` is set before the terminal event, so a SUCCEEDED job never has a missing result
        when that job type promises one.  Omit it for pipeline jobs whose result was already queued
        through ``set_result_threadsafe``.

        ``warning`` is a soft note the poll surfaces beside a successful outcome ("the scrape
        worked but the AI step degraded"). It arrives with the transition rather than being written
        onto the job separately, because a warning that lands after the SPA has already read `done`
        is a warning nobody sees.
        """
        job = self._jobs.get(job_id)
        if job is None or job.is_terminal():
            return False
        if result is not _KEEP_RESULT:
            job.result = result
        if warning:
            job.warning = warning
        event = StageEvent(
            job_id=job_id, stage=Stage.FINAL, status=StageStatus.DONE,
            timestamp=datetime.now(timezone.utc), payload=payload,
        )
        return self.append_event(job_id, event)

    def finish_failure(self, job_id: str, error: str, *,
                       payload: Optional[dict[str, Any]] = None) -> bool:
        """Publish one safe failure transition and revoke any provisional success result."""
        job = self._jobs.get(job_id)
        if job is None or job.is_terminal():
            return False
        job.result = None
        event = StageEvent(
            job_id=job_id, stage=Stage.FINAL, status=StageStatus.ERROR,
            timestamp=datetime.now(timezone.utc), error=str(error), payload=payload,
        )
        return self.append_event(job_id, event)

    def subscribe(self, job_id: str) -> Optional[asyncio.Queue]:
        """Register a NEW live-event queue for one SSE connection (CS-82 fan-out). Returns None for
        an unknown job. The caller MUST unsubscribe() when the stream ends (a `finally`)."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        q: asyncio.Queue = asyncio.Queue()
        job.subscribers.add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            job.subscribers.discard(q)

    def emit_threadsafe(self, job_id: str, event: StageEvent) -> None:
        """Thread-safe entry point used by the runner thread.

        Schedules append_event() on the job's loop. If the loop is gone
        (job evicted), the event is silently dropped.
        """
        job = self._jobs.get(job_id)
        if job is None or job.loop is None:
            return
        try:
            job.loop.call_soon_threadsafe(self.append_event, job_id, event)
        except RuntimeError as e:
            # Loop has shut down — drop silently
            logger.debug("Loop closed for job %s: %s", job_id, e)

    def set_result(
        self, job_id: str, result: dict[str, Any],
        manifest_path: Optional[str] = None,
        profile_path: Optional[str] = None,
    ) -> None:
        """Attach the final result + audit paths. Safe to call on the loop."""
        job = self._jobs.get(job_id)
        if job is None or job.status == JobStatus.FAILED:
            return
        job.result = result
        job.manifest_path = manifest_path
        job.profile_path = profile_path

    def set_result_threadsafe(
        self, job_id: str, result: dict[str, Any],
        manifest_path: Optional[str] = None,
        profile_path: Optional[str] = None,
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.loop is None:
            return
        try:
            job.loop.call_soon_threadsafe(
                self.set_result, job_id, result, manifest_path, profile_path,
            )
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Terminal transitions, callable from a worker thread
    # ------------------------------------------------------------------

    def _apply_on_loop(self, job_id: str, fn, *args, **kwargs) -> bool:
        """Run one store mutation on the job's own loop from ANY thread and return ITS answer.

        `emit_threadsafe` is fire-and-forget, which is right for progress and wrong for a terminal
        transition: the caller has to know whether IT published the outcome or lost to an earlier
        one, and a callback scheduled into the void cannot say. So the answer comes back on a
        `concurrent.futures.Future`.

        Waiting on that future from the loop thread itself would deadlock — the callback can only
        run once this returns — so a call already on the owning loop is applied inline instead.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False
        loop = job.loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is None or running is loop:
            return bool(fn(*args, **kwargs))
        answer: "_futures.Future[bool]" = _futures.Future()

        def _apply() -> None:
            try:
                answer.set_result(bool(fn(*args, **kwargs)))
            except BaseException as exc:  # noqa: BLE001 — re-raised in the waiting thread
                answer.set_exception(exc)

        try:
            loop.call_soon_threadsafe(_apply)
        except RuntimeError:
            # Loop has shut down. Nothing was published, and saying so is the truthful answer:
            # reporting True here would let a caller record a success the store never made.
            return False
        try:
            return answer.result(timeout=30)
        except _futures.TimeoutError:
            logger.warning("terminal transition for job %s did not reach the loop", job_id)
            return False

    def finish_success_threadsafe(self, job_id: str, *, result: Any = _KEEP_RESULT,
                                  payload: Optional[dict[str, Any]] = None,
                                  warning: str = "") -> bool:
        """`finish_success` from a worker thread, with its answer preserved."""
        return self._apply_on_loop(job_id, self.finish_success, job_id,
                                   result=result, payload=payload, warning=warning)

    def finish_failure_threadsafe(self, job_id: str, error: str, *,
                                  payload: Optional[dict[str, Any]] = None) -> bool:
        """`finish_failure` from a worker thread, with its answer preserved."""
        return self._apply_on_loop(job_id, self.finish_failure, job_id, error, payload=payload)

    def all_job_ids(self) -> list[str]:
        return list(self._jobs.keys())


# ---------------------------------------------------------------------
# Dependency-style accessor (for use in route handlers)
# ---------------------------------------------------------------------

def get_store(app) -> JobStore:
    """Return the JobStore mounted on the FastAPI app."""
    return app.state.store


# ---------------------------------------------------------------------
# Request-scoped handle, for the LEGACY persistence backend only
# ---------------------------------------------------------------------
# `get_persistence()` is a process-global cached singleton; the JobStore is created per app in the
# lifespan and mounted on `app.state`. So `LegacyPersistence` cannot be handed the store at
# construction time, and routes must stop reaching for `request.app.state.store` themselves.
#
# THIS WAS A PROCESS-GLOBAL AND THAT WAS WRONG. A module-level `_active_store` set from each app's
# lifespan is last-writer-wins: mounting a second app in one process silently rebinds the handle,
# so the FIRST app stops seeing its own jobs while it is still serving. MEASURED by
# `tests/test_legacy_store_isolation.py`, which is why that gate runs before any Job route is wired
# onto this. The global also only *appeared* to deny cross-app reads — it denied them by clobbering,
# not by isolating, which is the kind of accidental pass that stops holding the moment the ordering
# changes.
#
# A ContextVar is bound per REQUEST from `request.app.state.store`, so two apps served by one
# process never share it, and concurrent requests to different apps each see their own. Work
# spawned with `asyncio.create_task` during a request copies the current context, so a background
# job runner keeps the store its request was serving even after that request returns.
_active_store: "ContextVar[Optional[JobStore]]" = ContextVar(
    "baseera_active_job_store", default=None)


def bind_active_store(store: Optional[JobStore]):
    """Bind the store for the current context. Returns a token for `reset_active_store`."""
    return _active_store.set(store)


def reset_active_store(token) -> None:
    """Undo one `bind_active_store`, restoring whatever was bound before it."""
    _active_store.reset(token)


def active_store() -> Optional[JobStore]:
    """The store being served in THIS context, or None outside a request.

    None is a real answer, not an error: under the PostgreSQL backend no JobStore is mounted at
    all, and a legacy provider asked for a job outside any request must say "no such job" rather
    than raise into a caller.
    """
    return _active_store.get()


@contextmanager
def serving_store(store: Optional[JobStore]):
    """Bind `store` for the duration of a block, then restore what was bound before.

    Production binds per REQUEST, in the middleware. This is for the callers that drive a job
    outside one — harnesses and tests — which otherwise have no way to say WHICH store is serving
    the work, and would silently write into nothing.
    """
    token = bind_active_store(store)
    try:
        yield store
    finally:
        reset_active_store(token)


# Re-export the close sentinel for the SSE route
STREAM_CLOSE = _STREAM_CLOSE
