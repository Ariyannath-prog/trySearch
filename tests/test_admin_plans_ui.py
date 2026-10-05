"""Phase B: the admin plan management UI.

Two things are worth testing about a page that is mostly JavaScript:

* **Who can open it.** The page route is a separate authorization surface from the
  API, and getting it wrong would expose commercial controls to ordinary users.
* **That its wiring still exists.** The template is inert HTML until its JS calls
  the Phase A endpoints. Asserting the page references the endpoints it needs, and
  that those endpoints exist and are admin-gated, catches the realistic regression:
  someone renames a route and the page silently stops working.

The CRUD lifecycle is driven through exactly the endpoints the page calls, in the
order the page calls them, so the test fails if that contract breaks.
"""

import os
import unittest
from datetime import datetime

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'admin-plans-ui-test-secret'

import server_pg  # noqa: E402

from sqlalchemy import delete, insert, select, update  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import entitlements as ent  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import memberships, organizations, plans, users  # noqa: E402

PASSWORD = 'plans-ui-password-123'

# The endpoints templates/admin/plans.html drives. Kept here so a rename has to be
# made deliberately in two places rather than silently breaking the page.
UI_ENDPOINTS = (
    ('GET', '/api/admin/entitlement-keys'),
    ('GET', '/api/admin/plans'),
    ('POST', '/api/admin/plans'),
)


def make_user(username, *, platform_admin=False, active=True):
    with engine.begin() as conn:
        existing = conn.execute(
            select(users.c.id).where(users.c.username == username)).scalar_one_or_none()
        if existing is not None:
            conn.execute(update(users).where(users.c.id == existing).values(
                is_platform_admin=platform_admin, is_active=active))
            return existing
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@plansui.example',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(), is_platform_admin=platform_admin,
            is_active=active,
        )).inserted_primary_key[0]


def login(client, username):
    return client.post('/api/login',
                       json={'username': username, 'password': PASSWORD})


def reset_ui_plans():
    """Remove this module's plans, detaching organizations first.

    organizations.plan_id is ON DELETE RESTRICT, which is the behaviour the suite
    asserts elsewhere, so teardown clears the reference rather than fighting it.
    """
    with engine.begin() as conn:
        ids = [row[0] for row in conn.execute(
            select(plans.c.id).where(plans.c.slug.like('ui-%'))).all()]
        if not ids:
            return
        conn.execute(update(organizations)
                     .where(organizations.c.plan_id.in_(ids))
                     .values(plan_id=None, plan_status='none'))
        conn.execute(delete(plans).where(plans.c.id.in_(ids)))


class PlanPageAuthorizationTests(unittest.TestCase):
    """/admin/plans is platform-admin only."""

    @classmethod
    def setUpClass(cls):
        cls.admin = make_user('plansui_admin', platform_admin=True)
        cls.normal = make_user('plansui_normal')
        # An organization owner is still not a platform admin.
        with engine.begin() as conn:
            org_id = conn.execute(insert(organizations).values(
                name='Plans UI org', created_at=datetime.utcnow(),
            )).inserted_primary_key[0]
            conn.execute(insert(memberships).values(
                org_id=org_id, user_id=cls.normal, role='owner'))

    def test_anonymous_is_redirected_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/admin/plans')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/login'),
                        response.headers.get('Location'))

    def test_a_signed_in_non_admin_is_redirected_away(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_normal')
            response = client.get('/admin/plans')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/'),
                        'an organization owner must not reach plan management')

    def test_a_deactivated_platform_admin_cannot_reach_it(self):
        make_user('plansui_suspended', platform_admin=True, active=True)
        with server_pg.app.test_client() as client:
            self.assertEqual(login(client, 'plansui_suspended').status_code, 200)
            make_user('plansui_suspended', platform_admin=True, active=False)
            response = client.get('/admin/plans')
        self.assertEqual(response.status_code, 302,
                         'deactivating an admin must revoke the page too')

    def test_a_platform_admin_gets_the_page(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_admin')
            response = client.get('/admin/plans')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['Content-Type'])


