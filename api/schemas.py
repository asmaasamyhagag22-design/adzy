"""Public HTTP API schemas.

Deliberately separate from the internal `BusinessProfile` /
`ScrapeManifest` schemas. The HTTP layer is allowed to evolve
independently from the pipeline's data contracts.

If the frontend types start drifting from these, that's a sign we
need a single source of truth (e.g., generate TS types from these
Pydantic models). For Day 1 we mirror them manually.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator

from scraper.url_utils import validate_input_url


# ---------------------------------------------------------------------
# Stage taxonomy — single source of truth for the timeline UI
# ---------------------------------------------------------------------

class Stage(str, Enum):
    SCRAPE = "scrape"
    RULES = "rules"
    EVIDENCE_PACK = "evidence_pack"
    LLM = "llm"
    VALIDATE = "validate"
    MERGE = "merge"
    FINAL = "final"
    # Future stages — add here without touching the runner contract:
    # POSTER_BRIEF = "poster_brief"
    # IMAGE_GEN = "image_gen"
    # COMPOSE = "compose"


class StageStatus(str, Enum):
    PENDING = "pending"
    STARTED = "started"
    DONE = "done"
    ERROR = "error"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# ---------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------

class RunRequest(BaseModel):
    """POST /api/run body."""
    url: HttpUrl

    @field_validator("url")
    @classmethod
    def _reject_concatenated_urls(cls, value: HttpUrl) -> HttpUrl:
        validate_input_url(str(value))
        return value
    skip_llm: bool = Field(
        default=False,
        description="If true, run rules-only (no LLM calls).",
    )
    model: str = Field(
        default="",
        description="Advisory only — the pipeline runs on the configured Gemini stack.",
    )
    persist_to_disk: bool = Field(
        default=True,
        description="If true, also write business_profile.json next to the manifest.",
    )


# ---------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------

class RunResponse(BaseModel):
    """POST /api/run response."""
    job_id: str
    # Legacy /run has no account/session principal.  Return this opaque capability once; callers
    # present it as X-Job-Token on status/result/stream reads.  Only its digest is stored.
    job_token: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


class StageEvent(BaseModel):
    """One SSE event. Same shape used to push into the job's queue."""
    job_id: str
    stage: Stage
    status: StageStatus
    timestamp: datetime
    duration_ms: int = 0
    payload: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class JobStatusResponse(BaseModel):
    """GET /api/jobs/{job_id} response — snapshot."""
    job_id: str
    status: JobStatus
    current_stage: Optional[Stage] = None
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    events: list[StageEvent] = Field(default_factory=list)
    manifest_path: Optional[str] = None
    profile_path: Optional[str] = None


class JobResultResponse(BaseModel):
    """GET /api/jobs/{job_id}/result response.

    Wraps the BusinessProfile JSON as-is plus a small envelope of
    audit fields (paths, timing). The profile itself is a free-form
    dict here so we don't have to depend on the BusinessProfile
    Pydantic model in the API layer — keeps coupling loose.
    """
    job_id: str
    status: JobStatus
    profile: dict[str, Any]
    manifest_path: Optional[str] = None
    profile_path: Optional[str] = None


# ---------------------------------------------------------------------
# Error responses
# ---------------------------------------------------------------------

class APIError(BaseModel):
    error: str
    detail: Optional[str] = None
