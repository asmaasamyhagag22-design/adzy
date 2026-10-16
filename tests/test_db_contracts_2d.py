# -*- coding: utf-8 -*-
"""DB Phase 2D: business-semantic contracts for legacy files and PostgreSQL.

The adapters in this module are deliberately test-only.  They drive the real
``UserStore``/``BrandStore``/``JobStore`` and the real 2C ``PersistenceService``;
they do not introduce an application backend interface or route cutover.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import astuple, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_URL = os.environ.get("BASEERA_TEST_DATABASE_URL", "")
_USER_NAMESPACE = uuid.UUID("5481c32e-b091-5a93-bc15-7fbf81d9385d")
pytestmark = pytest.mark.skipif(
    not _URL,
    reason="set BASEERA_TEST_DATABASE_URL to a disposable PostgreSQL database",
)


# ============================================================================
# Classification was completed before scenarios were written.  This catalog is
# executable so a 2C surface cannot silently fall out of the final matrix.
# ============================================================================

PARITY = "PARITY REQUIRED"
DELTA = "INTENTIONAL SEMANTIC CHANGE"
EXTENSION = "POSTGRESQL-ONLY DURABILITY EXTENSION"
DEFERRED = "DEFERRED MIGRATION CONCERN"


@dataclass(frozen=True, slots=True)
class ContractRow:
    contract_id: str
    business_scenario: str
    legacy_authority: str
    postgres_authority: str
    parity_class: str
    observable_output: str
    observable_durable_state: str
    authorization_semantic: str
    error_semantic: str
    concurrency_semantic: str
    justification: str


def _c(contract_id: str, scenario: str, legacy: str, postgres: str, cls: str,
       output: str, state: str, auth: str, error: str, concurrency: str,
       why: str) -> ContractRow:
    return ContractRow(contract_id, scenario, legacy, postgres, cls, output, state,
                       auth, error, concurrency, why)


CONTRACT_CATALOG = (
    _c("AUTH-01", "canonical email, duplicate, hash and logical-id bridge",
       "api.user_auth.UserStore", "PersistenceService user methods", PARITY,
       "same canonical email and logical account", "hash bytes survive without rehash",
       "lookup resolves the same logical user", "duplicate canonical email is refused",
       "unique email/legacy identity", "authentication material must remain compatible"),
    _c("BRAND-01", "owner_of absent/unclaimed/owned",
       "BrandStore.owner_of", "PersistenceService.owner_of", PARITY,
       "three ownership states", "one canonical domain record", "owner is logical identity",
       "absence is not ownership", "read is advisory, final write decides",
       "scrape preflight needs the same meaning"),
    _c("BRAND-02", "visible get/list and foreign isolation",
       "BrandStore.get/list", "get_visible_brand/list_visible_brands", PARITY,
       "self and unclaimed visible; foreign indistinguishable from absent", "no mutation",
       "actor-scoped", "not-found-or-not-visible", "read committed snapshot",
       "backend switch must not enumerate foreign brands"),
    _c("BRAND-03", "newest-first Brand ordering when timestamps differ",
       "BrandStore.list", "BrandRepository.list_visible", PARITY,
       "newest first", "no mutation", "visible rows only", "none", "not applicable",
       "user-visible non-tie ordering is established behavior"),
    _c("SCRAPE-01", "missing Brand plus successful scrape",
       "BrandStore.upsert", "PersistenceService.commit_scrape_success", PARITY,
       "success, visible current profile", "Brand created and owned by actor",
       "authenticated actor becomes manager", "none", "unique domain chooses one owner",
       "shared product result despite different storage shape"),
    _c("SCRAPE-02", "missing Brand plus failed scrape",
       "JobStore.finish_failure without BrandStore write", "finish_job_failure", PARITY,
       "failed terminal job", "no Brand reservation", "job principal only",
       "safe failure", "failure cannot squat domain", "C2 recovery contract"),
    _c("SCRAPE-03", "existing unclaimed plus successful scrape",
       "BrandStore.upsert", "commit_scrape_success", PARITY,
       "success/current profile", "actor claims Brand with result", "winner becomes manager",
       "loser is foreign after race", "claim and result are atomic at each authority",
       "unclaimed is a real reachable state"),
    _c("SCRAPE-04", "self-owned re-scrape",
       "BrandStore.upsert", "commit_scrape_success", PARITY,
       "new current profile", "same owner", "self only", "none",
       "serialized per canonical Brand", "normal refresh must continue"),
    _c("SCRAPE-05", "foreign-owned successful persistence attempt",
       "BrandStore.upsert", "commit_scrape_success", PARITY,
       "refusal", "winner state unchanged", "foreign actor cannot write", "FOREIGN_OWNER",
       "final authority is inside write", "preflight is never authorization"),
    _c("RACE-01", "two users claim one unclaimed Brand",
       "BrandStore storage lock", "Brand row lock", PARITY,
       "one winner and one claim-lost/foreign", "one owner and winner profile",
       "loser cannot overwrite", "CLAIM_LOST/FOREIGN_OWNER", "genuine concurrent execution",
       "ownership winner must be singular"),
    _c("RACE-02", "two users persist first-ever Brand",
       "BrandStore storage lock", "unique domain plus Brand lock", PARITY,
       "one winner", "one canonical Brand and one owner", "loser cannot overwrite",
       "CLAIM_LOST/FOREIGN_OWNER", "genuine concurrent execution",
       "missing-row race must preserve business ownership"),
    _c("PROFILE-01", "current Business Profile after re-scrape",
       "embedded Brand _profile", "ProfileSnapshot plus current pointer", PARITY,
       "latest valid logical payload", "current points at second result", "visible actor only",
       "NOT_FOUND_OR_NOT_VISIBLE", "serialized update", "current output is shared contract"),
    _c("OWNER-01", "owner inputs set/read and isolation",
       "BrandStore.replace_block/get", "PersistenceService owner-input methods", PARITY,
       "same payload", "latest owner payload", "owner writes; foreign refused",
       "FOREIGN_OWNER/NOT_VISIBLE", "Brand lock orders writes",
       "the current product exposes both set and read"),
    _c("COMP-01", "owner-curated competitor add/list/remove membership",
       "competitor.registry", "PersistenceService competitor methods", PARITY,
       "same logical member set", "add/upsert/remove", "owner writes; foreign refused",
       "FOREIGN_OWNER/NOT_VISIBLE", "unique Brand/key", "legacy has a stable registry equivalent"),
    _c("CAL-01", "calendar order and duplicate dates",
       "embedded content_calendar list", "create_calendar/CalendarPost", PARITY,
       "same count, order and content", "all same-date posts retained", "brand visibility",
       "NOT_VISIBLE", "ordinal allocation under Brand lock", "date is data, never identity"),
    _c("JOB-01", "user-scoped Job lookup",
       "JobStore plus request_owns_job", "job_for_user", PARITY,
       "owner sees; foreign does not", "job unchanged", "principal-scoped",
       "NOT_FOUND_OR_NOT_VISIBLE", "not applicable", "shared anti-IDOR behavior"),
    _c("JOB-02", "queued/running/terminal truth",
       "JobStore terminal methods", "claim/finish methods", PARITY,
       "same logical terminal outcome/error", "first terminal truth survives",
       "job principal plus worker on PostgreSQL", "TERMINAL", "conditional terminal write",
       "worker lease is stronger but terminal meaning is shared"),
    _c("JOB-03", "capability Job access",
       "issue_job_capability/request_owns_job", "capability_digest/job_for_capability", PARITY,
       "right capability sees; wrong does not", "raw token is never stored",
       "opaque capability", "NOT_FOUND_OR_NOT_VISIBLE", "constant-time/digest equality",
       "current unauthenticated run route already promises capability access"),
    _c("GEO-01", "country/language provenance persistence",
       "embedded profile", "ProfileSnapshot payload and provenance columns", PARITY,
       "country/language/source/raw unchanged", "exact independent fields", "brand visibility",
       "none", "not applicable", "persistence must not introduce country-language coupling"),
    _c("NULL-01", "missing/null/empty string/object/list",
       "JSON document/block", "JSONB profile/owner-input payload", PARITY,
       "meaningful shapes preserved", "no blind coercion", "owner-scoped",
       "none", "not applicable", "migration bugs commonly collapse these states"),
    _c("DELETE-01", "deleted/archived Brand disappears from ordinary get/list",
       "BrandStore.delete", "archive_brand", PARITY,
       "not visible", "removed from live view", "self-owner only",
       "idempotent absence/foreign; unclaimed refused", "serialized destructive operation",
       "shared user-visible behavior is narrower than storage parity"),

    _c("DELTA-01", "physical file delete versus soft archive",
       "unlink Brand JSON", "Brand.deleted_at", DELTA,
       "both disappear from live reads", "PostgreSQL retains identity/history", "owner only",
       "same outward non-enumeration", "Brand lock", "durable references forbid hard deletion"),
    _c("DELTA-02", "same-owner re-scrape after deletion/archive",
       "new file is recreated", "same Brand row reactivated", DELTA,
       "same owner sees a live Brand", "new identity versus retained identity", "same owner",
       "none", "serialized", "history-preserving reactivation is deliberate"),
    _c("DELTA-03", "foreign re-scrape after prior owner's delete/archive",
       "deleted domain can be newly claimed", "archived owner rejects foreign actor", DELTA,
       "legacy may transfer; PostgreSQL refuses", "archived ownership retained", "foreign refused",
       "FOREIGN_OWNER", "Brand lock", "stronger PostgreSQL ownership safety is intentional"),
    _c("DELTA-04", "archived-unclaimed Brand",
       "no representable archive state", "ArchivedBrand", DELTA,
       "explicit refusal", "archive remains unclaimed", "no guessed transfer", "ARCHIVED",
       "Brand lock", "CUTOVER PRODUCT DECISION REQUIRED; do not redesign in 2D"),
    _c("DELTA-05", "mutable current output versus immutable history",
       "embedded profile/run-like blocks overwrite", "immutable ProfileSnapshot/Run rows", DELTA,
       "same current output", "old PostgreSQL truth retained", "brand-scoped", "none",
       "version/kind sequence", "intentional auditability improvement"),
    _c("DELTA-06", "SWOT regeneration invalidates prior approval",
       "mutable swot_approved bool currently leaks", "Approval bound to current Run", DELTA,
       "PostgreSQL closes gate for new SWOT", "old approval remains on old Run", "actor-scoped",
       "NOT_APPROVED until B approved", "Brand lock serializes run/approval",
       "intentional correction of a proven legacy gate bug"),
    _c("DELTA-07", "CalendarPost addressable identity",
       "embedded list/date lookup", "post UUID plus run ordinal", DELTA,
       "list output remains equal", "individual same-date posts remain addressable", "brand-scoped",
       "NOT_VISIBLE", "ordinal unique per run", "date matching cannot identify duplicates"),
    _c("DELTA-08", "creative selection versus human approval",
       "mutable selected_id-like state", "Run.selected_attempt_id distinct from Approval", DELTA,
       "selection does not imply approval", "separate durable facts", "owner/actor-scoped",
       "INVALID_TRANSITION for invalid selection", "Run/Attempt lock order",
       "two different business decisions must not collapse"),
    _c("DELTA-09", "tie ordering and normalized registry ordering",
       "filesystem/insertion order on ties", "explicit UUID/key tie-break", DELTA,
       "non-ties match; ties become deterministic", "no data loss", "visible set unchanged",
       "none", "not applicable", "accidental file/dict order is not a contract"),

    _c("EXT-01", "Jobs survive process restart", "process-local JobStore", "jobs table", EXTENSION,
       "durable lookup", "execution envelope retained", "principal-scoped", "NOT_VISIBLE",
       "transactional", "legacy fundamentally cannot survive restart"),
    _c("EXT-02", "worker claim, lease, renewal and stale-worker refusal", "none",
       "claim_job/renew_job/set_job_stage", EXTENSION, "lease owner controls mutation",
       "claimed_by/expiry", "worker lease", "CLAIM_LOST/TERMINAL", "conditional update",
       "stronger durable execution truth"),
    _c("EXT-03", "crash Class A/B detection", "TTL eviction only",
       "detect_crash_class_a/b", EXTENSION, "reconcilable stuck sets", "run/job states retained",
       "internal operator", "none", "predicate query", "needs durable independent Run and Job"),
    _c("EXT-04", "Profile version history and allocator", "current embedded value only",
       "ProfileSnapshot versions", EXTENSION, "current plus history", "immutable versions",
       "brand-scoped", "CONCURRENCY_CONFLICT via constraints", "Brand-locked allocator",
       "legacy has no immutable profile history"),
    _c("EXT-05", "immutable Runs and per-kind current sequence", "mutable analysis blocks",
       "Run create/current/finalize", EXTENSION, "current successful run", "old runs immutable",
       "brand-scoped", "TERMINAL/AMBIGUOUS_LEGACY_STATE", "Brand then Run lock",
       "durable operation identity is new"),
    _c("EXT-06", "multi-parent Run dependencies", "none", "RunDependency", EXTENSION,
       "queryable lineage", "immutable dependency edges", "same-brand FK", "constraint refusal",
       "transactional insert", "legacy has no relational lineage"),
    _c("EXT-07", "usage/cost attribution", "logs/embedded transient data",
       "record_usage", EXTENSION, "usage tied to real run", "immutable usage rows",
       "real run/brand context", "constraint refusal", "same transaction boundary",
       "normalized durable attribution is new"),
    _c("EXT-08", "creative Attempt lineage and finalization", "mutable candidate maps",
       "Attempt service methods", EXTENSION, "attempt terminal result", "ordinal/history retained",
       "visible run owner", "TERMINAL/INVALID_TRANSITION", "Run then Attempt lock",
       "legacy cannot provide durable attempt history"),
    _c("EXT-09", "verified Asset metadata", "gallery/generated files", "AssetRepository", EXTENSION,
       "addressable asset", "metadata only after durable bytes", "brand-scoped",
       "ASSET_NOT_DURABLE/ALREADY_EXISTS", "atomic metadata finalization",
       "transactional metadata is new"),
    _c("EXT-10", "Run/Asset/Attempt UUID anti-IDOR", "no equivalent stable UUIDs",
       "run_for_user/asset_for_user/attempt_for_user", EXTENSION, "foreign UUID hidden",
       "no mutation", "visibility inherited from Brand", "NOT_VISIBLE", "not applicable",
       "new identifiers need explicit security proof"),
    _c("EXT-11", "atomic scrape/creative artifact set and rollback", "file-level writes only",
       "PersistenceService UoW", EXTENSION, "all-or-nothing durable truth",
       "Brand/Profile/Run/Job or Run/Attempt/Asset/Job commit together", "scoped",
       "original failure with rollback", "single database transaction",
       "legacy cannot promise relational atomicity"),

    _c("2E-01", "28 inline gallery/history byte extractions", "history.json", "future loader",
       DEFERRED, "none in 2D", "quarantine unchanged", "migration-only", "defer", "not applicable",
       "requires migration tooling"),
    _c("2E-02", "conflicting-owner Brand .bak", "legacy backup", "future quarantine policy",
       DEFERRED, "none in 2D", "quarantine unchanged", "human decision", "defer", "not applicable",
       "ownership conflict must not contaminate semantic tests"),
    _c("2E-03", "orphan generated files", "generated/", "future asset loader/sweeper", DEFERRED,
       "none in 2D", "files untouched", "migration-only", "defer", "not applicable",
       "asset census belongs to migration"),
    _c("2E-04", "real legacy bulk migration", "authoritative stores", "future 2E command", DEFERRED,
       "none in 2D", "authoritative data untouched", "operator-only", "defer", "not applicable",
       "explicit phase boundary"),
    _c("2E-05", "backup manifest and cutover snapshot", "authoritative stores", "future manifest",
       DEFERRED, "none in 2D", "no snapshot created", "operator-only", "defer", "not applicable",
       "requires migration plan"),
    _c("2E-06", "production-copy migration", "database_export/legacy production copy",
       "future disposable rehearsal", DEFERRED, "none in 2D", "database_export untouched",
       "operator-only", "defer", "not applicable", "explicitly prohibited in 2D"),
)


def test_contract_catalog_is_total_exclusive_and_covers_2c_surface():
    allowed = {PARITY, DELTA, EXTENSION, DEFERRED}
    ids = [row.contract_id for row in CONTRACT_CATALOG]
    assert len(ids) == len(set(ids))
    assert {row.parity_class for row in CONTRACT_CATALOG} == allowed
    assert all(all(str(value).strip() for value in astuple(row))
               for row in CONTRACT_CATALOG)
    required = {
        "AUTH-01", "BRAND-01", "BRAND-02", "SCRAPE-01", "SCRAPE-02",
        "RACE-01", "RACE-02", "PROFILE-01", "OWNER-01", "COMP-01",
        "CAL-01", "JOB-01", "JOB-02", "JOB-03", "GEO-01", "NULL-01",
        "DELETE-01", "DELTA-06", "EXT-04", "EXT-05", "EXT-06", "EXT-07",
        "EXT-08", "EXT-09", "EXT-10", "EXT-11",
    }
    assert required <= set(ids)


# ============================================================================
# Real-backend fixtures and test-only normalization
# ============================================================================


@pytest.fixture(scope="module")
def engine():
    from alembic import command
    from alembic.config import Config

    from db.config import DatabaseSettings
    from db.models import Base
    from db.session import create_engine_from
    from db.test_database_safety import require_disposable_test_database

    require_disposable_test_database(_URL)
    db_engine = create_engine_from(DatabaseSettings(url=_URL, pool_size=8, max_overflow=4))
    Base.metadata.drop_all(db_engine, checkfirst=True)
    with db_engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    previous = os.environ.get("BASEERA_DATABASE_URL")
    os.environ["BASEERA_DATABASE_URL"] = _URL
    try:
        command.upgrade(Config(str(_ROOT / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("BASEERA_DATABASE_URL", None)
        else:
            os.environ["BASEERA_DATABASE_URL"] = previous
    yield db_engine
    Base.metadata.drop_all(db_engine, checkfirst=True)
    with db_engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine):
    from db.models import Base

    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.exec_driver_sql(f'TRUNCATE TABLE "{table.name}" CASCADE')
    yield
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.exec_driver_sql(f'TRUNCATE TABLE "{table.name}" CASCADE')


@pytest.fixture()
def service(engine):
    from db.services import PersistenceService
    from db.session import session_factory_from
    from db.uow import unit_of_work_factory

    return PersistenceService(unit_of_work_factory(session_factory_from(engine)))


@dataclass(frozen=True, slots=True)
class Actor:
    logical_id: str
    pg_id: uuid.UUID
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BrandObservation:
    domain_key: str
    ownership_state: str
    owner_logical_id: str | None
    visible: bool
    current_profile: dict[str, Any] | None
    lifecycle: str


@dataclass(frozen=True, slots=True)
class JobObservation:
    visible: bool
    status: str | None
    terminal: bool
    result_semantic: str | None
    error_semantic: str | None


@dataclass(frozen=True, slots=True)
class ApprovalObservation:
    current_swot_approved: bool
    approved_subject_is_current: bool


@dataclass(frozen=True, slots=True)
class CalendarObservation:
    count: int
    ordered_content: tuple[str, ...]
    same_date_multiplicity: int


class LegacyAdapter:
    """Thin calls into the current file stores, all rooted in pytest's temp dir."""

    def __init__(self, root: Path) -> None:
        from api.brand_store import BrandStore
        from api.jobs.store import JobStore
        from api.user_auth import UserStore

        self.root = root
        self.brands = BrandStore(root / "brands")
        self.users = UserStore(root / "users.json")
        self.jobs = JobStore()
        self.competitors = root / "competitors"

    def seed_brand(self, domain: str, owner: Actor | None,
                   profile: dict[str, Any] | None = None) -> None:
        self.brands.save({
            "domain_key": domain,
            "owner_id": owner.logical_id if owner else "",
            "display_name": domain,
            "_profile": profile,
        })

    def scrape_success(self, actor: Actor, domain: str,
                       profile: dict[str, Any]) -> str:
        result = self.brands.upsert({
            "domain_key": domain,
            "display_name": domain,
            "source_url": f"https://{domain.replace('_', '.')}",
            "_profile": profile,
        }, owner_id=actor.logical_id)
        return "success" if result is not None else "foreign_owner"

    def observe_brand(self, actor: Actor, domain: str) -> BrandObservation:
        owner = self.brands.owner_of(domain)
        visible = self.brands.get(domain, for_user=actor.logical_id)
        state = "absent" if owner is None else ("unclaimed" if owner == "" else "owned")
        return BrandObservation(
            domain, state, owner or None, visible is not None,
            (visible or {}).get("_profile"), "absent" if owner is None else "live")

    def list_domains(self, actor: Actor) -> tuple[str, ...]:
        return tuple(str(row["domain_key"]) for row in self.brands.list(
            for_user=actor.logical_id))


