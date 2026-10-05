#!/usr/bin/env python
"""Create INACTIVE example plans so the entitlement system can be exercised.

THESE ARE NOT COMMERCIAL PRICES.

The numbers below are placeholders for development and testing only. The PRD's
Starter/Growth/Concierge figures are explicitly labelled a proposal there, and the
PRD's own open-questions section still lists tier placement as undecided, so nothing
in this file should be read as a pricing decision. Final plans are created and
priced by a platform admin in the admin panel, which is the only authoritative
source.

Every plan is written with active=false. A draft plan is invisible to customers:
GET /api/plans returns only active, non-archived plans. Activating one is a
deliberate admin action, never a side effect of running this script.

Refuses to run against production. Usage:

    APP_ENV=development DATABASE_URL=postgresql://... python scripts/seed_dev_plans.py
    python scripts/seed_dev_plans.py --remove        # delete the seeded drafts again
"""

import argparse
import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Marker prefix, so --remove only ever deletes rows this script created and can
# never touch a plan an admin built by hand.
SLUG_PREFIX = 'dev-'

# (slug, name, description, price_monthly, trial_days, account_types, entitlements)
DEV_PLANS = (
    (
        f'{SLUG_PREFIX}brand-small',
        'Dev Brand Small (EXAMPLE - NOT FINAL PRICING)',
        'Development fixture for exercising brand entitlements. Not a commercial plan.',
        Decimal('10.00'), 14, ['brand'],
        {
            'workspace_limit': 1,
            'client_limit': 0,
            'team_member_limit': 2,
            'prompt_limit': 50,
            'scan_frequency': 'weekly',
            'engine_access': ['perplexity', 'openai'],
            'engine_limit': 2,
            'api_access': False,
            'reports': True,
            'alerts': False,
            'integrations': True,
            'white_label': False,
        },
    ),
    (
        f'{SLUG_PREFIX}brand-large',
        'Dev Brand Large (EXAMPLE - NOT FINAL PRICING)',
        'Development fixture with a wider entitlement set. Not a commercial plan.',
        Decimal('20.00'), 14, ['brand'],
        {
            'workspace_limit': 3,
            'client_limit': 0,
            'team_member_limit': 10,
            'prompt_limit': 150,
            'scan_frequency': 'daily',
            'engine_access': ['perplexity', 'openai', 'google_gemini', 'anthropic'],
            'engine_limit': 4,
            'api_access': True,
            'reports': True,
            'alerts': True,
            'integrations': True,
            'white_label': False,
        },
    ),
    (
        f'{SLUG_PREFIX}agency',
        'Dev Agency (EXAMPLE - NOT FINAL PRICING)',
        'Development fixture exercising agency-only entitlements. Not a commercial plan.',
        Decimal('30.00'), 0, ['agency'],
        {
            # The three agency limits are deliberately distinct, so a test can prove
            # that workspaces, clients and team members are counted separately.
            'workspace_limit': 25,
            'client_limit': 15,
            'team_member_limit': 8,
            'prompt_limit': 300,
            'scan_frequency': 'daily',
            'engine_access': ['perplexity', 'openai', 'google_gemini', 'anthropic', 'xai'],
            'engine_limit': 5,
            'api_access': True,
            'reports': True,
            'alerts': True,
            'integrations': True,
            'white_label': True,
        },
    ),
    (
        f'{SLUG_PREFIX}both',
        'Dev Shared (EXAMPLE - NOT FINAL PRICING)',
        'Development fixture available to both account types. Not a commercial plan.',
        Decimal('15.00'), 7, ['brand', 'agency'],
        {
            'workspace_limit': 2,
            'client_limit': 2,
            'team_member_limit': 4,
            'prompt_limit': 100,
            'scan_frequency': 'weekly',
            'engine_access': ['perplexity'],
            'engine_limit': 1,
            'api_access': False,
            'reports': True,
            'alerts': False,
            'integrations': False,
            'white_label': False,
        },
    ),
)


