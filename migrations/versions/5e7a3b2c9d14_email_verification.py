"""email verification, terms acceptance and case-insensitive email uniqueness

Revision ID: 5e7a3b2c9d14
Revises: 4c9d1f8e2b60
Create Date: 2026-10-02

Additive only. The one risk here is the functional unique index on lower(email):
it cannot be created if two accounts already differ only by case, so the upgrade
checks first and aborts with the offending addresses rather than failing on an
opaque index error.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5e7a3b2c9d14'
down_revision: Union[str, Sequence[str], None] = '4c9d1f8e2b60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL means unverified. Existing accounts stay NULL: this migration does not
    # retroactively declare anybody verified, and it does not lock anybody out
    # either - login keeps working, onboarding is what gets gated, in app code.
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('terms_accepted_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('terms_version', sa.Text(), nullable=True))

    op.create_table(
        'email_verification_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=False),
        # SHA-256 of the raw token. The raw value only ever exists in the email.
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('requested_ip', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
        sa.CheckConstraint("purpose IN ('email_verify', 'password_reset')",
                           name='ck_evt_purpose'),
    )
    op.create_index('ix_evt_user_purpose', 'email_verification_tokens',
                    ['user_id', 'purpose'], unique=False)

    # users.email already has a plain UNIQUE, which still permits 'A@x.com' and
    # 'a@x.com' side by side. Normalising in Python alone does not hold under
    # concurrent signups, so the invariant is enforced by the database - the same
    # reasoning as uq_extractions_current_answer. Pre-flight first so the failure
    # names the rows an operator has to fix.
    duplicates = op.get_bind().execute(sa.text(
        'SELECT lower(email) AS normalised, count(*) AS n '
        'FROM users GROUP BY lower(email) HAVING count(*) > 1 '
        'ORDER BY n DESC LIMIT 20'
    )).mappings().all()
    if duplicates:
        listed = ', '.join(f"{row['normalised']} (x{row['n']})" for row in duplicates)
        raise RuntimeError(
            'Cannot create a unique index on lower(users.email): these addresses '
            f'already exist more than once ignoring case: {listed}. Merge or rename '
            'the duplicate accounts, then re-run this migration.'
        )

    op.create_index('uq_users_email_lower', 'users', [sa.text('lower(email)')],
                    unique=True)


def downgrade() -> None:
    op.drop_index('uq_users_email_lower', table_name='users')
    op.drop_index('ix_evt_user_purpose', table_name='email_verification_tokens')
    op.drop_table('email_verification_tokens')
    op.drop_column('users', 'terms_version')
    op.drop_column('users', 'terms_accepted_at')
    op.drop_column('users', 'email_verified_at')
