"""postgresql-backed rate limit counters

Revision ID: 8b3d1f6c4e92
Revises: 7a2c9e5b1d38
Create Date: 2026-10-02

TrySearch runs under gunicorn, so a process-local counter would reset on every
restart and would be wrong as soon as a second worker exists. Counting in
PostgreSQL is correct across workers and across restarts, and adds no new service -
Redis is not introduced for this.

Fixed windows rather than one row per event: the table holds one row per
(bucket_key, window_start), so it stays bounded and the hot path is a single
INSERT ... ON CONFLICT DO UPDATE ... RETURNING hits. Old windows are pruned by the
existing CLI worker.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8b3d1f6c4e92'
down_revision: Union[str, Sequence[str], None] = '7a2c9e5b1d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rate_limit_counters',
        sa.Column('bucket_key', sa.Text(), nullable=False),
        sa.Column('window_start', sa.DateTime(), nullable=False),
        sa.Column('hits', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('bucket_key', 'window_start'),
    )
    # Pruning scans by window, so give it its own index rather than relying on the
    # composite primary key's leading column.
    op.create_index('ix_rate_limit_window', 'rate_limit_counters', ['window_start'],
                    unique=False)


def downgrade() -> None:
    op.drop_index('ix_rate_limit_window', table_name='rate_limit_counters')
    op.drop_table('rate_limit_counters')
