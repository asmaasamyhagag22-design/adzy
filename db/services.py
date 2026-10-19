# -*- coding: utf-8 -*-
"""Synchronous PostgreSQL application services for the future cutover.

No API route imports this module in Phase 2C.  Every method performs one short UnitOfWork; external
scraping/provider/filesystem work must finish before these methods are called.

Canonical multi-row lock order is:

    Brand -> Run -> Attempt -> Job

Job-only claim/renew/failure operations lock only Job.  No service acquires Brand after Job, so the
scrape/creative finalizers cannot deadlock with a terminalizer that touches only its Job row.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from db.errors import ApprovalMissing, InvalidTransition, NotFound
from db.repositories import utcnow
from db.records import (
    GalleryBlobRecord,
    GalleryEntryRecord,
    JobEventRecord,
    JobResultRecord,
    ApprovalRecord,
    AssetRecord,
    AttemptRecord,
    BrandOwnership,
    BrandRecord,
    CalendarPostRecord,
    CreativeCommitResult,
    JobRecord,
    ProfileRecord,
    RunRecord,
    ScrapeCommitResult,
    UsageRecordValue,
    UserRecord,
)
from db.uow import UnitOfWork

FailureInjector = Callable[[str], None]


def _no_failure(_point: str) -> None:
    return None


@dataclass(frozen=True, slots=True)
class ProfileData:
    payload: Mapping[str, Any]
    schema_version: int = 1
    country_canonical: str | None = None
    country_source: str | None = None
    country_raw: str | None = None
    primary_language: str | None = None
    language_source: str | None = None


@dataclass(frozen=True, slots=True)
class ScrapeSuccess:
    domain_key: str
    user_id: UUID
    job_id: UUID
    worker_id: str
    profile: ProfileData
    display_name: str | None = None
    source_url: str | None = None
    requested_config: Mapping[str, Any] = field(default_factory=dict)
    run_output: Mapping[str, Any] | None = None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class LiveRunData:
    brand_id: UUID
    user_id: UUID
    kind: str
    status: str = "queued"
    input_profile_id: UUID | None = None
    calendar_post_id: UUID | None = None
    requested_config: Mapping[str, Any] = field(default_factory=dict)
    effective_config: Mapping[str, Any] | None = None
    config_schema_version: int = 1
    output: Mapping[str, Any] | None = None
    output_schema_version: int | None = None
    error: str | None = None
    dependencies: Sequence[tuple[UUID, str]] = ()


@dataclass(frozen=True, slots=True)
class VerifiedAssetData:
    storage_key: str
    kind: str
    mime: str
    byte_count: int
    checksum_sha256: str
    durable: bool
    created_by_user_id: UUID | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class CreativeFinalization:
    user_id: UUID
    run_id: UUID
    attempt_id: UUID
    asset: VerifiedAssetData
    qa: Mapping[str, Any]
    qa_passed: bool
    output: Mapping[str, Any]
    output_schema_version: int = 1
    select_attempt: bool = True
    job_id: UUID | None = None
    worker_id: str | None = None


def _create_live_run_locked(uow, data: "LiveRunData"):
    """Create one live Run INSIDE the caller UnitOfWork. No commit here.

    Extracted from `create_live_run` so a Job-backed completion can commit the Run, the Job status
    and the terminal event as ONE fact. The service method opens and commits its own transaction,
    which is exactly what a composed finalizer must not do — the same defect that was found and
    fixed on the scrape path, and the reason this primitive exists BEFORE any durable job is wired
    behind the analysis and creative routes. Wiring those first would reopen, on every path at once,
    the window that was just closed on one.

    Every invariant stays where it was: the brand row is locked (`allow_unclaimed=True`), and
    `kind_seq` allocation, `input_profile_id` linkage, dependencies and output schema versioning all
    remain inside `runs.create_live_for_locked_brand`. Nothing is reimplemented here.
    """
    uow.brands.lock_owned(brand_id=data.brand_id, user_id=data.user_id, allow_unclaimed=True)
    return uow.runs.create_live_for_locked_brand(
        brand_id=data.brand_id, kind=data.kind,
        created_by_user_id=data.user_id, status=data.status,
        input_profile_id=data.input_profile_id,
        calendar_post_id=data.calendar_post_id,
        requested_config=data.requested_config,
        effective_config=data.effective_config,
        config_schema_version=data.config_schema_version,
        output=data.output, output_schema_version=data.output_schema_version,
        error=data.error, dependencies=data.dependencies,
    )


def _apply_event_locked(uow, *, job_id: UUID, stage: str, status: str,
                        occurred_at: datetime, duration_ms: int = 0,
                        payload: Mapping[str, Any] | None = None,
                        error: str | None = None, worker_id: str | None = None,
                        locked_job=None):
    """Append one event and apply the job transition it implies, INSIDE the caller UnitOfWork.

    Deliberately not a service method: a service method opens and commits its own transaction, and
    that is precisely what must NOT happen here. Business finalization has to commit the Run, the
    Job status and the terminal event together, so the event write must be composable into an
    existing UnitOfWork. Repositories flush; only the UnitOfWork commits.

    Returns None when the job is already terminal — terminal state is immutable, and a delayed
    callback must not turn a durable-commit failure into success.
    """
    from db.models import Job as _Job

    locked = locked_job if locked_job is not None else uow.session.get(
        _Job, job_id, with_for_update=True)
    if locked is None:
        raise NotFound("job not found")
    if locked.status in ("succeeded", "failed"):
        return None

    is_error = str(status) == "error"
    terminal = is_error or (str(stage) == "final" and str(status) == "done")

    event = uow.job_events.append_locked(
        job_id=job_id, stage=stage, status=status, occurred_at=occurred_at,
        duration_ms=duration_ms, payload=payload, error=error, is_terminal=terminal)

    if terminal:
        uow.jobs.finish(
            job_id=job_id, worker_id=worker_id or (locked.claimed_by or ""),
            status="failed" if is_error else "succeeded",
            brand_id=locked.brand_id, run_id=locked.run_id,
            error=error if is_error else None)
    elif str(status) == "started":
        locked.status = "running"
        locked.current_stage = str(stage)
        locked.updated_at = utcnow()
    return event


class PersistenceService:
    """Typed service surface. A factory returns a fresh UnitOfWork for every method call."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    # ------------------------------------------------------------------ users
    def create_user(self, *, email: str, hashed_password: str, full_name: str = "",
                    legacy_user_id: str | None = None) -> UserRecord:
        with self._uow_factory() as uow:
            record = uow.users.create(
                email=email, hashed_password=hashed_password, full_name=full_name,
                legacy_user_id=legacy_user_id)
            uow.commit()
            return record

    def user_by_id(self, user_id: UUID) -> UserRecord | None:
        with self._uow_factory() as uow:
            return uow.users.by_id(user_id)

    def user_by_legacy_id(self, legacy_user_id: str) -> UserRecord | None:
        with self._uow_factory() as uow:
            return uow.users.by_legacy_id(legacy_user_id)

    def user_by_email(self, email: str) -> UserRecord | None:
        with self._uow_factory() as uow:
            return uow.users.by_email(email)

    def verify_credentials(self, *, email: str,
                           verifier: Callable[[str], bool]) -> UserRecord | None:
        """Authenticate without the password hash ever leaving the persistence layer."""
        with self._uow_factory() as uow:
            return uow.users.verify_credentials(email=email, verifier=verifier)

    # ------------------------------------------------------------------ brands / visibility
    def owner_of(self, domain_key: str) -> BrandOwnership:
        with self._uow_factory() as uow:
            return uow.brands.owner_of(domain_key)

    def get_visible_brand(self, *, user_id: UUID, brand_id: UUID | None = None,
                          domain_key: str | None = None) -> BrandRecord:
        with self._uow_factory() as uow:
            return uow.brands.get_visible(
                user_id=user_id, brand_id=brand_id, domain_key=domain_key)

    def list_visible_brands(self, *, user_id: UUID, limit: int = 100) -> list[BrandRecord]:
        with self._uow_factory() as uow:
            return uow.brands.list_visible(user_id=user_id, limit=limit)

    def archive_brand(self, *, domain_key: str, user_id: UUID) -> bool:
        with self._uow_factory() as uow:
            archived = uow.brands.archive(domain_key=domain_key, user_id=user_id)
            uow.commit()
            return archived

    # ------------------------------------------------------------------ scrape/profile transaction
    def commit_scrape_success(
        self, data: ScrapeSuccess, *, inject: FailureInjector = _no_failure,
    ) -> ScrapeCommitResult:
        """Commit Brand ownership, immutable profile, scrape Run and Job terminal state atomically."""
        with self._uow_factory() as uow:
            job_before = uow.jobs.get_for_user(job_id=data.job_id, user_id=data.user_id)
            if job_before.requested_domain_key not in (None, data.domain_key):
                raise InvalidTransition("job was created for a different domain")

            canonical = uow.brands.lock_or_create_for_scrape(
                domain_key=data.domain_key, user_id=data.user_id,
                display_name=data.display_name, source_url=data.source_url)
            if canonical.created:
                inject("after_brand_insert")
            if canonical.claimed:
                inject("after_ownership_claim")

            profile = uow.profiles.create_version_for_locked_brand(
                brand_id=canonical.brand.id, profile=data.profile.payload,
                schema_version=data.profile.schema_version,
                country_canonical=data.profile.country_canonical,
                country_source=data.profile.country_source,
                country_raw=data.profile.country_raw,
                primary_language=data.profile.primary_language,
                language_source=data.profile.language_source,
            )
            inject("after_profile_insert")
            inject("before_current_profile_update")
            brand = uow.brands.set_current_profile(
                brand_id=canonical.brand.id, profile_id=profile.id)

            # The approved 2B lifecycle contract uses input_profile_id as the relational linkage
            # between the scrape Run and the snapshot committed with it. The immutable Run output
            # remains a payload; no arbitrary Brand JSON is updated.
            run_output = dict(data.run_output or {"profile_snapshot_id": str(profile.id)})
            run = uow.runs.create_live_for_locked_brand(
                brand_id=brand.id, kind="scrape", created_by_user_id=data.user_id,
                status="succeeded", input_profile_id=profile.id,
                requested_config=data.requested_config, output=run_output,
                output_schema_version=1,
            )
            inject("after_run_insert")
            inject("before_job_terminal_update")
            job, changed = uow.jobs.finish(
                job_id=data.job_id, worker_id=data.worker_id, status="succeeded",
                brand_id=brand.id, run_id=run.id, warning=data.warning)
            if not changed:
                raise InvalidTransition("scrape success was already committed")
            inject("after_job_terminal_update")
            # THE TERMINAL EVENT BELONGS TO THIS TRANSACTION, not to a follow-up call.
            #
            # Emitting it afterwards would reopen the C3 window from the other side: a crash between
            # the two commits leaves a succeeded Run and a succeeded Job with no terminal event, so
            # a reconnecting reader replaying the durable log never sees the stream end. Committing
            # it here makes "the business completed" and "the record says so" one fact.
            uow.job_events.append_locked(
                job_id=data.job_id, stage="final", status="done", occurred_at=utcnow(),
                is_terminal=True,
                payload={"domain_key": data.domain_key, "run_id": str(run.id),
                         "brand_id": str(brand.id)})
            inject("after_terminal_event_insert")
            inject("before_commit")
            uow.commit()
            return ScrapeCommitResult(brand, profile, run, job)

    def create_profile_version(self, *, brand_id: UUID, user_id: UUID,
                               profile: ProfileData) -> ProfileRecord:
        with self._uow_factory() as uow:
            uow.brands.lock_owned(brand_id=brand_id, user_id=user_id, allow_unclaimed=True)
            row = uow.profiles.create_version_for_locked_brand(
                brand_id=brand_id, profile=profile.payload,
                schema_version=profile.schema_version,
                country_canonical=profile.country_canonical,
                country_source=profile.country_source, country_raw=profile.country_raw,
                primary_language=profile.primary_language,
                language_source=profile.language_source,
            )
            uow.brands.set_current_profile(brand_id=brand_id, profile_id=row.id)
            uow.commit()
            return row

    def profile_version(self, *, profile_id: UUID) -> ProfileRecord | None:
        """One immutable profile version by id. Visibility is decided by the caller that already
        resolved the Job or Run this id came from — this is a follow-the-pointer read."""
        with self._uow_factory() as uow:
            return uow.profiles.by_id(profile_id)

    def current_profile(self, *, brand_id: UUID, user_id: UUID) -> ProfileRecord | None:
        with self._uow_factory() as uow:
            uow.brands.get_visible(user_id=user_id, brand_id=brand_id)
            return uow.profiles.current(brand_id=brand_id)

    # ------------------------------------------------------------------ runs / approvals
    def create_live_run(self, data: LiveRunData) -> RunRecord:
        """Standalone Run creation: open a UnitOfWork, delegate, commit.

        Composed callers use `_create_live_run_locked` directly inside their own UnitOfWork instead
        of calling this, so the Run and the Job facts it completes commit together.
        """
        with self._uow_factory() as uow:
            run = _create_live_run_locked(uow, data)
            uow.commit()
            return run

    def current_successful_run(self, *, brand_id: UUID, kind: str,
                               user_id: UUID) -> RunRecord | None:
        with self._uow_factory() as uow:
            uow.brands.get_visible(user_id=user_id, brand_id=brand_id)
            return uow.runs.current_successful(brand_id=brand_id, kind=kind)

    def finalize_run_success(self, *, run_id: UUID, user_id: UUID,
                             output: Mapping[str, Any], output_schema_version: int = 1
                             ) -> tuple[RunRecord, bool]:
        with self._uow_factory() as uow:
            visible = uow.runs.get_visible(run_id=run_id, user_id=user_id)
            uow.brands.lock_owned(
                brand_id=visible.brand_id, user_id=user_id, allow_unclaimed=True)
            uow.runs.lock(run_id)
            result = uow.runs.finalize_success(
                run_id=run_id, output=output, output_schema_version=output_schema_version)
            uow.commit()
            return result

    def finalize_run_failure(self, *, run_id: UUID, user_id: UUID,
                             error: str) -> tuple[RunRecord, bool]:
        with self._uow_factory() as uow:
            visible = uow.runs.get_visible(run_id=run_id, user_id=user_id)
            uow.brands.lock_owned(
                brand_id=visible.brand_id, user_id=user_id, allow_unclaimed=True)
            uow.runs.lock(run_id)
            result = uow.runs.finalize_failure(run_id=run_id, error=error)
            uow.commit()
            return result

    def approve_current_swot(self, *, brand_id: UUID, user_id: UUID,
                             expected_run_id: UUID, note: str | None = None) -> ApprovalRecord:
        from db.errors import NotVisible, StaleApprovalSubject

        with self._uow_factory() as uow:
            # Brand is the shared serialization point for both new Run allocation and approval.
            uow.brands.get_visible(user_id=user_id, brand_id=brand_id, lock=True)
            # Resolve membership before currentness: unknown/foreign/non-SWOT subjects must not
            # reveal their existence through a stale response. All writers share this brand lock.
            submitted = uow.runs.get_visible(run_id=expected_run_id, user_id=user_id)
            if (submitted.brand_id != brand_id or submitted.kind != "swot"
                    or submitted.status != "succeeded"):
                raise NotVisible("SWOT subject is absent or not visible")
            current = uow.runs.current_successful(brand_id=brand_id, kind="swot", lock=True)
            if current is None:
                raise ApprovalMissing("brand has no successful SWOT to approve")
            if current.id != expected_run_id:
                raise StaleApprovalSubject("reviewed SWOT is no longer current")
            # Currentness MUST precede this idempotent return, even for a previously approved A.
            existing = uow.approvals.approved_for_run(current.id)
            if existing is not None:
                return existing  # explicit idempotent no-op, including truthful imported approval
            approval = uow.approvals.create_live(
                brand_id=brand_id, run_id=current.id, actor_id=user_id, note=note)
            uow.commit()
            return approval

    def approval_for_current_swot(self, *, brand_id: UUID,
                                  user_id: UUID) -> ApprovalRecord | None:
        with self._uow_factory() as uow:
            uow.brands.get_visible(user_id=user_id, brand_id=brand_id, lock=True)
            current = uow.runs.current_successful(brand_id=brand_id, kind="swot")
            return uow.approvals.approved_for_run(current.id) if current else None

    def decide_current_strategy(self, *, brand_id: UUID, user_id: UUID, run_id: UUID,
                                decision: str, note: str | None = None) -> ApprovalRecord:
        """Append a human decision for the exact current immutable strategy Run.

        The brand lock serializes this decision with creation of a repaired/newer strategy. The
        eligibility check reads the persisted verifier result, never a client-supplied flag.
        """
        from db.errors import ApprovalIneligible, ApprovalMissing, StaleApprovalSubject

        if decision not in ("approved", "rejected"):
            raise ValueError("decision must be approved or rejected")
        with self._uow_factory() as uow:
            uow.brands.get_visible(user_id=user_id, brand_id=brand_id, lock=True)
            current = uow.runs.current_successful(brand_id=brand_id, kind="strategy", lock=True)
            if current is None:
                raise ApprovalMissing("brand has no successful strategy run")
            if current.id != run_id:
                raise StaleApprovalSubject("submitted strategy run is no longer current")
            canonical = ((current.output or {}).get("canonical_strategy")
                         if isinstance(current.output, dict) else None)
            verification = (canonical.get("verification") if isinstance(canonical, dict) else None)
            if not isinstance(verification, dict):
                raise ApprovalIneligible("run is not a canonical verified strategy")
            if decision == "approved" and not bool(verification.get("eligible_for_approval")):
                raise ApprovalIneligible("strategy verifier did not make this run eligible")
            existing = uow.approvals.latest_for_run(current.id)
            if existing is not None and existing.decision == decision:
                return existing
            approval = uow.approvals.create_live(
                brand_id=brand_id, run_id=current.id, actor_id=user_id, note=note,
                subject_kind="strategy", decision=decision)
            uow.commit()
            return approval

    def decision_for_current_strategy(self, *, brand_id: UUID,
                                      user_id: UUID) -> ApprovalRecord | None:
        with self._uow_factory() as uow:
            uow.brands.get_visible(user_id=user_id, brand_id=brand_id)
            current = uow.runs.current_successful(brand_id=brand_id, kind="strategy")
            return uow.approvals.latest_for_run(current.id) if current else None

    # ------------------------------------------------------------------ jobs
    def create_user_job(self, *, owner_user_id: UUID,
                        requested_domain_key: str | None = None) -> JobRecord:
        with self._uow_factory() as uow:
            job = uow.jobs.create_user(
                owner_user_id=owner_user_id, requested_domain_key=requested_domain_key)
            uow.commit()
            return job

    def create_capability_job(self, *, capability_digest: str,
                              requested_domain_key: str | None = None) -> JobRecord:
        with self._uow_factory() as uow:
            job = uow.jobs.create_capability(
                capability_digest=capability_digest,
                requested_domain_key=requested_domain_key)
            uow.commit()
            return job

    def job_by_id(self, *, job_id: UUID) -> JobRecord | None:
        """The job row with NO ownership scoping — for the worker that owns the job, not a client.

        Every client-facing read goes through `job_for_user` / `job_for_capability`, which answer
        the same None for absent and for foreign-owned so a job id cannot be probed. This one is
        reached only from the process that created the job, to ask what its own write actually did.
        """
        with self._uow_factory() as uow:
            return uow.jobs.by_id(job_id)

    def job_for_user(self, *, job_id: UUID, user_id: UUID) -> JobRecord:
        with self._uow_factory() as uow:
            return uow.jobs.get_for_user(job_id=job_id, user_id=user_id)

    def job_for_capability(self, *, job_id: UUID, capability_digest: str) -> JobRecord:
        with self._uow_factory() as uow:
            return uow.jobs.get_for_capability(
                job_id=job_id, capability_digest=capability_digest)

    def claim_job(self, *, job_id: UUID, worker_id: str,
                  lease_seconds: int = 300) -> JobRecord:
        with self._uow_factory() as uow:
            job = uow.jobs.claim(
                job_id=job_id, worker_id=worker_id, lease_seconds=lease_seconds)
            uow.commit()
            return job

    def renew_job(self, *, job_id: UUID, worker_id: str,
                  lease_seconds: int = 300) -> JobRecord:
        with self._uow_factory() as uow:
            job = uow.jobs.renew(
                job_id=job_id, worker_id=worker_id, lease_seconds=lease_seconds)
            uow.commit()
            return job

    def set_job_stage(self, *, job_id: UUID, worker_id: str, stage: str) -> JobRecord:
        with self._uow_factory() as uow:
            job = uow.jobs.set_stage(
                job_id=job_id, worker_id=worker_id, stage=stage)
            uow.commit()
            return job

    def finish_job_failure(self, *, job_id: UUID, worker_id: str,
                           error: str) -> tuple[JobRecord, bool]:
        with self._uow_factory() as uow:
            before = uow.jobs.by_id(job_id)
            if before is None:
                raise NotFound("job not found")
            result = uow.jobs.finish(
                job_id=job_id, worker_id=worker_id, status="failed",
                brand_id=before.brand_id, run_id=before.run_id, error=error)
            uow.commit()
            return result

    def finish_job_success(self, *, job_id: UUID, worker_id: str,
                           brand_id: UUID | None = None,
                           run_id: UUID | None = None,
                           warning: str | None = None) -> tuple[JobRecord, bool]:
        with self._uow_factory() as uow:
            before = uow.jobs.by_id(job_id)
            if before is None:
                raise NotFound("job not found")
            result = uow.jobs.finish(
                job_id=job_id, worker_id=worker_id, status="succeeded",
                brand_id=brand_id if brand_id is not None else before.brand_id,
                run_id=run_id if run_id is not None else before.run_id,
                warning=warning)
            uow.commit()
            return result

    # ------------------------------------------------------------------ direct job results
    def finish_direct_result_job_success(self, *, job_id: UUID, worker_id: str,
                                         result_kind: str, payload: Mapping[str, Any],
                                         schema_version: int = 1,
                                         occurred_at: datetime | None = None,
                                         event_payload: Mapping[str, Any] | None = None
                                         ) -> tuple[JobResultRecord | None, JobEventRecord | None]:
        """Direct result + Job succeeded + FINAL/DONE event — ONE transaction, ONE commit.

        This is the direct-result twin of `commit_scrape_success`. The provider work finished long
        before this is entered; nothing external runs while the job row is locked.

        All three facts or none. A result committed without its terminal event would be a succeeded
        business outcome beside a job that still looks running — C3 Class A — and a terminal event
        committed without its result would be a job that claims success and cannot say what it
        produced. Both are unreachable here because there is exactly one commit.

        Returns `(None, None)` when the job is ALREADY terminal. Terminal state is immutable, so a
        delayed or duplicated callback must not overwrite a decided outcome — and because
        `job_results.job_id` is the primary key, a second attempt on a non-terminal job fails on
        INSERT rather than replacing the first answer.

        Refuses a run-backed job outright. `runs.output` is the authority for those, and writing a
        direct result beside it would create two rows that can disagree about one fact with nothing
        afterwards able to say which was authoritative. PostgreSQL cannot express that as a
        cross-table CHECK without a trigger, so it is enforced here and pinned by a test.
        """
        from db.models import Job as _Job

        with self._uow_factory() as uow:
            locked = uow.session.get(_Job, job_id, with_for_update=True)
            if locked is None:
                raise NotFound("job not found")
            if locked.status in ("succeeded", "failed"):
                return None, None
            if locked.run_id is not None:
                raise InvalidTransition(
                    "this job is run-backed; its Run output is the business result authority "
                    "and a direct result must not be written beside it")
            result = uow.job_results.create_locked(
                job_id=job_id, result_kind=result_kind, payload=payload,
                schema_version=schema_version)
            terminal_payload = {"result_kind": str(result_kind).strip()}
            if event_payload:
                # The caller summary rides on the SAME terminal event the legacy store publishes,
                # so an SSE consumer sees one FINAL/DONE with one payload on either backend.
                # `result_kind` is written last so a caller cannot overwrite what the row says.
                terminal_payload = {**dict(event_payload), **terminal_payload}
            event = _apply_event_locked(
                uow, job_id=job_id, stage="final", status="done",
                occurred_at=occurred_at or utcnow(), worker_id=worker_id,
                payload=terminal_payload, locked_job=locked)
            uow.commit()
            return result, event

    def finish_enrichment_job_success(self, *, job_id: UUID, worker_id: str,
                                      data: LiveRunData,
                                      event_payload: Mapping[str, Any] | None = None,
                                      occurred_at: datetime | None = None
                                      ) -> tuple[RunRecord | None, JobEventRecord | None]:
        """Enrichment Run + Job succeeded + FINAL/DONE event — ONE transaction, ONE commit.

        The run-backed twin of `finish_direct_result_job_success`, and the reason
        `_create_live_run_locked` exists. `create_live_run` commits the Run alone; a job-backed
        completion that used it would leave a successful durable Run beside a job still marked
        running for as long as the next statement takes — the C3 Class-A window, opened on every
        analysis and influencer path at once.

        The Job is pointed at the Run it produced INSIDE the same transaction, so "this job
        succeeded" and "this is what it produced" become one fact rather than two rows that can
        disagree. Returns `(None, None)` when the job is already terminal: terminal state is
        immutable, and a duplicated callback must not create a second Run for one job.
        """
        from db.models import Job as _Job

        with self._uow_factory() as uow:
            locked = uow.session.get(_Job, job_id, with_for_update=True)
            if locked is None:
                raise NotFound("job not found")
            if locked.status in ("succeeded", "failed"):
                return None, None
            run = _create_live_run_locked(uow, data)
            locked.brand_id = data.brand_id
            locked.run_id = run.id
            event = _apply_event_locked(
                uow, job_id=job_id, stage="final", status="done",
                occurred_at=occurred_at or utcnow(), worker_id=worker_id,
                payload=dict(event_payload) if event_payload else None, locked_job=locked)
            uow.commit()
            return run, event

    def direct_job_result(self, *, job_id: UUID) -> JobResultRecord | None:
        """The direct result of a job, or None when it has one in a Run instead (or none at all)."""
        with self._uow_factory() as uow:
            return uow.job_results.for_job(job_id)

    # ------------------------------------------------------------------ durable job events
    def append_job_event(self, *, job_id: UUID, stage: str, status: str,
                         occurred_at: datetime, duration_ms: int = 0,
                         payload: Mapping[str, Any] | None = None,
                         error: str | None = None,
                         worker_id: str | None = None) -> JobEventRecord | None:
        """Append one event AND apply its job-state transition, in ONE transaction.

        The in-process store derives status from the event stream (`started` -> running, `error` ->
        failed, `final`+`done` -> succeeded); that mapping is reproduced rather than redesigned,
        because the SPA polls a projection of it.

        For a business completion the event must commit WITH the Run — see
        `commit_scrape_success`, which composes `_apply_event_locked` into its own transaction
        instead of calling this method afterwards.
        """
        with self._uow_factory() as uow:
            event = _apply_event_locked(
                uow, job_id=job_id, stage=stage, status=status, occurred_at=occurred_at,
                duration_ms=duration_ms, payload=payload, error=error, worker_id=worker_id)
            if event is None:
                return None
            uow.commit()
            return event

    def job_events_since(self, *, job_id: UUID, after_seq: int = 0,
                         limit: int = 500) -> list[JobEventRecord]:
        with self._uow_factory() as uow:
            return uow.job_events.since(job_id=job_id, after_seq=after_seq, limit=limit)

    def detect_crash_class_a(self) -> list[JobRecord]:
        with self._uow_factory() as uow:
            return uow.jobs.class_a_stuck()

    def detect_crash_class_a_direct(self) -> list[JobRecord]:
        """Class A for direct-result jobs — see `JobRepository.class_a_direct_stuck`."""
        with self._uow_factory() as uow:
            return uow.jobs.class_a_direct_stuck()

    def detect_crash_class_b(self) -> list[JobRecord]:
        with self._uow_factory() as uow:
            return uow.jobs.class_b_stuck()

    # ------------------------------------------------------------------ crash repair
    #
    # Repair takes the lease over rather than requiring one. Every other terminal path insists the
    # caller holds a live lease, which is exactly right while a worker is alive and exactly wrong
    # here: the case being repaired IS a worker that died holding one. The job row is locked FOR
    # UPDATE first, so two reconcilers racing the same job serialise, the loser reads a terminal
    # status and does nothing, and neither can write a second terminal event.

    def _take_over_locked(self, uow, locked, worker_id: str) -> None:
        now = utcnow()
        locked.status = "running"
        locked.claimed_by = worker_id
        locked.claimed_at = now
        locked.lease_expires_at = now + timedelta(seconds=60)
        locked.updated_at = now
        uow.session.flush()

    def repair_class_a(self, *, job_id: UUID, worker_id: str = "reconciler") -> bool:
        """A job whose successful durable result committed but whose terminal state did not.

        Verified, not assumed: the repair refuses unless a successful Run or a `job_results` row is
        actually there. Without that check this would be a routine that marks running jobs
        succeeded, which is the false success the whole lifecycle exists to make unreachable.

        True only when THIS call performed the repair, so a caller can count repairs rather than
        infer them; a job someone else already terminalised answers False.
        """
        from db.models import Job as _Job, Run

        with self._uow_factory() as uow:
            locked = uow.session.get(_Job, job_id, with_for_update=True)
            if locked is None or locked.status in ("succeeded", "failed"):
                return False
            run_id, source = None, None
            if locked.run_id is not None:
                run = uow.session.get(Run, locked.run_id)
                if run is not None and run.status == "succeeded":
                    run_id, source = run.id, "run"
            if source is None and uow.job_results.for_job(job_id) is not None:
                source = "job_result"
            if source is None:
                return False
            self._take_over_locked(uow, locked, worker_id)
            _row, changed = uow.jobs.finish(
                job_id=job_id, worker_id=worker_id, status="succeeded",
                brand_id=locked.brand_id, run_id=run_id)
            if not changed:
                return False
            uow.job_events.append_locked(
                job_id=job_id, stage="final", status="done", occurred_at=utcnow(),
                is_terminal=True, payload={"repaired": "class_a", "result_source": source})
            uow.commit()
            return True

    def repair_class_b(self, *, job_id: UUID, worker_id: str = "reconciler",
                       error: str = "worker stopped without finishing this job") -> bool:
        """A job left running by a dead worker with NO successful durable result.

        The truthful outcome is failure, and it is stated rather than left implicit: a job abandoned
        mid-flight and a job that failed are the same thing to every reader downstream — work that
        did not produce an answer — and leaving it RUNNING forever is the one reading that is
        certainly wrong. Refuses if a successful result IS present, so a Class-A job misrouted here
        cannot be marked failed on top of a result it actually produced.
        """
        from db.models import Job as _Job, Run

        with self._uow_factory() as uow:
            locked = uow.session.get(_Job, job_id, with_for_update=True)
            if locked is None or locked.status in ("succeeded", "failed"):
                return False
            if uow.job_results.for_job(job_id) is not None:
                return False
            if locked.run_id is not None:
                run = uow.session.get(Run, locked.run_id)
                if run is not None and run.status == "succeeded":
                    return False
            self._take_over_locked(uow, locked, worker_id)
            _row, changed = uow.jobs.finish(
                job_id=job_id, worker_id=worker_id, status="failed", error=error[:500])
            if not changed:
                return False
            uow.job_events.append_locked(
                job_id=job_id, stage="final", status="error", occurred_at=utcnow(),
                is_terminal=True, error=error[:500], payload={"repaired": "class_b"})
            uow.commit()
            return True

    # ------------------------------------------------------------------ gallery
    #
    # The bytes are ALREADY durable before any of these run — see `api/gallery_store.py`. These
    # methods record WHICH bytes and WHOSE publication, in one short transaction, and never touch
    # the filesystem themselves. That split is what makes the failure directions asymmetric on
    # purpose: an orphan file is recoverable, a row pointing at absent bytes is not.

    def record_gallery_entry(self, *, checksum: str, byte_size: int, mime: str, storage_key: str,
                             filename: str, owner_user_id: UUID | None = None,
                             brand_id: UUID | None = None, domain_key: str | None = None,
                             brand_name: str | None = None, content_type: str | None = None,
                             style: str | None = None,
                             metadata: Mapping[str, Any] | None = None,
                             origin: str = "live", legacy_seq: int | None = None,
                             imported_at: datetime | None = None,
                             created_at: datetime | None = None
                             ) -> tuple[GalleryBlobRecord, GalleryEntryRecord]:
        """Blob identity + logical entry, ONE transaction, ONE commit.

        The blob is reused when these bytes are already known and the entry is always new — that
        pair IS the physical/logical split. Five of the measured 28 entries share two blobs, and
        every one of the five is a separate publication somebody made.
        """
        with self._uow_factory() as uow:
            blob = uow.gallery_blobs.ensure(checksum=checksum, byte_size=byte_size, mime=mime,
                                            storage_key=storage_key)
            entry = uow.gallery_entries.create(
                checksum=blob.checksum, filename=filename, owner_user_id=owner_user_id,
                brand_id=brand_id, domain_key=domain_key, brand_name=brand_name,
                content_type=content_type, style=style, metadata=metadata, origin=origin,
                legacy_seq=legacy_seq, imported_at=imported_at, created_at=created_at)
            uow.commit()
            return blob, entry

    def gallery_history(self, *, user_id: UUID | None, limit: int = 100
                        ) -> list[GalleryEntryRecord]:
        with self._uow_factory() as uow:
            return uow.gallery_entries.visible_to(user_id=user_id, limit=limit)

    def poster_gallery_result(self, *, user_id: UUID, run_id: UUID | None = None,
                              domain_key: str | None = None) -> GalleryEntryRecord | None:
        with self._uow_factory() as uow:
            return uow.gallery_entries.poster_result(user_id=user_id, run_id=run_id, domain_key=domain_key)

    def gallery_entry_for_legacy_seq(self, *, legacy_seq: int) -> GalleryEntryRecord | None:
        """The deterministic import identity. Position in the source index IS the identity, so a
        re-import finds its own earlier work instead of duplicating it."""
        with self._uow_factory() as uow:
            return uow.gallery_entries.by_legacy_seq(legacy_seq)

    def gallery_blob(self, *, checksum: str) -> GalleryBlobRecord | None:
        with self._uow_factory() as uow:
            return uow.gallery_blobs.by_checksum(checksum)

    def gallery_entry_count(self) -> int:
        with self._uow_factory() as uow:
            return uow.gallery_entries.count()

    # ------------------------------------------------------------------ owner state / competitors
    def owner_inputs(self, *, brand_id: UUID, user_id: UUID) -> dict[str, Any]:
        with self._uow_factory() as uow:
            uow.brands.get_visible(user_id=user_id, brand_id=brand_id)
            return uow.owner_inputs.get(brand_id=brand_id)

    def set_owner_inputs(self, *, brand_id: UUID, user_id: UUID,
                         payload: Mapping[str, Any], schema_version: int = 1) -> dict[str, Any]:
        with self._uow_factory() as uow:
            uow.brands.lock_owned(brand_id=brand_id, user_id=user_id)
            result = uow.owner_inputs.set(
                brand_id=brand_id, payload=payload, schema_version=schema_version)
            uow.commit()
            return result

    def add_competitor(self, *, brand_id: UUID, user_id: UUID,
                       key: str, payload: Mapping[str, Any]) -> UUID:
        with self._uow_factory() as uow:
            uow.brands.lock_owned(brand_id=brand_id, user_id=user_id)
            result = uow.competitors.add(brand_id=brand_id, key=key, payload=payload)
            uow.commit()
            return result

    def remove_competitor(self, *, brand_id: UUID, user_id: UUID, key: str) -> bool:
        with self._uow_factory() as uow:
            uow.brands.lock_owned(brand_id=brand_id, user_id=user_id)
            result = uow.competitors.remove(brand_id=brand_id, key=key)
            uow.commit()
            return result

    def list_competitors(self, *, brand_id: UUID, user_id: UUID) -> list[dict[str, Any]]:
        with self._uow_factory() as uow:
            uow.brands.get_visible(user_id=user_id, brand_id=brand_id)
            return uow.competitors.list(brand_id=brand_id)

    # ------------------------------------------------------------------ calendar
    def create_calendar(
        self, *, brand_id: UUID, user_id: UUID, input_profile_id: UUID,
        posts: Sequence[Mapping[str, Any]], requested_config: Mapping[str, Any] | None = None,
    ) -> tuple[RunRecord, list[CalendarPostRecord]]:
        with self._uow_factory() as uow:
            uow.brands.lock_owned(
                brand_id=brand_id, user_id=user_id, allow_unclaimed=True)
            run = uow.runs.create_live_for_locked_brand(
                brand_id=brand_id, kind="calendar", created_by_user_id=user_id,
                status="running", input_profile_id=input_profile_id,
                requested_config=requested_config or {},
            )
            rows = uow.calendars.create_posts(
                calendar_run_id=run.id, brand_id=brand_id, posts=posts)
            run, _changed = uow.runs.finalize_success(
                run_id=run.id,
                output={"post_ids": [str(row.id) for row in rows]},
                output_schema_version=1,
            )
            uow.commit()
            return run, rows

    def calendar_post(self, *, post_id: UUID, user_id: UUID) -> CalendarPostRecord:
        with self._uow_factory() as uow:
            return uow.calendars.get_visible(post_id=post_id, user_id=user_id)

    # ------------------------------------------------------------------ attempts/assets/creative finalization
    def create_creative_attempt(self, *, run_id: UUID,
                                user_id: UUID) -> AttemptRecord:
        with self._uow_factory() as uow:
            run = uow.runs.get_visible(run_id=run_id, user_id=user_id)
            uow.brands.lock_owned(
                brand_id=run.brand_id, user_id=user_id, allow_unclaimed=True)
            locked = uow.runs.lock(run_id)
            if locked.status not in ("queued", "running"):
                raise InvalidTransition("creative run is already terminal")
            attempt = uow.attempts.create_for_locked_run(
                run_id=run_id, brand_id=run.brand_id)
            uow.commit()
            return attempt

    def finalize_creative_failure(self, *, run_id: UUID, attempt_id: UUID,
                                  user_id: UUID, error: str
                                  ) -> tuple[RunRecord, AttemptRecord]:
        """Fail the selected creative Attempt and its Run as one terminal fact.

        Also repairs the earlier half-state where the Run was already failed but its Attempt was
        left running: a failed Run is accepted as the immutable terminal outcome while the open
        child is closed in the same transaction.
        """
        with self._uow_factory() as uow:
            visible = uow.runs.get_visible(run_id=run_id, user_id=user_id)
            uow.brands.lock_owned(
                brand_id=visible.brand_id, user_id=user_id, allow_unclaimed=True)
            run = uow.runs.lock(run_id)
            attempt = uow.attempts.lock(attempt_id)
            if attempt.run_id != run.id or attempt.brand_id != run.brand_id:
                raise InvalidTransition("attempt does not belong to this run and brand")
            attempt = uow.attempts.finalize_failure(
                attempt_id=attempt.id, error=str(error)[:500], qa=attempt.qa)
            failed_run, changed = uow.runs.finalize_failure(
                run_id=run.id, error=str(error)[:500])
            if not changed and run.status != "failed":
                raise InvalidTransition(f"creative Run is already terminal as {run.status}")
            uow.commit()
            return failed_run, attempt

    def finalize_creative(
        self, data: CreativeFinalization, *, inject: FailureInjector = _no_failure,
    ) -> CreativeCommitResult:
        """Persist already-durable bytes, Attempt, selection, Run and Job in one short transaction."""
        with self._uow_factory() as uow:
            visible = uow.runs.get_visible(run_id=data.run_id, user_id=data.user_id)
            uow.brands.lock_owned(
                brand_id=visible.brand_id, user_id=data.user_id, allow_unclaimed=True)
            run = uow.runs.lock(data.run_id)
            attempt = uow.attempts.lock(data.attempt_id)
            if attempt.run_id != run.id or attempt.brand_id != run.brand_id:
                raise InvalidTransition("attempt does not belong to this run and brand")
            if data.select_attempt and not data.qa_passed:
                raise InvalidTransition("a rejected attempt is not eligible for selection")

            asset = uow.assets.record_verified(
                brand_id=run.brand_id, storage_key=data.asset.storage_key,
                kind=data.asset.kind, mime=data.asset.mime,
                byte_count=data.asset.byte_count,
                checksum_sha256=data.asset.checksum_sha256,
                durable=data.asset.durable,
                created_by_user_id=data.asset.created_by_user_id,
                width=data.asset.width, height=data.asset.height,
            )
            attempt = uow.attempts.finalize_success(
                attempt_id=attempt.id, asset_id=asset.id,
                qa={**(attempt.qa or {}), **data.qa}, qa_passed=data.qa_passed)
            inject("after_attempt_finalize")
            run, changed = uow.runs.finalize_success(
                run_id=run.id, output=data.output,
                output_schema_version=data.output_schema_version,
                selected_attempt_id=attempt.id if data.select_attempt else None,
            )
            if not changed:
                raise InvalidTransition("creative Run was already finalized")

            job: JobRecord | None = None
            if data.job_id is not None:
                if not data.worker_id:
                    raise ValueError("worker_id is required when finalizing a Job")
                inject("before_job_terminal_update")
                job, job_changed = uow.jobs.finish(
                    job_id=data.job_id, worker_id=data.worker_id, status="succeeded",
                    brand_id=run.brand_id, run_id=run.id)
                if not job_changed:
                    raise InvalidTransition("creative Job was already finalized")
            inject("before_commit")
            uow.commit()
            return CreativeCommitResult(run, attempt, asset, job)

    def asset_for_user(self, *, asset_id: UUID, user_id: UUID) -> AssetRecord:
        with self._uow_factory() as uow:
            return uow.assets.get_visible(asset_id=asset_id, user_id=user_id)

    def attempt_for_user(self, *, attempt_id: UUID, user_id: UUID) -> AttemptRecord:
        with self._uow_factory() as uow:
            return uow.attempts.get_visible(attempt_id=attempt_id, user_id=user_id)

    def run_for_user(self, *, run_id: UUID, user_id: UUID) -> RunRecord:
        with self._uow_factory() as uow:
            return uow.runs.get_visible(run_id=run_id, user_id=user_id)

    # ------------------------------------------------------------------ future NEW usage only
    def record_usage(
        self, *, run_id: UUID, brand_id: UUID, provider: str, model: str,
        input_tokens: int = 0, output_tokens: int = 0, thoughts_tokens: int = 0,
        cost_usd: Decimal = Decimal("0"), duration_ms: int | None = None,
        stage: str | None = None, attempt_id: UUID | None = None,
    ) -> UsageRecordValue:
        with self._uow_factory() as uow:
            result = uow.usage.record(
                run_id=run_id, brand_id=brand_id, provider=provider, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
                thoughts_tokens=thoughts_tokens, cost_usd=cost_usd,
                duration_ms=duration_ms, stage=stage, attempt_id=attempt_id)
            uow.commit()
            return result


__all__ = [
    "CreativeFinalization", "LiveRunData", "PersistenceService", "ProfileData",
    "ScrapeSuccess", "VerifiedAssetData",
]
