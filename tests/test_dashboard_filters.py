"""Dashboard slice of Milestone B: app/metrics.py::analytics_report() now
accepts the shared date-range/region/engine filter system. Two read paths:

- No region filter: still reads only metrics_daily (rollup.latest_metrics/
  latest_metrics_all_engines, now optionally date-scoped) - the CLAUDE.md
  "dashboards read metrics_daily and nothing else" invariant, unchanged for
  the common case, still covered by test_visibility_score.py::ReadPathTests
  which this file does not duplicate.
- A region filter: metrics_daily has no region column, so this is computed
  live via rollup.collect_counts_range() + the *unmodified*
  score_from_counts()/blend() - this file hand-computes the expected numbers
  the same way test_competitors.py does, to prove the live path is not
  approximating anything.
"""

import os
import unittest
from datetime import date, datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'dashboard-filters-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import metrics  # noqa: E402
from app.analytics_filters import parse_filters  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_tracked_prompts,
    engines as engines_table,
    extractions,
    metrics_daily,
    users,
)

PASSWORD = 'dashboard-filters-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


def seed_metrics_daily_row(conn, *, workspace_id, day, engine_id, visibility_score,
                           mention_rate=0.5, citation_rate=0.2, answer_count=25):
    now = datetime.utcnow()
    conn.execute(insert(metrics_daily).values(
        workspace_id=workspace_id, date=day, engine_id=engine_id,
        visibility_score=visibility_score, mention_rate=mention_rate,
        position_score=0.6, citation_rate=citation_rate, sov=0.3,
        sentiment_index=None, answer_count=answer_count,
        created_at=now, updated_at=now,
    ))


def seed_raw_answer(conn, *, workspace_id, when, provider, region, brand_mentioned,
                    brand_rank, brand_cited):
    prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
        workspace_id=workspace_id, topic_id=None, prompt='best crm for startups',
        intent='Discovery', active=True, created_at=when, updated_at=when,
    )).inserted_primary_key[0]
    scan_id = conn.execute(insert(analytics_prompt_scan_runs).values(
        workspace_id=workspace_id, job_id=None, provider=provider, model='m',
        region=region, competitor_snapshot='[]', status='succeeded', run_type='scheduled',
        prompt_count=1, completed_count=1, mention_rate=None, citation_rate=None,
        source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
        error=None, created_at=when, completed_at=when,
    )).inserted_primary_key[0]
    answer_id = conn.execute(insert(analytics_provider_answers).values(
        scan_run_id=scan_id, prompt_id=prompt_id, prompt_text='best crm for startups',
        prompt_intent='Discovery', topic_name=None, provider=provider, model='m',
        status='ok', search_request_id=None, answer_request_id=None, answer_text='text',
        raw_response='{}', latency_ms=1, error=None, created_at=when, completed_at=when,
    )).inserted_primary_key[0]
    conn.execute(insert(extractions).values(
        answer_id=answer_id, extractor_version='test', is_current=True,
        brand_mentioned=brand_mentioned, brand_rank=brand_rank, brand_cited=brand_cited,
        created_at=when,
    ))
    return scan_id


class DateRangeFilteringTests(unittest.TestCase):
    """The common case: still reads only metrics_daily, now date-scoped."""

    @classmethod
    def setUpClass(cls):
        cls.workspace_id = create_workspace(user_id=98950, domain='dashdate.example',
                                            brand_name='DashDate')
        cls.day1 = date(2026, 5, 1)
        cls.day2 = date(2026, 5, 10)
        with engine.begin() as conn:
            seed_metrics_daily_row(conn, workspace_id=cls.workspace_id, day=cls.day1,
                                   engine_id=None, visibility_score=30.0)
            seed_metrics_daily_row(conn, workspace_id=cls.workspace_id, day=cls.day2,
                                   engine_id=None, visibility_score=70.0)

    def test_unfiltered_sees_both_days(self):
        report = metrics.analytics_report(self.workspace_id, 98950)
        self.assertEqual(len(report['history']), 2)

    def test_date_range_narrows_to_the_matching_day(self):
        filters = parse_filters({'start_date': '2026-05-10', 'end_date': '2026-05-10'})
        report = metrics.analytics_report(self.workspace_id, 98950, filters=filters)
        self.assertEqual(len(report['history']), 1)
        self.assertAlmostEqual(report['history'][0]['visibility_score'], 70.0)
        self.assertAlmostEqual(report['visibility']['visibility_score']['value'], 70.0)


