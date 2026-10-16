# -*- coding: utf-8 -*-
"""Legacy-compatibility columns + explicit currentness.

Adds three things the file-store migration proved it needs, and nothing else. Like 0001 this
revision is FROZEN: the statements are literal text and it imports nothing from `db.models`, so
editing the models later cannot change what it does to a database.

--- 1. approvals may record an IMPORTED signature ------------------------------------------------
Three brands carry `swot_approved = true`. The approval is a real human decision and a real gate:
`/api/strategy/generate` returns 409 without it. But the file store records only the boolean — not
who approved, not when — and one of the three brands is UNCLAIMED, so no actor exists even in
principle.

`approvals.decided_at` was NOT NULL DEFAULT now(). Importing under that column would stamp a
historical signature with the migration's clock, which is fabricated evidence. Dropping the three
would destroy a real decision and silently re-close the strategy gate on three brands.

So `decided_at` becomes nullable and `origin` decides who may leave it empty. A LIVE approval must
still name both its actor and its moment — audited first: `POST /api/swot/approve` is the only
write path, it sits behind `require_user`, and no system-generated approval exists anywhere in the
product.

--- 2. users.legacy_user_id ----------------------------------------------------------------------
The file store issued `secrets.token_hex(8)` — 16 hex characters, not a UUID — so a migrated
account cannot keep its old id as a primary key. UUID remains the identity. This column records
where the account came from, and pays for itself three times: audit trail, migration idempotency
key, and an indexed lookup for JWTs already issued with `sub = <legacy id>` (7-day TTL), so nobody
is forced to log in again at cutover.

--- 3. runs.kind_seq -----------------------------------------------------------------------------
"Which SWOT is current" cannot be answered by `ORDER BY created_at DESC`. Imported runs truthfully
carry created_at = NULL, and PostgreSQL sorts NULLS FIRST on DESC — so a legacy run with no
timestamp would outrank every live run ever generated. Between two live runs created_at is
transaction-START time and can invert against commit order, so it cannot settle a race either.

`kind_seq` is allocated per (brand, kind) under the brand row lock — the same serialisation point
that already allocates profile versions — and NULL means "predates the sequence", which only an
import may claim. `ORDER BY kind_seq DESC NULLS LAST` is then correct in every case.

Revision ID: 0002_legacy_compat
Revises: 0001_initial_schema

NOTE ON THE IDENTIFIER. Alembic stores the current revision in `alembic_version.version_num`,
which it creates as VARCHAR(32). The first draft of this revision was named
`0002_legacy_compat_and_currentness` — 34 characters — and PostgreSQL rejected the bookkeeping
UPDATE at the very end of the upgrade with StringDataRightTruncation. The whole chain rolled back
cleanly (alembic runs it in one transaction), but the lesson is that a revision id has a hard
32-character budget. The file keeps the descriptive name; the ID is the short form.
"""
from __future__ import annotations

from alembic import op

revision = "0002_legacy_compat"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


