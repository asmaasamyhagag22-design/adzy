# -*- coding: utf-8 -*-
"""Baseera SCHEMA v1.2 — the durable shape of the product's domain.

v1.1 -> v1.2 is a real delta, not a renumbering. v1.1 was reviewed against a tree that predated the
C2-C7 recovery and it froze one contract WRONG: it recorded "an existing unclaimed brand refuses a
re-scrape", because that is what the surviving code did. The recovered contract is the opposite —
a successful authorised re-scrape CLAIMS an unclaimed brand, in the same transaction that persists
the result. `brands.owner_id` being NULLable is therefore a live state with a transition into it,
not a terminal one. C6-R1 also became real in the runtime, so country arrives here as a
(canonical, source, raw) triple instead of one flattened string.

TWO RULES DECIDE MOST OF WHAT FOLLOWS.

1. A POINTER MAY NOT CROSS ITS PARENT. It is not enough that a referenced row exists: Brand A must
   not be able to name a ProfileSnapshot of Brand B, and Run A must not select an Attempt of Run B.
   Every such pointer carries the parent key alongside it and is closed with a COMPOSITE foreign
   key, so the wrong row is unrepresentable rather than merely discouraged. The export this design
   was compared against has exactly this hole (`initiatives.strategy_report_id` can name another
   business's report) and it is not inherited.

2. ANYTHING INDEPENDENTLY ADDRESSABLE IS RELATIONAL. JSONB carries payloads, never identity. The
   405 KB mutable brand document this replaces grew precisely because addressable children —
   calendar entries, candidates, assets — had nowhere else to live.

Nothing here is wired to the application. Importing this module opens no connection.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base

# `gen_random_uuid()` is in the PostgreSQL core since 13 — no pgcrypto extension needed.
_UUID_PK = text("gen_random_uuid()")
_NOW = text("now()")


def _uuid_pk() -> Mapped[str]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_UUID_PK)


# =====================================================================
# identity
# =====================================================================

class User(Base):
    """An account. Replaces users.json."""

    __tablename__ = "users"

    id: Mapped[str] = _uuid_pk()
    # THE CANONICAL FORM, and only that. UserStore normalises with `.strip().lower()` at create,
    # at authenticate AND at lookup, and keys users.json by the result — so the product already
    # discards both the original casing and any surrounding whitespace.
    #
    # The CHECK below reproduces BOTH halves of that rule. `lower()` alone was not enough: it would
    # still have admitted " user@example.com " and "user@example.com " as separate rows, which is
    # the same duplicate-account bug in a different disguise. A non-canonical surface is now
    # REJECTED outright rather than quietly stored, so a caller that forgets to normalise fails
    # loudly instead of creating a twin account nobody notices until login breaks.
    #
    # No `citext`: an extension would re-implement the casing half and do nothing about the
    # whitespace half. The application already produces the canonical form; the database now
    # insists on it.
    email: Mapped[str] = mapped_column(Text, nullable=False)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # 0002. The pre-cutover store issued `secrets.token_hex(8)` — 16 hex chars, not a UUID — so a
    # migrated account CANNOT keep its old id as a primary key. UUID stays the identity; this
    # records where the account came from, and earns its place three times over:
    #   * audit    — the only durable link from a legacy id to the UUID that replaced it
    #   * idempotency — the migration's natural conflict key, so a retry updates instead of doubling
    #   * sessions — issued JWTs carry `sub = <legacy id>`, and this makes that an indexed lookup
    #                rather than a uuid5 recomputation living inside auth forever
    # NULL for every account created after cutover. See ADR in the 0002 revision docstring.
    legacy_user_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        # Two accounts may both be new (both NULL — NULLs are distinct in a UNIQUE index), but no
        # two accounts may claim the same legacy identity.
        UniqueConstraint("legacy_user_id", name="uq_users_legacy_user_id"),
        CheckConstraint(
            "legacy_user_id IS NULL OR legacy_user_id ~ '^[0-9a-f]{16}$'",
            name="legacy_user_id_is_16_hex"),
        # btrim AND lower — the two halves of UserStore's `.strip().lower()`.
        CheckConstraint("email = btrim(lower(email))", name="email_is_normalised"),
        CheckConstraint("length(email) > 0", name="email_not_blank"),
    )


class Brand(Base):
    """A site the product manages, keyed by domain_key. The ownership anchor.

    `owner_id` means THE ACCOUNT MANAGING THIS RECORD. It is not a claim about who legally owns the
    real company, and nothing verifies that.

    NULL owner is a real, reachable, non-terminal state: brands created before accounts existed are
    unclaimed, are visible to every account, and are claimed by whichever account next completes a
    successful authorised scrape of them (C2). Two paths write it — that claim, and the deliberate
    `python -m api.claim_brands` — and neither guesses.
    """

    __tablename__ = "brands"

    id: Mapped[str] = _uuid_pk()
    domain_key: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL", name="fk_brands_owner_id"))
    # The chosen current profile. A POINTER, not `max(version)`: the ownership race can persist a
    # snapshot that must NOT become current, and a derived answer cannot express that.
    current_profile_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    display_name: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    # Soft delete. The file store hard-unlinks and a later upsert silently RECREATES the brand;
    # under a durable store that resurrection would orphan every run pointing at the old identity.
    deleted_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("domain_key", name="uq_brands_domain_key"),
        # The identity backstop for the first-create race: two concurrent scrapes of one new domain
        # cannot both insert. Also the target of ON CONFLICT in the claim transaction.
        UniqueConstraint("id", "domain_key", name="uq_brands_id_domain_key"),
        # MEMBERSHIP. The FK includes this table's OWN primary key, so a row can only point at a
        # snapshot whose brand_id equals its own id. Cross-brand reference is unrepresentable.
        # MATCH SIMPLE (the default) skips the check when current_profile_id is NULL, which is
        # exactly right for a brand that has no profile yet.
        # use_alter: brands and profile_snapshots reference each other, so this one is emitted as
        # an ALTER after both tables exist rather than depending on creation order.
        ForeignKeyConstraint(
            ["current_profile_id", "id"],
            ["profile_snapshots.id", "profile_snapshots.brand_id"],
            name="fk_brands_current_profile", use_alter=True, ondelete="RESTRICT"),
        Index("ix_brands_owner_id", "owner_id"),
        Index("ix_brands_live_updated", "updated_at",
              postgresql_where=text("deleted_at IS NULL")),
    )


# =====================================================================
# profile history
# =====================================================================

class ProfileSnapshot(Base):
    """An immutable, versioned BusinessProfile. There is no UPDATE path to this table.

    That immutability is the only reason a JSONB blob is safe here: `profile` is written once and
    never edited, so it cannot become the mutable dumping ground the old brand document was.
    """

    __tablename__ = "profile_snapshots"

    id: Mapped[str] = _uuid_pk()
    brand_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="RESTRICT", name="fk_profile_snapshots_brand_id"),
        nullable=False)
    # Allocated under a `SELECT ... FROM brands WHERE id = ? FOR UPDATE` row lock — the same lock
    # the ownership decision takes, so both are decided in one critical section. The UNIQUE below
    # is the backstop: if a future path forgets the lock it gets a violation, not a duplicate.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- C6 / C6-R1 projections -------------------------------------------------
    # DERIVED and NON-AUTHORITATIVE: `profile` is the authority. These are computed once at insert
    # and never independently edited, so the two copies cannot drift.
    #
    # Three columns rather than one, because `country_of` alone cannot be stored honestly: "the
    # UAE" read off a stated address and "the UAE" inferred from a .ae domain are different facts
    # with different strength, and a single string can never tell them apart again. `country_raw`
    # keeps the surface actually seen, before normalisation folded it.
    country_canonical: Mapped[str | None] = mapped_column(Text)
    country_source: Mapped[str | None] = mapped_column(Text)
    country_raw: Mapped[str | None] = mapped_column(Text)
    # Language is resolved INDEPENDENTLY of country and is stored independently of it. One "locale"
    # column would conflate country, content language and dialect — the exact collapse the runtime
    # spent a phase separating.
    primary_language: Mapped[str | None] = mapped_column(String(8))
    language_source: Mapped[str | None] = mapped_column(Text)

    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'live'"))
    created_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("brand_id", "version", name="uq_profile_snapshots_brand_id_version"),
        # The referenced key for `brands.fk_brands_current_profile` and `runs.fk_runs_input_profile`.
        UniqueConstraint("id", "brand_id", name="uq_profile_snapshots_id_brand_id"),
        CheckConstraint("version > 0", name="version_is_positive"),
        CheckConstraint("origin IN ('live','legacy_import')", name="origin_is_known"),
        CheckConstraint(
            "country_source IS NULL OR country_source IN "
            "('explicit_country','location_country','website_cctld','service_area',"
            "'phone_prefix','unknown')",
            name="country_source_is_known"),
        # A legacy import genuinely does not know when it was created. NULL says so; migration
        # time goes in `imported_at` and is never passed off as creation time.
        CheckConstraint("origin = 'legacy_import' OR created_at IS NOT NULL",
                        name="live_rows_know_their_creation_time"),
        Index("ix_profile_snapshots_brand_version", "brand_id", "version"),
    )


# =====================================================================
# runs — the durable business operations
# =====================================================================

RUN_KINDS = ("scrape", "trends", "swot", "strategy", "calendar", "competitors",
             "poster", "social", "reel", "influencer_poster")
CREATIVE_KINDS = ("poster", "social", "reel", "influencer_poster")
TERMINAL_STATUSES = ("queued", "running", "succeeded", "failed")


class Run(Base):
    """One durable business operation and its result.

    Separate from Job on purpose. A Run is WHAT THE PRODUCT DID; a Job is one execution envelope
    that tried to do it. Keeping them apart is what makes the two crash classes distinguishable
    after a restart: "result committed but the job never finalised" is only expressible when the
    result and the execution have independent status.
    """

    __tablename__ = "runs"

    id: Mapped[str] = _uuid_pk()
    brand_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="RESTRICT", name="fk_runs_brand_id"),
        nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))

    input_profile_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    calendar_post_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    selected_attempt_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))

    # What the caller asked for vs what the runtime actually applied. Both versioned, both
    # immutable once the run is terminal. This is what lets a control that turns out to be inert be
    # PROVEN inert later, from data, without a schema change and without one column per UI knob.
    requested_config: Mapped[dict] = mapped_column(JSONB, nullable=False,
                                                   server_default=text("'{}'::jsonb"))
    effective_config: Mapped[dict | None] = mapped_column(JSONB)
    config_schema_version: Mapped[int] = mapped_column(Integer, nullable=False,
                                                       server_default=text("1"))
    # Immutable once terminal. Payload only — every independently addressable child is relational.
    output: Mapped[dict | None] = mapped_column(JSONB)
    output_schema_version: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    # 0002. WHICH RUN OF THIS KIND IS CURRENT — allocated under the brand row lock, never derived
    # from a clock.
    #
    # The obvious rule, "newest succeeded run by created_at", breaks twice here. Imported runs
    # truthfully carry created_at = NULL because no historical execution time exists, and
    # `ORDER BY created_at DESC` puts NULLs FIRST in PostgreSQL — so a legacy SWOT with no
    # timestamp outranks every live one ever generated. And even between two live runs, created_at
    # is transaction-START time, which can invert against commit order, so it cannot decide a race
    # either. The same reasoning already made `version` (not created_at) the authority for profile
    # snapshots; this is that rule applied per (brand, kind).
    #
    # NULL means "predates our sequence" — imports only. Ordering `kind_seq DESC NULLS LAST` then
    # reads correctly in every case: legacy alone still wins by being the only row, and any live
    # run outranks it.
    kind_seq: Mapped[int | None] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'live'"))
    lineage_complete: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                   server_default=text("true"))
    legacy_source_key: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_runs_created_by_user_id"))
    created_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("id", "brand_id", name="uq_runs_id_brand_id"),
        # Carries `kind` so a child can require BOTH the right brand and the right kind of parent.
        UniqueConstraint("id", "brand_id", "kind", name="uq_runs_id_brand_id_kind"),
        ForeignKeyConstraint(
            ["input_profile_id", "brand_id"],
            ["profile_snapshots.id", "profile_snapshots.brand_id"],
            name="fk_runs_input_profile", ondelete="RESTRICT"),
        # MEMBERSHIP for the selected candidate: the FK's second column is this run's OWN id, so
        # the attempt must have run_id = this run. Selecting another run's attempt cannot be
        # expressed. Circular with `attempts.fk_attempts_run`, hence use_alter.
        ForeignKeyConstraint(
            ["selected_attempt_id", "id"], ["attempts.id", "attempts.run_id"],
            name="fk_runs_selected_attempt", use_alter=True, ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["calendar_post_id", "brand_id"],
            ["calendar_posts.id", "calendar_posts.brand_id"],
            name="fk_runs_calendar_post", use_alter=True, ondelete="RESTRICT"),
        CheckConstraint(f"kind IN {RUN_KINDS!r}".replace('"', "'"), name="kind_is_known"),
        CheckConstraint(f"status IN {TERMINAL_STATUSES!r}".replace('"', "'"),
                        name="status_is_known"),
        # Only a creative run may target a calendar post; everything else targets the brand.
        CheckConstraint(
            f"calendar_post_id IS NULL OR kind IN {CREATIVE_KINDS!r}".replace('"', "'"),
            name="only_creative_runs_target_a_post"),
        CheckConstraint("origin IN ('live','adopted','legacy_import')", name="origin_is_known"),
        # A live run always knows its own lineage; only an import may say it does not.
        CheckConstraint("origin <> 'live' OR lineage_complete", name="live_lineage_is_complete"),
        CheckConstraint("origin <> 'live' OR created_by_user_id IS NOT NULL",
                        name="live_runs_know_their_creator"),
        CheckConstraint("origin = 'legacy_import' OR created_at IS NOT NULL",
                        name="live_rows_know_their_creation_time"),
        # Anything the runtime produced allocates a sequence; only an import may skip it.
        CheckConstraint("origin = 'legacy_import' OR kind_seq IS NOT NULL",
                        name="runtime_runs_allocate_a_sequence"),
        CheckConstraint("kind_seq IS NULL OR kind_seq >= 1", name="kind_seq_starts_at_one"),
        # The allocation is serialised by the brand row lock; this is the backstop that turns a
        # forgotten lock into a violation instead of two runs both claiming to be current.
        UniqueConstraint("brand_id", "kind", "kind_seq", name="uq_runs_brand_id_kind_kind_seq"),
        Index("ix_runs_current_by_kind", "brand_id", "kind", "kind_seq"),
        Index("ix_runs_brand_kind_created", "brand_id", "kind", "created_at"),
        Index("ix_runs_calendar_post_id", "calendar_post_id"),
        Index("ix_runs_open", "status", postgresql_where=text("status IN ('queued','running')")),
    )


class RunDependency(Base):
    """Multi-parent DATA lineage between runs.

    A single `parent_run_id` cannot express what the runtime actually does: SWOT consumes the
    profile AND the trends run; the calendar consumes the profile AND the SWOT AND the strategy.
    Modelling that as a tree loses a parent every time.

    DATA dependency only. A retry is an EXECUTION relationship and lives on `jobs.execution_no` —
    putting it here would make "we ran it twice" look like "the result was derived from itself".
    """

    __tablename__ = "run_dependencies"

    run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    depends_on_run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # Carried so BOTH endpoints can be constrained to the same brand. Cross-brand lineage becomes
    # unrepresentable rather than merely unexpected.
    brand_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        ForeignKeyConstraint(["run_id", "brand_id"], ["runs.id", "runs.brand_id"],
                             name="fk_run_dependencies_run", ondelete="RESTRICT"),
        ForeignKeyConstraint(["depends_on_run_id", "brand_id"], ["runs.id", "runs.brand_id"],
                             name="fk_run_dependencies_depends_on", ondelete="RESTRICT"),
        CheckConstraint("run_id <> depends_on_run_id", name="no_self_dependency"),
        # Only what the traced flows actually prove. Not a vocabulary to grow speculatively.
        CheckConstraint("relation IN ('uses_trends','uses_swot','uses_strategy')",
                        name="relation_is_known"),
        Index("ix_run_dependencies_depends_on_run_id", "depends_on_run_id"),
    )


# =====================================================================
# calendar
# =====================================================================

class CalendarPost(Base):
    """One addressable calendar entry.

    This table exists because the runtime already ADDRESSES individual posts — the poster route
    looks one up to attach a render to it — while the file store gives them no identity at all. It
    matches on `date`, and `date` is not unique: the strategy builder sorts items by
    `(date, platform)`, which is only meaningful because a date can hold several. So the second
    post on a day can never be reached, and regenerating the calendar replaces the whole list and
    silently discards every attached poster.

    Identity is `(calendar_run_id, ordinal)` — generation order, always unique. `scheduled_date` is
    data, never a key.
    """

    __tablename__ = "calendar_posts"

    id: Mapped[str] = _uuid_pk()
    calendar_run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    brand_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Pinned discriminator: lets the composite FK below require a parent of kind 'calendar'.
    calendar_run_kind: Mapped[str] = mapped_column(Text, nullable=False,
                                                   server_default=text("'calendar'"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_date: Mapped[str | None] = mapped_column(Date)
    channel: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)
    funnel_stage: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text)
    hook: Mapped[str | None] = mapped_column(Text)
    cta: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False,
                                          server_default=text("'{}'::jsonb"))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("calendar_run_id", "ordinal",
                         name="uq_calendar_posts_calendar_run_id_ordinal"),
        UniqueConstraint("id", "brand_id", name="uq_calendar_posts_id_brand_id"),
        # THREE columns: the parent must be the right row, of the right brand, of the right KIND.
        # A calendar post hanging off a strategy run is unrepresentable.
        ForeignKeyConstraint(
            ["calendar_run_id", "brand_id", "calendar_run_kind"],
            ["runs.id", "runs.brand_id", "runs.kind"],
            name="fk_calendar_posts_calendar_run", ondelete="RESTRICT"),
        CheckConstraint("calendar_run_kind = 'calendar'", name="parent_is_a_calendar_run"),
        CheckConstraint("ordinal >= 0", name="ordinal_is_not_negative"),
        # Mirrors the builder, which already discards anything outside these three.
        CheckConstraint("funnel_stage IS NULL OR funnel_stage IN ('tofu','mofu','bofu')",
                        name="funnel_stage_is_known"),
        Index("ix_calendar_posts_brand_scheduled_date", "brand_id", "scheduled_date"),
    )


# =====================================================================
# creative attempts + assets
# =====================================================================

class Asset(Base):
    """Durable metadata for one stored media object. THE BYTES STAY OUTSIDE.

    PostgreSQL cannot enrol a filesystem write in its transaction, so the ordering carries the
    guarantee instead: bytes are written and verified FIRST, the row is committed second. A crash
    between them leaves an orphan blob, which a sweeper can reclaim. The reverse — a row promising
    bytes that never landed — would be served to a user as a broken image, so the safe direction is
    chosen deliberately, not by accident.
    """

    __tablename__ = "assets"

    id: Mapped[str] = _uuid_pk()
    brand_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="RESTRICT", name="fk_assets_brand_id"),
        nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # AUDIT ONLY — deliberately not called owner_*. A user may generate assets on an UNCLAIMED
    # brand, so the creator and the brand's manager genuinely differ; a field named `owner_user_id`
    # would assert an ownership that does not exist. Ownership flows from the Brand.
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_assets_created_by_user_id"))
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_assets_storage_key"),
        UniqueConstraint("id", "brand_id", name="uq_assets_id_brand_id"),
        CheckConstraint("bytes IS NULL OR bytes >= 0", name="bytes_is_not_negative"),
        Index("ix_assets_brand_created", "brand_id", "created_at"),
    )


class Attempt(Base):
    """One candidate produced inside a creative Run.

    An Attempt is a CANDIDATE, never a retry. Retries are executions and live on `jobs`. Keeping
    the two words apart is what stops "generate three faces and pick one" from being confused with
    "the job failed twice".
    """

    __tablename__ = "attempts"

    id: Mapped[str] = _uuid_pk()
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Carried so the asset can be constrained to the same brand without another join.
    brand_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'running'"))
    asset_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    # The QA field set is still moving; locking it into columns now would be premature. The boolean
    # beside it is the queryable projection.
    qa: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    qa_passed: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)
    finished_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Referenced by `runs.fk_runs_selected_attempt`.
        UniqueConstraint("id", "run_id", name="uq_attempts_id_run_id"),
        UniqueConstraint("id", "brand_id", name="uq_attempts_id_brand_id"),
        UniqueConstraint("run_id", "ordinal", name="uq_attempts_run_id_ordinal"),
        ForeignKeyConstraint(["run_id", "brand_id"], ["runs.id", "runs.brand_id"],
                             name="fk_attempts_run", ondelete="RESTRICT"),
        # Same-brand asset, enforced by the database rather than by whoever writes the service.
        ForeignKeyConstraint(["asset_id", "brand_id"], ["assets.id", "assets.brand_id"],
                             name="fk_attempts_asset", ondelete="RESTRICT"),
        CheckConstraint("status IN ('running','succeeded','failed')", name="status_is_known"),
        # A succeeded candidate always produced something. This is what makes the eligibility
        # predicate on selection meaningful.
        CheckConstraint("status <> 'succeeded' OR asset_id IS NOT NULL",
                        name="succeeded_attempts_have_an_asset"),
        CheckConstraint("ordinal >= 0", name="ordinal_is_not_negative"),
        Index("ix_attempts_run_id_ordinal", "run_id", "ordinal"),
    )


# =====================================================================
# execution
# =====================================================================

class Job(Base):
    """One durable execution envelope. Replaces the process-local JobStore.

    Two things the in-memory store cannot do, and the reason this table exists:

    * A RUNNING job whose process died is currently EVICTED after an hour — deleted, along with the
      evidence that it ever ran. A durable store must be able to RECONCILE it instead, which needs
      a claimant and a lease that outlive the process.
    * `asyncio.to_thread` work cannot be cancelled, and CancelledError is a BaseException that the
      runners' `except Exception` does not catch. So a cancelled coroutine can leave the business
      result committed and the job never finalised. That state is detectable here — a non-terminal
      job beside a succeeded run — and nowhere else.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = _uuid_pk()
    # BOTH NULLABLE, and that is the first-ever-scrape lifecycle, not laxity.
    #
    # `runs.brand_id` is NOT NULL, so a Run cannot exist before its Brand. For a domain nobody has
    # ever scraped there IS no Brand yet — and creating an "anchor" Brand before the work would be
    # worse: a failed first scrape would leave an unclaimed Brand row for a domain that was never
    # successfully read, and unclaimed brands are visible to every account, so any caller could
    # populate the global namespace just by starting scrapes that fail.
    #
    # So: the Job is durable BEFORE the expensive work, carrying only the requested key; the Brand,
    # the ProfileSnapshot and the succeeded Run are created together in the final transaction. If
    # that scrape fails, the Job alone is the history — which is the truth, because nothing about
    # that domain was ever established. A re-scrape of an EXISTING brand does create its Run up
    # front, so a failure there is recorded as a failed Run against the brand it belongs to.
    run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="RESTRICT", name="fk_jobs_run_id"))
    brand_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brands.id", ondelete="RESTRICT", name="fk_jobs_brand_id"))
    # What was asked for, known before any work and independent of whether a Brand ever existed.
    # The runtime already computes exactly this (`job.domain_key = domain_key_from_url(url)`) at
    # job creation. Without it a failed first-ever scrape would be a Job that cannot say what it
    # was for. NULL for the legacy /api/run route, which scrapes to disk and names no brand.
    requested_domain_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    # Retry ordinal. NAMED execution_no, not `attempt`, so it can never be read as the Attempt
    # entity: Run -> N Jobs, each Job one execution.
    execution_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # `owner_principal` in the runtime is a prefix-tagged union: `user:<id>` or
    # `capability:<sha256>`. Split into an explicit discriminator plus two columns so the database
    # can state the rule instead of leaving it to string parsing.
    access_mode: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_jobs_owner_user_id"))
    # THE DIGEST ONLY. The runtime already stores a domain-separated SHA-256 and returns the raw
    # 256-bit token to the caller exactly once; the secret must not acquire a durable home here.
    capability_digest: Mapped[str | None] = mapped_column(String(64))

    current_stage: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    warning: Mapped[str | None] = mapped_column(Text)

    # Staleness. A heartbeat is the only signal that survives an unclean kill, because no shutdown
    # hook runs. `lease_expires_at` is refreshed while working; past + still running = abandoned.
    claimed_by: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)
    finished_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # NULLs are distinct in a UNIQUE index, so legacy /api/run jobs (no Run) are unconstrained
        # here — which is the desired behaviour, not an oversight.
        UniqueConstraint("run_id", "execution_no", name="uq_jobs_run_id_execution_no"),
        CheckConstraint("status IN ('queued','running','succeeded','failed')",
                        name="status_is_known"),
        CheckConstraint("access_mode IN ('user','capability')", name="access_mode_is_known"),
        # Exactly one principal form, never both, never neither.
        CheckConstraint(
            "(access_mode = 'user' AND owner_user_id IS NOT NULL AND capability_digest IS NULL)"
            " OR (access_mode = 'capability' AND capability_digest IS NOT NULL"
            "     AND owner_user_id IS NULL)",
            name="exactly_one_principal"),
        # Without this the stale-job predicate has nothing to test.
        CheckConstraint("status <> 'running' OR claimed_by IS NOT NULL",
                        name="running_jobs_name_their_claimant"),
        CheckConstraint("execution_no >= 1", name="execution_no_starts_at_one"),
        Index("ix_jobs_expired_leases", "lease_expires_at",
              postgresql_where=text("status = 'running'")),
        Index("ix_jobs_requested_domain_key", "requested_domain_key"),
        Index("ix_jobs_open", "status", postgresql_where=text("status IN ('queued','running')")),
        Index("ix_jobs_owner_user_id", "owner_user_id"),
    )


