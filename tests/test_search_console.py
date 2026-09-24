"""Analytics / Traffic Intelligence page (/search-console) and
app/integrations/gsc.py::gsc_report() - the aggregation this milestone
extends with top_queries/top_pages/history. The OAuth start/callback
network flow is unchanged apart from its redirect target and is not
re-tested here (it had no coverage before this milestone and mocking the
full Google OAuth round trip is out of proportion for a redirect-string
change); this file covers the report aggregation and the routes/page that
are new or extended.
"""

import os
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'search-console-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app.db import engine  # noqa: E402
from app.integrations import gsc  # noqa: E402
from app.models import (  # noqa: E402
    gsc_connections,
    gsc_properties,
    gsc_query_rows,
    gsc_sync_runs,
    users,
)

PASSWORD = 'search-console-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


class SearchConsoleTestCase(unittest.TestCase):
    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def new_workspace(self, tag):
        user_id = make_user(f'sc_{tag}')
        workspace_id = create_workspace(user_id=user_id, domain=f'{tag}.example', brand_name=tag)
        return workspace_id, f'sc_{tag}'


class AggregationHelperTests(unittest.TestCase):
    """Pure-function tests for the shared rollup gsc_report() now reuses."""

    def test_aggregate_query_rows_weights_position_by_impressions(self):
        rows = [
            {'clicks': 10, 'impressions': 100, 'position': 3.0},
            {'clicks': 5, 'impressions': 50, 'position': 5.0},
        ]
        result = gsc._aggregate_query_rows(rows)
        self.assertAlmostEqual(result['clicks'], 15.0)
        self.assertAlmostEqual(result['impressions'], 150.0)
        self.assertAlmostEqual(result['ctr'], 10.0)
        self.assertAlmostEqual(result['position'], (3 * 100 + 5 * 50) / 150, places=2)

    def test_aggregate_query_rows_of_no_rows_is_none(self):
        self.assertIsNone(gsc._aggregate_query_rows([]))

    def test_top_by_groups_and_sorts_by_clicks_descending(self):
        rows = [
            {'query': 'best crm', 'page': '/a', 'clicks': 10, 'impressions': 100, 'position': 3.0},
            {'query': 'best crm', 'page': '/b', 'clicks': 5, 'impressions': 50, 'position': 5.0},
            {'query': 'crm pricing', 'page': '/a', 'clicks': 2, 'impressions': 20, 'position': 8.0},
        ]
        by_query = gsc._top_by(rows, 'query')
        self.assertEqual([r['query'] for r in by_query], ['best crm', 'crm pricing'])
        self.assertAlmostEqual(by_query[0]['clicks'], 15.0)

        by_page = gsc._top_by(rows, 'page')
        pages = {r['page']: r for r in by_page}
        self.assertAlmostEqual(pages['/a']['clicks'], 12.0)
        self.assertAlmostEqual(pages['/b']['clicks'], 5.0)