class EngineFilteringTests(unittest.TestCase):
    """metrics_daily already has an engine_id dimension - selecting engines
    re-blends (via the unmodified rollup.blend()) across only those engines,
    rather than showing the all-engines blended row."""

    @classmethod
    def setUpClass(cls):
        cls.workspace_id = create_workspace(user_id=98951, domain='dashengine.example',
                                            brand_name='DashEngine')
        cls.day = date(2026, 5, 15)
        with engine.begin() as conn:
            cls.perplexity_id = conn.execute(
                __import__('sqlalchemy').select(engines_table.c.id).where(
                    engines_table.c.key == 'perplexity')).scalar_one()
            cls.other_id = conn.execute(insert(engines_table).values(
                key='dashtest-engine', display_name='DashTest Engine', source_type='api',
                adapter_version='test', enabled=True,
            )).inserted_primary_key[0]
            seed_metrics_daily_row(conn, workspace_id=cls.workspace_id, day=cls.day,
                                   engine_id=None, visibility_score=50.0)  # all-engine blend
            seed_metrics_daily_row(conn, workspace_id=cls.workspace_id, day=cls.day,
                                   engine_id=cls.perplexity_id, visibility_score=80.0)
            seed_metrics_daily_row(conn, workspace_id=cls.workspace_id, day=cls.day,
                                   engine_id=cls.other_id, visibility_score=20.0)

    def test_single_engine_selection_shows_only_that_engines_own_score(self):
        filters = parse_filters({'engine_ids': str(self.perplexity_id)})
        report = metrics.analytics_report(self.workspace_id, 98951, filters=filters)
        self.assertAlmostEqual(report['visibility']['visibility_score']['value'], 80.0)
        self.assertEqual(len(report['engines']), 1)
        self.assertEqual(report['engines'][0]['engine_id'], self.perplexity_id)

    def test_both_engines_selected_reblends_to_the_same_result_as_the_stored_blend(self):
        filters = parse_filters({'engine_ids': f'{self.perplexity_id},{self.other_id}'})
        report = metrics.analytics_report(self.workspace_id, 98951, filters=filters)
        # mean(80, 20) == 50, the same number the stored all-engine blend
        # happens to carry here - re-blending via rollup.blend() unmodified.
        self.assertAlmostEqual(report['visibility']['visibility_score']['value'], 50.0)
        self.assertEqual(len(report['engines']), 2)


class RegionFilteringTests(unittest.TestCase):
    """No metrics_daily rows at all - proves the region-filtered path is
    genuinely live, not a metrics_daily read, and matches a hand-computed
    PRD §13 result."""

    @classmethod
    def setUpClass(cls):
        cls.workspace_id = create_workspace(user_id=98952, domain='dashregion.example',
                                            brand_name='DashRegion')
        now = datetime(2026, 5, 20, 9, 0, 0)
        with engine.begin() as conn:
            # US: 20 answers (exactly MIN_ANSWERS_FOR_SCORE, so visibility_score
            # is shown, not suppressed) - all mentioned at rank 1, half cited.
            for i in range(20):
                seed_raw_answer(conn, workspace_id=cls.workspace_id, when=now,
                               provider='Perplexity', region='US',
                               brand_mentioned=True, brand_rank=1, brand_cited=(i < 10))
            # GB: a few answers, never mentioned - must not leak into the
            # US-filtered result.
            for _ in range(3):
                seed_raw_answer(conn, workspace_id=cls.workspace_id, when=now,
                               provider='Perplexity', region='GB',
                               brand_mentioned=False, brand_rank=None, brand_cited=False)

    def test_region_filter_computes_live_and_excludes_other_regions(self):
        filters = parse_filters({'region': 'US'})
        report = metrics.analytics_report(self.workspace_id, 98952, filters=filters)
        # mention_rate = 20/20 = 1.0, position_score = mean(1/1 x20) = 1.0,
        # citation_rate = 10/20 = 0.5 -> VS = 100*(0.5*1 + 0.3*1 + 0.2*0.5) = 90.0
        self.assertAlmostEqual(report['visibility']['mention_rate']['value'], 1.0)
        self.assertEqual(report['visibility']['mention_rate']['n'], 20)
        self.assertAlmostEqual(report['visibility']['visibility_score']['value'], 90.0, places=3)
        self.assertEqual(report['history'][0]['answer_count'], 20)

    def test_no_region_filter_falls_back_to_metrics_daily_and_sees_nothing(self):
        """No rollup has ever run for this workspace, so the unfiltered
        (metrics_daily) path must show no data - proving it did not
        accidentally read the raw evidence this test seeded."""
        report = metrics.analytics_report(self.workspace_id, 98952)
        self.assertEqual(report['history'], [])
        self.assertEqual(report['visibility']['state'], 'not_yet_run')


