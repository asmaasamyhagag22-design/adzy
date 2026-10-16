# -*- coding: utf-8 -*-
"""Schema v1.3 -> v1.4: a durable result for Jobs that intentionally have no Run.

FROZEN ON RELEASE. Literal SQL, no `Base.metadata` reflection: a migration that builds itself from
the ORM describes whatever the ORM says TODAY, so replaying history stops reproducing history.
0001, 0002 and 0003 are historical and are not touched by this revision.

WHY A NEW TABLE, AND WHY NOT ANY OF THE ALTERNATIVES

Most jobs finish by producing a Run, and for those `runs.output` IS the business result. `/api/run`
is different, and measured rather than assumed: it is a standalone capability-owned job that
deliberately has no Brand and no Run — `db/models.py::Job` says so outright, and
`api/jobs/runner.py` writes its BusinessProfile to DISK. So under PostgreSQL a succeeded `/api/run`
job had no durable answer to "what did it produce", and a process restart lost it. That was
discovered by wiring the cutover, not predicted; this revision is the schema requirement it
uncovered.

Three alternatives were rejected for concrete reasons:

  * COPY THE RUN OUTPUT INTO A RESULT TABLE FOR EVERY JOB — creates two rows that can disagree
    about one fact, with no way afterwards to say which is authoritative. Run-backed jobs keep
    their Run as the sole authority and get NO row here.

  * FABRICATE A BRAND + RUN FOR `/api/run` — puts a namespace nobody asked for into the brand
    tables, so "which brands exist" starts answering differently. That is exactly the pollution
    `Job.requested_domain_key` was introduced to avoid.

  * A JSONB RESULT COLUMN ON `jobs` — turns the job envelope into an arbitrary document store, the
    shape this schema exists to replace, and makes the result's kind and version unaddressable.

ONE RESULT PER JOB, STRUCTURALLY. `job_id` is the PRIMARY KEY, not a unique foreign key. A second
direct result is therefore unrepresentable rather than merely rejected, so a retry or a double
finalisation fails on INSERT instead of quietly producing two answers to one question.

`payload` is NOT NULL. A row here exists only to carry a result; a NULL one would assert "this job
succeeded and produced nothing", which is the false success the cutover is trying to make
unreachable. A job with no result has no row.

WHAT IS DELIBERATELY NOT A CONSTRAINT. That a job has EITHER `jobs.run_id` OR a row here, never
both, is enforced in the service layer and pinned by a test. PostgreSQL cannot express a
cross-table CHECK without a trigger, and a trigger that silently rewrote finalisation would be a
worse hazard than the invariant it guards.

CHECK names carry the `ck_<table>_<name>` convention the ORM metadata applies. Writing the bare name
would pass every behavioural test and fail the HEAD-to-ORM introspection parity check — which is
exactly the drift that check exists to catch.

Revision id length is deliberate: `alembic_version.version_num` is VARCHAR(32), and a longer id is
rejected by PostgreSQL at the bookkeeping UPDATE, after the DDL has already run.
"""
from __future__ import annotations

from alembic import op

revision = "0004_job_results"
down_revision = "0003_job_events_gallery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE job_results (
            job_id          UUID PRIMARY KEY,
            result_kind     TEXT NOT NULL,
            schema_version  INTEGER NOT NULL DEFAULT 1,
            payload         JSONB NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_job_results_job_id FOREIGN KEY (job_id)
                REFERENCES jobs (id) ON DELETE RESTRICT,
            CONSTRAINT ck_job_results_schema_version_starts_at_one CHECK (schema_version >= 1),
            CONSTRAINT ck_job_results_result_kind_is_not_blank
                CHECK (length(btrim(result_kind)) > 0)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS job_results")
