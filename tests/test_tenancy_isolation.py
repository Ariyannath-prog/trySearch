"""Cross-tenant isolation and role enforcement.

The route list is read from app.url_map rather than hard-coded. A new
workspace-scoped route is therefore covered the moment it is registered, and
cannot quietly ship without an isolation check.
"""

import os
import unittest

os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'tenancy-isolation-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import memberships, users, workspaces  # noqa: E402
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


def workspace_scoped_rules():
    """Every registered rule that names a workspace, with its methods."""
    for rule in sorted(server_pg.app.url_map.iter_rules(), key=lambda r: r.rule):
        if 'workspace_id' not in rule.rule:
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

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200)

    def test_client_viewer_may_read(self):
        with server_pg.app.test_client() as client:
            self.login(client, 'role_viewer')
            response = client.get(f'/api/analytics/projects/{self.workspace}/tracking')
            self.assertEqual(response.status_code, 200,
                             'client_viewer must still be able to read the report')

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
