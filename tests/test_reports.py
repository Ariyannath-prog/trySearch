"""T16: the white-label report and its share link."""

import os
import unittest
from datetime import date, datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'reports-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert, select  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import reports  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    analytics_prompt_scan_runs,
    analytics_tracked_prompts,
    metrics_daily,
    report_shares,
    users,
    workspace_branding,
)

PASSWORD = 'reports-password-1'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


def seed_metrics(workspace_id, *, answer_count=100, vs=44.5):
    now = datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(insert(metrics_daily).values(
            workspace_id=workspace_id, date=date.today(), engine_id=None,
            visibility_score=vs, mention_rate=0.40, position_score=0.7167,
            citation_rate=0.15, sov=0.62, sentiment_index=None,
            answer_count=answer_count, created_at=now, updated_at=now))


def seed_run(workspace_id, status, *, when=None):
    now = when or datetime.utcnow()
    with engine.begin() as conn:
        return conn.execute(insert(analytics_prompt_scan_runs).values(
            workspace_id=workspace_id, job_id=None, provider='Perplexity',
            model='m', region=None, competitor_snapshot='[]', status=status,
            run_type='scheduled', prompt_count=5, completed_count=5,
            mention_rate=None, citation_rate=None, source_presence_rate=None,
            share_of_voice=None, recommendation_summary=None, error=None,
            created_at=now, completed_at=now if status != 'running' else None,
        )).inserted_primary_key[0]


class ReportGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = make_user('report_owner')
        cls.workspace = create_workspace(user_id=cls.user, domain='report.example',
                                         brand_name='Report')
        seed_metrics(cls.workspace)
        seed_run(cls.workspace, 'succeeded')
        now = datetime.utcnow()
        with engine.begin() as conn:
            conn.execute(insert(analytics_tracked_prompts).values(
                workspace_id=cls.workspace, topic_id=None, prompt='best crm',
                intent='Discovery', active=True, created_at=now, updated_at=now))

    def test_report_generates_for_workspace_with_data(self):
        report = reports.build_report(self.workspace)
        self.assertIsNotNone(report)
        self.assertEqual(report['visibility']['state'], 'ok')
        self.assertEqual(report['visibility']['visibility_score']['value'], 44.5)
        self.assertEqual(report['prompts']['n'], 1)
        self.assertIn('methodology', report)

    def test_every_metric_on_the_report_carries_a_sample_size(self):
        """A report that hides n is worse than no report - it travels further."""
        report = reports.build_report(self.workspace)
        visibility = report['visibility']
        for key in ('visibility_score', 'mention_rate', 'citation_rate'):
            with self.subTest(metric=key):
                self.assertIsNotNone(visibility[key], f'{key} missing')
                self.assertIn('n', visibility[key], f'{key} has no sample size')
                self.assertEqual(visibility[key]['n'], 100)
        self.assertIn('n', report['share_of_voice'])
        self.assertIn('n', report['prompts'])

    def test_sections_are_selectable(self):
        report = reports.build_report(self.workspace, sections=['visibility'])
        self.assertIn('visibility', report)
        self.assertNotIn('citations', report)
        self.assertNotIn('prompts', report)

    def test_methodology_states_the_formula_and_the_threshold(self):
        method = reports.build_report(self.workspace)['methodology']
        self.assertIn('0.5', method['formula'])
        self.assertIn('Wilson', method['interval'])
        self.assertIn('20 answers', method['threshold'])


class EmptyWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = make_user('report_empty')
        cls.workspace = create_workspace(user_id=cls.user, domain='empty.example',
                                         brand_name='Empty')

    def test_report_generates_for_empty_workspace_without_erroring(self):
        report = reports.build_report(self.workspace)
        self.assertIsNotNone(report)
        self.assertEqual(report['visibility']['state'], 'not_yet_run')

    def test_empty_sections_say_which_empty_state_not_a_zero(self):
        """No fabricated numbers. An empty section explains itself."""
        report = reports.build_report(self.workspace)
        self.assertIsNone(report['visibility']['visibility_score'])
        self.assertIn('empty_reason', report['visibility'])
        self.assertIn('No scan has completed', report['visibility']['empty_reason'])

        self.assertIsNone(report['share_of_voice']['value'])
        self.assertIsNotNone(report['share_of_voice']['empty_reason'])
        self.assertIsNotNone(report['citations']['empty_reason'])
        self.assertIsNotNone(report['prompts']['empty_reason'])

    def test_insufficient_data_is_distinct_from_never_run(self):
        workspace = create_workspace(user_id=make_user('report_few'),
                                     domain='few.example', brand_name='Few')
        seed_metrics(workspace, answer_count=12)
        seed_run(workspace, 'succeeded')
        report = reports.build_report(workspace)
        self.assertEqual(report['visibility']['state'], 'insufficient')
        self.assertIn('Too few answers', report['visibility']['empty_reason'])


