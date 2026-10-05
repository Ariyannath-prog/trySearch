"""plans, plan entitlements and organization commercial fields

Revision ID: 4c9d1f8e2b60
Revises: c8e56078e771
Create Date: 2026-10-02

Creates the commercial model. Deliberately seeds NO plans: invented prices in a
migration would land in production and make the PRD's explicitly-proposed numbers
look authoritative. Development plans come from scripts/seed_dev_plans.py, and
production plans are created by a platform admin in the admin panel.

Additive only - no drops, no type changes, no data rewrites.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '4c9d1f8e2b60'
down_revision: Union[str, Sequence[str], None] = 'c8e56078e771'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('archived_at', sa.DateTime(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='USD'),
        sa.Column('billing_interval', sa.Text(), nullable=False, server_default='monthly'),
        sa.Column('price_monthly', sa.Numeric(10, 2), nullable=True),
        sa.Column('price_annual', sa.Numeric(10, 2), nullable=True),
        sa.Column('trial_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('account_types', postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column('monthly_cost_ceiling_usd', sa.Numeric(10, 2), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
        sa.CheckConstraint("billing_interval IN ('monthly', 'annual')",
                           name='ck_plans_billing_interval'),
        sa.CheckConstraint('trial_days >= 0', name='ck_plans_trial_days'),
        sa.CheckConstraint('display_order >= 0', name='ck_plans_display_order'),
        sa.CheckConstraint("account_types <@ ARRAY['brand', 'agency']::text[]",
                           name='ck_plans_account_types'),
    )
    op.create_index('ix_plans_active_order', 'plans', ['display_order', 'id'],
                    unique=False, postgresql_where=sa.text('active'))

    op.create_table(
        'plan_entitlements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('value_type', sa.Text(), nullable=False),
        sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_id', 'key', name='uq_plan_entitlement'),
        # Type correctness as a database guarantee, not an application convention.
        sa.CheckConstraint(
            "(value_type = 'int' AND jsonb_typeof(value) = 'number') OR "
            "(value_type = 'bool' AND jsonb_typeof(value) = 'boolean') OR "
            "(value_type = 'string' AND jsonb_typeof(value) = 'string') OR "
            "(value_type = 'list' AND jsonb_typeof(value) = 'array')",
            name='ck_plan_entitlement_value',
        ),
    )
    op.create_index('ix_plan_entitlements_plan_id', 'plan_entitlements', ['plan_id'],
                    unique=False)

    # Commercial account type. Separate from the plan on purpose: a brand and an
    # agency can share a plan, and one plan may be sold to either.
    op.add_column('organizations', sa.Column(
        'account_type', sa.Text(), nullable=False, server_default='brand'))
    op.add_column('organizations', sa.Column(
        'plan_status', sa.Text(), nullable=False, server_default='none'))
    op.add_column('organizations', sa.Column('trial_ends_at', sa.DateTime(), nullable=True))
    op.add_column('organizations', sa.Column('plan_started_at', sa.DateTime(), nullable=True))

    op.create_check_constraint(
        'ck_organizations_account_type', 'organizations',
        "account_type IN ('brand', 'agency')")
    op.create_check_constraint(
        'ck_organizations_plan_status', 'organizations',
        "plan_status IN ('none', 'trialing', 'active', 'past_due', 'canceled')")

    # organizations.plan_id has existed since the tenancy migration as a bare
    # nullable integer, because plans did not exist yet. Now it does, so the
    # spec's intended foreign key becomes real. RESTRICT is what makes "a plan
    # referenced by an organization cannot be deleted" enforceable in the
    # database; archive/deactivate is the only retirement path in the API.
    #
    # Any pre-existing non-NULL plan_id would now have to point at a real plan.
    # No plan row can exist yet (the table was created in this same migration),
    # so a stale value would make this FK unsatisfiable. Fail loudly with an
    # actionable message rather than emitting a confusing constraint error.
    stale = op.get_bind().execute(sa.text(
        'SELECT count(*) FROM organizations WHERE plan_id IS NOT NULL'
    )).scalar_one()
    if stale:
        raise RuntimeError(
            f'{stale} organization row(s) have a non-NULL plan_id but no plans table '
            'existed before this migration, so those values cannot reference a real '
            'plan. Set organizations.plan_id = NULL for those rows, or create the '
            'matching plans first, then re-run this migration.'
        )

    op.create_foreign_key(
        'fk_organizations_plan_id', 'organizations', 'plans',
        ['plan_id'], ['id'], ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint('fk_organizations_plan_id', 'organizations', type_='foreignkey')
    op.drop_constraint('ck_organizations_plan_status', 'organizations', type_='check')
    op.drop_constraint('ck_organizations_account_type', 'organizations', type_='check')
    op.drop_column('organizations', 'plan_started_at')
    op.drop_column('organizations', 'trial_ends_at')
    op.drop_column('organizations', 'plan_status')
    op.drop_column('organizations', 'account_type')

    op.drop_index('ix_plan_entitlements_plan_id', table_name='plan_entitlements')
    op.drop_table('plan_entitlements')
    op.drop_index('ix_plans_active_order', table_name='plans')
    op.drop_table('plans')