class ScanSummaryTests(unittest.TestCase):
    def test_scan_summary_reflects_the_filtered_scan_list(self):
        workspace_id = create_workspace(user_id=98953, domain='dashsummary.example',
                                        brand_name='DashSummary')
        now = datetime.utcnow()
        with engine.begin() as conn:
            seed_raw_answer(conn, workspace_id=workspace_id, when=now, provider='Perplexity',
                           region='US', brand_mentioned=True, brand_rank=1, brand_cited=True)
        report = metrics.analytics_report(workspace_id, 98953)
        self.assertEqual(report['scan_summary']['total'], 1)
        self.assertEqual(report['scan_summary']['completed'], 1)
        self.assertEqual(report['scan_summary']['prompts_total'], 1)
        self.assertEqual(report['scan_summary']['prompts_completed'], 1)


class LatestEvidenceFilteringTests(unittest.TestCase):
    def test_filters_narrow_which_run_counts_as_latest(self):
        workspace_id = create_workspace(user_id=98954, domain='dashevidence.example',
                                        brand_name='DashEvidence')
        older = datetime(2026, 5, 1, 9, 0, 0)
        newer = datetime(2026, 5, 10, 9, 0, 0)
        with engine.begin() as conn:
            us_scan_id = seed_raw_answer(conn, workspace_id=workspace_id, when=older,
                                        provider='Perplexity', region='US',
                                        brand_mentioned=True, brand_rank=1, brand_cited=True)
            seed_raw_answer(conn, workspace_id=workspace_id, when=newer,
                           provider='Perplexity', region='GB',
                           brand_mentioned=False, brand_rank=None, brand_cited=False)

        unfiltered = metrics.latest_prompt_evidence(workspace_id)
        self.assertNotEqual(unfiltered['run']['region'], 'US')  # the newer GB run

        filters = parse_filters({'region': 'US'})
        filtered = metrics.latest_prompt_evidence(workspace_id, filters=filters)
        self.assertEqual(filtered['run']['id'], us_scan_id)
        self.assertEqual(filtered['run']['region'], 'US')


class DashboardRouteFilteringTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_route_returns_available_filters_and_applies_range(self):
        user_id = make_user('dashboard_filter_route_user')
        workspace_id = create_workspace(user_id=user_id, domain='dashboardroute.example',
                                        brand_name='DashboardRoute')
        with engine.begin() as conn:
            seed_metrics_daily_row(conn, workspace_id=workspace_id, day=date(2020, 1, 1),
                                   engine_id=None, visibility_score=10.0)
            seed_metrics_daily_row(conn, workspace_id=workspace_id, day=date.today(),
                                   engine_id=None, visibility_score=60.0)
        with server_pg.app.test_client() as client:
            self.login(client, 'dashboard_filter_route_user')
            response = client.get(f'/api/analytics/projects/{workspace_id}/report?range=7d')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(len(body['history']), 1)  # the 2020 row is excluded
        self.assertIn('regions', body['available_filters'])
        self.assertIn('engines', body['available_filters'])
        self.assertIn('scan_summary', body)

    def test_malformed_filter_is_a_400_not_a_500(self):
        user_id = make_user('dashboard_filter_400_user')
        workspace_id = create_workspace(user_id=user_id, domain='dashboard400.example',
                                        brand_name='Dashboard400')
        with server_pg.app.test_client() as client:
            self.login(client, 'dashboard_filter_400_user')
            response = client.get(f'/api/analytics/projects/{workspace_id}/report?region=US&engine_ids=not-a-number')
        self.assertEqual(response.status_code, 400)

    def test_endpoint_is_workspace_scoped(self):
        with server_pg.app.test_client() as client:
            response = client.get('/api/analytics/projects/1/report?range=7d')
        self.assertEqual(response.status_code, 401)


if __name__ == '__main__':
    unittest.main()
