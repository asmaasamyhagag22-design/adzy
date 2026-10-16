# -*- coding: utf-8 -*-
"""Schema v1.2 -> v1.3: durable job events, and the gallery as blobs + logical entries.

FROZEN. Literal SQL, no `Base.metadata` reflection: a migration that builds itself from the ORM
describes whatever the ORM says TODAY, so replaying history stops reproducing history. 0001 and
0002 are historical and are not touched by this revision.

WHY THREE TABLES

  job_events      the SSE stream had no durable form. Status lived in PostgreSQL while the events
                  explaining it lived in one process's memory, so a restart, or simply a second
                  worker, could not answer "what happened to job X".

  gallery_blobs   physical bytes, addressed by content.
  gallery_entries logical publications. Measured on the real history index: 28 entries carry 25
                  distinct checksums, so three are the same image published again. One table would
                  force a choice between losing three real events and storing bytes three times.

AUDITED AGAINST THE LIVE CONTRACT BEFORE RELEASE. The first draft of this revision stored only
(stage, status, is_terminal, payload, created_at). Reading the actual SSE surface showed that is not
enough to replay an event: the stream emits `StageEvent.model_dump_json()`, which also carries
`timestamp`, `duration_ms` and `error` — and the suite asserts on `error` and `timestamp` directly.
Storing those inside `payload` would have worked and been wrong: `payload` is caller-supplied, so a
caller with an `error` key would have silently overwritten the event's own. Three real columns
instead. `occurred_at` is the event's own moment and is distinct from `created_at`, the insert
moment; a threadsafe emit from a worker thread separates them, and collapsing the two would make
replayed timestamps drift from the ones live subscribers already received.

This revision was REVISED IN PLACE rather than superseded by a 0004. It is unreleased — untracked
by git, never committed, and applied only to the disposable test database, which every migration
suite recreates. The immutability rule protects migrations that exist in some real database's
history; 0001 and 0002 do and are untouched. Adding a 0004 to patch a mistake made minutes earlier
in the same run would put a permanent scar in the chain to protect nothing.

Revision id length is deliberate: `alembic_version.version_num` is VARCHAR(32), and a longer id is
rejected by PostgreSQL at the bookkeeping UPDATE, after the DDL has already run.
"""
from __future__ import annotations

from alembic import op

revision = "0003_job_events_gallery"
down_revision = "0002_legacy_compat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE job_events (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id       UUID NOT NULL,
            seq          INTEGER NOT NULL,
            stage        TEXT NOT NULL,
            status       TEXT NOT NULL,
            is_terminal  BOOLEAN NOT NULL DEFAULT false,
            occurred_at  TIMESTAMPTZ NOT NULL,
            duration_ms  INTEGER NOT NULL DEFAULT 0,
            error        TEXT,
            payload      JSONB,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_job_events_job_id FOREIGN KEY (job_id)
                REFERENCES jobs (id) ON DELETE CASCADE,
            CONSTRAINT uq_job_events_job_id_seq UNIQUE (job_id, seq),
            -- CHECK names carry the `ck_<table>_<name>` convention the ORM metadata applies.
            -- Writing the bare name here would pass every behavioural test and fail the HEAD-to-ORM
            -- introspection parity check, which is exactly the drift that check exists to catch.
            CONSTRAINT ck_job_events_seq_starts_at_one CHECK (seq >= 1),
            CONSTRAINT ck_job_events_duration_is_not_negative CHECK (duration_ms >= 0)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_job_events_one_terminal
            ON job_events (job_id) WHERE is_terminal
    """)
    op.execute("CREATE INDEX ix_job_events_job_id_seq ON job_events (job_id, seq)")

    op.execute("""
        CREATE TABLE gallery_blobs (
            checksum     TEXT PRIMARY KEY,
            byte_size    BIGINT NOT NULL,
            mime         TEXT NOT NULL DEFAULT 'image/png',
            storage_key  TEXT NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_gallery_blobs_blob_has_bytes CHECK (byte_size > 0),
            CONSTRAINT ck_gallery_blobs_checksum_is_sha256_hex CHECK (length(checksum) = 64)
        )
    """)

    op.execute("""
        CREATE TABLE gallery_entries (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_user_id  UUID,
            brand_id       UUID,
            checksum       TEXT NOT NULL,
            filename       TEXT NOT NULL,
            domain_key     TEXT,
            brand_name     TEXT,
            content_type   TEXT,
            style          TEXT,
            metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
            origin         TEXT NOT NULL DEFAULT 'live',
            legacy_seq     INTEGER,
            imported_at    TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_gallery_entries_owner_user_id FOREIGN KEY (owner_user_id)
                REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_gallery_entries_brand_id FOREIGN KEY (brand_id)
                REFERENCES brands (id) ON DELETE RESTRICT,
            CONSTRAINT fk_gallery_entries_checksum FOREIGN KEY (checksum)
                REFERENCES gallery_blobs (checksum) ON DELETE RESTRICT,
            CONSTRAINT ck_gallery_entries_gallery_origin_is_known
                CHECK (origin IN ('live','legacy_import')),
            CONSTRAINT ck_gallery_entries_legacy_gallery_imports_are_positioned CHECK (
                origin <> 'legacy_import'
                OR (legacy_seq IS NOT NULL AND imported_at IS NOT NULL)),
            CONSTRAINT ck_gallery_entries_live_gallery_entries_are_not_imports CHECK (
                origin <> 'live' OR (legacy_seq IS NULL AND imported_at IS NULL))
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_gallery_entries_legacy_seq
            ON gallery_entries (legacy_seq) WHERE origin = 'legacy_import'
    """)
    op.execute("""
        CREATE INDEX ix_gallery_entries_owner_created
            ON gallery_entries (owner_user_id, created_at)
    """)


def downgrade() -> None:
    # Children first: gallery_entries references gallery_blobs.
    op.execute("DROP TABLE IF EXISTS gallery_entries")
    op.execute("DROP TABLE IF EXISTS gallery_blobs")
    op.execute("DROP TABLE IF EXISTS job_events")
