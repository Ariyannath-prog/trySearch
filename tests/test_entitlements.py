"""Phase A: the entitlement layer.

The assertion that matters most is the backward-compatibility one: every
organization in production carries plan_id = NULL, so the no-plan default path is
the only path on the day this ships. If it is wrong, every existing workspace
breaks.
"""

import os
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'entitlements-test-secret'

import server_pg  # noqa: E402,F401

from sqlalchemy import delete, insert, select, update  # noqa: E402

from app import entitlements as ent  # noqa: E402
from app.config import ANALYTICS_MAX_TRACKED_PROMPTS  # noqa: E402
from app.costs import ceiling_for_org, default_ceiling  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import organizations, plan_entitlements, plans  # noqa: E402


def make_plan(slug, entitlements=None, *, active=True, account_types=('brand',),
              price=Decimal('10.00'), ceiling=None, trial_days=0):
    now = datetime.utcnow()
    with engine.begin() as conn:
        # organizations.plan_id is ON DELETE RESTRICT, so an organization created by
        # an earlier test in this module has to be detached before its plan can be
        # replaced. Fighting the constraint here would mean weakening the very
        # guarantee the suite asserts elsewhere.
        stale = [row[0] for row in conn.execute(
            select(plans.c.id).where(plans.c.slug == slug)).all()]
        if stale:
            conn.execute(update(organizations)
                         .where(organizations.c.plan_id.in_(stale))
                         .values(plan_id=None, plan_status='none'))
            conn.execute(delete(plans).where(plans.c.id.in_(stale)))
        plan_id = conn.execute(insert(plans).values(
            slug=slug, name=f'Plan {slug}', description='', active=active,
            display_order=0, currency='USD', billing_interval='monthly',
            price_monthly=price, trial_days=trial_days,
            account_types=list(account_types), monthly_cost_ceiling_usd=ceiling,
            created_at=now, updated_at=now,
        )).inserted_primary_key[0]
        for key, value in (entitlements or {}).items():
            conn.execute(insert(plan_entitlements).values(
                plan_id=plan_id, key=key, value_type=ent.REGISTRY[key].value_type,
                value=ent.coerce_value(key, value), created_at=now, updated_at=now,
            ))
    return plan_id


