"""Auditable unused reservation adjustment; no settlement or request-count reset."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '0007_unused_reservations'
down_revision = '0006_poster_operations'
branch_labels = depends_on = None

def upgrade():
    op.create_table('reservation_adjustments',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('run_id', UUID(as_uuid=True), sa.ForeignKey('runs.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('owner_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('version', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('before_usd', sa.Numeric(20,9), nullable=False),
        sa.Column('after_usd', sa.Numeric(20,9), nullable=False),
        sa.Column('released_usd', sa.Numeric(20,9), nullable=False),
        sa.Column('exposure', JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('run_id','version',name='uq_reservation_adjustment_version'),
        sa.CheckConstraint('before_usd >= after_usd AND after_usd >= 0 AND released_usd = before_usd - after_usd',name='valid_reservation_release'))

def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM reservation_adjustments) THEN RAISE EXCEPTION 'Preserve reservation adjustment history'; END IF; END $$")
    op.drop_table('reservation_adjustments')
