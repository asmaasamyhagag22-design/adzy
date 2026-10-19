"""Owner-scoped poster operations on Runs and subordinate dispatch evidence."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
revision = '0006_poster_operations'
down_revision = '0005_strategy_approvals'
branch_labels = depends_on = None

def upgrade():
    for name in ('operation_key','request_hash','operation_state','operation_reason'):
        op.add_column('runs',sa.Column(name,sa.Text()))
    for name in ('reserved_usd','estimated_usd','known_usage_usd','settled_usd'):
        op.add_column('runs',sa.Column(name,sa.Numeric(20,9)))
    op.create_unique_constraint('uq_runs_owner_operation','runs',['created_by_user_id','operation_key'])
    op.create_check_constraint('ck_runs_reservation_nonnegative','runs','reserved_usd IS NULL OR reserved_usd >= 0')
    op.alter_column('usage_records','cost_usd',type_=sa.Numeric(20,9),nullable=True)
    for name in ('input_tokens','output_tokens','thoughts_tokens'):
        op.alter_column('usage_records',name,nullable=True)
    for name in ('state','provider_reference'):
        op.add_column('usage_records',sa.Column(name,sa.Text()))
    op.add_column('usage_records',sa.Column('estimate_usd',sa.Numeric(20,9)))
    op.add_column('usage_records',sa.Column('usage',JSONB()))


def downgrade():
    # Do not destroy unresolved accounting or silently turn unknown cost into zero.
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM usage_records WHERE cost_usd IS NULL) THEN RAISE EXCEPTION 'Resolve unknown costs before downgrade'; END IF; END $$")
    for name in ('state','provider_reference','estimate_usd','usage'):
        op.drop_column('usage_records',name)
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM usage_records WHERE input_tokens IS NULL OR output_tokens IS NULL OR thoughts_tokens IS NULL OR cost_usd <> round(cost_usd,6)) THEN RAISE EXCEPTION 'Resolve unknown token usage or precision before downgrade'; END IF; END $$")
    op.alter_column('usage_records','cost_usd',type_=sa.Numeric(12,6),nullable=False)
    for name in ('input_tokens','output_tokens','thoughts_tokens'):
        op.alter_column('usage_records',name,nullable=False)
    op.drop_constraint('uq_runs_owner_operation','runs')
    op.drop_constraint('ck_runs_reservation_nonnegative','runs')
    for name in ('operation_key','request_hash','operation_state','operation_reason','reserved_usd','estimated_usd','known_usage_usd','settled_usd'):
        op.drop_column('runs',name)
