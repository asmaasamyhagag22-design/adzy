# -*- coding: utf-8 -*-
"""Typed PostgreSQL repositories.

Every repository shares a caller-owned SQLAlchemy Session.  None creates a Session and none calls
commit: transaction ownership belongs to the UnitOfWork/application service.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.errors import (
    AlreadyExists,
    AmbiguousCurrentRun,
    ArchivedBrand,
    AssetNotDurable,
    ClaimLost,
    ConcurrencyConflict,
    ForeignOwner,
    InvalidTransition,
    NotFound,
    NotVisible,
    TerminalState,
    UnclaimedBrand,
    raise_mapped_integrity,
)
from db.models import (
    GalleryBlob,
    GalleryEntry,
    JobEvent,
    JobResult,
    Approval,
    Asset,
    Attempt,
    Brand,
    BrandCompetitor,
    BrandOwnerInputs,
    CalendarPost,
    Job,
    ProfileSnapshot,
    Run,
    RunDependency,
    UsageRecord,
    User,
)
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
    CanonicalBrand,
    JobRecord,
    ProfileRecord,
    RunRecord,
    UsageRecordValue,
    UserRecord,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _user(row: User) -> UserRecord:
    return UserRecord(row.id, row.email, row.full_name, row.legacy_user_id,
                      bool(row.is_active), row.created_at)


def _brand(row: Brand) -> BrandRecord:
    return BrandRecord(row.id, row.domain_key, row.owner_id, row.current_profile_id,
                       row.display_name, row.source_url, row.deleted_at,
                       row.created_at, row.updated_at)


def _profile(row: ProfileSnapshot) -> ProfileRecord:
    return ProfileRecord(row.id, row.brand_id, row.version, dict(row.profile),
                         row.schema_version, row.created_at)


def _run(row: Run) -> RunRecord:
    return RunRecord(
        id=row.id, brand_id=row.brand_id, kind=row.kind, status=row.status,
        kind_seq=row.kind_seq, input_profile_id=row.input_profile_id,
        selected_attempt_id=row.selected_attempt_id,
        output=dict(row.output) if row.output is not None else None,
        error=row.error, created_at=row.created_at, finished_at=row.finished_at,
        requested_config=dict(row.requested_config or {}),
        effective_config=(dict(row.effective_config) if row.effective_config is not None else None),
        config_schema_version=row.config_schema_version,
        output_schema_version=row.output_schema_version)


def _approval(row: Approval) -> ApprovalRecord:
    return ApprovalRecord(
        id=row.id, brand_id=row.brand_id, subject_run_id=row.subject_run_id,
        decision=row.decision, decided_by_user_id=row.decided_by_user_id,
        decided_at=row.decided_at, origin=row.origin, imported_at=row.imported_at,
        subject_kind=row.subject_kind, note=row.note)


def _job(row: Job) -> JobRecord:
    return JobRecord(
        id=row.id, run_id=row.run_id, brand_id=row.brand_id,
        requested_domain_key=row.requested_domain_key, status=row.status,
        execution_no=row.execution_no, access_mode=row.access_mode,
        owner_user_id=row.owner_user_id, capability_digest=row.capability_digest,
        current_stage=row.current_stage, error=row.error, warning=row.warning,
        claimed_by=row.claimed_by, claimed_at=row.claimed_at,
        lease_expires_at=row.lease_expires_at, created_at=row.created_at,
        updated_at=row.updated_at, finished_at=row.finished_at,
    )


def _calendar_post(row: CalendarPost) -> CalendarPostRecord:
    return CalendarPostRecord(
        row.id, row.calendar_run_id, row.brand_id, row.ordinal, row.scheduled_date,
        row.channel, row.content_type, dict(row.payload),
    )


def _asset(row: Asset) -> AssetRecord:
    return AssetRecord(row.id, row.brand_id, row.storage_key, row.kind, row.mime,
                       row.bytes, row.checksum_sha256, row.created_by_user_id)


def _attempt(row: Attempt) -> AttemptRecord:
    return AttemptRecord(row.id, row.run_id, row.brand_id, row.ordinal, row.status,
                         row.asset_id, dict(row.qa), row.qa_passed, row.error)


def _visible_brand(user_id: UUID):
    return and_(Brand.deleted_at.is_(None),
                or_(Brand.owner_id.is_(None), Brand.owner_id == user_id))


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, email: str, hashed_password: str, full_name: str = "",
               legacy_user_id: str | None = None) -> UserRecord:
        canonical = str(email or "").strip().lower()
        statement = (
            pg_insert(User).values(
                email=canonical, hashed_password=hashed_password,
                full_name=str(full_name or "").strip() or None,
                legacy_user_id=legacy_user_id,
            ).on_conflict_do_nothing(index_elements=[User.email]).returning(User.id)
        )
        try:
            user_id = self.session.execute(statement).scalar_one_or_none()
            if user_id is None:
                raise AlreadyExists("canonical email already exists")
            row = self.session.get(User, user_id)
            assert row is not None
            return _user(row)
        except IntegrityError as exc:
            raise_mapped_integrity(exc, {
                ("23505", "uq_users_legacy_user_id"):
                    (AlreadyExists, "legacy user identity already exists"),
            })

    def by_id(self, user_id: UUID) -> UserRecord | None:
        row = self.session.get(User, user_id)
        return _user(row) if row is not None else None

    def by_legacy_id(self, legacy_user_id: str) -> UserRecord | None:
        row = self.session.scalar(select(User).where(User.legacy_user_id == legacy_user_id))
        return _user(row) if row is not None else None

    def by_email(self, email: str) -> UserRecord | None:
        canonical = str(email or "").strip().lower()
        row = self.session.scalar(select(User).where(User.email == canonical))
        return _user(row) if row is not None else None

    def verify_credentials(self, *, email: str, verifier: Callable[[str], bool],
                           ) -> UserRecord | None:
        """Check a password WITHOUT letting the hash leave this layer.

        `UserRecord` deliberately carries no `hashed_password`, so callers cannot accidentally log
        or serialise it — which means authentication cannot be "fetch the record, compare the
        hash". The caller supplies a verifier instead; the stored hash is handed to it and nothing
        else, and only the public record comes back.

        The hash format is self-describing (`pbkdf2_sha256$iterations$salt$dk`), so a row migrated
        verbatim from the file store verifies with the same function that wrote it — no rehash, no
        reset, no plaintext.

        An inactive account fails the same way a wrong password does: the caller learns "no", not
        which of the two it was.
        """
        canonical = str(email or "").strip().lower()
        row = self.session.scalar(select(User).where(User.email == canonical))
        if row is None or not row.is_active:
            return None
        return _user(row) if verifier(row.hashed_password or "") else None


class BrandRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def owner_of(self, domain_key: str) -> BrandOwnership:
        row = self.session.execute(
            select(Brand.owner_id, Brand.deleted_at).where(Brand.domain_key == domain_key)
        ).one_or_none()
        if row is None:
            return BrandOwnership(False)
        return BrandOwnership(True, row.owner_id, row.deleted_at is not None)

    def get_visible(self, *, user_id: UUID, brand_id: UUID | None = None,
                    domain_key: str | None = None, lock: bool = False) -> BrandRecord:
        if (brand_id is None) == (domain_key is None):
            raise ValueError("exactly one of brand_id/domain_key is required")
        condition = Brand.id == brand_id if brand_id is not None else Brand.domain_key == domain_key
        query = select(Brand).where(condition, _visible_brand(user_id))
        if lock:
            query = query.with_for_update()
        row = self.session.scalar(query)
        if row is None:
            raise NotVisible("brand is absent, archived, or not visible to this user")
        return _brand(row)

    def list_visible(self, *, user_id: UUID, limit: int = 100) -> list[BrandRecord]:
        rows = self.session.scalars(
            select(Brand).where(_visible_brand(user_id))
            .order_by(Brand.updated_at.desc(), Brand.id).limit(max(0, min(int(limit), 500)))
        )
        return [_brand(row) for row in rows]

    def lock_or_create_for_scrape(
        self, *, domain_key: str, user_id: UUID, display_name: str | None,
        source_url: str | None,
    ) -> CanonicalBrand:
        """Resolve the unique domain then lock its canonical Brand row.

        The INSERT's unique-domain conflict serialises the no-row-yet case.  Every later mutation
        uses this same Brand row lock, which is the first lock in the canonical order.
        """
        created_id = self.session.execute(
            pg_insert(Brand).values(
                domain_key=domain_key, owner_id=user_id,
                display_name=display_name, source_url=source_url,
            ).on_conflict_do_nothing(index_elements=[Brand.domain_key]).returning(Brand.id)
        ).scalar_one_or_none()
        row = self.session.scalar(
            select(Brand).where(Brand.domain_key == domain_key).with_for_update())
        if row is None:  # pragma: no cover - unique insert/select in one transaction guarantees it
            raise ConcurrencyConflict("canonical brand disappeared during domain resolution")

        created = created_id is not None
        claimed = False
        reactivated = False
        if not created:
            if row.deleted_at is not None:
                if row.owner_id is None:
                    raise ArchivedBrand("an archived unclaimed brand requires an explicit decision")
                if row.owner_id != user_id:
                    raise ForeignOwner("archived brand belongs to another account")
                row.deleted_at = None
                reactivated = True
            if row.owner_id is None:
                row.owner_id = user_id
                claimed = True
            elif row.owner_id != user_id:
                raise ForeignOwner("brand belongs to another account")

        row.display_name = display_name
        row.source_url = source_url
        row.updated_at = utcnow()
        self.session.flush()
        return CanonicalBrand(_brand(row), created, claimed, reactivated)

    def lock_owned(self, *, brand_id: UUID, user_id: UUID,
                   allow_unclaimed: bool = False) -> BrandRecord:
        row = self.session.scalar(select(Brand).where(Brand.id == brand_id).with_for_update())
        if row is None or row.deleted_at is not None:
            raise NotVisible("brand is absent or archived")
        if row.owner_id is None:
            if not allow_unclaimed:
                raise UnclaimedBrand("operation requires a recorded brand owner")
        elif row.owner_id != user_id:
            raise ForeignOwner("brand belongs to another account")
        return _brand(row)

    def set_current_profile(self, *, brand_id: UUID, profile_id: UUID) -> BrandRecord:
        row = self.session.get(Brand, brand_id)
        if row is None:
            raise NotFound("brand not found")
        row.current_profile_id = profile_id
        row.updated_at = utcnow()
        self.session.flush()
        return _brand(row)

    def archive(self, *, domain_key: str, user_id: UUID) -> bool:
        row = self.session.scalar(
            select(Brand).where(Brand.domain_key == domain_key).with_for_update())
        if row is None or row.deleted_at is not None:
            return False
        if row.owner_id is None:
            raise UnclaimedBrand("an unclaimed brand cannot be archived")
        if row.owner_id != user_id:
            return False  # same non-enumerating outcome as absence
        row.deleted_at = utcnow()
        row.updated_at = row.deleted_at
        self.session.flush()
        return True


class ProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_version_for_locked_brand(
        self, *, brand_id: UUID, profile: Mapping[str, Any], schema_version: int,
        country_canonical: str | None = None, country_source: str | None = None,
        country_raw: str | None = None, primary_language: str | None = None,
        language_source: str | None = None,
    ) -> ProfileRecord:
        version = self.session.scalar(
            select(func.coalesce(func.max(ProfileSnapshot.version), 0) + 1)
            .where(ProfileSnapshot.brand_id == brand_id))
        row = ProfileSnapshot(
            brand_id=brand_id, version=int(version or 1), profile=dict(profile),
            schema_version=schema_version, country_canonical=country_canonical,
            country_source=country_source, country_raw=country_raw,
            primary_language=primary_language, language_source=language_source,
            origin="live", created_at=utcnow(), imported_at=None,
        )
        self.session.add(row)
        self.session.flush()
        return _profile(row)

    def by_id(self, profile_id: UUID) -> ProfileRecord | None:
        """One immutable profile VERSION by id — the snapshot a Run was built from.

        Distinct from `current`: a scrape job's deliverable is the profile THAT scrape committed,
        not whatever became current afterwards. Answering with `current` would quietly hand a
        later re-scrape's output to a caller asking about an older job.
        """
        row = self.session.get(ProfileSnapshot, profile_id)
        return _profile(row) if row is not None else None

    def current(self, *, brand_id: UUID) -> ProfileRecord | None:
        row = self.session.scalar(
            select(ProfileSnapshot).join(
                Brand,
                and_(Brand.current_profile_id == ProfileSnapshot.id,
                     Brand.id == ProfileSnapshot.brand_id),
            ).where(Brand.id == brand_id, Brand.deleted_at.is_(None)))
        return _profile(row) if row is not None else None


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_live_for_locked_brand(
        self, *, brand_id: UUID, kind: str, created_by_user_id: UUID,
        status: str = "queued", input_profile_id: UUID | None = None,
        calendar_post_id: UUID | None = None,
        requested_config: Mapping[str, Any] | None = None,
        effective_config: Mapping[str, Any] | None = None,
        config_schema_version: int = 1,
        output: Mapping[str, Any] | None = None,
        output_schema_version: int | None = None,
        error: str | None = None,
        dependencies: Sequence[tuple[UUID, str]] = (),
    ) -> RunRecord:
        seq = self.session.scalar(
            select(func.coalesce(func.max(Run.kind_seq), 0) + 1)
            .where(Run.brand_id == brand_id, Run.kind == kind))
        terminal = status in ("succeeded", "failed")
        row = Run(
            brand_id=brand_id, kind=kind, status=status,
            input_profile_id=input_profile_id, calendar_post_id=calendar_post_id,
            requested_config=dict(requested_config or {}),
            effective_config=(dict(effective_config) if effective_config is not None else None),
            config_schema_version=config_schema_version,
            output=(dict(output) if output is not None else None),
            output_schema_version=output_schema_version, error=error,
            kind_seq=int(seq or 1), origin="live", lineage_complete=True,
            created_by_user_id=created_by_user_id, created_at=utcnow(),
            finished_at=utcnow() if terminal else None,
        )
        self.session.add(row)
        self.session.flush()
        for depends_on_run_id, relation in dependencies:
            self.session.add(RunDependency(
                run_id=row.id, depends_on_run_id=depends_on_run_id,
                brand_id=brand_id, relation=relation,
            ))
        self.session.flush()
        return _run(row)

    def lock(self, run_id: UUID) -> RunRecord:
        row = self.session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if row is None:
            raise NotFound("run not found")
        return _run(row)

    def get_visible(self, *, run_id: UUID, user_id: UUID) -> RunRecord:
        row = self.session.scalar(
            select(Run).join(Brand, Brand.id == Run.brand_id)
            .where(Run.id == run_id, _visible_brand(user_id)))
        if row is None:
            raise NotVisible("run is absent or not visible")
        return _run(row)

    def current_successful(self, *, brand_id: UUID, kind: str,
                           lock: bool = False) -> RunRecord | None:
        query = (
            select(Run).where(
                Run.brand_id == brand_id, Run.kind == kind, Run.status == "succeeded")
            .order_by(Run.kind_seq.desc().nulls_last(), Run.id.desc()).limit(2)
        )
        if lock:
            query = query.with_for_update()
        rows = list(self.session.scalars(query))
        if len(rows) > 1 and rows[0].kind_seq is None and rows[1].kind_seq is None:
            raise AmbiguousCurrentRun(
                "multiple pre-sequence successful runs have no truthful current ordering")
        return _run(rows[0]) if rows else None

    def finalize_success(
        self, *, run_id: UUID, output: Mapping[str, Any], output_schema_version: int,
        selected_attempt_id: UUID | None = None,
    ) -> tuple[RunRecord, bool]:
        now = utcnow()
        changed = self.session.execute(
            update(Run).where(Run.id == run_id, Run.status.in_(("queued", "running"))).values(
                status="succeeded", output=dict(output),
                output_schema_version=output_schema_version,
                selected_attempt_id=selected_attempt_id, error=None, finished_at=now,
            ).returning(Run.id)
        ).scalar_one_or_none()
        row = self.session.get(Run, run_id)
        if row is None:
            raise NotFound("run not found")
        if changed is not None:
            return _run(row), True
        if (row.status == "succeeded" and row.output == dict(output)
                and row.output_schema_version == output_schema_version
                and row.selected_attempt_id == selected_attempt_id):
            return _run(row), False
        raise TerminalState(f"run is already terminal as {row.status}")

    def finalize_failure(self, *, run_id: UUID, error: str) -> tuple[RunRecord, bool]:
        changed = self.session.execute(
            update(Run).where(Run.id == run_id, Run.status.in_(("queued", "running"))).values(
                status="failed", error=str(error), output=None, finished_at=utcnow(),
            ).returning(Run.id)
        ).scalar_one_or_none()
        row = self.session.get(Run, run_id)
        if row is None:
            raise NotFound("run not found")
        if changed is not None:
            return _run(row), True
        if row.status == "failed" and row.error == str(error):
            return _run(row), False
        raise TerminalState(f"run is already terminal as {row.status}")


class ApprovalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def approved_for_run(self, run_id: UUID) -> ApprovalRecord | None:
        row = self.session.scalar(
            select(Approval).where(
                Approval.subject_run_id == run_id, Approval.decision == "approved")
            .order_by(Approval.decided_at.desc().nulls_last(), Approval.id).limit(1))
        return _approval(row) if row is not None else None

    def latest_for_run(self, run_id: UUID) -> ApprovalRecord | None:
        row = self.session.scalar(
            select(Approval).where(Approval.subject_run_id == run_id)
            .order_by(Approval.decided_at.desc().nulls_last(), Approval.id.desc()).limit(1))
        return _approval(row) if row is not None else None

    def create_live(self, *, brand_id: UUID, run_id: UUID,
                    actor_id: UUID, note: str | None = None,
                    subject_kind: str = "swot", decision: str = "approved") -> ApprovalRecord:
        row = Approval(
            brand_id=brand_id, subject_run_id=run_id, subject_kind=subject_kind,
            decision=decision, decided_by_user_id=actor_id, decided_at=utcnow(),
            origin="live", imported_at=None, note=note,
        )
        self.session.add(row)
        self.session.flush()
        return _approval(row)


def _job_event(row: JobEvent) -> JobEventRecord:
    return JobEventRecord(
        row.id, row.job_id, row.seq, row.stage, row.status, row.is_terminal,
        row.occurred_at, row.duration_ms, row.error,
        dict(row.payload) if row.payload is not None else None, row.created_at)


def _job_result(row: "JobResult") -> JobResultRecord:
    return JobResultRecord(
        row.job_id, row.result_kind, row.schema_version, dict(row.payload), row.created_at)


class JobResultRepository:
    """The direct durable result of a Job that has no Run. At most one, structurally.

    `job_results.job_id` is the PRIMARY KEY, so a second result for one job is a violated primary
    key rather than a policy this class has to remember. That is deliberate: retries and double
    finalisation are exactly the paths that would otherwise produce two answers to one question,
    and they are also the paths least likely to be exercised before production.

    NO UPDATE PATH. A result is written once, inside the transaction that terminalises its job.
    There is no `set`, no `upsert` and no `replace`, because a terminal job whose result can still
    change is a job whose answer depends on when you asked.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_locked(self, *, job_id: UUID, result_kind: str, payload: Mapping[str, Any],
                      schema_version: int = 1) -> JobResultRecord:
        """Insert the direct result INSIDE the caller's transaction. No commit here.

        The caller must already hold the job row lock, because this row and the job's terminal
        status are one fact: committing them separately is the C3 Class-A window in miniature.
        """
        if not str(result_kind or "").strip():
            raise ValueError("a direct job result needs a result_kind")
        if payload is None:
            raise ValueError("a direct job result needs a payload")
        row = JobResult(
            job_id=job_id, result_kind=str(result_kind).strip(),
            schema_version=int(schema_version or 1), payload=dict(payload))
        self.session.add(row)
        self.session.flush()
        return _job_result(row)

    def for_job(self, job_id: UUID) -> JobResultRecord | None:
        row = self.session.get(JobResult, job_id)
        return _job_result(row) if row is not None else None


