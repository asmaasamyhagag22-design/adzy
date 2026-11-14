"""Bind new Campaign approvals to the two previewed PNGs and exact ZIP."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0009_campaign_artifacts"
down_revision = "0008_campaign_review"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("approvals", sa.Column("artifact_manifest", JSONB()))
    op.add_column("approvals", sa.Column("artifact_archive", sa.LargeBinary()))
    op.create_check_constraint(
        op.f("ck_approvals_artifact_bundle_is_paired"), "approvals",
        "(artifact_manifest IS NULL AND artifact_archive IS NULL) OR "
        "(subject_kind = 'campaign' AND decision = 'approved' "
        "AND artifact_manifest IS NOT NULL AND jsonb_typeof(artifact_manifest) = 'object' "
        "AND artifact_archive IS NOT NULL AND octet_length(artifact_archive) BETWEEN 1 AND 16777216)",
    )


def downgrade():
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM approvals WHERE artifact_manifest IS NOT NULL) "
        "THEN RAISE EXCEPTION 'Preserve approved Campaign artifact history'; END IF; END $$"
    )
    op.drop_constraint(op.f("ck_approvals_artifact_bundle_is_paired"), "approvals", type_="check")
    op.drop_column("approvals", "artifact_archive")
    op.drop_column("approvals", "artifact_manifest")
