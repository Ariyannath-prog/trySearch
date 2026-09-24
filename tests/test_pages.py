"""Static app-shell pages served by app/routes/pages.py."""

import os
import unittest

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'pages-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import users  # noqa: E402
from sqlalchemy import insert  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

PASSWORD = 'pages-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=__import__('datetime').datetime.utcnow(),
        )).inserted_primary_key[0]


class AnalyticsPageTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_analytics_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/analytics')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_analytics_returns_200_for_logged_in_user(self):
        user_id = make_user('pages_dashboard_user')
        create_workspace(user_id=user_id, domain='pages-test.example', brand_name='PagesTest')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_dashboard_user')
            response = client.get('/analytics')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


class WorkspacePageTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_workspace_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/workspace')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_workspace_returns_200_for_logged_in_user(self):
        make_user('pages_workspace_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_workspace_user')
            response = client.get('/workspace')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


class OnboardingPageTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_onboarding_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/onboarding')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_onboarding_returns_200_for_logged_in_user(self):
        make_user('pages_onboarding_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_onboarding_user')
            response = client.get('/onboarding')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


class PromptIntelligencePageTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_prompt_intelligence_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/prompt-intelligence')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_prompt_intelligence_returns_200_for_logged_in_user(self):
        make_user('pages_prompt_intel_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_prompt_intel_user')
            response = client.get('/prompt-intelligence')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


class VisibilityTrackingPageTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_visibility_tracking_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/visibility-tracking')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_visibility_tracking_returns_200_for_logged_in_user(self):
        make_user('pages_visibility_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_visibility_user')
            response = client.get('/visibility-tracking')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


if __name__ == '__main__':
    unittest.main()
