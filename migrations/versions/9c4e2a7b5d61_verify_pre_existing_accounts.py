"""treat accounts that predate email verification as verified

Revision ID: 9c4e2a7b5d61
Revises: 8b3d1f6c4e92
Create Date: 2026-10-04

Phase C introduces an email-confirmation gate on onboarding. Every account
created before this point has `email_verified_at IS NULL` simply because the
column did not exist when they signed up - not because anyone failed to confirm.
Leaving them NULL would lock existing customers out of onboarding on deploy.

So this backfills them as verified, timestamped with their own `created_at`
rather than "now", so the row does not claim a confirmation happened today.

This is the only Phase C schema/data change. No columns or tables are added:
`users.email_verified_at`, `users.terms_accepted_at`, `users.terms_version` and
the `email_verification_tokens` table all already exist from Phase A
(4c9d1f8e2b60 / 5e7a3b2c9d14), and `organizations.account_type` already exists for
the brand/agency distinction.

`terms_accepted_at` is deliberately left NULL for these accounts. They never
accepted the current terms, and inventing an acceptance timestamp would be a
false record - the kind of thing that matters precisely when someone asks for
proof. A legacy account is therefore distinguishable from a Phase C signup by
having a verification timestamp but no terms acceptance.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9c4e2a7b5d61'
down_revision: Union[str, Sequence[str], None] = '8b3d1f6c4e92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    pending = bind.execute(sa.text(
        'SELECT count(*) FROM users WHERE email_verified_at IS NULL'
    )).scalar_one()

    result = bind.execute(sa.text(
        'UPDATE users SET email_verified_at = created_at '
        'WHERE email_verified_at IS NULL'
    ))

    print(f'verify_pre_existing_accounts: {pending} account(s) had no verification '
          f'timestamp, {result.rowcount} backfilled from created_at.')


def downgrade() -> None:
    """Clear the backfill, as precisely as the data allows.

    Only rows that look like a legacy backfill are reverted: verified exactly at
    their creation instant and with no terms acceptance recorded. A genuine Phase C
    signup accepted terms and was verified strictly after being created, so it is
    left alone.

    Reverting this is in any case optional - an account marked verified is harmless
    once the gate is gone, which is why the application rollback does not depend on
    running it.
    """
    op.get_bind().execute(sa.text(
        'UPDATE users SET email_verified_at = NULL '
        'WHERE email_verified_at = created_at AND terms_accepted_at IS NULL'
    ))
