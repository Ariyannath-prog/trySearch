"""Phase A: admin plan management and the customer plan list.

Covers the three invariants the admin plan API exists to guarantee: only platform
admins may write a plan, a plan referenced by an organization cannot be archived, and
the customer payload never leaks anything commercially internal.
"""

import os
import unittest
from datetime import datetime

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'plans-admin-test-secret'

import server_pg  # noqa: E402

from sqlalchemy import delete, insert, select, update  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import entitlements as ent  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    admin_audit_logs,
    memberships,
    organizations,
    plan_entitlements,
    plans,
    users,
)

PASSWORD = 'plans-admin-password-123'


def make_user(username, *, platform_admin=False):
    with engine.begin() as conn:
        existing = conn.execute(
            select(users.c.id).where(users.c.username == username)).scalar_one_or_none()
        if existing is not None:
            conn.execute(update(users).where(users.c.id == existing).values(
                is_platform_admin=platform_admin, is_active=True))
            return existing
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@plans.example',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(), is_platform_admin=platform_admin,
            is_active=True,
        )).inserted_primary_key[0]


def login(client, username):
    return client.post('/api/login',
                       json={'username': username, 'password': PASSWORD})



def _reset_test_plans(slug_pattern):
    """Remove this module's plans, detaching any organization first.

    organizations.plan_id is ON DELETE RESTRICT, so a plan an earlier test assigned
    to an organization cannot simply be deleted - which is the behaviour the suite
    asserts elsewhere. Test teardown therefore has to clear the reference rather
    than fight the constraint.
    """
    with engine.begin() as conn:
        plan_ids = [row[0] for row in conn.execute(
            select(plans.c.id).where(plans.c.slug.like(slug_pattern))).all()]
        if not plan_ids:
            return
        conn.execute(update(organizations)
                     .where(organizations.c.plan_id.in_(plan_ids))
                     .values(plan_id=None, plan_status='none'))
        conn.execute(delete(plans).where(plans.c.id.in_(plan_ids)))


class PlanAuthorizationTests(unittest.TestCase):
    """Only platform admins may touch global commercial definitions."""

    @classmethod
    def setUpClass(cls):
        cls.admin = make_user('plans_admin', platform_admin=True)
        cls.normal = make_user('plans_normal')
        # An organization *owner* is still not a platform admin. This is the
        # distinction the requirement calls out explicitly.
        with engine.begin() as conn:
            org_id = conn.execute(insert(organizations).values(
                name='Owner org', created_at=datetime.utcnow())).inserted_primary_key[0]
            conn.execute(insert(memberships).values(
                org_id=org_id, user_id=cls.normal, role='owner'))

    def test_anonymous_cannot_list_plans(self):
        with server_pg.app.test_client() as client:
            self.assertEqual(client.get('/api/admin/plans').status_code, 401)

    def test_an_organization_owner_cannot_create_a_plan(self):
        with server_pg.app.test_client() as client:
            login(client, 'plans_normal')
            response = client.post('/api/admin/plans', json={
                'name': 'Sneaky', 'account_types': ['brand']})
            self.assertEqual(
                response.status_code, 403,
                'an org owner must never be able to define global pricing')

    def test_an_organization_owner_cannot_archive_a_plan(self):
        with server_pg.app.test_client() as client:
            login(client, 'plans_normal')
            self.assertEqual(
                client.post('/api/admin/plans/1/archive').status_code, 403)

    def test_a_platform_admin_can_list_plans(self):
        with server_pg.app.test_client() as client:
            login(client, 'plans_admin')
            self.assertEqual(client.get('/api/admin/plans').status_code, 200)


class PlanLifecycleTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.admin = make_user('lifecycle_admin', platform_admin=True)

    def setUp(self):
        _reset_test_plans('t-%')

    def create(self, client, **overrides):
        payload = {
            'name': 'Test Plan',
            'slug': 't-plan',
            'account_types': ['brand'],
            'price_monthly': '49.00',
            'entitlements': {'workspace_limit': 3, 'prompt_limit': 120},
        }
        payload.update(overrides)
        return client.post('/api/admin/plans', json=payload)

    def test_a_new_plan_is_a_draft(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            response = self.create(client)
            self.assertEqual(response.status_code, 201)
            plan = response.get_json()['plan']
            self.assertFalse(
                plan['active'],
                'a newly created plan must not be purchasable until an admin activates it')

    def test_a_draft_plan_is_invisible_to_customers(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            self.create(client)
            response = client.get('/api/plans?account_type=brand')
            self.assertEqual(response.status_code, 200)
            slugs = {plan['slug'] for plan in response.get_json()['plans']}
            self.assertNotIn('t-plan', slugs)

    def test_activating_a_plan_makes_it_visible(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            plan_id = self.create(client).get_json()['plan']['id']
            patch = client.patch(f'/api/admin/plans/{plan_id}', json={'active': True})
            self.assertEqual(patch.status_code, 200, patch.get_data(as_text=True))
            listing = client.get('/api/plans?account_type=brand').get_json()['plans']
            self.assertIn('t-plan', {plan['slug'] for plan in listing})

    def test_a_plan_cannot_be_activated_without_a_price(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            plan_id = self.create(client, price_monthly=None).get_json()['plan']['id']
            response = client.patch(f'/api/admin/plans/{plan_id}', json={'active': True})
            self.assertEqual(response.status_code, 400)
            self.assertIn('price', response.get_json()['error'].lower())

    def test_a_plan_cannot_be_activated_without_an_account_type(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            plan_id = self.create(client, account_types=[]).get_json()['plan']['id']
            response = client.patch(f'/api/admin/plans/{plan_id}', json={'active': True})
            self.assertEqual(response.status_code, 400)

    def test_duplicate_slug_is_a_conflict(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            self.assertEqual(self.create(client).status_code, 201)
            self.assertEqual(self.create(client).status_code, 409)

    def test_an_undeclared_entitlement_key_is_rejected(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            response = self.create(client, entitlements={'make_me_rich': True})
            self.assertEqual(
                response.status_code, 400,
                'an undeclared key would be a limit nobody enforces')
            self.assertIn('make_me_rich', response.get_json()['error'])

    def test_a_badly_typed_entitlement_is_rejected(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            response = self.create(client, entitlements={'workspace_limit': 'lots'})
            self.assertEqual(response.status_code, 400)

    def test_money_is_rejected_beyond_two_decimal_places(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            self.assertEqual(self.create(client, price_monthly='49.999').status_code, 400)

    def test_entitlements_are_replaced_as_a_full_snapshot(self):
        """A key the admin removed must actually disappear, not linger as an override."""
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            plan_id = self.create(client).get_json()['plan']['id']
            client.patch(f'/api/admin/plans/{plan_id}',
                         json={'entitlements': {'workspace_limit': 9}})
            detail = client.get(f'/api/admin/plans/{plan_id}').get_json()['plan']
            self.assertEqual(detail['entitlements_stored'], {'workspace_limit': 9})
            # prompt_limit was dropped, so it falls back to the registry default.
            self.assertEqual(detail['entitlements']['prompt_limit'],
                             ent.REGISTRY['prompt_limit'].default)

    def test_there_is_no_delete_endpoint_for_a_plan(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            plan_id = self.create(client).get_json()['plan']['id']
            response = client.delete(f'/api/admin/plans/{plan_id}')
            self.assertIn(response.status_code, (404, 405),
                          'plans are archived, never deleted')

    def test_archiving_a_referenced_plan_is_refused(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            plan_id = self.create(client).get_json()['plan']['id']
            with engine.begin() as conn:
                conn.execute(insert(organizations).values(
                    name='On the plan', plan_id=plan_id, plan_status='active',
                    created_at=datetime.utcnow()))
            response = client.post(f'/api/admin/plans/{plan_id}/archive')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json()['organization_count'], 1)

    def test_archiving_an_unreferenced_plan_also_deactivates_it(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            plan_id = self.create(client).get_json()['plan']['id']
            client.patch(f'/api/admin/plans/{plan_id}', json={'active': True})
            response = client.post(f'/api/admin/plans/{plan_id}/archive')
            self.assertEqual(response.status_code, 200)
            plan = response.get_json()['plan']
            self.assertIsNotNone(plan['archived_at'])
            self.assertFalse(plan['active'],
                             'an archived plan must never stay purchasable')

    def test_an_archived_plan_cannot_be_edited(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            plan_id = self.create(client).get_json()['plan']['id']
            client.post(f'/api/admin/plans/{plan_id}/archive')
            response = client.patch(f'/api/admin/plans/{plan_id}', json={'name': 'New'})
            self.assertEqual(response.status_code, 409)

    def test_restore_returns_the_plan_as_a_draft(self):
        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            plan_id = self.create(client).get_json()['plan']['id']
            client.patch(f'/api/admin/plans/{plan_id}', json={'active': True})
            client.post(f'/api/admin/plans/{plan_id}/archive')
            response = client.post(f'/api/admin/plans/{plan_id}/restore')
            self.assertEqual(response.status_code, 200)
            plan = response.get_json()['plan']
            self.assertIsNone(plan['archived_at'])
            self.assertFalse(plan['active'],
                             'restore must not silently put a plan back on sale')

    def test_the_database_refuses_to_delete_a_referenced_plan(self):
        """Belt and braces: ON DELETE RESTRICT, independent of the API."""
        from sqlalchemy.exc import IntegrityError

        with server_pg.app.test_client() as client:
            login(client, 'lifecycle_admin')
            plan_id = self.create(client).get_json()['plan']['id']
        with engine.begin() as conn:
            conn.execute(insert(organizations).values(
                name='Blocks delete', plan_id=plan_id, plan_status='active',
                created_at=datetime.utcnow()))
        with self.assertRaises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(delete(plans).where(plans.c.id == plan_id))


class PlanAuditTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.admin = make_user('audit_admin', platform_admin=True)

    def recent_actions(self, plan_id):
        with engine.connect() as conn:
            return [row[0] for row in conn.execute(
                select(admin_audit_logs.c.action)
                .where((admin_audit_logs.c.target_type == 'plan')
                       & (admin_audit_logs.c.target_id == str(plan_id)))
                .order_by(admin_audit_logs.c.id)
            ).all()]

    def test_every_plan_mutation_is_audited_with_a_distinct_action(self):
        with server_pg.app.test_client() as client:
            login(client, 'audit_admin')
            created = client.post('/api/admin/plans', json={
                'name': 'Audited', 'slug': 't-audited', 'account_types': ['brand'],
                'price_monthly': '10.00', 'entitlements': {'workspace_limit': 1},
            })
            plan_id = created.get_json()['plan']['id']
            client.patch(f'/api/admin/plans/{plan_id}', json={'active': True})
            client.patch(f'/api/admin/plans/{plan_id}', json={'active': False})
            client.patch(f'/api/admin/plans/{plan_id}',
                         json={'entitlements': {'workspace_limit': 5}})
            client.post(f'/api/admin/plans/{plan_id}/archive')

        actions = self.recent_actions(plan_id)
        self.assertEqual(actions, [
            'plan.created', 'plan.activated', 'plan.deactivated',
            'plan.entitlements.updated', 'plan.archived',
        ])

    def test_a_price_change_records_the_before_and_after(self):
        with server_pg.app.test_client() as client:
            login(client, 'audit_admin')
            plan_id = client.post('/api/admin/plans', json={
                'name': 'Priced', 'slug': 't-priced', 'account_types': ['brand'],
                'price_monthly': '10.00',
            }).get_json()['plan']['id']
            client.patch(f'/api/admin/plans/{plan_id}', json={'price_monthly': '20.00'})

        with engine.connect() as conn:
            details = conn.execute(
                select(admin_audit_logs.c.details)
                .where((admin_audit_logs.c.target_type == 'plan')
                       & (admin_audit_logs.c.target_id == str(plan_id))
                       & (admin_audit_logs.c.action == 'plan.updated'))
                .order_by(admin_audit_logs.c.id.desc()).limit(1)
            ).scalar_one()
        self.assertEqual(details['before']['price_monthly'], '10.00')
        self.assertEqual(details['after']['price_monthly'], '20.00')

    def test_the_audit_log_is_readable_through_the_api(self):
        with server_pg.app.test_client() as client:
            login(client, 'audit_admin')
            response = client.get('/api/admin/audit-logs?limit=5')
            self.assertEqual(response.status_code, 200)
            body = response.get_json()
            self.assertIn('events', body)
            self.assertLessEqual(len(body['events']), 5)
            self.assertNotIn('user_agent', body['events'][0] if body['events'] else {})


class CustomerPlanListTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.admin = make_user('customer_list_admin', platform_admin=True)
        cls.customer = make_user('customer_list_user')

    def setUp(self):
        _reset_test_plans('c-%')

    def make_active_plan(self, client, slug, account_types, **overrides):
        payload = {
            'name': f'Plan {slug}', 'slug': slug, 'account_types': account_types,
            'price_monthly': '25.00', 'entitlements': {'workspace_limit': 2},
        }
        payload.update(overrides)
        plan_id = client.post('/api/admin/plans', json=payload).get_json()['plan']['id']
        client.patch(f'/api/admin/plans/{plan_id}', json={'active': True})
        return plan_id

    def test_plans_are_filtered_by_account_type(self):
        with server_pg.app.test_client() as client:
            login(client, 'customer_list_admin')
            self.make_active_plan(client, 'c-brand-only', ['brand'])
            self.make_active_plan(client, 'c-agency-only', ['agency'])
            self.make_active_plan(client, 'c-both', ['brand', 'agency'])

        with server_pg.app.test_client() as client:
            login(client, 'customer_list_user')
            brand = {p['slug'] for p in
                     client.get('/api/plans?account_type=brand').get_json()['plans']}
            agency = {p['slug'] for p in
                      client.get('/api/plans?account_type=agency').get_json()['plans']}

        self.assertIn('c-brand-only', brand)
        self.assertNotIn('c-agency-only', brand)
        self.assertIn('c-both', brand)
        self.assertIn('c-agency-only', agency)
        self.assertNotIn('c-brand-only', agency)

    def test_the_customer_payload_withholds_internal_commercial_fields(self):
        with server_pg.app.test_client() as client:
            login(client, 'customer_list_admin')
            self.make_active_plan(client, 'c-internal', ['brand'],
                                  monthly_cost_ceiling_usd='500.00',
                                  price_annual='250.00')

        with server_pg.app.test_client() as client:
            login(client, 'customer_list_user')
            plans_payload = client.get('/api/plans?account_type=brand').get_json()['plans']

        plan = next(p for p in plans_payload if p['slug'] == 'c-internal')
        self.assertNotIn('monthly_cost_ceiling_usd', plan,
                         'infrastructure cost control is not a customer-facing field')
        self.assertNotIn('price_annual', plan,
                         'annual pricing is not sold in this phase')
        self.assertEqual(plan['billing_interval'], 'monthly')
        self.assertNotIn('monthly_cost_ceiling_usd', plan['entitlements'])

    def test_the_customer_payload_exposes_the_resolved_entitlements(self):
        with server_pg.app.test_client() as client:
            login(client, 'customer_list_admin')
            self.make_active_plan(client, 'c-ent', ['brand'],
                                  entitlements={'workspace_limit': 4})

        with server_pg.app.test_client() as client:
            login(client, 'customer_list_user')
            plan = next(p for p in
                        client.get('/api/plans?account_type=brand').get_json()['plans']
                        if p['slug'] == 'c-ent')
        self.assertEqual(plan['entitlements']['workspace_limit'], 4)
        self.assertEqual(set(plan['entitlements']), set(ent.REGISTRY))

    def test_an_archived_plan_disappears_from_the_customer_list(self):
        with server_pg.app.test_client() as client:
            login(client, 'customer_list_admin')
            plan_id = self.make_active_plan(client, 'c-archived', ['brand'])
            client.post(f'/api/admin/plans/{plan_id}/archive')

        with server_pg.app.test_client() as client:
            login(client, 'customer_list_user')
            slugs = {p['slug'] for p in
                     client.get('/api/plans?account_type=brand').get_json()['plans']}
        self.assertNotIn('c-archived', slugs)

    def test_an_invalid_account_type_is_rejected(self):
        with server_pg.app.test_client() as client:
            login(client, 'customer_list_user')
            self.assertEqual(
                client.get('/api/plans?account_type=enterprise').status_code, 400)

    def test_the_customer_list_requires_authentication(self):
        with server_pg.app.test_client() as client:
            self.assertEqual(client.get('/api/plans').status_code, 401)


class OrganizationPlanAssignmentTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.admin = make_user('org_assign_admin', platform_admin=True)

    def test_assigning_a_plan_sets_a_status_so_entitlements_take_effect(self):
        with server_pg.app.test_client() as client:
            login(client, 'org_assign_admin')
            plan_id = client.post('/api/admin/plans', json={
                'name': 'Assignable', 'slug': 't-assign', 'account_types': ['brand'],
                'price_monthly': '30.00', 'entitlements': {'workspace_limit': 6},
            }).get_json()['plan']['id']

            with engine.begin() as conn:
                org_id = conn.execute(insert(organizations).values(
                    name='Assign target', created_at=datetime.utcnow(),
                )).inserted_primary_key[0]

            response = client.patch(f'/api/admin/organizations/{org_id}',
                                    json={'plan_id': plan_id})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.get_json()['organization']['plan_status'], 'active')

        self.assertEqual(ent.entitlements_for_org(org_id)['workspace_limit'], 6)

    def test_a_plan_cannot_be_assigned_to_the_wrong_account_type(self):
        with server_pg.app.test_client() as client:
            login(client, 'org_assign_admin')
            plan_id = client.post('/api/admin/plans', json={
                'name': 'Agency only', 'slug': 't-agency-only',
                'account_types': ['agency'], 'price_monthly': '30.00',
            }).get_json()['plan']['id']

            with engine.begin() as conn:
                org_id = conn.execute(insert(organizations).values(
                    name='Brand org', account_type='brand',
                    created_at=datetime.utcnow())).inserted_primary_key[0]

            response = client.patch(f'/api/admin/organizations/{org_id}',
                                    json={'plan_id': plan_id})
            self.assertEqual(response.status_code, 409)

    def test_an_archived_plan_cannot_be_assigned(self):
        with server_pg.app.test_client() as client:
            login(client, 'org_assign_admin')
            plan_id = client.post('/api/admin/plans', json={
                'name': 'Archived target', 'slug': 't-archived-assign',
                'account_types': ['brand'], 'price_monthly': '30.00',
            }).get_json()['plan']['id']
            client.post(f'/api/admin/plans/{plan_id}/archive')

            with engine.begin() as conn:
                org_id = conn.execute(insert(organizations).values(
                    name='Wants archived', created_at=datetime.utcnow(),
                )).inserted_primary_key[0]

            self.assertEqual(
                client.patch(f'/api/admin/organizations/{org_id}',
                             json={'plan_id': plan_id}).status_code, 409)

    def test_a_non_platform_admin_cannot_assign_a_plan(self):
        normal = make_user('org_assign_normal')
        with engine.begin() as conn:
            org_id = conn.execute(insert(organizations).values(
                name='Self serve', created_at=datetime.utcnow())).inserted_primary_key[0]
            conn.execute(insert(memberships).values(
                org_id=org_id, user_id=normal, role='owner'))

        with server_pg.app.test_client() as client:
            login(client, 'org_assign_normal')
            self.assertEqual(
                client.patch(f'/api/admin/organizations/{org_id}',
                             json={'plan_id': 1}).status_code, 403,
                'an org owner must not be able to grant themselves a plan')


if __name__ == '__main__':
    unittest.main()