class InProgressRunTests(unittest.TestCase):
    def test_in_progress_run_uses_last_complete_and_labels_it(self):
        workspace = create_workspace(user_id=make_user('report_running'),
                                     domain='running.example', brand_name='Running')
        earlier = datetime.utcnow() - timedelta(days=1)
        seed_run(workspace, 'succeeded', when=earlier)
        seed_run(workspace, 'running')
        seed_metrics(workspace)

        report = reports.build_report(workspace)
        self.assertTrue(report['scan_in_progress'],
                        'the report must admit a scan is running')
        self.assertIsNotNone(report['as_of'],
                             'and label which complete run it used')


class BrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = make_user('report_brand')
        cls.workspace = create_workspace(user_id=cls.user, domain='brand.example',
                                         brand_name='Brand')
        seed_metrics(cls.workspace)

    def test_branding_changes_the_output(self):
        before = reports.build_report(self.workspace)['branding']
        now = datetime.utcnow()
        with engine.begin() as conn:
            conn.execute(insert(workspace_branding).values(
                workspace_id=self.workspace, display_name='Agency X',
                logo_url='https://agency.example/logo.png', accent_colour='#ff0066',
                hide_trysearch_mark=False, created_at=now, updated_at=now))
        after = reports.build_report(self.workspace)['branding']

        self.assertNotEqual(before, after)
        self.assertEqual(after['display_name'], 'Agency X')
        self.assertEqual(after['accent_colour'], '#ff0066')

    def test_mark_shows_by_default(self):
        """A missing branding row must never produce an unbranded report."""
        workspace = create_workspace(user_id=make_user('report_nomark'),
                                     domain='nomark.example', brand_name='NoMark')
        branding = reports.build_report(workspace)['branding']
        self.assertTrue(branding['show_trysearch_mark'])


class ShareLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = make_user('report_share')
        cls.workspace = create_workspace(user_id=cls.user, domain='share.example',
                                         brand_name='Share')
        seed_metrics(cls.workspace)

    def login(self, client):
        client.post('/api/login', json={'username': 'report_share',
                                        'password': PASSWORD})

    def test_share_token_is_unguessable_and_read_only(self):
        share = reports.create_share(self.workspace)
        token = share['token']

        # Unguessable: 32 random bytes, url-safe, and never derived from the id.
        self.assertGreaterEqual(len(token), 40)
        self.assertNotIn(str(self.workspace), token)
        tokens = {reports.create_share(self.workspace)['token'] for _ in range(20)}
        self.assertEqual(len(tokens), 20, 'tokens must not collide or increment')

        with server_pg.app.test_client() as client:
            # Read-only and session-free.
            ok = client.get(f'/api/reports/shared/{token}')
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.get_json()['report']['shared'])

            # No write verb is reachable on the shared surface.
            for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                with self.subTest(method=method):
                    denied = client.open(f'/api/reports/shared/{token}',
                                         method=method, json={})
                    self.assertEqual(denied.status_code, 405,
                                     f'{method} must not be routable on a share link')

    def test_unknown_revoked_and_expired_tokens_are_indistinguishable(self):
        live = reports.create_share(self.workspace)['token']
        revoked = reports.create_share(self.workspace)['token']
        reports.revoke_share(revoked)

        expired_token = reports.create_share(self.workspace)['token']
        with engine.begin() as conn:
            conn.execute(report_shares.update()
                         .where(report_shares.c.token == expired_token)
                         .values(expires_at=datetime.utcnow() - timedelta(days=1)))

        with server_pg.app.test_client() as client:
            self.assertEqual(client.get(f'/api/reports/shared/{live}').status_code, 200)
            for token in ('never-existed', revoked, expired_token):
                with self.subTest(token=token[:12]):
                    response = client.get(f'/api/reports/shared/{token}')
                    self.assertEqual(response.status_code, 404)
                    self.assertIn('not available', response.get_json()['error'])

    def test_a_token_only_ever_reaches_its_own_workspace(self):
        """The workspace comes from the token, never from the caller."""
        other = create_workspace(user_id=make_user('report_other'),
                                 domain='other.example', brand_name='Other')
        token = reports.create_share(self.workspace)['token']
        with server_pg.app.test_client() as client:
            body = client.get(
                f'/api/reports/shared/{token}?workspace_id={other}').get_json()
        self.assertEqual(body['report']['workspace']['id'], self.workspace)

    def test_share_requires_workspace_access_to_mint(self):
        outsider = make_user('report_outsider')
        with server_pg.app.test_client() as client:
            client.post('/api/login', json={'username': 'report_outsider',
                                            'password': PASSWORD})
            response = client.post(
                f'/api/analytics/projects/{self.workspace}/report/shares', json={})
        self.assertEqual(response.status_code, 404,
                         'a non-member cannot mint a link to another tenant')


if __name__ == '__main__':
    unittest.main()