#: Event -> job status, exactly as the in-process store derives it today. Reproduced rather than
#: redesigned: the SPA polls a projection of these statuses, so inventing a new mapping here would
#: change what a running job looks like on screen.
_STARTED = "started"
_DONE = "done"
_ERROR = "error"
_FINAL_STAGE = "final"


class JobEventRepository:
    """Append-only durable event log, one monotonic sequence per job.

    ORDER COMES FROM `seq`, NOT FROM TIME. Two events emitted inside the same millisecond order
    arbitrarily by timestamp, and a clock adjustment inverts them outright — so the sequence is
    allocated inside the caller's transaction while the JOB ROW IS LOCKED, which serialises every
    appender for that job against each other and nobody else. `UNIQUE(job_id, seq)` turns a mistake
    in that allocation into a failed insert rather than a silently reordered stream.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def next_seq(self, job_id: UUID) -> int:
        """The next sequence number. The caller MUST already hold the job row lock."""
        highest = self.session.scalar(
            select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))
        return int(highest or 0) + 1

    def append_locked(self, *, job_id: UUID, stage: str, status: str, occurred_at: datetime,
                      duration_ms: int = 0, payload: Mapping[str, Any] | None = None,
                      error: str | None = None, is_terminal: bool = False) -> JobEventRecord:
        row = JobEvent(
            job_id=job_id, seq=self.next_seq(job_id), stage=str(stage), status=str(status),
            is_terminal=bool(is_terminal), occurred_at=occurred_at,
            duration_ms=int(duration_ms or 0), error=error,
            payload=dict(payload) if payload is not None else None)
        self.session.add(row)
        self.session.flush()
        return _job_event(row)

    def since(self, *, job_id: UUID, after_seq: int = 0, limit: int = 500
              ) -> list[JobEventRecord]:
        """Events after `after_seq`, in sequence order — the replay a reconnecting reader needs."""
        rows = self.session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.seq > int(after_seq))
            .order_by(JobEvent.seq)
            .limit(int(limit))
        ).all()
        return [_job_event(r) for r in rows]

    def terminal(self, job_id: UUID) -> JobEventRecord | None:
        row = self.session.scalar(
            select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.is_terminal.is_(True)))
        return _job_event(row) if row is not None else None


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_user(self, *, owner_user_id: UUID, requested_domain_key: str | None = None,
                    run_id: UUID | None = None, brand_id: UUID | None = None,
                    execution_no: int = 1) -> JobRecord:
        row = Job(
            run_id=run_id, brand_id=brand_id, requested_domain_key=requested_domain_key,
            status="queued", execution_no=execution_no, access_mode="user",
            owner_user_id=owner_user_id, capability_digest=None,
        )
        self.session.add(row)
        self.session.flush()
        return _job(row)

    def create_capability(self, *, capability_digest: str,
                          requested_domain_key: str | None = None) -> JobRecord:
        if not _SHA256.fullmatch(str(capability_digest or "")):
            raise ValueError("capability_digest must be a lowercase SHA-256 hex digest")
        row = Job(
            requested_domain_key=requested_domain_key, status="queued", access_mode="capability",
            owner_user_id=None, capability_digest=capability_digest,
        )
        self.session.add(row)
        self.session.flush()
        return _job(row)

    def by_id(self, job_id: UUID) -> JobRecord | None:
        row = self.session.get(Job, job_id)
        return _job(row) if row is not None else None

    def get_for_user(self, *, job_id: UUID, user_id: UUID) -> JobRecord:
        row = self.session.scalar(select(Job).where(
            Job.id == job_id, Job.access_mode == "user", Job.owner_user_id == user_id))
        if row is None:
            raise NotVisible("job is absent or not visible")
        return _job(row)

    def get_for_capability(self, *, job_id: UUID, capability_digest: str) -> JobRecord:
        row = self.session.scalar(select(Job).where(
            Job.id == job_id, Job.access_mode == "capability",
            Job.capability_digest == capability_digest))
        if row is None:
            raise NotVisible("job is absent or not visible")
        return _job(row)

    def claim(self, *, job_id: UUID, worker_id: str,
              lease_seconds: int) -> JobRecord:
        if not str(worker_id or "").strip():
            raise ValueError("worker_id is required")
        now = utcnow()
        changed = self.session.execute(
            update(Job).where(Job.id == job_id, Job.status == "queued").values(
                status="running", claimed_by=worker_id, claimed_at=now,
                lease_expires_at=now + timedelta(seconds=max(1, int(lease_seconds))),
                updated_at=now,
            ).returning(Job.id)
        ).scalar_one_or_none()
        row = self.session.get(Job, job_id)
        if row is None:
            raise NotFound("job not found")
        if changed is None:
            raise ConcurrencyConflict(f"job cannot be claimed from status {row.status}")
        return _job(row)

    def renew(self, *, job_id: UUID, worker_id: str,
              lease_seconds: int) -> JobRecord:
        now = utcnow()
        changed = self.session.execute(
            update(Job).where(
                Job.id == job_id, Job.status == "running", Job.claimed_by == worker_id,
                Job.lease_expires_at >= now,
            ).values(
                lease_expires_at=now + timedelta(seconds=max(1, int(lease_seconds))),
                updated_at=now,
            ).returning(Job.id)
        ).scalar_one_or_none()
        row = self.session.get(Job, job_id)
        if row is None:
            raise NotFound("job not found")
        if changed is None:
            if row.status in ("succeeded", "failed"):
                raise TerminalState(f"job is already terminal as {row.status}")
            raise ClaimLost("worker does not own a live lease")
        return _job(row)

    def set_stage(self, *, job_id: UUID, worker_id: str, stage: str) -> JobRecord:
        """Move the durable polling projection while a worker still owns the live lease."""
        current_stage = str(stage or "").strip()
        if not current_stage:
            raise ValueError("job stage is required")
        now = utcnow()
        changed = self.session.execute(
            update(Job).where(
                Job.id == job_id, Job.status == "running", Job.claimed_by == worker_id,
                Job.lease_expires_at >= now,
            ).values(current_stage=current_stage, updated_at=now).returning(Job.id)
        ).scalar_one_or_none()
        row = self.session.get(Job, job_id)
        if row is None:
            raise NotFound("job not found")
        if changed is None:
            if row.status in ("succeeded", "failed"):
                raise TerminalState(f"job is already terminal as {row.status}")
            raise ClaimLost("worker does not own a live lease")
        return _job(row)

    def finish(
        self, *, job_id: UUID, worker_id: str, status: str,
        brand_id: UUID | None = None, run_id: UUID | None = None,
        error: str | None = None, warning: str | None = None,
    ) -> tuple[JobRecord, bool]:
        if status not in ("succeeded", "failed"):
            raise ValueError("terminal job status must be succeeded or failed")
        now = utcnow()
        values: dict[str, Any] = {
            "status": status, "error": error if status == "failed" else None,
            "warning": warning, "finished_at": now, "lease_expires_at": None,
            "updated_at": now,
        }
        if brand_id is not None:
            values["brand_id"] = brand_id
        if run_id is not None:
            values["run_id"] = run_id
        changed = self.session.execute(
            update(Job).where(
                Job.id == job_id, Job.status == "running", Job.claimed_by == worker_id,
                Job.lease_expires_at >= now,
            ).values(**values).returning(Job.id)
        ).scalar_one_or_none()
        row = self.session.get(Job, job_id)
        if row is None:
            raise NotFound("job not found")
        if changed is not None:
            return _job(row), True
        identical = (row.status == status and row.brand_id == brand_id and row.run_id == run_id
                     and (status != "failed" or row.error == error))
        if identical:
            return _job(row), False
        if row.status in ("succeeded", "failed"):
            raise TerminalState(f"job is already terminal as {row.status}")
        raise ClaimLost("worker does not own a live lease")

    def class_a_stuck(self) -> list[JobRecord]:
        rows = self.session.scalars(
            select(Job).join(Run, Run.id == Job.run_id).where(
                Job.status.in_(("queued", "running")), Run.status == "succeeded"))
        return [_job(row) for row in rows]

    def class_a_direct_stuck(self) -> list[JobRecord]:
        """Class A for jobs whose result is a `job_results` row rather than a Run.

        `class_a_stuck` joins through `run_id`, so it is structurally blind to a direct-result job:
        that job has no Run and never will. The condition is the same fact in the other shape — an
        unambiguous successful durable result beside a job that never reached terminal — and it
        needs its own query rather than a looser version of the first, because widening the
        run-backed query would also match jobs that simply have no result at all.
        """
        rows = self.session.scalars(
            select(Job).join(JobResult, JobResult.job_id == Job.id).where(
                Job.status.in_(("queued", "running"))))
        return [_job(row) for row in rows]

    def class_b_stuck(self, *, at: datetime | None = None) -> list[JobRecord]:
        moment = at or utcnow()
        succeeded = select(Run.id).where(
            Run.id == Job.run_id, Run.status == "succeeded").exists()
        rows = self.session.scalars(select(Job).where(
            Job.status == "running", Job.lease_expires_at < moment,
            or_(Job.run_id.is_(None), ~succeeded)))
        return [_job(row) for row in rows]


def _gallery_blob(row: "GalleryBlob") -> GalleryBlobRecord:
    return GalleryBlobRecord(row.checksum, row.byte_size, row.mime, row.storage_key,
                             row.created_at)


def _gallery_entry(row: "GalleryEntry", blob: "GalleryBlob | None" = None) -> GalleryEntryRecord:
    return GalleryEntryRecord(
        row.id, row.owner_user_id, row.brand_id, row.checksum, row.filename, row.domain_key,
        row.brand_name, row.content_type, row.style, dict(row.metadata_json or {}), row.origin,
        row.legacy_seq, row.imported_at, row.created_at,
        storage_key=(blob.storage_key if blob is not None else ""),
        mime=(blob.mime if blob is not None else ""),
        byte_size=(blob.byte_size if blob is not None else 0))


class GalleryBlobRepository:
    """Physical bytes, addressed by content. One row per distinct image, however often it appears.

    NO UPDATE PATH, and no delete. A blob is identified BY its bytes, so "change this blob" is not
    an operation that means anything: different bytes are a different row. Deleting one while an
    entry still points at it is refused by `ON DELETE RESTRICT` at the schema, which is why history
    survives a brand archive (§16).
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def by_checksum(self, checksum: str) -> GalleryBlobRecord | None:
        row = self.session.get(GalleryBlob, str(checksum))
        return _gallery_blob(row) if row is not None else None

    def ensure(self, *, checksum: str, byte_size: int, mime: str,
               storage_key: str) -> GalleryBlobRecord:
        """The blob for these bytes, creating it only if it is not already there. No commit.

        Idempotent by identity rather than by policy: the checksum IS the primary key, so a
        concurrent writer that got there first is found by the read and reused. The caller must
        already have made the bytes durable — this row is a claim that they exist.
        """
        existing = self.session.get(GalleryBlob, str(checksum))
        if existing is not None:
            return _gallery_blob(existing)
        row = GalleryBlob(checksum=str(checksum), byte_size=int(byte_size), mime=str(mime),
                          storage_key=str(storage_key))
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError:
            # Lost a race for the same bytes. That is the dedup working, not a failure.
            self.session.rollback()
            raise ConcurrencyConflict("this blob was created concurrently") from None
        return _gallery_blob(row)