def make_org(name, *, plan_id=None, plan_status='none', account_type='brand',
             ceiling=None):
    with engine.begin() as conn:
        return conn.execute(insert(organizations).values(
            name=name, plan_id=plan_id, plan_status=plan_status,
            account_type=account_type, monthly_cost_ceiling_usd=ceiling,
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


class RegistryTests(unittest.TestCase):

    def test_every_registry_default_matches_its_declared_type(self):
        for key, item in ent.REGISTRY.items():
            with self.subTest(key=key):
                # coerce_value is what the admin API uses, so a default that cannot
                # survive it would be a default an admin could never re-save.
                self.assertEqual(ent.coerce_value(key, item.default), item.default)

    def test_an_undeclared_key_is_rejected(self):
        with self.assertRaises(ent.EntitlementError):
            ent.coerce_value('not_a_real_entitlement', 5)

    def test_a_boolean_is_not_accepted_as_an_integer_limit(self):
        """True is an int in Python; a checkbox must not become a limit of 1."""
        with self.assertRaises(ent.EntitlementError):
            ent.coerce_value('workspace_limit', True)

    def test_scan_frequency_is_restricted_to_what_the_scheduler_understands(self):
        for frequency in ('daily', 'weekly', 'monthly'):
            self.assertEqual(ent.coerce_value('scan_frequency', frequency), frequency)
        with self.assertRaises(ent.EntitlementError):
            ent.coerce_value('scan_frequency', 'hourly')

    def test_scan_frequency_vocabulary_matches_the_scheduler(self):
        from app.scanning import next_schedule_time

        for frequency in ent.SCAN_FREQUENCY_ORDER:
            # Raises KeyError if the scheduler cannot honour it, which would make
            # the entitlement unenforceable.
            self.assertIsNotNone(next_schedule_time(frequency))

    def test_list_values_are_deduplicated(self):
        self.assertEqual(
            ent.coerce_value('engine_access', ['perplexity', 'perplexity', 'openai']),
            ['perplexity', 'openai'])

    def test_negative_limits_below_the_unlimited_sentinel_are_rejected(self):
        self.assertEqual(ent.coerce_value('workspace_limit', ent.UNLIMITED), ent.UNLIMITED)
        with self.assertRaises(ent.EntitlementError):
            ent.coerce_value('workspace_limit', -2)


class NoPlanBackwardCompatibilityTests(unittest.TestCase):
    """The only path that exists in production today."""

    def test_prompt_limit_for_a_plan_less_org_is_the_pre_existing_global_default(self):
        org_id = make_org('No plan org')
        resolved = ent.entitlements_for_org(org_id)
        self.assertEqual(
            resolved['prompt_limit'], ANALYTICS_MAX_TRACKED_PROMPTS,
            'introducing entitlements must not reduce an existing workspace below the '
            'cap it already had (ANALYTICS_MAX_TRACKED_PROMPTS)')
        self.assertEqual(resolved['prompt_limit'], 100)

    def test_prompt_limit_default_is_not_hardcoded_separately_from_config(self):
        """The default must *be* the config value, not a copy that can drift."""
        self.assertIs(ent.REGISTRY['prompt_limit'].default, ANALYTICS_MAX_TRACKED_PROMPTS)

    def test_a_plan_less_org_resolves_every_declared_key(self):
        org_id = make_org('No plan complete')
        resolved = ent.entitlements_for_org(org_id)
        self.assertEqual(set(resolved), set(ent.REGISTRY),
                         'a missing key would be an unenforceable limit')

    def test_an_unknown_org_still_resolves_to_defaults(self):
        self.assertEqual(ent.entitlements_for_org(9_999_999), ent.defaults())


class PlanResolutionTests(unittest.TestCase):

    def test_an_active_plan_overrides_the_defaults(self):
        plan_id = make_plan('ent-active', {'workspace_limit': 7, 'prompt_limit': 250})
        org_id = make_org('Active plan org', plan_id=plan_id, plan_status='active')
        resolved = ent.entitlements_for_org(org_id)
        self.assertEqual(resolved['workspace_limit'], 7)
        self.assertEqual(resolved['prompt_limit'], 250)
        # An unset key still falls through to the registry default.
        self.assertEqual(resolved['white_label'], ent.REGISTRY['white_label'].default)

    def test_a_trialing_plan_is_in_force(self):
        plan_id = make_plan('ent-trial', {'workspace_limit': 4}, trial_days=14)
        org_id = make_org('Trialing org', plan_id=plan_id, plan_status='trialing')
        self.assertEqual(ent.entitlements_for_org(org_id)['workspace_limit'], 4)

    def test_a_canceled_plan_falls_back_to_defaults(self):
        """A cancelled organization must not keep entitlements it stopped paying for."""
        plan_id = make_plan('ent-canceled', {'workspace_limit': 50})
        org_id = make_org('Canceled org', plan_id=plan_id, plan_status='canceled')
        self.assertEqual(ent.entitlements_for_org(org_id)['workspace_limit'],
                         ent.REGISTRY['workspace_limit'].default)

    def test_a_past_due_plan_falls_back_to_defaults(self):
        plan_id = make_plan('ent-pastdue', {'workspace_limit': 50})
        org_id = make_org('Past due org', plan_id=plan_id, plan_status='past_due')
        self.assertEqual(ent.entitlements_for_org(org_id)['workspace_limit'],
                         ent.REGISTRY['workspace_limit'].default)

    def test_a_plan_with_status_none_is_not_in_force(self):
        plan_id = make_plan('ent-nostatus', {'workspace_limit': 50})
        org_id = make_org('Assigned but inert', plan_id=plan_id, plan_status='none')
        self.assertEqual(ent.entitlements_for_org(org_id)['workspace_limit'],
                         ent.REGISTRY['workspace_limit'].default)

    def test_an_undeclared_stored_key_is_ignored_rather_than_surfaced(self):
        """The registry decides what the application enforces, not the table."""
        plan_id = make_plan('ent-stale', {'workspace_limit': 3})
        now = datetime.utcnow()
        with engine.begin() as conn:
            conn.execute(insert(plan_entitlements).values(
                plan_id=plan_id, key='retired_feature', value_type='bool',
                value=True, created_at=now, updated_at=now))
        org_id = make_org('Stale key org', plan_id=plan_id, plan_status='active')
        resolved = ent.entitlements_for_org(org_id)
        self.assertNotIn('retired_feature', resolved)
        self.assertEqual(resolved['workspace_limit'], 3)


class LimitHelperTests(unittest.TestCase):

    def test_within_limit_reports_the_boundary(self):
        plan_id = make_plan('ent-limit', {'workspace_limit': 2})
        org_id = make_org('Limit org', plan_id=plan_id, plan_status='active')
        self.assertEqual(ent.within_limit(org_id, 'workspace_limit', 1), (True, 2))
        self.assertEqual(ent.within_limit(org_id, 'workspace_limit', 2), (False, 2))

    def test_unlimited_is_never_exceeded(self):
        plan_id = make_plan('ent-unlimited', {'workspace_limit': ent.UNLIMITED})
        org_id = make_org('Unlimited org', plan_id=plan_id, plan_status='active')
        allowed, limit = ent.within_limit(org_id, 'workspace_limit', 10_000)
        self.assertTrue(allowed)
        self.assertTrue(ent.is_unlimited(limit))

    def test_allows_requires_a_boolean_entitlement(self):
        org_id = make_org('Bool org')
        self.assertIs(ent.allows(org_id, 'reports'), False)
        with self.assertRaises(ent.EntitlementError):
            ent.allows(org_id, 'workspace_limit')

    def test_scan_frequency_ordering_permits_less_frequent_scans(self):
        plan_id = make_plan('ent-weekly', {'scan_frequency': 'weekly'})
        org_id = make_org('Weekly org', plan_id=plan_id, plan_status='active')
        self.assertEqual(ent.allows_scan_frequency(org_id, 'monthly')[0], True)
        self.assertEqual(ent.allows_scan_frequency(org_id, 'weekly')[0], True)
        self.assertEqual(ent.allows_scan_frequency(org_id, 'daily')[0], False)

    def test_agency_limits_are_three_independent_numbers(self):
        plan_id = make_plan(
            'ent-agency',
            {'workspace_limit': 25, 'client_limit': 15, 'team_member_limit': 8},
            account_types=('agency',))
        org_id = make_org('Agency org', plan_id=plan_id, plan_status='active',
                          account_type='agency')
        resolved = ent.entitlements_for_org(org_id)
        self.assertEqual(
            (resolved['workspace_limit'], resolved['client_limit'],
             resolved['team_member_limit']), (25, 15, 8))


class CustomerPayloadTests(unittest.TestCase):

    def test_the_plan_cost_ceiling_never_appears_in_a_customer_payload(self):
        """Infrastructure cost control is not a product feature."""
        payload = ent.customer_payload(ent.defaults())
        self.assertNotIn('monthly_cost_ceiling_usd', payload)

    def test_only_registry_keys_survive(self):
        payload = ent.customer_payload({**ent.defaults(), 'internal_thing': 1})
        self.assertNotIn('internal_thing', payload)
        self.assertEqual(set(payload), set(ent.REGISTRY))


class CostCeilingResolutionTests(unittest.TestCase):
    """org override -> plan -> env default, in that order."""

    def test_an_org_with_no_plan_and_no_override_uses_the_env_default(self):
        org_id = make_org('Ceiling default')
        self.assertEqual(ceiling_for_org(org_id), default_ceiling())

    def test_the_plan_ceiling_applies_when_the_org_has_no_override(self):
        plan_id = make_plan('ceil-plan', ceiling=Decimal('123.00'))
        org_id = make_org('Ceiling from plan', plan_id=plan_id, plan_status='active')
        self.assertEqual(ceiling_for_org(org_id), Decimal('123.00'))

    def test_an_org_override_beats_the_plan(self):
        plan_id = make_plan('ceil-plan-2', ceiling=Decimal('123.00'))
        org_id = make_org('Ceiling override', plan_id=plan_id, plan_status='active',
                          ceiling=Decimal('7.50'))
        self.assertEqual(ceiling_for_org(org_id), Decimal('7.50'))

    def test_clearing_the_override_falls_back_to_the_plan(self):
        plan_id = make_plan('ceil-plan-3', ceiling=Decimal('99.00'))
        org_id = make_org('Ceiling cleared', plan_id=plan_id, plan_status='active',
                          ceiling=Decimal('5.00'))
        with engine.begin() as conn:
            conn.execute(update(organizations).where(organizations.c.id == org_id)
                         .values(monthly_cost_ceiling_usd=None))
        self.assertEqual(ceiling_for_org(org_id), Decimal('99.00'))


if __name__ == '__main__':
    unittest.main()
