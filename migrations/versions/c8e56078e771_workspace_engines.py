"""Per-workspace engine selection for onboarding's "choose engines" step.

A workspace with no rows here behaves exactly as before this table existed -
every platform-enabled engine runs. Purely additive: no existing table or
column changes, no backfill.
"""

from alembic import op
import sqlalchemy as sa


revision = 'c8e56078e771'
down_revision = '3b8e6d14c5f7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'workspace_engines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workspace_id', sa.Integer(),
                   sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('engine_id', sa.Integer(),
                   sa.ForeignKey('engines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('workspace_id', 'engine_id', name='uq_workspace_engines_workspace_engine'),
    )


def downgrade():
    op.drop_table('workspace_engines')