class GscReportTests(unittest.TestCase):

    def test_disconnected_workspace_has_empty_shape(self):
        workspace_id = create_workspace(user_id=99001, domain='disconnected.example',
                                        brand_name='Disconnected')
        report = gsc.gsc_report(workspace_id, user_id=99001)
        self.assertEqual(report['status'], 'disconnected')
        self.assertIsNone(report['metrics'])
        self.assertEqual(report['queries'], [])
        self.assertEqual(report['top_queries'], [])
        self.assertEqual(report['top_pages'], [])
        self.assertEqual(report['history'], [])

    def test_connected_workspace_aggregates_the_latest_sync(self):
        workspace_id = create_workspace(user_id=99002, domain='connected.example',
                                        brand_name='Connected')
        now = datetime.utcnow()
        older = now - timedelta(days=7)
        with engine.begin() as conn:
            connection_id = conn.execute(insert(gsc_connections).values(
                workspace_id=workspace_id, encrypted_refresh_token=None,
                encrypted_access_token=None, token_expires_at=None, granted_scopes=None,
                selected_property='https://connected.example/', status='connected',
                last_error=None, created_at=older, updated_at=now,
            )).inserted_primary_key[0]
            conn.execute(insert(gsc_properties).values(
                connection_id=connection_id, site_url='https://connected.example/',
                permission_level='siteOwner', selected=True,
            ))
            sync1 = conn.execute(insert(gsc_sync_runs).values(
                workspace_id=workspace_id, connection_id=connection_id,
                property_url='https://connected.example/', status='succeeded',
                start_date='2026-01-01', end_date='2026-01-07', rows_saved=1,
                data_state='final', error=None, created_at=older, completed_at=older,
            )).inserted_primary_key[0]
            sync2 = conn.execute(insert(gsc_sync_runs).values(
                workspace_id=workspace_id, connection_id=connection_id,
                property_url='https://connected.example/', status='succeeded',
                start_date='2026-01-08', end_date='2026-01-14', rows_saved=3,
                data_state='final', error=None, created_at=now, completed_at=now,
            )).inserted_primary_key[0]
            conn.execute(insert(gsc_query_rows).values(
                sync_run_id=sync1, query='old query', page='/old-page',
                clicks=1, impressions=10, ctr=0.1, position=2.0))
            conn.execute(insert(gsc_query_rows), [
                {'sync_run_id': sync2, 'query': 'best crm', 'page': '/page-a',
                 'clicks': 10, 'impressions': 100, 'ctr': 0.1, 'position': 3.0},
                {'sync_run_id': sync2, 'query': 'best crm', 'page': '/page-b',
                 'clicks': 5, 'impressions': 50, 'ctr': 0.1, 'position': 5.0},
                {'sync_run_id': sync2, 'query': 'crm pricing', 'page': '/page-a',
                 'clicks': 2, 'impressions': 20, 'ctr': 0.1, 'position': 8.0},
            ])

        report = gsc.gsc_report(workspace_id, user_id=99002)
        self.assertEqual(report['status'], 'connected')
        self.assertEqual(report['property'], 'https://connected.example/')

        m = report['metrics']
        self.assertAlmostEqual(m['clicks'], 17.0)
        self.assertAlmostEqual(m['impressions'], 170.0)
        self.assertAlmostEqual(m['ctr'], 10.0)
        self.assertAlmostEqual(m['position'], 710 / 170, places=2)
        self.assertEqual(m['rows_saved'], 3)

        self.assertEqual(report['top_queries'][0]['query'], 'best crm')
        self.assertAlmostEqual(report['top_queries'][0]['clicks'], 15.0)
        self.assertEqual(report['top_pages'][0]['page'], '/page-a')
        self.assertAlmostEqual(report['top_pages'][0]['clicks'], 12.0)

        self.assertEqual(len(report['history']), 2)
        self.assertAlmostEqual(report['history'][0]['metrics']['clicks'], 1.0)  # older, chronological first
        self.assertAlmostEqual(report['history'][1]['metrics']['clicks'], 17.0)  # latest


class SearchConsoleRouteTests(SearchConsoleTestCase):

    def test_get_returns_project_and_search_console(self):
        workspace_id, username = self.new_workspace('routeget')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            response = client.get(f'/api/analytics/projects/{workspace_id}/search-console')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn('project', body)
        self.assertIn('search_console', body)
        self.assertEqual(body['search_console']['status'], 'disconnected')

    def test_get_is_workspace_scoped(self):
        with server_pg.app.test_client() as client:
            response = client.get('/api/analytics/projects/1/search-console')
        self.assertEqual(response.status_code, 401)


class SearchConsolePageTests(SearchConsoleTestCase):

    def test_search_console_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/search-console')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_search_console_returns_200_for_logged_in_user(self):
        make_user('pages_search_console_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_search_console_user')
            response = client.get('/search-console')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


if __name__ == '__main__':
    unittest.main()