class PostgresAdapter:
    """Thin calls into the real 2C service; direct ORM use is fixture seeding/observation only."""

    def __init__(self, service, engine, logical_by_uuid: dict[uuid.UUID, str]) -> None:
        self.service = service
        self.engine = engine
        self.logical_by_uuid = logical_by_uuid

    def seed_brand(self, domain: str, owner: Actor | None) -> uuid.UUID:
        from sqlalchemy.orm import Session
        from db.models import Brand

        with Session(self.engine) as session:
            row = Brand(domain_key=domain, owner_id=owner.pg_id if owner else None,
                        display_name=domain, source_url=f"https://{domain.replace('_', '.')}")
            session.add(row)
            session.commit()
            return row.id

    def _job(self, actor: Actor, domain: str, worker: str):
        job = self.service.create_user_job(
            owner_user_id=actor.pg_id, requested_domain_key=domain)
        return self.service.claim_job(job_id=job.id, worker_id=worker, lease_seconds=600)

    def scrape_success(self, actor: Actor, domain: str, profile: dict[str, Any],
                       worker: str | None = None):
        from db.services import ProfileData, ScrapeSuccess

        worker = worker or f"worker-{uuid.uuid4().hex[:10]}"
        job = self._job(actor, domain, worker)
        pdata = ProfileData(
            payload=profile,
            country_canonical=profile.get("country"),
            country_source=profile.get("country_source"),
            country_raw=profile.get("country_raw"),
            primary_language=profile.get("primary_language"),
            language_source=profile.get("language_source"),
        )
        return self.service.commit_scrape_success(ScrapeSuccess(
            domain_key=domain, user_id=actor.pg_id, job_id=job.id, worker_id=worker,
            display_name=domain, source_url=f"https://{domain.replace('_', '.')}",
            profile=pdata,
        ))

    def scrape_outcome(self, actor: Actor, domain: str, profile: dict[str, Any]) -> str:
        from db.errors import ArchivedBrand, ForeignOwner

        try:
            self.scrape_success(actor, domain, profile)
            return "success"
        except ForeignOwner:
            return "foreign_owner"
        except ArchivedBrand:
            return "archived"

    def observe_brand(self, actor: Actor, domain: str) -> BrandObservation:
        from db.errors import NotVisible

        ownership = self.service.owner_of(domain)
        if not ownership.exists:
            state, owner, lifecycle = "absent", None, "absent"
        else:
            state = "unclaimed" if ownership.owner_id is None else "owned"
            owner = self.logical_by_uuid.get(ownership.owner_id) if ownership.owner_id else None
            lifecycle = "archived" if ownership.archived else "live"
        try:
            visible = self.service.get_visible_brand(user_id=actor.pg_id, domain_key=domain)
            profile = self.service.current_profile(
                brand_id=visible.id, user_id=actor.pg_id)
        except NotVisible:
            visible, profile = None, None
        return BrandObservation(domain, state, owner, visible is not None,
                                profile.profile if profile else None, lifecycle)

    def list_domains(self, actor: Actor) -> tuple[str, ...]:
        return tuple(row.domain_key for row in self.service.list_visible_brands(
            user_id=actor.pg_id))