# =====================================================================
# cost + approval
# =====================================================================

class UsageRecord(Base):
    """One PROVIDER CALL. Not one per run.

    `Usage` is produced per LLM call and a single run makes many — generating the plan alone calls
    the media-plan builder and the strategy builder, each of which calls the model repeatedly.
    Aggregating at run level would destroy real cost attribution before it was ever stored.
    """

    __tablename__ = "usage_records"

    id: Mapped[str] = _uuid_pk()
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    brand_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Kept separate on purpose: thinking tokens bill at the OUTPUT rate but are excluded from
    # candidates_token_count, so folding them into output_tokens would understate the real cost.
    thoughts_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        ForeignKeyConstraint(["run_id", "brand_id"], ["runs.id", "runs.brand_id"],
                             name="fk_usage_records_run", ondelete="RESTRICT"),
        ForeignKeyConstraint(["attempt_id", "run_id"], ["attempts.id", "attempts.run_id"],
                             name="fk_usage_records_attempt", ondelete="RESTRICT"),
        CheckConstraint("cost_usd >= 0", name="cost_is_not_negative"),
        Index("ix_usage_records_run_id", "run_id"),
        Index("ix_usage_records_brand_created", "brand_id", "created_at"),
    )


class Approval(Base):
    """A human decision about ONE immutable run.

    Human decisions cover the SWOT gate and exact canonical strategy Runs. Selection is NOT
    approval and is not stored here — the influencer picker writes `runs.selected_attempt_id`.

    Binding the approval to a specific run is what fixes a live defect. Today `swot_approved` is a
    boolean on the brand with no subject, so regenerating the SWOT leaves the old approval standing
    and the strategy gate opens on content nobody approved. Here a regenerated SWOT is a NEW run
    with no approval row, so the gate correctly closes again. The bug is not fixed by policy; it
    becomes unrepresentable.
    """

    __tablename__ = "approvals"

    id: Mapped[str] = _uuid_pk()
    brand_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'swot'"))
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_approvals_decided_by_user_id"))
    # 0002. NULLABLE, and only for imports. Three brands carry `swot_approved = true` in the file
    # store. The APPROVAL IS A FACT; who signed it and when are not recorded anywhere — one of the
    # three is on an unclaimed brand, so no actor exists even in principle. Dating those signatures
    # to the migration would invent evidence, and dropping them would destroy a real human decision
    # and silently re-close the strategy gate on three brands. So the column admits "unknown", and
    # the CHECKs below make that admission available ONLY to imports.
    decided_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'live'"))
    imported_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # Right row, right brand, right KIND — all three in the database.
        ForeignKeyConstraint(
            ["subject_run_id", "brand_id", "subject_kind"],
            ["runs.id", "runs.brand_id", "runs.kind"],
            name="fk_approvals_subject_run", ondelete="RESTRICT"),
        CheckConstraint("subject_kind IN ('swot','strategy')", name="subject_kind_is_known"),
        CheckConstraint("decision IN ('approved','rejected')", name="decision_is_known"),
        CheckConstraint("origin IN ('live','legacy_import')", name="origin_is_known"),
        # A LIVE approval is a person deciding behind `require_user`; the SWOT and strategy routes
        # are the only write paths and neither generates a system approval, so a live row must name
        # BOTH its actor and its moment. Audited before enforcing.
        CheckConstraint(
            "origin <> 'live' OR (decided_at IS NOT NULL AND decided_by_user_id IS NOT NULL"
            " AND imported_at IS NULL)",
            name="live_approvals_name_actor_and_time"),
        # This legacy source proves the decision, but neither its actor nor its moment. Import time
        # is the only timestamp it may carry; accepting either historical field would let a loader
        # silently manufacture stronger provenance than the source contains.
        CheckConstraint(
            "origin <> 'legacy_import' OR (decided_by_user_id IS NULL AND decided_at IS NULL"
            " AND imported_at IS NOT NULL)",
            name="legacy_imports_have_no_fabricated_metadata"),
        UniqueConstraint("subject_run_id", "decided_at",
                         name="uq_approvals_subject_run_id_decided_at"),
        # Live approvals stay an append-only decision log (the UNIQUE above only separates them by
        # instant). Imports get a stricter rule: at most ONE per run, so re-running the migration
        # cannot double them — NULL decided_at makes the composite UNIQUE above useless there,
        # because NULLs are distinct.
        Index("uq_approvals_one_import_per_run", "subject_run_id", unique=True,
              postgresql_where=text("origin = 'legacy_import'")),
        Index("ix_approvals_subject_run_id", "subject_run_id"),
    )


