"""Cross-tenant isolation and role enforcement.

The route list is read from app.url_map rather than hard-coded. A new
workspace-scoped route is therefore covered the moment it is registered, and
cannot quietly ship without an isolation check.
"""

import os
import unittest

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'tenancy-isolation-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import memberships, users, workspace_access, workspaces  # noqa: E402
from sqlalchemy import insert, select, delete  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

PASSWORD = 'isolation-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=__import__('datetime').datetime.utcnow(),
        )).inserted_primary_key[0]



def conn_insert_workspace(org_id, brand_name='Ungranted'):
    """Add another workspace to an existing org, with no access grants."""
    import datetime

    now = datetime.datetime.utcnow()
    with engine.begin() as conn:
        return conn.execute(insert(workspaces).values(
            org_id=org_id, brand_name=brand_name, domains=['ungranted.example'],
            geo='US', language='en', kind='project', status='active', created_at=now,
            domain='ungranted.example', website_url='https://ungranted.example/',
            industry='Software', updated_at=now,
        )).inserted_primary_key[0]

def workspace_scoped_rules():
    """Every registered rule that names a workspace, with its methods.

    Platform-admin routes (/api/admin/...) are excluded: they are gated by
    require_platform_admin_api(), a separate authorization tier from org
    membership, and correctly return 403 for every workspace_id -- including
    ones that don't exist -- rather than the 404 org members get for a
    workspace outside their own org. See app/admin_auth.py.
    """
    for rule in sorted(server_pg.app.url_map.iter_rules(), key=lambda r: r.rule):
        if 'workspace_id' not in rule.rule:
            continue
        if rule.rule.startswith('/api/admin/'):
            continue
        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            yield rule, method


def concrete_url(rule, workspace_id):
    """Fill the rule's placeholders, using the given workspace and 1 for the rest."""
    url = rule.rule.replace('<int:workspace_id>', str(workspace_id))
    for argument in rule.arguments:
        if argument == 'workspace_id':
            continue
        url = url.replace(f'<int:{argument}>', '1').replace(f'<{argument}>', '1')
    return url


class CrossTenantIsolationTests(unittest.TestCase):
    """Two orgs, one workspace each. Neither may see the other's."""

    @classmethod
    def setUpClass(cls):
        cls.user_a = make_user('tenant_a_owner')
        cls.user_b = make_user('tenant_b_owner')
        cls.workspace_a = create_workspace(user_id=cls.user_a, domain='alpha.example',
                                           brand_name='Alpha')
        cls.workspace_b = create_workspace(user_id=cls.user_b, domain='beta.example',
                                           brand_name='Beta')

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_two_orgs_do_not_share_a_workspace(self):
        with engine.connect() as conn:
            org_a = conn.execute(select(workspaces.c.org_id).where(
                workspaces.c.id == self.workspace_a)).scalar_one()
            org_b = conn.execute(select(workspaces.c.org_id).where(
                workspaces.c.id == self.workspace_b)).scalar_one()
        self.assertNotEqual(org_a, org_b, 'the fixture must build two distinct orgs')

    def test_every_workspace_route_is_404_for_the_wrong_org(self):
        rules = list(workspace_scoped_rules())
        self.assertGreater(len(rules), 0, 'no workspace-scoped routes found to test')

        with server_pg.app.test_client() as client:
            self.login(client, 'tenant_a_owner')
            for rule, method in rules:
                # User A reaches for B's workspace.
                url = concrete_url(rule, self.workspace_b)
                with self.subTest(route=f'{method} {rule.rule}'):
                    response = client.open(url, method=method, json={})
                    self.assertEqual(
                        response.status_code, 404,
                        f'{method} {url} returned {response.status_code}, not 404. '
                        f'A cross-tenant request must be indistinguishable from a '
                        f'missing workspace.\nbody: {response.get_data(as_text=True)[:200]}',
                    )

    def test_own_workspace_is_not_404(self):
        """The isolation test would pass vacuously if everything 404'd."""
        with server_pg.app.test_client() as client:
            self.login(client, 'tenant_a_owner')
            response = client.get(f'/api/analytics/projects/{self.workspace_a}/tracking')
            self.assertEqual(response.status_code, 200)

    def test_anonymous_is_401_not_404(self):
        with server_pg.app.test_client() as client:
            response = client.get(f'/api/analytics/projects/{self.workspace_a}/tracking')
            self.assertEqual(response.status_code, 401)