@dataclass(slots=True)
class Harness:
    legacy: LegacyAdapter
    pg: PostgresAdapter
    engine: Any
    service: Any
    logical_by_uuid: dict[uuid.UUID, str]

    def actor(self, email: str) -> Actor:
        """Create real legacy auth material, then seed its migration-like PostgreSQL twin."""
        from sqlalchemy.orm import Session
        from db.models import User

        password = "temporary-contract-password"
        public = self.legacy.users.create(email, password, full_name="Contract User")
        assert public is not None
        canonical = email.strip().lower()
        raw = json.loads((self.legacy.root / "users.json").read_text(encoding="utf-8"))
        legacy_id = str(public["id"])
        pg_id = uuid.uuid5(_USER_NAMESPACE, legacy_id)
        with Session(self.engine) as session:
            session.add(User(
                id=pg_id, email=canonical, hashed_password=raw[canonical]["password_hash"],
                full_name="Contract User", legacy_user_id=legacy_id,
            ))
            session.commit()
        self.logical_by_uuid[pg_id] = legacy_id
        return Actor(legacy_id, pg_id, canonical, password)


@pytest.fixture()
def harness(tmp_path, monkeypatch, engine, service) -> Iterator[Harness]:
    import api.user_auth as user_auth

    root = tmp_path / "phase2d-legacy"
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(root))
    previous_secret = user_auth._SECRET_CACHE
    user_auth._SECRET_CACHE = None
    logical_by_uuid: dict[uuid.UUID, str] = {}
    legacy = LegacyAdapter(root)
    yield Harness(legacy, PostgresAdapter(service, engine, logical_by_uuid),
                  engine, service, logical_by_uuid)
    user_auth._SECRET_CACHE = previous_secret


