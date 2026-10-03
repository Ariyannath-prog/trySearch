"""explicit workspace-level access for client viewers

Revision ID: 7a2c9e5b1d38
Revises: 6f1b8d4a7c25
Create Date: 2026-10-02

Why this table exists
---------------------
tenancy.require_workspace() joins memberships on org_id, so *any* membership row
grants access to *every* workspace in that organization. client_viewer is excluded
from WRITE_ROLES, which restricts writes but not visibility. For an agency that
means one client could read another client's workspace. This is the smallest
mechanism that fixes it without weakening anything else:

    owner / admin / member -> org membership alone grants the workspace (unchanged)
    client_viewer          -> org membership AND a row here for that workspace

memberships remains the only place a role is stored; this table only narrows.

Backward compatibility
----------------------
Enforcement is "no row means no access", which would silently revoke access from
any client_viewer that already exists. So every pre-existing client_viewer is
backfilled with an explicit grant to every workspace in their organization - their
effective access after this migration is byte-for-byte what it was before.

Only *new* client_viewer assignments require an explicit grant, which is the
intended behaviour going forward. Production was inspected before writing this and
has zero client_viewer memberships, so the backfill is expected to insert nothing;
it is written to be correct anyway rather than relying on that observation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7a2c9e5b1d38'
down_revision: Union[str, Sequence[str], None] = '6f1b8d4a7c25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workspace_access',
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('granted_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('workspace_id', 'user_id'),
    )
    op.create_index('ix_workspace_access_user', 'workspace_access', ['user_id'],
                    unique=False)

    # Grandfather every client_viewer that already exists, so enforcement cannot
    # revoke access that was previously granted. granted_by stays NULL: no
    # administrator performed this grant, the migration did.
    bind = op.get_bind()
    existing = bind.execute(sa.text(
        "SELECT count(*) FROM memberships WHERE role = 'client_viewer'"
    )).scalar_one()

    result = bind.execute(sa.text(
        'INSERT INTO workspace_access (workspace_id, user_id, granted_by, created_at) '
        'SELECT w.id, m.user_id, NULL, CURRENT_TIMESTAMP '
        'FROM memberships m '
        'JOIN workspaces w ON w.org_id = m.org_id '
        "WHERE m.role = 'client_viewer' "
        'ON CONFLICT (workspace_id, user_id) DO NOTHING'
    ))
    print(
        f'workspace_access: {existing} existing client_viewer membership(s) found, '
        f'{result.rowcount} compatibility grant(s) inserted.'
    )


def downgrade() -> None:
    op.drop_index('ix_workspace_access_user', table_name='workspace_access')
    op.drop_table('workspace_access')