# =====================================================================
# owner-curated brand extensions
# =====================================================================

class BrandOwnerInputs(Base):
    """The owner's manual inputs. Mutable BY DESIGN — this is user input, not generated output,
    so it never belongs inside an immutable run payload."""

    __tablename__ = "brand_owner_inputs"

    brand_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="CASCADE", name="fk_brand_owner_inputs_brand_id"),
        primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False,
                                          server_default=text("'{}'::jsonb"))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)


class BrandCompetitor(Base):
    """The owner's hand-curated competitor registry.

    Kept apart from generated competitor discovery, exactly as the runtime already insists: that
    field is the owner's, and a generated list must not silently replace a chosen one.
    """

    __tablename__ = "brand_competitors"

    id: Mapped[str] = _uuid_pk()
    brand_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="CASCADE", name="fk_brand_competitors_brand_id"),
        nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False,
                                          server_default=text("'{}'::jsonb"))
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        # Matches DELETE /api/brands/{dk}/competitors/{key}.
        UniqueConstraint("brand_id", "key", name="uq_brand_competitors_brand_id_key"),
    )


class JobEvent(Base):
    """One durable progress/terminal event of a Job — the SSE stream's source of truth.

    WHY THIS EXISTS. The stream was an in-process `asyncio.Queue` plus a list on a `Job` object. That
    is a fine transport and an impossible authority: a second web worker cannot see it, a restart
    erases it, and "what happened to job X" has no answer once the process that ran it is gone. With
    PostgreSQL owning job status, leaving events in memory would put the two halves of one truth in
    two stores — status durable, the events explaining it not.

    ORDER COMES FROM `seq`, NEVER FROM TIME. Two events inserted inside the same millisecond order
    arbitrarily by timestamp, and clock adjustment can invert them outright. `seq` is allocated
    under the Job row lock, so it is gapless per job and total. UNIQUE(job_id, seq) is the backstop
    that turns a mistake in that allocation into a failed insert rather than a silently reordered
    stream.

    `is_terminal` is stored rather than derived from `stage`/`status` text, because the terminal
    event is the one thing a reader MUST be able to identify without knowing the stage vocabulary —
    it is what ends a stream and what a restarted process replays to decide the job is over.

    `occurred_at`, `duration_ms` and `error` are real columns rather than keys inside `payload`,
    because `payload` is caller-supplied: a caller passing its own `error` key would otherwise
    overwrite the event's. The set is exactly what `api.schemas.StageEvent` serialises, so a
    replayed event is byte-comparable with the one a live subscriber received.
    """

    __tablename__ = "job_events"

    id: Mapped[str] = _uuid_pk()
    job_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE", name="fk_job_events_job_id"),
        nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                              server_default=text("false"))
    #: The event's OWN moment, as the emitter stamped it — distinct from `created_at`, the moment
    #: the row was inserted. A worker thread emits through a threadsafe hop, so the two genuinely
    #: differ, and replaying `created_at` would hand a reconnecting client different timestamps
    #: from the ones live subscribers already received for the same events.
    occurred_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    #: Caller-supplied. NULLable because `StageEvent.payload` is optional, and because a NULL and
    #: an empty object are different answers to "did this event carry one?".
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="uq_job_events_job_id_seq"),
        CheckConstraint("seq >= 1", name="seq_starts_at_one"),
        CheckConstraint("duration_ms >= 0", name="duration_is_not_negative"),
        # A job has at most ONE terminal event. Two would mean the stream can end twice, and a
        # reader replaying after a restart could not say which ending was real.
        Index("uq_job_events_one_terminal", "job_id", unique=True,
              postgresql_where=text("is_terminal")),
        Index("ix_job_events_job_id_seq", "job_id", "seq"),
    )