def _business_brand(obs: BrandObservation) -> tuple[Any, ...]:
    return (obs.domain_key, obs.ownership_state, obs.owner_logical_id,
            obs.visible, obs.current_profile)


def _pg_row_counts(engine, domain: str) -> tuple[int, int, int]:
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        brand_id = session.execute(
            text("SELECT id FROM brands WHERE domain_key=:d"), {"d": domain}).scalar_one_or_none()
        if brand_id is None:
            return 0, 0, 0
        profiles = session.execute(text(
            "SELECT count(*) FROM profile_snapshots WHERE brand_id=:b"), {"b": brand_id}).scalar_one()
        runs = session.execute(text(
            "SELECT count(*) FROM runs WHERE brand_id=:b"), {"b": brand_id}).scalar_one()
        return 1, int(profiles), int(runs)


# ============================================================================
# Shared business-semantic proofs
# ============================================================================


def test_auth_canonical_duplicate_hash_and_deterministic_identity_bridge(harness):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from db.errors import AlreadyExists
    from db.models import User

    actor = harness.actor("  Mixed.Case@Contract.Test ")
    assert actor.email == "mixed.case@contract.test"
    assert harness.legacy.users.get_by_email(" MIXED.CASE@CONTRACT.TEST ")["id"] == actor.logical_id
    assert harness.legacy.users.get_by_id(actor.logical_id)["email"] == actor.email
    assert harness.legacy.users.authenticate(actor.email.upper(), actor.password)["id"] == actor.logical_id
    assert harness.service.user_by_email(" MIXED.CASE@CONTRACT.TEST ").id == actor.pg_id
    assert harness.service.user_by_legacy_id(actor.logical_id).id == actor.pg_id
    assert harness.service.user_by_id(actor.pg_id).legacy_user_id == actor.logical_id
    assert actor.pg_id == uuid.uuid5(_USER_NAMESPACE, actor.logical_id)

    raw = json.loads((harness.legacy.root / "users.json").read_text(encoding="utf-8"))
    legacy_hash = raw[actor.email]["password_hash"]
    with Session(harness.engine) as session:
        postgres_hash = session.scalar(select(User.hashed_password).where(User.id == actor.pg_id))
    assert hmac.compare_digest(str(postgres_hash), str(legacy_hash)), (
        "password hash changed during migration-like persistence")

    assert harness.legacy.users.create(actor.email.upper(), "different") is None
    with pytest.raises(AlreadyExists):
        harness.service.create_user(
            email=f" {actor.email.upper()} ", hashed_password="must-not-replace-existing")


def test_owner_of_visibility_and_foreign_non_enumeration(harness):
    owner = harness.actor("owner@contract.test")
    other = harness.actor("other@contract.test")
    cases = (("missing_contract_test", None), ("unclaimed_contract_test", None),
             ("self_contract_test", owner), ("foreign_contract_test", other))

    for domain, actor in cases[1:]:
        harness.legacy.seed_brand(domain, actor)
        harness.pg.seed_brand(domain, actor)

    for domain, _seed_owner in cases:
        legacy = harness.legacy.observe_brand(owner, domain)
        postgres = harness.pg.observe_brand(owner, domain)
        assert _business_brand(legacy) == _business_brand(postgres), domain

    assert set(harness.legacy.list_domains(owner)) == set(harness.pg.list_domains(owner))
    assert "foreign_contract_test" not in harness.legacy.list_domains(owner)
    assert "foreign_contract_test" not in harness.pg.list_domains(owner)


def test_brand_list_newest_first_for_distinct_timestamps(harness, monkeypatch):
    import api.brand_store as brand_store

    actor = harness.actor("ordering@contract.test")
    # ``dict.setdefault(key, time.time())`` evaluates its default even when created_at exists.
    ticks = iter((99.0, 100.0, 199.0, 200.0))
    monkeypatch.setattr(brand_store.time, "time", lambda: next(ticks))
    for domain in ("older_contract_test", "newer_contract_test"):
        harness.legacy.brands.save({
            "domain_key": domain, "owner_id": actor.logical_id, "created_at": 1,
        })
    harness.pg.scrape_success(actor, "older_contract_test", {"version": "old"})
    harness.pg.scrape_success(actor, "newer_contract_test", {"version": "new"})
    assert harness.legacy.list_domains(actor)[:2] == (
        "newer_contract_test", "older_contract_test")
    assert harness.pg.list_domains(actor)[:2] == (
        "newer_contract_test", "older_contract_test")