def _guard_environment(force):
    """Refuse to run anywhere that looks like production."""
    app_env = os.environ.get('APP_ENV', 'development').lower()
    if app_env == 'production' and not force:
        raise SystemExit(
            'Refusing to run: APP_ENV=production. These are example plans, not '
            'commercial pricing. Create production plans in the admin panel. '
            '(--i-know-this-is-production overrides, but you almost certainly want '
            'the admin panel instead.)'
        )
    database_url = os.environ.get('DATABASE_URL', '')
    if not database_url:
        raise SystemExit('DATABASE_URL must be set.')
    # A second, independent signal: the production database for this project is
    # named `trysearch`. APP_ENV alone is one environment variable away from wrong.
    looks_production = database_url.rstrip('/').endswith('/trysearch')
    if looks_production and not force:
        raise SystemExit(
            'Refusing to run: DATABASE_URL points at the `trysearch` database, which '
            'is production for this project. Point it at a development or test '
            'database, or create plans in the admin panel.'
        )


def seed(remove=False):
    from sqlalchemy import delete, insert, select

    from app import entitlements as ent
    from app.db import engine
    from app.models import organizations, plan_entitlements, plans

    now = datetime.utcnow()

    if remove:
        with engine.begin() as conn:
            rows = conn.execute(
                select(plans.c.id, plans.c.slug).where(plans.c.slug.like(f'{SLUG_PREFIX}%'))
            ).mappings().all()
            removed, skipped = 0, []
            for row in rows:
                in_use = conn.execute(
                    select(organizations.c.id)
                    .where(organizations.c.plan_id == row['id']).limit(1)
                ).scalar_one_or_none()
                if in_use is not None:
                    # The same rule the API enforces: a referenced plan is never
                    # deleted. Reassign the organization first.
                    skipped.append(row['slug'])
                    continue
                conn.execute(delete(plans).where(plans.c.id == row['id']))
                removed += 1
        print(f'Removed {removed} dev plan(s).')
        if skipped:
            print('Skipped (still assigned to an organization): ' + ', '.join(skipped))
        return

    created, updated = 0, 0
    with engine.begin() as conn:
        for slug, name, description, price, trial_days, account_types, entitlement_map in DEV_PLANS:
            # Validate against the registry before writing, so this script cannot
            # introduce a key the application does not enforce.
            cleaned = {
                key: ent.coerce_value(key, value)
                for key, value in entitlement_map.items()
            }
            unknown = sorted(set(entitlement_map) - set(ent.REGISTRY))
            if unknown:
                raise SystemExit(f'{slug}: undeclared entitlement keys {unknown}')

            existing = conn.execute(
                select(plans.c.id).where(plans.c.slug == slug)).scalar_one_or_none()

            values = {
                'name': name,
                'description': description,
                # Never active. A draft plan cannot reach a customer.
                'active': False,
                'display_order': 0,
                'currency': 'USD',
                'billing_interval': 'monthly',
                'price_monthly': price,
                'price_annual': None,
                'trial_days': trial_days,
                'account_types': account_types,
                'monthly_cost_ceiling_usd': Decimal('25.00'),
                'updated_at': now,
            }

            if existing is None:
                plan_id = conn.execute(insert(plans).values(
                    slug=slug, created_at=now, **values)).inserted_primary_key[0]
                created += 1
            else:
                plan_id = existing
                from sqlalchemy import update as sa_update
                conn.execute(sa_update(plans).where(plans.c.id == plan_id).values(**values))
                updated += 1

            conn.execute(delete(plan_entitlements)
                         .where(plan_entitlements.c.plan_id == plan_id))
            for key, value in sorted(cleaned.items()):
                conn.execute(insert(plan_entitlements).values(
                    plan_id=plan_id, key=key,
                    value_type=ent.REGISTRY[key].value_type, value=value,
                    created_at=now, updated_at=now))

    print(f'Seeded {created} new and refreshed {updated} existing dev plan(s), all INACTIVE.')
    print('These are development fixtures, not commercial pricing. Create real plans '
          'in /admin/plans.')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--remove', action='store_true',
                        help='delete the dev plans this script created')
    parser.add_argument('--i-know-this-is-production', action='store_true',
                        dest='force', help=argparse.SUPPRESS)
    args = parser.parse_args()
    _guard_environment(args.force)
    seed(remove=args.remove)


if __name__ == '__main__':
    main()
