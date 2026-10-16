# -*- coding: utf-8 -*-
"""The ONE definition of what a job looks like to a client.

WHY THIS MODULE EXISTS. `api/routes/jobs.py` derived `status`/`progress`/`step` from an in-memory
`Job` object. Under a durable backend the same three fields have to be derived from PostgreSQL
rows instead. Two derivations of one public contract is the shape that drifts: the SPA polls until
`status` is `done` or `error`, so a backend that computed `done` one stage earlier than the other
would not fail a test — it would just finish the progress bar at the wrong moment, on one backend
only, with nothing in either store disagreeing.

So the mapping lives here, exactly once, and both backends project through it. The tables are
copied verbatim from the route that owned them; this module changed WHERE they live, not WHAT they
say.

`stage` and `status` arrive as plain strings because the durable store speaks strings; the enum
values in `api/schemas.py` are those same strings, so a legacy caller may pass either.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

#: Progress %% + human step label per pipeline stage — the AdArchitect frontend renders a progress
#: bar and a rotating status line off these (its 7 cosmetic stages are driven purely by `progress`).
STAGE_PROGRESS: dict[str, tuple[int, str]] = {
    "scrape": (15, "Crawling the website"),
    "rules": (35, "Extracting brand facts"),
    "evidence_pack": (50, "Assembling evidence"),
    "llm": (75, "Understanding the brand"),
    "validate": (85, "Verifying against sources"),
    "merge": (92, "Building the profile"),
    "final": (100, "Done"),
}

#: The frontend polls until status is one of these; anything else means "keep polling".
STATUS_MAP: dict[str, str] = {
    "queued": "running",
    "running": "running",
    "succeeded": "done",
    "failed": "error",
}

#: What the durable store calls terminal. Kept beside STATUS_MAP because the two must agree: a
#: status that projects to `done`/`error` and is not terminal would make the SPA stop polling a job
#: that is still moving.
TERMINAL_STATUSES = frozenset({"succeeded", "failed"})


def _value(raw: Any) -> str:
    """Accept an enum or a plain string. The durable store speaks strings; the legacy store enums."""
    return str(getattr(raw, "value", raw) or "")


def project_job(*, job_id: str, status: Any, current_stage: Any = None,
                domain_key: Any = None, warning: Any = None,
                error: Any = None) -> dict:
    """The shape the frontend expects: status in {running,done,error} + progress/step/domain_key.

    Lifted verbatim from `api/routes/jobs.py::_webapp_job_view` so the two backends cannot disagree
    about when the bar reaches 100.
    """
    projected = STATUS_MAP.get(_value(status), "running")
    stage = _value(current_stage) or "scrape"
    progress, step = STAGE_PROGRESS.get(stage, (5, "Starting"))
    if projected == "done":
        progress, step = 100, "Done"
    elif projected == "error":
        step = "Failed"
    return {
        "job_id": str(job_id),
        "status": projected,
        "progress": progress,
        "step": step,
        "domain_key": domain_key or None,
        "warning": warning or None,
        "error": error or None,
    }


def project_event(*, stage: Any, status: Any, occurred_at: Any,
                  duration_ms: int = 0, payload: Optional[Mapping[str, Any]] = None,
                  error: Any = None, seq: int = 0) -> dict:
    """One StageEvent as the SSE stream and the snapshot both publish it.

    `seq` is carried alongside so a durable consumer can resume after the last event it saw without
    re-deriving an ordering from timestamps — two events inside one transaction share a timestamp
    to the microsecond, and ordering them by time would be a coin flip.
    """
    stamp = occurred_at
    iso = stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp or "")
    return {
        "stage": _value(stage),
        "status": _value(status),
        "timestamp": iso,
        "duration_ms": int(duration_ms or 0),
        "payload": dict(payload or {}),
        "error": error or None,
        "seq": int(seq or 0),
    }


__all__ = ["STAGE_PROGRESS", "STATUS_MAP", "TERMINAL_STATUSES",
           "project_job", "project_event"]