@pytest.mark.parametrize("initial", ["missing", "unclaimed", "self", "foreign"])
def test_scrape_success_contract_across_initial_ownership_states(harness, initial):
    actor = harness.actor(f"actor-{initial}@contract.test")
    other = harness.actor(f"other-{initial}@contract.test")
    domain = f"scrape_{initial}_contract_test"
    if initial != "missing":
        initial_owner = None if initial == "unclaimed" else (actor if initial == "self" else other)
        harness.legacy.seed_brand(domain, initial_owner, {"version": 0})
        harness.pg.seed_brand(domain, initial_owner)

    profile = {"version": 1, "actor": actor.logical_id,
               "optional": None, "empty": "", "items": [], "metadata": {}}
    legacy_outcome = harness.legacy.scrape_success(actor, domain, profile)
    postgres_outcome = harness.pg.scrape_outcome(actor, domain, profile)
    assert legacy_outcome == postgres_outcome
    legacy = harness.legacy.observe_brand(actor, domain)
    postgres = harness.pg.observe_brand(actor, domain)
    assert _business_brand(legacy) == _business_brand(postgres)
    if initial == "foreign":
        assert legacy.current_profile is None and postgres.current_profile is None
        assert harness.legacy.observe_brand(other, domain).current_profile == {"version": 0}
        assert _pg_row_counts(harness.engine, domain) == (1, 0, 0)
    else:
        assert legacy.current_profile == profile == postgres.current_profile


def test_failed_first_scrape_has_shared_terminal_failure_and_no_brand(harness):
    from api.job_access import user_job_principal
    from api.schemas import JobStatus, Stage, StageEvent, StageStatus

    actor = harness.actor("failed-first@contract.test")
    domain = "failed_first_contract_test"
    legacy_job = harness.legacy.jobs.create(
        owner_principal=user_job_principal(actor.logical_id))
    harness.legacy.jobs.append_event(legacy_job.job_id, StageEvent(
        job_id=legacy_job.job_id, stage=Stage.SCRAPE, status=StageStatus.STARTED,
        timestamp=datetime.now(timezone.utc)))
    assert harness.legacy.jobs.finish_failure(legacy_job.job_id, "scrape failed")

    pg_job = harness.service.create_user_job(
        owner_user_id=actor.pg_id, requested_domain_key=domain)
    harness.service.claim_job(job_id=pg_job.id, worker_id="failed-worker", lease_seconds=600)
    failed, changed = harness.service.finish_job_failure(
        job_id=pg_job.id, worker_id="failed-worker", error="scrape failed")
    legacy_obs = JobObservation(True, legacy_job.status.value, legacy_job.is_terminal(),
                                None, "failed")
    pg_obs = JobObservation(True, failed.status, failed.status in ("succeeded", "failed"),
                            None, "failed" if failed.error else None)
    assert changed and legacy_job.status == JobStatus.FAILED
    assert legacy_obs == pg_obs
    assert harness.legacy.brands.owner_of(domain) is None
    assert _pg_row_counts(harness.engine, domain) == (0, 0, 0)


def _race_legacy(adapter: LegacyAdapter, actors: tuple[Actor, Actor], domain: str
                 ) -> tuple[list[str], BrandObservation]:
    gate = threading.Barrier(2)

    def one(actor: Actor) -> str:
        gate.wait()
        return adapter.scrape_success(
            actor, domain, {"winner": actor.logical_id, "domain": domain})

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(one, actors))
    owner = adapter.brands.owner_of(domain)
    winner = actors[0] if owner == actors[0].logical_id else actors[1]
    return outcomes, adapter.observe_brand(winner, domain)


def _race_postgres(adapter: PostgresAdapter, actors: tuple[Actor, Actor], domain: str
                   ) -> tuple[list[str], BrandObservation]:
    from db.errors import ForeignOwner
    from db.services import ProfileData, ScrapeSuccess

    prepared = []
    for index, actor in enumerate(actors):
        worker = f"race-worker-{index}"
        prepared.append((actor, worker, adapter._job(actor, domain, worker)))
    gate = threading.Barrier(2)

    def one(item) -> str:
        actor, worker, job = item
        gate.wait()
        try:
            adapter.service.commit_scrape_success(ScrapeSuccess(
                domain_key=domain, user_id=actor.pg_id, job_id=job.id, worker_id=worker,
                profile=ProfileData(payload={
                    "winner": actor.logical_id, "domain": domain}),
            ))
            return "success"
        except ForeignOwner:
            return "foreign_owner"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(one, prepared))
    ownership = adapter.service.owner_of(domain)
    logical = adapter.logical_by_uuid[ownership.owner_id]
    winner = actors[0] if logical == actors[0].logical_id else actors[1]
    return outcomes, adapter.observe_brand(winner, domain)


@pytest.mark.parametrize("initial", ["unclaimed", "missing"])
def test_real_concurrent_claim_and_first_ever_races_have_one_winner(harness, initial):
    actors = (harness.actor(f"race-a-{initial}@contract.test"),
              harness.actor(f"race-b-{initial}@contract.test"))
    domain = f"race_{initial}_contract_test"
    if initial == "unclaimed":
        harness.legacy.seed_brand(domain, None, {"before": True})
        harness.pg.seed_brand(domain, None)

    legacy_outcomes, legacy = _race_legacy(harness.legacy, actors, domain)
    pg_outcomes, postgres = _race_postgres(harness.pg, actors, domain)
    assert sorted(legacy_outcomes) == sorted(pg_outcomes) == ["foreign_owner", "success"]
    # The two backend races are independent schedules: which actor wins may differ, but the
    # externally meaningful contract is one owned/visible canonical Brand whose profile names its
    # own winner. Comparing raw winner ids would turn scheduler order into a product contract.
    assert (legacy.domain_key, legacy.ownership_state, legacy.visible) == (
        postgres.domain_key, postgres.ownership_state, postgres.visible) == (
            domain, "owned", True)
    assert legacy.current_profile["winner"] == legacy.owner_logical_id
    assert postgres.current_profile["winner"] == postgres.owner_logical_id
    assert _pg_row_counts(harness.engine, domain) == (1, 1, 1)


def test_rescrape_returns_same_current_profile_while_postgres_keeps_history(harness):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from db.models import ProfileSnapshot

    actor = harness.actor("profiles@contract.test")
    domain = "profiles_contract_test"
    first = {"version": 1, "immutable": "old"}
    second = {"version": 2, "immutable": "new"}
    harness.legacy.scrape_success(actor, domain, first)
    first_pg = harness.pg.scrape_success(actor, domain, first)
    harness.legacy.scrape_success(actor, domain, second)
    second_pg = harness.pg.scrape_success(actor, domain, second)

    assert harness.legacy.observe_brand(actor, domain).current_profile == second
    assert harness.pg.observe_brand(actor, domain).current_profile == second
    with Session(harness.engine) as session:
        rows = list(session.scalars(select(ProfileSnapshot).where(
            ProfileSnapshot.brand_id == first_pg.brand.id).order_by(ProfileSnapshot.version)))
    assert [row.profile for row in rows] == [first, second]
    assert [row.version for row in rows] == [1, 2]
    assert first_pg.profile.id != second_pg.profile.id