UPGRADE_SQL: tuple[str, ...] = (
    # --- approvals ---------------------------------------------------------------
    """ALTER TABLE approvals ALTER COLUMN decided_at DROP NOT NULL""",
    """ALTER TABLE approvals ALTER COLUMN decided_at DROP DEFAULT""",
    """ALTER TABLE approvals ADD COLUMN origin TEXT DEFAULT 'live' NOT NULL""",
    """ALTER TABLE approvals ADD COLUMN imported_at TIMESTAMP WITH TIME ZONE""",
    """ALTER TABLE approvals ADD CONSTRAINT ck_approvals_origin_is_known
       CHECK (origin IN ('live', 'legacy_import'))""",
    """ALTER TABLE approvals ADD CONSTRAINT ck_approvals_live_approvals_name_actor_and_time
       CHECK (origin <> 'live' OR (decided_at IS NOT NULL AND decided_by_user_id IS NOT NULL
              AND imported_at IS NULL))""",
    """ALTER TABLE approvals ADD CONSTRAINT
       ck_approvals_legacy_imports_have_no_fabricated_metadata
       CHECK (origin <> 'legacy_import' OR (decided_by_user_id IS NULL AND decided_at IS NULL
              AND imported_at IS NOT NULL))""",
    # At most ONE imported approval per run: with decided_at nullable the composite UNIQUE cannot
    # catch duplicates (NULLs are distinct), so a re-run of the migration would double them.
    """CREATE UNIQUE INDEX uq_approvals_one_import_per_run ON approvals (subject_run_id)
       WHERE origin = 'legacy_import'""",

    # --- users -------------------------------------------------------------------
    """ALTER TABLE users ADD COLUMN legacy_user_id TEXT""",
    """ALTER TABLE users ADD CONSTRAINT uq_users_legacy_user_id UNIQUE (legacy_user_id)""",
    """ALTER TABLE users ADD CONSTRAINT ck_users_legacy_user_id_is_16_hex
       CHECK (legacy_user_id IS NULL OR legacy_user_id ~ '^[0-9a-f]{16}$')""",

    # --- runs --------------------------------------------------------------------
    """ALTER TABLE runs ADD COLUMN kind_seq INTEGER""",
    """ALTER TABLE runs ADD CONSTRAINT ck_runs_runtime_runs_allocate_a_sequence
       CHECK (origin = 'legacy_import' OR kind_seq IS NOT NULL)""",
    """ALTER TABLE runs ADD CONSTRAINT ck_runs_kind_seq_starts_at_one
       CHECK (kind_seq IS NULL OR kind_seq >= 1)""",
    """ALTER TABLE runs ADD CONSTRAINT uq_runs_brand_id_kind_kind_seq
       UNIQUE (brand_id, kind, kind_seq)""",
    """CREATE INDEX ix_runs_current_by_kind ON runs (brand_id, kind, kind_seq)""",
)


# Reverses exactly what this revision added, in reverse order. It restores the NOT NULL and the
# DEFAULT on approvals.decided_at, so downgrading a database that holds IMPORTED approvals with a
# NULL decided_at will fail loudly rather than silently rewriting three historical signatures with
# the clock. That refusal is the correct behaviour: the operator must decide what happens to them.
DOWNGRADE_SQL: tuple[str, ...] = (
    """DROP INDEX ix_runs_current_by_kind""",
    """ALTER TABLE runs DROP CONSTRAINT uq_runs_brand_id_kind_kind_seq""",
    """ALTER TABLE runs DROP CONSTRAINT ck_runs_kind_seq_starts_at_one""",
    """ALTER TABLE runs DROP CONSTRAINT ck_runs_runtime_runs_allocate_a_sequence""",
    """ALTER TABLE runs DROP COLUMN kind_seq""",
    """ALTER TABLE users DROP CONSTRAINT ck_users_legacy_user_id_is_16_hex""",
    """ALTER TABLE users DROP CONSTRAINT uq_users_legacy_user_id""",
    """ALTER TABLE users DROP COLUMN legacy_user_id""",
    """DROP INDEX uq_approvals_one_import_per_run""",
    """ALTER TABLE approvals DROP CONSTRAINT
       ck_approvals_legacy_imports_have_no_fabricated_metadata""",
    """ALTER TABLE approvals DROP CONSTRAINT ck_approvals_live_approvals_name_actor_and_time""",
    """ALTER TABLE approvals DROP CONSTRAINT ck_approvals_origin_is_known""",
    """ALTER TABLE approvals DROP COLUMN imported_at""",
    """ALTER TABLE approvals DROP COLUMN origin""",
    """ALTER TABLE approvals ALTER COLUMN decided_at SET DEFAULT now()""",
    """ALTER TABLE approvals ALTER COLUMN decided_at SET NOT NULL""",
)


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_SQL:
        op.execute(statement)