class JobResult(Base):
    """The business result of a Job that intentionally has NO Run.

    WHY THIS EXISTS. Most jobs finish by producing a Run, and for those the Run's `output` IS the
    result — this table must never hold a copy of one. But `/api/run` is a measured, real product
    shape that deliberately has no Brand and no Run: it is a standalone capability-owned job whose
    output is a BusinessProfile. Until now that profile lived only on disk and in the process-local
    JobStore, so a succeeded durable job had no durable answer to "what did it produce", and a
    restart lost it.

    The two shapes are kept apart rather than merged. Fabricating a Brand and a Run purely to give
    `/api/run` somewhere to write would put a namespace nobody asked for into the brand tables and
    make "which brands exist" answer differently — the same pollution `Job.requested_domain_key`
    exists to avoid. So: `jobs.run_id` for run-backed results, this table for direct ones, and the
    service decides which is authoritative. The route never learns the difference.

    ONE RESULT PER JOB, STRUCTURALLY. `job_id` is the primary key, not merely a unique foreign key,
    so a second direct result is unrepresentable rather than prevented by a check. Retries and
    double-finalisation therefore fail on insert instead of quietly producing two answers to one
    question.

    `payload` is NOT NULL: a direct result row exists only to carry a result, and a NULL one would
    mean "this job succeeded and produced nothing", which is precisely the false success the whole
    cutover is trying to make unreachable. A job with no result simply has no row here.

    RESTRICT, NOT CASCADE, unlike `job_events`. An event is a progress breadcrumb and means
    nothing without its job; a result IS the deliverable — the BusinessProfile the caller was
    given. `db/schema.sql`'s delete policy keeps EVIDENCE on RESTRICT precisely so history
    cannot be erased as a side effect of deleting a parent, and this is evidence. The product
    never hard-deletes a job (archival is a flag), so RESTRICT costs nothing operationally and
    fails in the safe direction: a blocked delete is recoverable, an erased deliverable is not.

    NOT ENFORCED IN SQL, ENFORCED IN THE SERVICE: that a job has EITHER a `run_id` OR a row here,
    never both. PostgreSQL cannot express a cross-table CHECK without a trigger, and a trigger that
    silently rewrote finalisation would be worse than the invariant it guards. The service refuses
    it and a test pins the refusal.
    """

    __tablename__ = "job_results"

    job_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="RESTRICT", name="fk_job_results_job_id"),
        primary_key=True)
    #: What KIND of result this is, so a reader never has to infer it from the payload's shape.
    #: `/api/run` writes `business_profile`; the column exists so a second direct-result job type
    #: can be added without guessing.
    result_kind: Mapped[str] = mapped_column(Text, nullable=False)
    #: Versioned for the same reason `profile_snapshots.schema_version` is: the payload outlives the
    #: code that wrote it, and a reader must be able to tell which contract it is looking at.
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_starts_at_one"),
        CheckConstraint("length(btrim(result_kind)) > 0", name="result_kind_is_not_blank"),
    )