@pytest.mark.parametrize("country,language,country_source,country_raw,language_source", [
    ("Egypt", "en", "location_country", "Cairo, Egypt", "html_lang"),
    ("Qatar", "en", "explicit_country", "Doha, Qatar", "measured_content"),
    ("Saudi Arabia", "ar", "service_area", "المملكة العربية السعودية", "html_lang"),
    (None, "fr", "unknown", None, "measured_content"),
])
def test_country_language_provenance_and_nulls_round_trip_independently(
    harness, country, language, country_source, country_raw, language_source,
):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from db.models import ProfileSnapshot

    suffix = hashlib.sha256(repr((country, language)).encode()).hexdigest()[:8]
    actor = harness.actor(f"geo-{suffix}@contract.test")
    domain = f"geo_{suffix}_contract_test"
    profile = {
        "country": country,
        "country_source": country_source,
        "country_raw": country_raw,
        "primary_language": language,
        "language_source": language_source,
        "empty_string": "",
        "empty_object": {},
        "empty_list": [],
        "explicit_null": None,
    }
    harness.legacy.scrape_success(actor, domain, profile)
    result = harness.pg.scrape_success(actor, domain, profile)
    assert harness.legacy.observe_brand(actor, domain).current_profile == profile
    assert harness.pg.observe_brand(actor, domain).current_profile == profile
    assert "missing_key" not in profile
    with Session(harness.engine) as session:
        row = session.scalar(select(ProfileSnapshot).where(
            ProfileSnapshot.id == result.profile.id))
        typed = (row.country_canonical, row.country_source, row.country_raw,
                 row.primary_language, row.language_source)
    assert typed == (country, country_source, country_raw, language, language_source)


def test_owner_inputs_set_read_payload_shapes_and_foreign_isolation(harness):
    from db.errors import ForeignOwner, NotVisible

    owner = harness.actor("inputs-owner@contract.test")
    other = harness.actor("inputs-other@contract.test")
    domain = "inputs_contract_test"
    harness.legacy.scrape_success(owner, domain, {"profile": True})
    committed = harness.pg.scrape_success(owner, domain, {"profile": True})

    assert (harness.legacy.brands.get(domain, for_user=owner.logical_id)
            .get("owner_inputs") or {}) == {}
    assert harness.service.owner_inputs(
        brand_id=committed.brand.id, user_id=owner.pg_id) == {}

    payload = {"monthly_budget": None, "label": "", "channels": [], "details": {}}
    assert harness.legacy.brands.replace_block(
        domain, "owner_inputs", payload, for_user=owner.logical_id) is not None
    assert harness.service.set_owner_inputs(
        brand_id=committed.brand.id, user_id=owner.pg_id, payload=payload) == payload
    legacy_value = harness.legacy.brands.get(
        domain, for_user=owner.logical_id)["owner_inputs"]
    postgres_value = harness.service.owner_inputs(
        brand_id=committed.brand.id, user_id=owner.pg_id)
    assert legacy_value == postgres_value == payload

    assert harness.legacy.brands.replace_block(
        domain, "owner_inputs", {"stolen": True}, for_user=other.logical_id) is None
    with pytest.raises(ForeignOwner):
        harness.service.set_owner_inputs(
            brand_id=committed.brand.id, user_id=other.pg_id, payload={"stolen": True})
    with pytest.raises(NotVisible):
        harness.service.owner_inputs(brand_id=committed.brand.id, user_id=other.pg_id)
    assert harness.service.owner_inputs(
        brand_id=committed.brand.id, user_id=owner.pg_id) == payload


def _competitor_semantics(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(str(row.get("name") or ""), str(row.get("source") or "")) for row in rows}


def test_competitor_membership_add_upsert_remove_and_foreign_isolation(harness):
    from competitor.registry import (
        add_user_competitor, competitor_key, list_competitors, remove_user_competitor,
    )
    from db.errors import ForeignOwner

    owner = harness.actor("competitor-owner@contract.test")
    other = harness.actor("competitor-other@contract.test")
    domain = "competitors_contract_test"
    harness.legacy.scrape_success(owner, domain, {"website": "https://brand.invalid"})
    committed = harness.pg.scrape_success(owner, domain, {"website": "https://brand.invalid"})
    brand_ref = "contract-brand-ref"
    entry = add_user_competitor(
        brand_ref, name="Rival One", store_dir=str(harness.legacy.competitors),
        now="2026-01-01T00:00:00+00:00")
    assert entry is not None
    key = competitor_key("Rival One", "")
    harness.service.add_competitor(
        brand_id=committed.brand.id, user_id=owner.pg_id, key=key,
        payload={"name": "Rival One", "website": "", "source": "user"})
    legacy_rows = list_competitors(brand_ref, store_dir=str(harness.legacy.competitors))
    pg_rows = harness.service.list_competitors(
        brand_id=committed.brand.id, user_id=owner.pg_id)
    assert _competitor_semantics(legacy_rows) == _competitor_semantics(pg_rows) == {
        ("Rival One", "user")}

    # Re-adding the same logical key is an upsert, never a duplicate.
    add_user_competitor(brand_ref, name="Rival One",
                        store_dir=str(harness.legacy.competitors), now="2026-01-02T00:00:00+00:00")
    harness.service.add_competitor(
        brand_id=committed.brand.id, user_id=owner.pg_id, key=key,
        payload={"name": "Rival One", "website": "", "source": "user"})
    assert len(list_competitors(brand_ref, store_dir=str(harness.legacy.competitors))) == 1
    assert len(harness.service.list_competitors(
        brand_id=committed.brand.id, user_id=owner.pg_id)) == 1
    with pytest.raises(ForeignOwner):
        harness.service.add_competitor(
            brand_id=committed.brand.id, user_id=other.pg_id, key="stolen",
            payload={"name": "Stolen"})
    assert remove_user_competitor(
        brand_ref, key, store_dir=str(harness.legacy.competitors))
    assert harness.service.remove_competitor(
        brand_id=committed.brand.id, user_id=owner.pg_id, key=key)
    assert list_competitors(brand_ref, store_dir=str(harness.legacy.competitors)) == []
    assert harness.service.list_competitors(
        brand_id=committed.brand.id, user_id=owner.pg_id) == []