class RoleEnforcementTests(unittest.TestCase):
    """client_viewer reads but never writes."""

    @classmethod
    def setUpClass(cls):
        cls.owner = make_user('role_owner')
        cls.workspace = create_workspace(user_id=cls.owner, domain='roles.example',
                                         brand_name='Roles')
        with engine.connect() as conn:
            cls.org_id = conn.execute(select(workspaces.c.org_id).where(
                workspaces.c.id == cls.workspace)).scalar_one()
        cls.viewer = make_user('role_viewer')
        with engine.begin() as conn:
            conn.execute(insert(memberships).values(
                org_id=cls.org_id, user_id=cls.viewer, role='client_viewer'))
            # client_viewer is now scoped to individually assigned workspaces, so
            # org membership alone no longer grants one. This viewer is explicitly
            # granted *this* workspace; the test below proves that a viewer without
            # a grant cannot reach it.
            conn.execute(insert(workspace_access).values(
                workspace_id=cls.workspace, user_id=cls.viewer, granted_by=cls.owner,
                created_at=__import__('datetime').datetime.utcnow()))

        # A second workspace in the SAME org that the viewer is NOT granted.
        cls.ungranted_workspace = conn_insert_workspace(cls.org_id)

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200)

    def test_client_viewer_may_read_a_granted_workspace(self):
        with server_pg.app.test_client() as client:
            self.login(client, 'role_viewer')
            response = client.get(f'/api/analytics/projects/{self.workspace}/tracking')
            self.assertEqual(response.status_code, 200,
                             'client_viewer must still be able to read the report for a '
                             'workspace they have been granted')

    def test_client_viewer_cannot_read_an_ungranted_workspace_in_the_same_org(self):
        """The agency isolation rule.

        Before workspace_access existed, membership was joined on org_id alone, so
        one agency client could read every other client's workspace in that agency.
        404 rather than 403, for the same reason a non-member gets 404: a client must
        not learn that another client's workspace exists.
        """
        with server_pg.app.test_client() as client:
            self.login(client, 'role_viewer')
            response = client.get(
                f'/api/analytics/projects/{self.ungranted_workspace}/tracking')
            self.assertEqual(
                response.status_code, 404,
                'a client_viewer with no workspace_access row must not reach another '
                'workspace in the same organization')

    def test_owner_still_reaches_the_ungranted_workspace(self):
        """Proves the 404 above is role scoping, not a broken workspace."""
        with server_pg.app.test_client() as client:
            self.login(client, 'role_owner')
            response = client.get(
                f'/api/analytics/projects/{self.ungranted_workspace}/tracking')
            self.assertEqual(response.status_code, 200,
                             'org-wide roles must be unaffected by workspace_access')

    def test_ungranted_workspace_is_hidden_from_the_viewer_listing(self):
        with server_pg.app.test_client() as client:
            self.login(client, 'role_viewer')
            response = client.get('/api/analytics/projects')
            self.assertEqual(response.status_code, 200)
            listed = {project['id'] for project in response.get_json()['projects']}
            self.assertIn(self.workspace, listed)
            self.assertNotIn(
                self.ungranted_workspace, listed,
                'the workspace listing must apply the same grant rule as the guard')

    def test_client_viewer_is_rejected_on_every_write(self):
        writes = [(rule, method) for rule, method in workspace_scoped_rules()
                  if method in ('POST', 'PUT', 'PATCH', 'DELETE')]
        self.assertGreater(len(writes), 0)

        with server_pg.app.test_client() as client:
            self.login(client, 'role_viewer')
            for rule, method in writes:
                url = concrete_url(rule, self.workspace)
                with self.subTest(route=f'{method} {rule.rule}'):
                    response = client.open(url, method=method, json={})
                    self.assertEqual(
                        response.status_code, 403,
                        f'{method} {url} returned {response.status_code}, not 403. '
                        f'client_viewer must not be able to write.\n'
                        f'body: {response.get_data(as_text=True)[:200]}',
                    )

    def test_owner_is_not_rejected_on_the_same_writes(self):
        """Proves the 403s above come from the role, not from a broken route."""
        with server_pg.app.test_client() as client:
            self.login(client, 'role_owner')
            response = client.post(
                f'/api/analytics/projects/{self.workspace}/topics',
                json={'name': 'Owner may write'},
            )
            self.assertEqual(response.status_code, 201)


if __name__ == '__main__':
    unittest.main()
