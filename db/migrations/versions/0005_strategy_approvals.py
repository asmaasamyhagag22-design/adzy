"""Gate 2B: allow human decisions on exact immutable strategy Runs.

No new authority is introduced: the existing composite foreign key still proves that the subject
run belongs to the same brand and has the declared kind. This revision only widens the frozen kind
vocabulary from SWOT to SWOT + strategy.
"""
from __future__ import annotations

from alembic import op

revision = "0005_strategy_approvals"
down_revision = "0004_job_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE approvals DROP CONSTRAINT ck_approvals_subject_kind_is_known")
    op.execute("""
        ALTER TABLE approvals ADD CONSTRAINT ck_approvals_subject_kind_is_known
        CHECK (subject_kind IN ('swot','strategy'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE approvals DROP CONSTRAINT ck_approvals_subject_kind_is_known")
    op.execute("""
        ALTER TABLE approvals ADD CONSTRAINT ck_approvals_subject_kind_is_known
        CHECK (subject_kind IN ('swot'))
    """)