def _request(**headers):
    from starlette.requests import Request

    encoded = [(name.replace("_", "-").encode("ascii"), value.encode("ascii"))
               for name, value in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": encoded})


def test_user_job_principal_visibility_is_shared(harness):
    from api.job_access import request_owns_job, user_job_principal
    from api.user_auth import issue_token
    from db.errors import NotVisible

    owner = harness.actor("job-owner@contract.test")
    other = harness.actor("job-other@contract.test")
    legacy_job = harness.legacy.jobs.create(
        owner_principal=user_job_principal(owner.logical_id))
    owner_token = issue_token(owner.logical_id)
    other_token = issue_token(other.logical_id)
    assert request_owns_job(
        _request(authorization=f"Bearer {owner_token}"), legacy_job.owner_principal)
    assert not request_owns_job(
        _request(authorization=f"Bearer {other_token}"), legacy_job.owner_principal)

    pg_job = harness.service.create_user_job(owner_user_id=owner.pg_id)
    assert harness.service.job_for_user(job_id=pg_job.id, user_id=owner.pg_id).id == pg_job.id
    with pytest.raises(NotVisible):
        harness.service.job_for_user(job_id=pg_job.id, user_id=other.pg_id)


def test_capability_job_right_token_accesses_and_wrong_token_does_not(harness):
    from api.job_access import issue_job_capability, request_owns_job
    from db.errors import NotVisible
    from db.security import capability_digest

    raw_token, principal = issue_job_capability()
    legacy_job = harness.legacy.jobs.create(owner_principal=principal)
    right_legacy = request_owns_job(_request(x_job_token=raw_token), legacy_job.owner_principal)
    wrong_legacy = request_owns_job(_request(x_job_token="wrong-capability-value"),
                                    legacy_job.owner_principal)
    pg_job = harness.service.create_capability_job(
        capability_digest=capability_digest(raw_token))
    right_pg = harness.service.job_for_capability(
        job_id=pg_job.id, capability_digest=capability_digest(raw_token)).id == pg_job.id
    with pytest.raises(NotVisible):
        harness.service.job_for_capability(
            job_id=pg_job.id, capability_digest=capability_digest("wrong-capability-value"))
    assert right_legacy and right_pg and not wrong_legacy
    assert raw_token not in repr(legacy_job)
    assert raw_token not in repr(pg_job)


def test_job_terminal_truth_is_shared_while_worker_lease_is_stronger(harness):
    from api.job_access import user_job_principal
    from api.schemas import Stage, StageEvent, StageStatus
    from db.errors import TerminalState

    actor = harness.actor("terminal@contract.test")
    legacy_job = harness.legacy.jobs.create(
        owner_principal=user_job_principal(actor.logical_id))
    harness.legacy.jobs.append_event(legacy_job.job_id, StageEvent(
        job_id=legacy_job.job_id, stage=Stage.SCRAPE, status=StageStatus.STARTED,
        timestamp=datetime.now(timezone.utc)))
    first_legacy = harness.legacy.jobs.finish_success(
        legacy_job.job_id, result={"logical_result": "ready"})
    replay_legacy = harness.legacy.jobs.finish_success(
        legacy_job.job_id, result={"logical_result": "ready"})
    late_legacy = harness.legacy.jobs.finish_failure(legacy_job.job_id, "late")

    pg_job = harness.service.create_user_job(owner_user_id=actor.pg_id)
    harness.service.claim_job(job_id=pg_job.id, worker_id="terminal-worker", lease_seconds=600)
    first_pg, first_changed = harness.service.finish_job_success(
        job_id=pg_job.id, worker_id="terminal-worker")
    replay_pg, replay_changed = harness.service.finish_job_success(
        job_id=pg_job.id, worker_id="terminal-worker")
    with pytest.raises(TerminalState):
        harness.service.finish_job_failure(
            job_id=pg_job.id, worker_id="terminal-worker", error="late")

    legacy_obs = JobObservation(True, legacy_job.status.value, legacy_job.is_terminal(),
                                "ready", None)
    postgres_obs = JobObservation(True, first_pg.status,
                                  first_pg.status in ("succeeded", "failed"), "ready", None)
    assert legacy_obs == postgres_obs
    assert first_legacy and first_changed
    assert not replay_legacy and not replay_changed and replay_pg.status == "succeeded"
    assert not late_legacy


def _legacy_swot_sequence(adapter: LegacyAdapter, actor: Actor, domain: str
                          ) -> tuple[ApprovalObservation, ...]:
    def observation() -> ApprovalObservation:
        brand = adapter.brands.get(domain, for_user=actor.logical_id) or {}
        approved = bool(brand.get("swot_approved"))
        return ApprovalObservation(approved, approved)

    adapter.seed_brand(domain, actor, {"profile": True})
    adapter.brands.update(domain, {"swot": {"id": "A"}, "has_swot": True},
                          for_user=actor.logical_id)
    before = observation()
    adapter.brands.update(domain, {"swot_approved": True}, for_user=actor.logical_id)
    approved_a = observation()
    # This is the exact persistence performed for generate_swot's returned patch.
    adapter.brands.update(domain, {"swot": {"id": "B"}, "has_swot": True},
                          for_user=actor.logical_id)
    after_b = observation()
    adapter.brands.update(domain, {"swot_approved": True}, for_user=actor.logical_id)
    approved_b = observation()
    return before, approved_a, after_b, approved_b


def _postgres_swot_sequence(adapter: PostgresAdapter, actor: Actor, domain: str
                            ) -> tuple[ApprovalObservation, ...]:
    from db.services import LiveRunData

    committed = adapter.scrape_success(actor, domain, {"profile": True})

    def observation(current_id: uuid.UUID) -> ApprovalObservation:
        approval = adapter.service.approval_for_current_swot(
            brand_id=committed.brand.id, user_id=actor.pg_id)
        return ApprovalObservation(approval is not None,
                                   bool(approval and approval.subject_run_id == current_id))

    run_a = adapter.service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=actor.pg_id, kind="swot", status="succeeded",
        input_profile_id=committed.profile.id, output={"swot": {"id": "A"}},
        output_schema_version=1))
    before = observation(run_a.id)
    adapter.service.approve_current_swot(brand_id=committed.brand.id, user_id=actor.pg_id, expected_run_id=run_a.id)
    approved_a = observation(run_a.id)
    run_b = adapter.service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=actor.pg_id, kind="swot", status="succeeded",
        input_profile_id=committed.profile.id, output={"swot": {"id": "B"}},
        output_schema_version=1))
    after_b = observation(run_b.id)
    adapter.service.approve_current_swot(brand_id=committed.brand.id, user_id=actor.pg_id, expected_run_id=run_b.id)
    approved_b = observation(run_b.id)
    return before, approved_a, after_b, approved_b


def test_swot_approval_legacy_red_is_explicit_intentional_bugfix_delta(harness):
    actor = harness.actor("approval@contract.test")
    legacy = _legacy_swot_sequence(harness.legacy, actor, "legacy_approval_contract_test")
    postgres = _postgres_swot_sequence(harness.pg, actor, "pg_approval_contract_test")
    expected_gate = (False, True, False, True)
    legacy_gate = tuple(item.current_swot_approved for item in legacy)
    postgres_gate = tuple(item.current_swot_approved for item in postgres)
    assert legacy_gate == (False, True, True, True)  # measured RED: A leaks onto B
    assert postgres_gate == expected_gate
    assert all(item.approved_subject_is_current for item in (postgres[1], postgres[3]))
    assert not postgres[2].approved_subject_is_current


def test_calendar_preserves_order_content_and_multiple_same_date_posts(harness):
    actor = harness.actor("calendar@contract.test")
    legacy_domain = "legacy_calendar_contract_test"
    pg_domain = "pg_calendar_contract_test"
    posts = [
        {"date": "2026-09-01", "channel": "instagram", "topic": "first"},
        {"date": "2026-09-01", "channel": "linkedin", "topic": "second"},
        {"date": "2026-09-02", "channel": "facebook", "topic": "third"},
    ]
    harness.legacy.seed_brand(legacy_domain, actor, {"profile": True})
    harness.legacy.brands.replace_block(
        legacy_domain, "content_calendar", {"calendar": posts},
        for_user=actor.logical_id)
    legacy_rows = harness.legacy.brands.get(
        legacy_domain, for_user=actor.logical_id)["content_calendar"]["calendar"]

    committed = harness.pg.scrape_success(actor, pg_domain, {"profile": True})
    _run, pg_rows = harness.service.create_calendar(
        brand_id=committed.brand.id, user_id=actor.pg_id,
        input_profile_id=committed.profile.id, posts=posts)
    legacy = CalendarObservation(
        len(legacy_rows), tuple(row["topic"] for row in legacy_rows),
        sum(row["date"] == "2026-09-01" for row in legacy_rows))
    postgres = CalendarObservation(
        len(pg_rows), tuple(row.payload["topic"] for row in pg_rows),
        sum(str(row.scheduled_date) == "2026-09-01" for row in pg_rows))
    assert legacy == postgres == CalendarObservation(3, ("first", "second", "third"), 2)
    assert [row.ordinal for row in pg_rows] == [0, 1, 2]
    assert len({row.id for row in pg_rows}) == 3


# ============================================================================
# Intentional differences and PostgreSQL-only strength
# ============================================================================