class PlanPageWiringTests(unittest.TestCase):
    """The page must still reference the API it depends on."""

    @classmethod
    def setUpClass(cls):
        cls.admin = make_user('plansui_wiring', platform_admin=True)

    def page(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_wiring')
            return client.get('/admin/plans').get_data(as_text=True)

    def test_the_page_references_every_endpoint_it_drives(self):
        html = self.page()
        for path in ('/api/admin/entitlement-keys', '/api/admin/plans'):
            self.assertIn(path, html, f'page no longer calls {path}')
        for suffix in ("'/archive'", "'/restore'"):
            self.assertIn(suffix, html, f'page no longer calls {suffix}')

    def test_mutations_go_through_the_csrf_aware_helper(self):
        """adminFetch attaches the CSRF token; a bare fetch() would be refused."""
        html = self.page()
        self.assertIn('adminFetch(', html)
        script = html.split('{% raw %}')[0]
        self.assertNotIn("await fetch('/api/admin/plans", script,
                         'mutating calls must use adminFetch, not bare fetch')

    def test_the_page_carries_a_csrf_token(self):
        self.assertIn('window.TS_CSRF', self.page())

    def test_no_price_or_plan_name_is_hardcoded_in_the_page(self):
        """Commercial values must come from the backend, never the template.

        A bare '$' is deliberately not checked: JS template literals use ${...}
        throughout, so it would match constantly and say nothing. What matters is
        that no plan name or price figure appears anywhere in the markup - not even
        as an input placeholder, which is how a suggested price creeps in.
        """
        html = self.page()
        for forbidden in ('149', '449', '2000', 'Starter', 'Growth', 'Concierge',
                          'Enterprise', 'Free Trial'):
            self.assertNotIn(forbidden, html,
                             f'{forbidden!r} must not be hardcoded in the plan UI')

    def test_the_admin_nav_links_to_plans(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_wiring')
            overview = client.get('/admin/').get_data(as_text=True)
        self.assertIn('href="/admin/plans"', overview)


class UiEndpointAuthorizationTests(unittest.TestCase):
    """Every endpoint the page drives is platform-admin only."""

    @classmethod
    def setUpClass(cls):
        cls.normal = make_user('plansui_api_normal')

    def test_anonymous_is_401_on_every_ui_endpoint(self):
        with server_pg.app.test_client() as client:
            for method, path in UI_ENDPOINTS:
                with self.subTest(route=f'{method} {path}'):
                    response = client.open(path, method=method, json={})
                    self.assertEqual(response.status_code, 401)

    def test_a_non_admin_is_403_on_every_ui_endpoint(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_api_normal')
            for method, path in UI_ENDPOINTS:
                with self.subTest(route=f'{method} {path}'):
                    response = client.open(path, method=method, json={})
                    self.assertEqual(response.status_code, 403)


class EntitlementRegistryContractTests(unittest.TestCase):
    """The editor renders exactly what the enforcement layer declares."""

    @classmethod
    def setUpClass(cls):
        cls.admin = make_user('plansui_registry', platform_admin=True)

    def test_every_enforced_key_is_offered_to_the_editor(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_registry')
            body = client.get('/api/admin/entitlement-keys').get_json()
        offered = {item['key'] for item in body['keys']}
        self.assertEqual(
            offered, set(ent.REGISTRY),
            'the editor must offer exactly the keys the backend enforces')

    def test_every_offered_key_has_a_type_the_editor_can_render(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_registry')
            body = client.get('/api/admin/entitlement-keys').get_json()
        renderable = {'int', 'bool', 'string', 'list'}
        for item in body['keys']:
            with self.subTest(key=item['key']):
                self.assertIn(item['value_type'], renderable)
                self.assertIn('default', item)

    def test_scan_frequency_options_are_supplied(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_registry')
            body = client.get('/api/admin/entitlement-keys').get_json()
        self.assertEqual(list(body['scan_frequencies']), list(ent.SCAN_FREQUENCY_ORDER))
        self.assertEqual(body['unlimited'], ent.UNLIMITED)


class PlanLifecycleThroughTheUiTests(unittest.TestCase):
    """The exact call sequence the page makes, in order."""

    @classmethod
    def setUpClass(cls):
        cls.admin = make_user('plansui_crud', platform_admin=True)

    def setUp(self):
        reset_ui_plans()

    def create(self, client, **overrides):
        payload = {
            'name': 'UI Plan',
            'slug': 'ui-plan',
            'description': 'Created through the admin UI.',
            'currency': 'USD',
            'billing_interval': 'monthly',
            'price_monthly': '49.00',
            'price_annual': None,
            'trial_days': 14,
            'display_order': 2,
            'monthly_cost_ceiling_usd': '80.00',
            'account_types': ['brand'],
            'entitlements': {'workspace_limit': 3, 'prompt_limit': 120,
                             'scan_frequency': 'weekly', 'reports': True,
                             'engine_access': ['perplexity', 'openai']},
        }
        payload.update(overrides)
        return client.post('/api/admin/plans', json=payload)

    def test_create_lists_opens_and_edits(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_crud')

            created = self.create(client)
            self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
            plan = created.get_json()['plan']
            plan_id = plan['id']
            self.assertFalse(plan['active'], 'a new plan must be a draft')

            listed = client.get('/api/admin/plans').get_json()['plans']
            self.assertIn('ui-plan', {p['slug'] for p in listed})

            detail = client.get(f'/api/admin/plans/{plan_id}').get_json()['plan']
            # Shape the editor relies on.
            self.assertEqual(detail['entitlements_stored']['workspace_limit'], 3)
            self.assertEqual(detail['entitlements']['prompt_limit'], 120)
            self.assertEqual(detail['entitlements_stored']['engine_access'],
                             ['perplexity', 'openai'])
            self.assertIn('organization_count', detail)
            self.assertIn('can_archive', detail)
            self.assertEqual(detail['trial_days'], 14)
            self.assertEqual(detail['price_monthly'], '49.00')

            edited = client.patch(f'/api/admin/plans/{plan_id}', json={
                'name': 'UI Plan Renamed',
                'price_monthly': '59.00',
                'entitlements': {'workspace_limit': 5},
            })
            self.assertEqual(edited.status_code, 200, edited.get_data(as_text=True))
            after = edited.get_json()['plan']
            self.assertEqual(after['name'], 'UI Plan Renamed')
            self.assertEqual(after['price_monthly'], '59.00')
            self.assertEqual(after['entitlements_stored'], {'workspace_limit': 5})
            # Dropped keys fall back to the registry default, not the old value.
            self.assertEqual(after['entitlements']['prompt_limit'],
                             ent.REGISTRY['prompt_limit'].default)

    def test_activate_then_deactivate(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_crud')
            plan_id = self.create(client).get_json()['plan']['id']

            activated = client.patch(f'/api/admin/plans/{plan_id}', json={'active': True})
            self.assertEqual(activated.status_code, 200)
            self.assertTrue(activated.get_json()['plan']['active'])

            deactivated = client.patch(f'/api/admin/plans/{plan_id}', json={'active': False})
            self.assertEqual(deactivated.status_code, 200)
            self.assertFalse(deactivated.get_json()['plan']['active'])

    def test_a_draft_is_invisible_to_customers_and_an_active_plan_is_visible(self):
        customer = make_user('plansui_customer')
        with server_pg.app.test_client() as client:
            login(client, 'plansui_crud')
            plan_id = self.create(client).get_json()['plan']['id']

        with server_pg.app.test_client() as client:
            login(client, 'plansui_customer')
            slugs = {p['slug'] for p in
                     client.get('/api/plans?account_type=brand').get_json()['plans']}
        self.assertNotIn('ui-plan', slugs, 'a draft must never reach a customer')

        with server_pg.app.test_client() as client:
            login(client, 'plansui_crud')
            client.patch(f'/api/admin/plans/{plan_id}', json={'active': True})

        with server_pg.app.test_client() as client:
            login(client, 'plansui_customer')
            payload = client.get('/api/plans?account_type=brand').get_json()['plans']
        plan = next(p for p in payload if p['slug'] == 'ui-plan')
        self.assertNotIn('monthly_cost_ceiling_usd', plan)
        self.assertNotIn('price_annual', plan)

    def test_archive_is_refused_while_an_organization_is_assigned(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_crud')
            plan_id = self.create(client).get_json()['plan']['id']
            with engine.begin() as conn:
                conn.execute(insert(organizations).values(
                    name='UI assigned org', plan_id=plan_id, plan_status='active',
                    created_at=datetime.utcnow()))

            detail = client.get(f'/api/admin/plans/{plan_id}').get_json()['plan']
            self.assertEqual(detail['organization_count'], 1)
            self.assertFalse(detail['can_archive'],
                             'the page uses can_archive to disable the button')

            refused = client.post(f'/api/admin/plans/{plan_id}/archive')
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.get_json()['organization_count'], 1)

    def test_archive_then_restore_returns_a_draft(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_crud')
            plan_id = self.create(client).get_json()['plan']['id']
            client.patch(f'/api/admin/plans/{plan_id}', json={'active': True})

            archived = client.post(f'/api/admin/plans/{plan_id}/archive')
            self.assertEqual(archived.status_code, 200)
            self.assertIsNotNone(archived.get_json()['plan']['archived_at'])
            self.assertFalse(archived.get_json()['plan']['active'])

            # Archived plans are read-only until restored.
            self.assertEqual(
                client.patch(f'/api/admin/plans/{plan_id}', json={'name': 'nope'}).status_code,
                409)

            restored = client.post(f'/api/admin/plans/{plan_id}/restore')
            self.assertEqual(restored.status_code, 200)
            self.assertIsNone(restored.get_json()['plan']['archived_at'])
            self.assertFalse(restored.get_json()['plan']['active'],
                             'restore must not put a plan straight back on sale')

    def test_archived_plans_are_hidden_unless_requested(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_crud')
            plan_id = self.create(client).get_json()['plan']['id']
            client.post(f'/api/admin/plans/{plan_id}/archive')

            default_slugs = {p['slug'] for p in
                             client.get('/api/admin/plans').get_json()['plans']}
            self.assertNotIn('ui-plan', default_slugs)

            with_archived = {p['slug'] for p in
                             client.get('/api/admin/plans?include_archived=true')
                             .get_json()['plans']}
            self.assertIn('ui-plan', with_archived)

    def test_an_undeclared_entitlement_key_from_the_editor_is_rejected(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_crud')
            response = self.create(client, entitlements={'not_a_key': 1})
        self.assertEqual(response.status_code, 400)
        self.assertIn('not_a_key', response.get_json()['error'])

    def test_there_is_still_no_delete_endpoint(self):
        with server_pg.app.test_client() as client:
            login(client, 'plansui_crud')
            plan_id = self.create(client).get_json()['plan']['id']
            response = client.delete(f'/api/admin/plans/{plan_id}')
        self.assertIn(response.status_code, (404, 405),
                      'plans are archived, never deleted')


if __name__ == '__main__':
    unittest.main()
