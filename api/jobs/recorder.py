# -*- coding: utf-8 -*-
"""What the pipeline runner writes a job's progress through.

WHY THIS EXISTS. `run_pipeline_job` took a `JobStore` and called three methods on it. Under the
durable backend there is no JobStore, so the runner would have had to know which backend it was on
— and a runner that branches on the backend is a runner that can emit progress to one store while
its result lands in another. The recorder keeps the runner's call sites verbatim and moves the
decision to the provider, where it already lives for every other kind of write.

It is deliberately NOT a second job store:

- events go straight to `provider.record_job_event`, which is the durable log under PostgreSQL and
  the in-memory queue under legacy;
- the pipeline's output is HELD here as a plain value, exactly the way a local variable holds it,
  and is written durably only by the finalizer that also terminalises the job. Holding it is not a
  claim that anything succeeded — which is the whole reason it is not written when produced;
- `last_started_stage` replaces the runner's old "scan the job's event history backwards for the
  last STARTED" read. The recorder emitted those events, so it already knows, and a failure no
  longer needs to read job state back out of a store to describe itself.

One recorder per job. It is created on the event loop and used from the worker thread, which is
safe because it only forwards: the legacy provider marshals back to the loop itself, and the
durable provider opens its own short transaction per call.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..schemas import Stage, StageEvent, StageStatus

logger = logging.getLogger(__name__)


class JobRecorder:
    """The runner's write surface for ONE job. Duck-types the JobStore methods it used to call."""

    __slots__ = ("job_id", "_provider", "result", "manifest_path", "profile_path",
                 "last_started_stage")

    def __init__(self, *, job_id: str, provider: Any) -> None:
        self.job_id = str(job_id)
        self._provider = provider
        #: The pipeline's output, held in memory until a finalizer commits it with the job's
        #: terminal state. Under legacy the provider ALSO lands it on the in-memory Job, because
        #: there the Job is the result store; under PostgreSQL nothing durable is written here.
        self.result: Optional[dict[str, Any]] = None
        self.manifest_path: Optional[str] = None
        self.profile_path: Optional[str] = None
        self.last_started_stage: Optional[Stage] = None

    # ------------------------------------------------------------------
    # the three calls the runner makes
    # ------------------------------------------------------------------

    def emit_threadsafe(self, job_id: str, event: StageEvent) -> None:
        """Record one stage event. Callable from the pipeline's worker thread."""
        if event.status == StageStatus.STARTED:
            self.last_started_stage = event.stage
        try:
            self._provider.record_job_event(
                job_id=str(job_id), stage=event.stage.value, status=event.status.value,
                occurred_at=event.timestamp, duration_ms=int(event.duration_ms or 0),
                payload=event.payload, error=event.error or "")
        except Exception as e:  # noqa: BLE001 — progress must never kill the pipeline
            logger.exception("could not record %s/%s for job %s: %s",
                             event.stage.value, event.status.value, job_id, e)

    def set_result_threadsafe(self, job_id: str, result: dict[str, Any],
                              manifest_path: Optional[str] = None,
                              profile_path: Optional[str] = None) -> None:
        """Hold the pipeline's output for the finalizer. Not a durable success on its own."""
        self.result = dict(result or {})
        self.manifest_path = manifest_path
        self.profile_path = profile_path
        try:
            self._provider.hold_job_result(
                job_id=str(job_id), profile=self.result,
                manifest_path=manifest_path or "", profile_path=profile_path or "")
        except Exception as e:  # noqa: BLE001
            logger.exception("could not hold the result for job %s: %s", job_id, e)

    # ------------------------------------------------------------------

    def failed_stage(self) -> Stage:
        """The stage a failure belongs to: the last one that STARTED, else the first."""
        return self.last_started_stage or Stage.SCRAPE


__all__ = ["JobRecorder"]