class GalleryBlob(Base):
    """PHYSICAL bytes, addressed by content. One row per distinct image, however often it appears.

    Split from `GalleryEntry` because the measured history proves the two are different things: 28
    logical gallery entries carry only 25 distinct byte checksums. Three of those entries are the
    SAME image published again — a real, separate event in the owner's history — and folding them
    into one row because the pixels match would delete history to save disk.
    """

    __tablename__ = "gallery_blobs"

    checksum: Mapped[str] = mapped_column(Text, primary_key=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'image/png'"))
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        CheckConstraint("byte_size > 0", name="blob_has_bytes"),
        CheckConstraint("length(checksum) = 64", name="checksum_is_sha256_hex"),
    )


class GalleryEntry(Base):
    """One LOGICAL event in the generated-image gallery: this image was published, then.

    Owner and brand are NULLABLE and stay NULL when the source does not prove them. The legacy
    history index records a `domain_key` string and an owner only for entries created after accounts
    existed; the rest are genuinely unattributed. Inferring an owner from the file's directory, its
    mtime, or the account that happens to hold that domain today would manufacture provenance —
    and provenance invented once is indistinguishable from provenance measured.

    `legacy_seq` is the entry's position in the source index. It is what makes the import
    DETERMINISTIC and idempotent: the same history file re-imported produces the same rows, and the
    partial UNIQUE below refuses a second copy.
    """

    __tablename__ = "gallery_entries"

    id: Mapped[str] = _uuid_pk()
    owner_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_gallery_entries_owner_user_id"))
    brand_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="RESTRICT", name="fk_gallery_entries_brand_id"))
    checksum: Mapped[str] = mapped_column(
        Text,
        ForeignKey("gallery_blobs.checksum", ondelete="RESTRICT",
                   name="fk_gallery_entries_checksum"),
        nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    domain_key: Mapped[str | None] = mapped_column(Text)
    brand_name: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)
    style: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False,
                                                server_default=text("'{}'::jsonb"))
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'live'"))
    legacy_seq: Mapped[int | None] = mapped_column(Integer)
    imported_at: Mapped[str | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False,
                                            server_default=_NOW)

    __table_args__ = (
        CheckConstraint("origin IN ('live','legacy_import')", name="gallery_origin_is_known"),
        # An import must say it is one and must carry its position; a live entry must not pretend
        # to have either. Same rule the approvals table already enforces, for the same reason.
        CheckConstraint(
            "origin <> 'legacy_import' OR (legacy_seq IS NOT NULL AND imported_at IS NOT NULL)",
            name="legacy_gallery_imports_are_positioned",
        ),
        CheckConstraint(
            "origin <> 'live' OR (legacy_seq IS NULL AND imported_at IS NULL)",
            name="live_gallery_entries_are_not_imports",
        ),
        # Re-running the import cannot double the history.
        Index("uq_gallery_entries_legacy_seq", "legacy_seq", unique=True,
              postgresql_where=text("origin = 'legacy_import'")),
        Index("ix_gallery_entries_owner_created", "owner_user_id", "created_at"),
    )


__all__ = [
    "Approval", "Asset", "Attempt", "Brand", "BrandCompetitor", "BrandOwnerInputs",
    "CalendarPost", "GalleryBlob", "GalleryEntry", "Job", "JobEvent", "ProfileSnapshot", "Run",
    "RunDependency", "UsageRecord", "User",
    "RUN_KINDS", "CREATIVE_KINDS",
]
