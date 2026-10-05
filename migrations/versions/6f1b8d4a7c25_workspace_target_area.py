"""workspace target area and competitor provenance

Revision ID: 6f1b8d4a7c25
Revises: 5e7a3b2c9d14
Create Date: 2026-10-02

workspaces.geo and workspaces.language are NOT changed here. They keep their
meaning - geo is the primary country (ISO 3166-1 alpha-2), language is BCP-47 -
and what changes in Phase A is that application code stops hardcoding 'US'/'en'
and persists the onboarding selection instead.

Geographic *reach* is a separate axis: a single text column cannot hold a scope
plus a list of named places, which is why two fields are added rather than
overloading geo. analytics_prompt_scan_runs.region is a per-run engine locale and
is deliberately untouched.

Existing rows keep working: both new columns are NOT NULL with server defaults, so
the two live workspaces become scope='country' with no target locations, which is
exactly their current effective meaning.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '6f1b8d4a7c25'
down_revision: Union[str, Sequence[str], None] = '5e7a3b2c9d14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workspaces', sa.Column(
        'target_scope', sa.Text(), nullable=False, server_default='country'))
    op.add_column('workspaces', sa.Column(
        'target_locations', postgresql.ARRAY(sa.Text()), nullable=False,
        server_default=sa.text("'{}'::text[]")))
    op.create_check_constraint(
        'ck_workspaces_target_scope', 'workspaces',
        "target_scope IN ('city', 'region', 'country', 'worldwide')")

    # Provenance of an *active* competitor, enumerated rather than free text.
    # Every existing row predates AI suggestion attribution, so 'manual' is the
    # honest default - it does not claim a provenance that was never recorded.
    # This is not a suggestion-history system: rejected suggestions are never
    # persisted, because onboarding's preview writes nothing and approve inserts
    # only what was submitted.
    op.add_column('competitors', sa.Column(
        'source', sa.Text(), nullable=False, server_default='manual'))
    op.create_check_constraint(
        'ck_competitors_source', 'competitors',
        "source IN ('ai_suggested', 'manual', 'imported')")


def downgrade() -> None:
    op.drop_constraint('ck_competitors_source', 'competitors', type_='check')
    op.drop_column('competitors', 'source')
    op.drop_constraint('ck_workspaces_target_scope', 'workspaces', type_='check')
    op.drop_column('workspaces', 'target_locations')
    op.drop_column('workspaces', 'target_scope')