def test_delete_archive_shared_visibility_and_intentional_reactivation_policy(harness):
    from api.brand_store import DELETED

    owner = harness.actor("archive-owner@contract.test")
    other = harness.actor("archive-other@contract.test")
    legacy_domain = "legacy_archive_contract_test"
    pg_domain = "pg_archive_contract_test"
    harness.legacy.scrape_success(owner, legacy_domain, {"version": 1})
    pg_first = harness.pg.scrape_success(owner, pg_domain, {"version": 1})
    assert harness.legacy.brands.delete(
        legacy_domain, for_user=owner.logical_id) == DELETED
    assert harness.service.archive_brand(domain_key=pg_domain, user_id=owner.pg_id)
    assert not harness.legacy.observe_brand(owner, legacy_domain).visible
    assert not harness.pg.observe_brand(owner, pg_domain).visible
    assert legacy_domain not in harness.legacy.list_domains(owner)
    assert pg_domain not in harness.pg.list_domains(owner)

    # Same owner: same live business result, but PostgreSQL preserves Brand identity/history.
    assert harness.legacy.scrape_success(owner, legacy_domain, {"version": 2}) == "success"
    pg_second = harness.pg.scrape_success(owner, pg_domain, {"version": 2})
    assert pg_second.brand.id == pg_first.brand.id and pg_second.brand.deleted_at is None
    assert harness.legacy.observe_brand(owner, legacy_domain).current_profile == {"version": 2}
    assert harness.pg.observe_brand(owner, pg_domain).current_profile == {"version": 2}

    # Delete/archive once more: file deletion permits a future claimant; durable archive refuses it.
    assert harness.legacy.brands.delete(
        legacy_domain, for_user=owner.logical_id) == DELETED
    assert harness.service.archive_brand(domain_key=pg_domain, user_id=owner.pg_id)
    assert harness.legacy.scrape_success(other, legacy_domain, {"version": 3}) == "success"
    assert harness.pg.scrape_outcome(other, pg_domain, {"version": 3}) == "foreign_owner"
    assert harness.legacy.brands.owner_of(legacy_domain) == other.logical_id
    assert harness.service.owner_of(pg_domain).owner_id == owner.pg_id


def test_archived_unclaimed_is_explicit_safe_policy_pending_cutover_decision(harness):
    from sqlalchemy.orm import Session
    from db.models import Brand

    actor = harness.actor("archived-unclaimed@contract.test")
    domain = "archived_unclaimed_contract_test"
    with Session(harness.engine) as session:
        session.add(Brand(domain_key=domain, owner_id=None,
                          deleted_at=datetime.now(timezone.utc)))
        session.commit()
    assert harness.pg.scrape_outcome(actor, domain, {"must_not": "claim"}) == "archived"
    ownership = harness.service.owner_of(domain)
    assert ownership.exists and ownership.archived and ownership.owner_id is None
    assert _pg_row_counts(harness.engine, domain) == (1, 0, 0)


def test_postgres_run_dependencies_and_old_outputs_remain_immutable(harness):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from db.models import Run, RunDependency
    from db.services import LiveRunData

    actor = harness.actor("lineage@contract.test")
    committed = harness.pg.scrape_success(
        actor, "lineage_contract_test", {"version": 1})
    swot_a = harness.service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=actor.pg_id, kind="swot", status="succeeded",
        input_profile_id=committed.profile.id, output={"value": "A"}, output_schema_version=1))
    strategy = harness.service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=actor.pg_id, kind="strategy", status="succeeded",
        input_profile_id=committed.profile.id, output={"value": "strategy"},
        output_schema_version=1, dependencies=((swot_a.id, "uses_swot"),)))
    swot_b = harness.service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=actor.pg_id, kind="swot", status="succeeded",
        input_profile_id=committed.profile.id, output={"value": "B"}, output_schema_version=1))
    with Session(harness.engine) as session:
        old_output = session.scalar(select(Run.output).where(Run.id == swot_a.id))
        edges = list(session.scalars(select(RunDependency).where(
            RunDependency.run_id == strategy.id)))
    assert old_output == {"value": "A"}
    assert len(edges) == 1 and edges[0].depends_on_run_id == swot_a.id
    assert harness.service.current_successful_run(
        brand_id=committed.brand.id, kind="swot", user_id=actor.pg_id).id == swot_b.id


def test_postgres_new_uuids_cannot_bypass_brand_visibility(harness):
    from db.errors import NotVisible
    from db.services import CreativeFinalization, LiveRunData, VerifiedAssetData

    owner = harness.actor("uuid-owner@contract.test")
    other = harness.actor("uuid-other@contract.test")
    committed = harness.pg.scrape_success(owner, "uuid_scope_contract_test", {"profile": True})
    run = harness.service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=owner.pg_id, kind="poster", status="running",
        input_profile_id=committed.profile.id))
    attempt = harness.service.create_creative_attempt(run_id=run.id, user_id=owner.pg_id)
    finalized = harness.service.finalize_creative(CreativeFinalization(
        user_id=owner.pg_id, run_id=run.id, attempt_id=attempt.id,
        asset=VerifiedAssetData(
            storage_key="phase2d/verified-test-only.png", kind="poster", mime="image/png",
            byte_count=12, checksum_sha256="a" * 64, durable=True,
            created_by_user_id=owner.pg_id),
        qa={"contract": "passed"}, qa_passed=True,
        output={"asset": "verified-test-only"},
    ))
    assert harness.service.run_for_user(run_id=run.id, user_id=owner.pg_id).id == run.id
    assert harness.service.attempt_for_user(
        attempt_id=attempt.id, user_id=owner.pg_id).id == attempt.id
    assert harness.service.asset_for_user(
        asset_id=finalized.asset.id, user_id=owner.pg_id).id == finalized.asset.id
    for call in (
        lambda: harness.service.run_for_user(run_id=run.id, user_id=other.pg_id),
        lambda: harness.service.attempt_for_user(attempt_id=attempt.id, user_id=other.pg_id),
        lambda: harness.service.asset_for_user(asset_id=finalized.asset.id, user_id=other.pg_id),
    ):
        with pytest.raises(NotVisible):
            call()


@contextmanager
def _query_counter(engine) -> Iterator[list[int]]:
    from sqlalchemy import event

    count = [0]

    def before_cursor_execute(*_args, **_kwargs):
        count[0] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield count
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def test_postgres_representative_reads_have_bounded_query_counts(harness):
    from db.services import LiveRunData

    actor = harness.actor("queries@contract.test")
    committed = harness.pg.scrape_success(actor, "queries_contract_test", {"profile": True})
    run = harness.service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=actor.pg_id, kind="swot", status="succeeded",
        input_profile_id=committed.profile.id, output={"ok": True}, output_schema_version=1))
    job = harness.service.create_user_job(owner_user_id=actor.pg_id)
    operations = {
        "brand_list": lambda: harness.service.list_visible_brands(user_id=actor.pg_id),
        "brand_detail": lambda: harness.service.get_visible_brand(
            user_id=actor.pg_id, brand_id=committed.brand.id),
        "current_profile": lambda: harness.service.current_profile(
            brand_id=committed.brand.id, user_id=actor.pg_id),
        "current_run": lambda: harness.service.current_successful_run(
            brand_id=committed.brand.id, kind="swot", user_id=actor.pg_id),
        "job_get": lambda: harness.service.job_for_user(job_id=job.id, user_id=actor.pg_id),
    }
    measured = {}
    for name, operation in operations.items():
        with _query_counter(harness.engine) as count:
            operation()
        measured[name] = count[0]
    assert measured == {
        "brand_list": 1, "brand_detail": 1, "current_profile": 2,
        "current_run": 2, "job_get": 1,
    }
