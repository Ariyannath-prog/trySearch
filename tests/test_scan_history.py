"""Scan History + Scan Detail (/scans). List: app/metrics.py::scan_history()
(new - plain columns already stored on analytics_prompt_scan_runs, no
aggregation). Detail: the existing GET .../evidence?run_id=X
(app/metrics.py::latest_prompt_evidence(), completely unchanged) - this file
adds the one thing that had no coverage before: that passing an explicit,
non-latest run_id actually returns *that* run, not the latest one.
"""

import os
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'scan-history-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import metrics  # noqa: E402
from app.analytics_filters import parse_filters  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import analytics_prompt_scan_runs, engines as engines_table, users  # noqa: E402
from sqlalchemy import select  # noqa: E402

PASSWORD = 'scan-history-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


def seed_scan_run(conn, *, workspace_id, when, provider='Perplexity', status='succeeded',
                  error=None, mention_rate=50.0):
    return conn.execute(insert(analytics_prompt_scan_runs).values(
        workspace_id=workspace_id, job_id=None, provider=provider, model='m',
        region='US', competitor_snapshot='[]', status=status, run_type='scheduled',
        prompt_count=5, completed_count=5 if status == 'succeeded' else 3,
        mention_rate=mention_rate, citation_rate=20.0, source_presence_rate=30.0,
        share_of_voice=40.0, recommendation_summary=None, error=error,
        created_at=when, completed_at=when,
    )).inserted_primary_key[0]


class ScanHistoryTests(unittest.TestCase):

    def test_lists_newest_first_and_is_workspace_scoped(self):
        workspace_id = create_workspace(user_id=98800, domain='scanhist.example',
                                        brand_name='ScanHist')
        other_workspace_id = create_workspace(user_id=98801, domain='otherscan.example',
                                              brand_name='OtherScan')
        older = datetime(2026, 4, 1, 9, 0, 0)
        newer = datetime(2026, 4, 5, 9, 0, 0)
        with engine.begin() as conn:
            older_id = seed_scan_run(conn, workspace_id=workspace_id, when=older)
            newer_id = seed_scan_run(conn, workspace_id=workspace_id, when=newer, provider='OpenAI')
            seed_scan_run(conn, workspace_id=other_workspace_id, when=newer)

        scans = metrics.scan_history(workspace_id)
        self.assertEqual(len(scans), 2)
        self.assertEqual(scans[0]['id'], newer_id)  # newest first
        self.assertEqual(scans[1]['id'], older_id)
        self.assertEqual(scans[0]['provider'], 'OpenAI')

    def test_includes_stored_error_for_failed_runs(self):
        workspace_id = create_workspace(user_id=98802, domain='scanerror.example',
                                        brand_name='ScanError')
        with engine.begin() as conn:
            seed_scan_run(conn, workspace_id=workspace_id, when=datetime.utcnow(),
                         status='failed', error='No Perplexity provider credential is configured.')
        scans = metrics.scan_history(workspace_id)
        self.assertEqual(scans[0]['status'], 'failed')
        self.assertEqual(scans[0]['error'], 'No Perplexity provider credential is configured.')

    def test_empty_workspace_has_no_scans(self):
        workspace_id = create_workspace(user_id=98803, domain='noscans.example', brand_name='NoScans')
        self.assertEqual(metrics.scan_history(workspace_id), [])


class ScanDetailViaRunIdTests(unittest.TestCase):
    """The existing GET .../evidence?run_id=X path - unchanged, but never
    tested for the case that matters for Scan Detail: an explicit,
    non-latest run_id."""

    def test_explicit_run_id_returns_that_run_not_the_latest(self):
        workspace_id = create_workspace(user_id=98804, domain='rundetail.example',
                                        brand_name='RunDetail')
        older = datetime(2026, 4, 1, 9, 0, 0)
        newer = datetime(2026, 4, 5, 9, 0, 0)
        with engine.begin() as conn:
            older_id = seed_scan_run(conn, workspace_id=workspace_id, when=older, provider='Perplexity')
            seed_scan_run(conn, workspace_id=workspace_id, when=newer, provider='OpenAI')

        latest = metrics.latest_prompt_evidence(workspace_id)
        self.assertEqual(latest['run']['provider'], 'OpenAI')

        explicit = metrics.latest_prompt_evidence(workspace_id, older_id)
        self.assertEqual(explicit['run']['id'], older_id)
        self.assertEqual(explicit['run']['provider'], 'Perplexity')


class ScanHistoryRouteTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_get_returns_project_and_scans(self):
        user_id = make_user('scans_route_user')
        workspace_id = create_workspace(user_id=user_id, domain='scansroute.example',
                                        brand_name='ScansRoute')
        with engine.begin() as conn:
            seed_scan_run(conn, workspace_id=workspace_id, when=datetime.utcnow())
        with server_pg.app.test_client() as client:
            self.login(client, 'scans_route_user')
            response = client.get(f'/api/analytics/projects/{workspace_id}/scans')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn('project', body)
        self.assertEqual(len(body['scans']), 1)

    def test_endpoint_is_workspace_scoped(self):
        with server_pg.app.test_client() as client:
            response = client.get('/api/analytics/projects/1/scans')
        self.assertEqual(response.status_code, 401)


class ScansPageTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_scans_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/scans')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_scans_returns_200_for_logged_in_user(self):
        make_user('pages_scans_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_scans_user')
            response = client.get('/scans')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


class ScanHistoryFilteringTests(unittest.TestCase):
    """scan_history()'s optional `filters` argument - the pilot retrofit of
    Milestone B's shared date-range/region/engine filter system."""

    @classmethod
    def setUpClass(cls):
        cls.workspace_id = create_workspace(user_id=98850, domain='scanfilter.example',
                                            brand_name='ScanFilter')
        cls.jan = datetime(2026, 1, 15, 9, 0, 0)
        cls.feb = datetime(2026, 2, 15, 9, 0, 0)
        with engine.begin() as conn:
            cls.jan_us_perplexity = seed_scan_run(
                conn, workspace_id=cls.workspace_id, when=cls.jan, provider='Perplexity')
            cls.feb_gb_openai = seed_scan_run(
                conn, workspace_id=cls.workspace_id, when=cls.feb, provider='OpenAI')
            conn.execute(analytics_prompt_scan_runs.update().where(
                analytics_prompt_scan_runs.c.id == cls.feb_gb_openai
            ).values(region='GB'))
            cls.perplexity_id = conn.execute(select(engines_table.c.id).where(
                engines_table.c.key == 'perplexity')).scalar_one()
            # The test database only ever seeds 'perplexity' (conftest.py's
            # _seed_engines() mirrors just the one migrated row); every other
            # engine only exists in production via later one-off inserts, so
            # a second engine needed for a filter test has to insert its own.
            cls.openai_id = conn.execute(select(engines_table.c.id).where(
                engines_table.c.key == 'openai')).scalar_one_or_none()
            if cls.openai_id is None:
                cls.openai_id = conn.execute(insert(engines_table).values(
                    key='openai', display_name='OpenAI', source_type='api',
                    adapter_version='test', enabled=True,
                )).inserted_primary_key[0]

    def test_unfiltered_returns_both(self):
        scans = metrics.scan_history(self.workspace_id)
        self.assertEqual({s['id'] for s in scans}, {self.jan_us_perplexity, self.feb_gb_openai})

    def test_date_range_narrows_to_the_matching_run(self):
        filters = parse_filters({'start_date': '2026-02-01', 'end_date': '2026-02-28'})
        scans = metrics.scan_history(self.workspace_id, filters=filters)
        self.assertEqual([s['id'] for s in scans], [self.feb_gb_openai])

    def test_region_filter_narrows_to_the_matching_run(self):
        filters = parse_filters({'region': 'GB'})
        scans = metrics.scan_history(self.workspace_id, filters=filters)
        self.assertEqual([s['id'] for s in scans], [self.feb_gb_openai])

    def test_engine_ids_filter_resolves_to_provider_and_narrows(self):
        filters = parse_filters({'engine_ids': str(self.openai_id)})
        scans = metrics.scan_history(self.workspace_id, filters=filters)
        self.assertEqual([s['id'] for s in scans], [self.feb_gb_openai])

    def test_combined_filters_can_exclude_everything(self):
        filters = parse_filters({'region': 'GB', 'engine_ids': str(self.perplexity_id)})
        scans = metrics.scan_history(self.workspace_id, filters=filters)
        self.assertEqual(scans, [])


class ScanHistoryRouteFilteringTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_route_applies_range_and_returns_available_filters(self):
        user_id = make_user('scans_filter_route_user')
        workspace_id = create_workspace(user_id=user_id, domain='scansfilterroute.example',
                                        brand_name='ScansFilterRoute')
        with engine.begin() as conn:
            seed_scan_run(conn, workspace_id=workspace_id, when=datetime(2020, 1, 1))
            seed_scan_run(conn, workspace_id=workspace_id, when=datetime.utcnow())
        with server_pg.app.test_client() as client:
            self.login(client, 'scans_filter_route_user')
            response = client.get(f'/api/analytics/projects/{workspace_id}/scans?range=7d')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(len(body['scans']), 1)  # the 2020 run is excluded
        self.assertIn('regions', body['available_filters'])
        self.assertIn('engines', body['available_filters'])

    def test_malformed_filter_is_a_400_not_a_500(self):
        user_id = make_user('scans_filter_400_user')
        workspace_id = create_workspace(user_id=user_id, domain='scansfilter400.example',
                                        brand_name='ScansFilter400')
        with server_pg.app.test_client() as client:
            self.login(client, 'scans_filter_400_user')
            response = client.get(f'/api/analytics/projects/{workspace_id}/scans?range=bogus')
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
