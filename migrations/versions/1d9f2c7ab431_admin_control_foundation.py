"""platform admin foundation

Revision ID: 1d9f2c7ab431
Revises: c468a55ebf16
Create Date: 2026-09-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '1d9f2c7ab431'
down_revision: Union[str, Sequence[str], None] = 'c468a55ebf16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('is_platform_admin', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.add_column(
        'users',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.add_column(
        'users',
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
    )

    op.create_table(
        'admin_audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('target_type', sa.String(length=80), nullable=True),
        sa.Column('target_id', sa.String(length=120), nullable=True),
        sa.Column(
            'details',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['actor_user_id'],
            ['users.id'],
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_admin_audit_logs_created_at',
        'admin_audit_logs',
        ['created_at'],
        unique=False,
    )
    op.create_index(
        'ix_admin_audit_logs_target',
        'admin_audit_logs',
        ['target_type', 'target_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_admin_audit_logs_actor_user_id'),
        'admin_audit_logs',
        ['actor_user_id'],
        unique=False,
    )

    op.create_table(
        'provider_credentials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=80), nullable=False),
        sa.Column('engine_id', sa.Integer(), nullable=True),
        sa.Column('label', sa.String(length=160), nullable=False),
        sa.Column('encrypted_secret', sa.Text(), nullable=False),
        sa.Column('secret_hint', sa.String(length=32), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('last_tested_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['engine_id'],
            ['engines.id'],
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'provider',
            'label',
            name='uq_provider_credentials_provider_label',
        ),
    )
    op.create_index(
        'ix_provider_credentials_engine_id',
        'provider_credentials',
        ['engine_id'],
        unique=False,
    )

    op.create_table(
        'feature_flags',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=160), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column(
            'config',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )

    op.create_table(
        'system_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=160), nullable=False),
        sa.Column('value', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('system_settings')
    op.drop_table('feature_flags')

    op.drop_index(
        'ix_provider_credentials_engine_id',
        table_name='provider_credentials',
    )
    op.drop_table('provider_credentials')

    op.drop_index(
        op.f('ix_admin_audit_logs_actor_user_id'),
        table_name='admin_audit_logs',
    )
    op.drop_index(
        'ix_admin_audit_logs_target',
        table_name='admin_audit_logs',
    )
    op.drop_index(
        'ix_admin_audit_logs_created_at',
        table_name='admin_audit_logs',
    )
    op.drop_table('admin_audit_logs')

    op.drop_column('users', 'last_login_at')
    op.drop_column('users', 'is_active')
    op.drop_column('users', 'is_platform_admin')
