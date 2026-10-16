# -*- coding: utf-8 -*-
"""Immutable values crossing the PostgreSQL persistence boundary."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: UUID
    email: str
    full_name: str | None
    legacy_user_id: str | None
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BrandRecord:
    id: UUID
    domain_key: str
    owner_id: UUID | None
    current_profile_id: UUID | None
    display_name: str | None
    source_url: str | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def unclaimed(self) -> bool:
        return self.owner_id is None


@dataclass(frozen=True, slots=True)
class BrandOwnership:
    exists: bool
    owner_id: UUID | None = None
    archived: bool = False


@dataclass(frozen=True, slots=True)
class CanonicalBrand:
    brand: BrandRecord
    created: bool
    claimed: bool
    reactivated: bool


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    id: UUID
    brand_id: UUID
    version: int
    profile: dict[str, Any]
    schema_version: int
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class GalleryBlobRecord:
    checksum: str
    byte_size: int
    mime: str
    storage_key: str
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class GalleryEntryRecord:
    """One logical publication. `owner_user_id` and `brand_id` are None when unproven, and stay
    None — see `db.models.GalleryEntry`."""
    id: UUID
    owner_user_id: UUID | None
    brand_id: UUID | None
    checksum: str
    filename: str
    domain_key: str | None
    brand_name: str | None
    content_type: str | None
    style: str | None
    metadata: dict[str, Any]
    origin: str
    legacy_seq: int | None
    imported_at: datetime | None
    created_at: datetime | None
    #: Denormalised from the blob so a history page needs one query, not one per row.
    storage_key: str = ""
    mime: str = ""
    byte_size: int = 0


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: UUID
    brand_id: UUID
    kind: str
    status: str
    kind_seq: int | None
    input_profile_id: UUID | None
    selected_attempt_id: UUID | None
    output: dict[str, Any] | None
    error: str | None
    created_at: datetime | None
    finished_at: datetime | None
    requested_config: dict[str, Any] = field(default_factory=dict)
    effective_config: dict[str, Any] | None = None
    config_schema_version: int = 1
    output_schema_version: int | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: UUID
    brand_id: UUID
    subject_run_id: UUID
    decision: str
    decided_by_user_id: UUID | None
    decided_at: datetime | None
    origin: str
    imported_at: datetime | None
    subject_kind: str = "swot"
    note: str | None = None


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: UUID
    run_id: UUID | None
    brand_id: UUID | None
    requested_domain_key: str | None
    status: str
    execution_no: int
    access_mode: str
    owner_user_id: UUID | None
    capability_digest: str | None = field(repr=False)
    current_stage: str | None = None
    error: str | None = None
    warning: str | None = None
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class JobEventRecord:
    """One durable job event, carrying exactly what the public SSE surface serialises.

    `occurred_at` is the emitter's own moment; `created_at` is when the row landed. They differ
    whenever a worker thread emits through a threadsafe hop, and replaying the wrong one would give
    a reconnecting client different timestamps from those live subscribers already received.
    """

    id: UUID
    job_id: UUID
    seq: int
    stage: str
    status: str
    is_terminal: bool
    occurred_at: datetime
    duration_ms: int
    error: str | None
    payload: dict[str, Any] | None
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class JobResultRecord:
    """The business result of a Job that intentionally has NO Run.

    Not a copy of `runs.output`. A run-backed job keeps its Run as the sole authority and has no
    row here at all; this exists for `/api/run`, which produces a BusinessProfile and names no
    brand, so there is no Run for its output to live in.
    """

    job_id: UUID
    result_kind: str
    schema_version: int
    payload: dict[str, Any]
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class CalendarPostRecord:
    id: UUID
    calendar_run_id: UUID
    brand_id: UUID
    ordinal: int
    scheduled_date: date | None
    channel: str | None
    content_type: str | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AssetRecord:
    id: UUID
    brand_id: UUID
    storage_key: str
    kind: str
    mime: str
    bytes: int | None
    checksum_sha256: str | None
    created_by_user_id: UUID | None


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    id: UUID
    run_id: UUID
    brand_id: UUID
    ordinal: int
    status: str
    asset_id: UUID | None
    qa: dict[str, Any]
    qa_passed: bool | None
    error: str | None


@dataclass(frozen=True, slots=True)
class ScrapeCommitResult:
    brand: BrandRecord
    profile: ProfileRecord
    run: RunRecord
    job: JobRecord


@dataclass(frozen=True, slots=True)
class CreativeCommitResult:
    run: RunRecord
    attempt: AttemptRecord
    asset: AssetRecord
    job: JobRecord | None


@dataclass(frozen=True, slots=True)
class UsageRecordValue:
    id: UUID
    run_id: UUID
    brand_id: UUID
    provider: str
    model: str
    cost_usd: Decimal


__all__ = [name for name in globals() if name.endswith(("Record", "Ownership", "Brand", "Result", "Value"))]