class GalleryEntryRepository:
    """Logical publications. Physically identical images stay SEPARATE rows here.

    The measured corpus is why: 28 logical entries carry 25 distinct checksums, so five of them are
    the same image published again. Each is a real event in someone's history, and folding them
    together to save disk would delete history — which is what a single table keyed by checksum
    would have forced.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, checksum: str, filename: str, owner_user_id: UUID | None = None,
               brand_id: UUID | None = None, domain_key: str | None = None,
               brand_name: str | None = None, content_type: str | None = None,
               style: str | None = None, metadata: Mapping[str, Any] | None = None,
               origin: str = "live", legacy_seq: int | None = None,
               imported_at: datetime | None = None,
               created_at: datetime | None = None) -> GalleryEntryRecord:
        """One logical entry. No commit — the caller owns the transaction.

        `created_at` is accepted so an IMPORT can carry the source's own timestamp. It is not
        defaulted to "now" here: a fabricated publication date is provenance invented, and
        provenance invented once is indistinguishable from provenance measured.
        """
        if str(origin) not in ("live", "legacy_import"):
            raise ValueError(f"{origin!r} is not a known gallery origin")
        row = GalleryEntry(
            owner_user_id=owner_user_id, brand_id=brand_id, checksum=str(checksum),
            filename=str(filename), domain_key=domain_key, brand_name=brand_name,
            content_type=content_type, style=style,
            metadata_json=dict(metadata or {}), origin=str(origin),
            legacy_seq=legacy_seq, imported_at=imported_at)
        if created_at is not None:
            row.created_at = created_at
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            if "uq_gallery_entries_legacy_seq" in str(exc.orig):
                raise ConcurrencyConflict(
                    f"legacy gallery entry {legacy_seq} is already imported") from None
            raise
        return _gallery_entry(row)

    def by_legacy_seq(self, legacy_seq: int) -> GalleryEntryRecord | None:
        row = self.session.scalar(select(GalleryEntry).where(
            GalleryEntry.origin == "legacy_import", GalleryEntry.legacy_seq == int(legacy_seq)))
        return _gallery_entry(row) if row is not None else None

    def visible_to(self, *, user_id: UUID | None, limit: int = 100
                   ) -> list[GalleryEntryRecord]:
        """This caller's history: their own entries, plus the ones with no recorded owner.

        The unowned entries are the six the forensic pass could not attribute. They are VISIBLE to
        everyone, exactly as the legacy index shows them, and they are never assigned to whoever
        asked — the request is a read, and a read must not create provenance. Ordered newest first,
        the order the SPA renders.
        """
        condition = GalleryEntry.owner_user_id.is_(None)
        if user_id is not None:
            condition = or_(condition, GalleryEntry.owner_user_id == user_id)
        rows = self.session.execute(
            select(GalleryEntry, GalleryBlob)
            .join(GalleryBlob, GalleryBlob.checksum == GalleryEntry.checksum)
            .where(condition)
            .order_by(GalleryEntry.created_at.desc(), GalleryEntry.id.desc())
            .limit(max(0, int(limit)))
        ).all()
        return [_gallery_entry(entry, blob) for entry, blob in rows]

    def count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(GalleryEntry)) or 0)


class CalendarRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_posts(self, *, calendar_run_id: UUID, brand_id: UUID,
                     posts: Sequence[Mapping[str, Any]]) -> list[CalendarPostRecord]:
        out: list[CalendarPostRecord] = []
        for ordinal, item in enumerate(posts):
            raw_date = item.get("scheduled_date") or item.get("date")
            scheduled = (date.fromisoformat(str(raw_date)) if raw_date else None)
            row = CalendarPost(
                calendar_run_id=calendar_run_id, brand_id=brand_id,
                calendar_run_kind="calendar", ordinal=ordinal, scheduled_date=scheduled,
                channel=item.get("channel") or item.get("platform"),
                content_type=item.get("content_type"), funnel_stage=item.get("funnel_stage"),
                topic=item.get("topic"), hook=item.get("hook"), cta=item.get("cta"),
                payload=dict(item), schema_version=int(item.get("schema_version") or 1),
            )
            self.session.add(row)
            self.session.flush()
            out.append(_calendar_post(row))
        return out

    def get_visible(self, *, post_id: UUID, user_id: UUID) -> CalendarPostRecord:
        row = self.session.scalar(
            select(CalendarPost).join(Brand, Brand.id == CalendarPost.brand_id)
            .where(CalendarPost.id == post_id, _visible_brand(user_id)))
        if row is None:
            raise NotVisible("calendar post is absent or not visible")
        return _calendar_post(row)


class AssetRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_verified(
        self, *, brand_id: UUID, storage_key: str, kind: str, mime: str,
        byte_count: int, checksum_sha256: str, durable: bool,
        created_by_user_id: UUID | None = None,
        width: int | None = None, height: int | None = None,
    ) -> AssetRecord:
        if not durable:
            raise AssetNotDurable("asset bytes must be durable before metadata is inserted")
        if byte_count < 0 or not _SHA256.fullmatch(str(checksum_sha256 or "")):
            raise ValueError("verified asset needs non-negative bytes and lowercase SHA-256")
        row = Asset(
            brand_id=brand_id, storage_key=storage_key, kind=kind, mime=mime,
            bytes=byte_count, checksum_sha256=checksum_sha256,
            width=width, height=height, created_by_user_id=created_by_user_id,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise_mapped_integrity(exc, {
                ("23505", "uq_assets_storage_key"):
                    (AlreadyExists, "asset storage key already exists"),
            })
        return _asset(row)

    def get_visible(self, *, asset_id: UUID, user_id: UUID) -> AssetRecord:
        row = self.session.scalar(
            select(Asset).join(Brand, Brand.id == Asset.brand_id)
            .where(Asset.id == asset_id, _visible_brand(user_id)))
        if row is None:
            raise NotVisible("asset is absent or not visible")
        return _asset(row)


class AttemptRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_for_locked_run(self, *, run_id: UUID, brand_id: UUID) -> AttemptRecord:
        ordinal = self.session.scalar(
            select(func.coalesce(func.max(Attempt.ordinal), -1) + 1)
            .where(Attempt.run_id == run_id))
        row = Attempt(
            run_id=run_id, brand_id=brand_id, ordinal=int(ordinal or 0),
            status="running", qa={},
        )
        self.session.add(row)
        self.session.flush()
        return _attempt(row)

    def lock(self, attempt_id: UUID) -> AttemptRecord:
        row = self.session.scalar(
            select(Attempt).where(Attempt.id == attempt_id).with_for_update())
        if row is None:
            raise NotFound("attempt not found")
        return _attempt(row)

    def finalize_success(self, *, attempt_id: UUID, asset_id: UUID,
                         qa: Mapping[str, Any], qa_passed: bool) -> AttemptRecord:
        changed = self.session.execute(
            update(Attempt).where(
                Attempt.id == attempt_id, Attempt.status == "running").values(
                status="succeeded", asset_id=asset_id, qa=dict(qa), qa_passed=qa_passed,
                error=None, finished_at=utcnow(),
            ).returning(Attempt.id)
        ).scalar_one_or_none()
        row = self.session.get(Attempt, attempt_id)
        if row is None:
            raise NotFound("attempt not found")
        if changed is None:
            raise TerminalState(f"attempt is already terminal as {row.status}")
        return _attempt(row)

    def finalize_failure(self, *, attempt_id: UUID, error: str,
                         qa: Mapping[str, Any] | None = None) -> AttemptRecord:
        changed = self.session.execute(
            update(Attempt).where(
                Attempt.id == attempt_id, Attempt.status == "running").values(
                status="failed", error=str(error), qa=dict(qa or {}),
                qa_passed=False, finished_at=utcnow(),
            ).returning(Attempt.id)
        ).scalar_one_or_none()
        row = self.session.get(Attempt, attempt_id)
        if row is None:
            raise NotFound("attempt not found")
        if changed is None:
            raise TerminalState(f"attempt is already terminal as {row.status}")
        return _attempt(row)

    def get_visible(self, *, attempt_id: UUID, user_id: UUID) -> AttemptRecord:
        row = self.session.scalar(
            select(Attempt).join(Brand, Brand.id == Attempt.brand_id)
            .where(Attempt.id == attempt_id, _visible_brand(user_id)))
        if row is None:
            raise NotVisible("attempt is absent or not visible")
        return _attempt(row)


class OwnerInputRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, brand_id: UUID) -> dict[str, Any]:
        row = self.session.get(BrandOwnerInputs, brand_id)
        return dict(row.payload) if row is not None else {}

    def set(self, *, brand_id: UUID, payload: Mapping[str, Any],
            schema_version: int = 1) -> dict[str, Any]:
        self.session.execute(
            pg_insert(BrandOwnerInputs).values(
                brand_id=brand_id, payload=dict(payload), schema_version=schema_version,
                updated_at=utcnow(),
            ).on_conflict_do_update(
                index_elements=[BrandOwnerInputs.brand_id],
                set_={"payload": dict(payload), "schema_version": schema_version,
                      "updated_at": utcnow()},
            )
        )
        return dict(payload)


class CompetitorRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, *, brand_id: UUID, key: str, payload: Mapping[str, Any]) -> UUID:
        competitor_id = self.session.execute(
            pg_insert(BrandCompetitor).values(
                brand_id=brand_id, key=key, payload=dict(payload),
            ).on_conflict_do_update(
                index_elements=[BrandCompetitor.brand_id, BrandCompetitor.key],
                set_={"payload": dict(payload)},
            ).returning(BrandCompetitor.id)
        ).scalar_one()
        return competitor_id

    def remove(self, *, brand_id: UUID, key: str) -> bool:
        return self.session.execute(delete(BrandCompetitor).where(
            BrandCompetitor.brand_id == brand_id,
            BrandCompetitor.key == key)).rowcount == 1

    def list(self, *, brand_id: UUID) -> list[dict[str, Any]]:
        rows = self.session.scalars(select(BrandCompetitor).where(
            BrandCompetitor.brand_id == brand_id).order_by(BrandCompetitor.key))
        return [{"id": str(row.id), "key": row.key, **dict(row.payload)} for row in rows]


class UsageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self, *, run_id: UUID, brand_id: UUID, provider: str, model: str,
        input_tokens: int = 0, output_tokens: int = 0, thoughts_tokens: int = 0,
        cost_usd: Decimal = Decimal("0"), duration_ms: int | None = None,
        stage: str | None = None, attempt_id: UUID | None = None,
    ) -> UsageRecordValue:
        row = UsageRecord(
            run_id=run_id, brand_id=brand_id, attempt_id=attempt_id,
            provider=provider, model=model, input_tokens=input_tokens,
            output_tokens=output_tokens, thoughts_tokens=thoughts_tokens,
            cost_usd=cost_usd, duration_ms=duration_ms, stage=stage,
        )
        self.session.add(row)
        self.session.flush()
        return UsageRecordValue(row.id, row.run_id, row.brand_id,
                                row.provider, row.model, Decimal(row.cost_usd))


__all__ = [name for name in globals() if name.endswith("Repository")]
