"""The dashboard shows a scan the moment it finishes.

Two defects met here and between them blanked the whole Dashboard for a
workspace whose real scans had all been run by hand:

1. app/static/js/components/filters.js sends `?engine_ids=1,4,11`, and
   parse_engine_ids() rejected exactly that form on a real request, because
   MultiDict.getlist() returns ['1,4,11'] - one truthy element - so the
   comma-splitting fallback never ran. Every filtered request was a 400 and
   the page silently kept its empty first render.
2. metrics_daily is scheduled-only by design (PRD §13), so even once the
   request succeeded the KPI cards had nothing behind them: an on-demand
   scan never reaches the rollup.

The daily *trend* stays scheduled-only - that part of PRD §13 is the point
and is asserted here too. What changed is that the cards fall back to a
live read of the same evidence, through the unmodified score_from_counts()/
blend(), when the rollup has measured nothing in range.
"""

import os
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'dashboard-on-demand-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert, select  # noqa: E402
from werkzeug.datastructures import MultiDict  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import metrics  # noqa: E402
from app import rollup  # noqa: E402
from app.analytics_filters import parse_filters  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_tracked_prompts,
    engines as engines_table,
    extractions,
    users,
)

PASSWORD = 'dashboard-on-demand-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


def engine_id_for(key):
    with engine.connect() as conn:
        return conn.execute(
            select(engines_table.c.id).where(engines_table.c.key == key)
        ).scalar_one_or_none()


def ensure_engine(key, display_name):
    """The conftest seed only guarantees Perplexity; the multi-engine cases
    need real rows for the others rather than invented ids."""
    existing = engine_id_for(key)
    if existing is not None:
        return existing
    with engine.begin() as conn:
        return conn.execute(insert(engines_table).values(
            key=key, display_name=display_name, source_type='api',
            adapter_version='test', enabled=True,
        )).inserted_primary_key[0]


def seed_scan(workspace_id, *, when, run_type='on_demand', run_provider='Perplexity',
              region=None, status='partial', answers=()):
    """One scan run plus its answers.

    `answers` is [(provider, brand_mentioned, brand_rank, brand_cited), ...].
    A provider whose entry is None models a failed answer: the row exists,
    no extraction does, exactly like a provider call that errored in
    production. Those must not land in any denominator.
    """
    with engine.begin() as conn:
        prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
            workspace_id=workspace_id, topic_id=None, prompt='best crm for startups',
            intent='Discovery', active=True, created_at=when, updated_at=when,
        )).inserted_primary_key[0]
        scan_id = conn.execute(insert(analytics_prompt_scan_runs).values(
            workspace_id=workspace_id, job_id=None, provider=run_provider, model='m',
            region=region, competitor_snapshot='[]', status=status, run_type=run_type,
            prompt_count=1, completed_count=1, mention_rate=None, citation_rate=None,
            source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
            error=None, created_at=when, completed_at=when,
        )).inserted_primary_key[0]
        for provider, mentioned, rank, cited in answers:
            answer_id = conn.execute(insert(analytics_provider_answers).values(
                scan_run_id=scan_id, prompt_id=prompt_id, prompt_text='best crm for startups',
                prompt_intent='Discovery', topic_name=None, provider=provider, model='m',
                status='succeeded' if mentioned is not None else 'failed',
                answer_text='text' if mentioned is not None else None,
                raw_response='{}', latency_ms=1,
                error=None if mentioned is not None else 'ProviderAPIError',
                created_at=when, completed_at=when,
            )).inserted_primary_key[0]
            if mentioned is None:
                continue
            conn.execute(insert(extractions).values(
                answer_id=answer_id, extractor_version='test', is_current=True,
                brand_mentioned=mentioned, brand_rank=rank, brand_cited=cited,
                created_at=when,
            ))
    return scan_id


class EngineIdQueryStringFormTests(unittest.TestCase):
    """Both wire forms, parsed off a real MultiDict - the thing a plain dict
    in the older unit tests could never have caught."""

    def test_comma_separated_form_from_a_real_multidict(self):
        args = MultiDict([('range', 'today'), ('engine_ids', '1,4,11')])
        self.assertEqual(parse_filters(args)['engine_ids'], [1, 4, 11])

    def test_repeated_parameter_form_from_a_real_multidict(self):
        args = MultiDict([('engine_ids', '1'), ('engine_ids', '4'), ('engine_ids', '11')])
        self.assertEqual(parse_filters(args)['engine_ids'], [1, 4, 11])

    def test_both_forms_at_once_are_merged_and_deduped(self):
        args = MultiDict([('engine_ids', '1,4'), ('engine_ids', '4'), ('engine_ids', '11')])
        self.assertEqual(parse_filters(args)['engine_ids'], [1, 4, 11])

    def test_a_single_id_still_works(self):
        self.assertEqual(parse_filters(MultiDict([('engine_ids', '7')]))['engine_ids'], [7])

    def test_absent_is_still_no_filter(self):
        self.assertIsNone(parse_filters(MultiDict([('range', 'today')]))['engine_ids'])

    def test_blank_values_are_not_a_filter(self):
        self.assertIsNone(parse_filters(MultiDict([('engine_ids', '')]))['engine_ids'])

    def test_non_integer_is_still_a_filter_error(self):
        from app.analytics_filters import FilterError
        with self.assertRaises(FilterError):
            parse_filters(MultiDict([('engine_ids', '1,perplexity')]))


class OnDemandScanReachesTheDashboardTests(unittest.TestCase):
    """A workspace whose only measurements are on-demand, which is what
    every workspace looks like right after its first manual scan."""

    @classmethod
    def setUpClass(cls):
        cls.user_id = make_user('ondemand_dash_user')
        cls.workspace_id = create_workspace(
            user_id=cls.user_id, domain='ondemanddash.example', brand_name='OnDemandDash')
        cls.perplexity_id = engine_id_for('perplexity')
        cls.openai_id = ensure_engine('openai', 'OpenAI')
        cls.openrouter_id = ensure_engine('openrouter', 'OpenRouter')
        cls.when = datetime.utcnow()
        # The production shape: a multi-engine run labelled 'Perplexity' whose
        # Perplexity and OpenAI calls failed and whose OpenRouter call answered.
        cls.scan_id = seed_scan(
            cls.workspace_id, when=cls.when, run_type='on_demand',
            run_provider='Perplexity', status='partial',
            answers=[
                ('Perplexity', None, None, None),
                ('OpenAI', None, None, None),
                ('OpenRouter', True, 1, False),
            ])

    def test_the_scan_is_listed_and_summarised(self):
        report = metrics.analytics_report(self.workspace_id, self.user_id)
        self.assertEqual(report['scan_summary']['total'], 1)
        self.assertEqual(report['scan_summary']['partial'], 1)

    def test_kpi_cards_are_computed_from_the_on_demand_answers(self):
        report = metrics.analytics_report(self.workspace_id, self.user_id)
        visibility = report['visibility']
        self.assertEqual(visibility['source'], 'live_scan_evidence')
        self.assertTrue(visibility['includes_on_demand'])
        # One answer was actually measured. The two failed provider calls have
        # no extraction and must not be counted as "ran and was absent".
        self.assertEqual(visibility['n'], 1)
        self.assertEqual(visibility['mention_rate']['value'], 1.0)
        self.assertEqual(visibility['mention_rate']['n'], 1)
        self.assertEqual(visibility['citation_rate']['value'], 0.0)

    def test_visibility_score_is_withheld_not_invented(self):
        report = metrics.analytics_report(self.workspace_id, self.user_id)
        visibility = report['visibility']
        self.assertIsNone(visibility['visibility_score'],
                          'one answer cannot support a Visibility Score')
        self.assertEqual(visibility['state'], 'insufficient')
        self.assertEqual(visibility['threshold'], rollup_threshold())

    def test_the_daily_trend_stays_empty_until_scheduled_data_exists(self):
        report = metrics.analytics_report(self.workspace_id, self.user_id)
        self.assertEqual(report['history'], [],
                         'metrics_daily is scheduled-only; the trend must not '
                         'be back-filled from on-demand runs')

    def test_the_rollup_itself_still_refuses_on_demand_runs(self):
        blended = rollup.rollup_workspace_day(self.workspace_id, self.when.date())
        self.assertEqual(blended['answer_count'], 0)
        self.assertIsNone(blended['visibility_score'])

    def test_per_engine_rows_name_the_engine_that_actually_answered(self):
        report = metrics.analytics_report(self.workspace_id, self.user_id)
        names = {row['display_name'] for row in report['engines']}
        self.assertEqual(names, {'OpenRouter'},
                         'only the engine with a measured answer is comparable')

    def test_date_filter_includes_today_and_excludes_other_days(self):
        today = metrics.analytics_report(
            self.workspace_id, self.user_id, filters=parse_filters(MultiDict([('range', 'today')])))
        self.assertEqual(today['scan_summary']['total'], 1)
        self.assertEqual(today['visibility']['n'], 1)

        yesterday = (self.when - timedelta(days=1)).date().isoformat()
        elsewhere = metrics.analytics_report(
            self.workspace_id, self.user_id,
            filters=parse_filters(MultiDict([('start_date', yesterday), ('end_date', yesterday)])))
        self.assertEqual(elsewhere['scan_summary']['total'], 0)
        self.assertEqual(elsewhere['visibility']['n'], 0)
        self.assertEqual(elsewhere['visibility']['state'], 'not_yet_run')

    def test_engine_filter_selects_the_engine_that_answered(self):
        filters = parse_filters(MultiDict([('engine_ids', str(self.openrouter_id))]))
        report = metrics.analytics_report(self.workspace_id, self.user_id, filters=filters)
        self.assertEqual(report['visibility']['n'], 1)
        self.assertEqual(report['scan_summary']['total'], 1,
                         'a multi-engine run must be listed under an engine it '
                         'actually answered on, not only its legacy run label')

    def test_engine_filter_excluding_the_answering_engine_measures_nothing(self):
        filters = parse_filters(MultiDict([('engine_ids', str(self.openai_id))]))
        report = metrics.analytics_report(self.workspace_id, self.user_id, filters=filters)
        # OpenAI's call failed, so there is no measured answer to count - but
        # the run is still listed, because it did attempt that engine.
        self.assertEqual(report['visibility']['n'], 0)
        self.assertEqual(report['visibility']['state'], 'not_yet_run')

    def test_all_engines_selected_matches_no_engine_filter(self):
        every = MultiDict([('engine_ids',
                            f'{self.perplexity_id},{self.openai_id},{self.openrouter_id}')])
        filtered = metrics.analytics_report(
            self.workspace_id, self.user_id, filters=parse_filters(every))
        unfiltered = metrics.analytics_report(self.workspace_id, self.user_id)
        self.assertEqual(filtered['visibility']['n'], unfiltered['visibility']['n'])
        self.assertEqual(filtered['visibility']['mention_rate']['value'],
                         unfiltered['visibility']['mention_rate']['value'])

    def test_region_filter_narrows_live_on_demand_evidence(self):
        workspace_id = create_workspace(user_id=self.user_id, domain='ondemandregion.example',
                                        brand_name='OnDemandRegion')
        now = datetime.utcnow()
        seed_scan(workspace_id, when=now, run_type='on_demand', region='US',
                  answers=[('Perplexity', True, 1, True)])
        seed_scan(workspace_id, when=now, run_type='on_demand', region='GB',
                  answers=[('Perplexity', False, None, False)])

        us = metrics.analytics_report(workspace_id, self.user_id,
                                      filters=parse_filters(MultiDict([('region', 'US')])))
        self.assertEqual(us['visibility']['n'], 1)
        self.assertEqual(us['visibility']['mention_rate']['value'], 1.0)

        gb = metrics.analytics_report(workspace_id, self.user_id,
                                      filters=parse_filters(MultiDict([('region', 'GB')])))
        self.assertEqual(gb['visibility']['n'], 1)
        self.assertEqual(gb['visibility']['mention_rate']['value'], 0.0)


class ScheduledDataStillWinsTests(unittest.TestCase):
    """The fallback is a fallback. A workspace with a real rollup must keep
    reading it, on-demand runs excluded exactly as PRD §13 says."""

    def test_rollup_numbers_are_used_when_the_rollup_has_measured_something(self):
        user_id = make_user('scheduled_wins_user')
        workspace_id = create_workspace(user_id=user_id, domain='scheduledwins.example',
                                        brand_name='ScheduledWins')
        now = datetime.utcnow()
        # 20 scheduled answers, none mentioning the brand...
        seed_scan(workspace_id, when=now, run_type='scheduled',
                  answers=[('Perplexity', False, None, False)] * 20)
        rollup.rollup_workspace_day(workspace_id, now.date())
        # ...and a flattering on-demand run on the same day, which must not
        # be blended into the cards now that the rollup has real numbers.
        seed_scan(workspace_id, when=now, run_type='on_demand',
                  answers=[('Perplexity', True, 1, True)] * 5)

        report = metrics.analytics_report(workspace_id, user_id)
        self.assertEqual(report['visibility']['source'], 'scheduled_rollup')
        self.assertFalse(report['visibility']['includes_on_demand'])
        self.assertEqual(report['visibility']['n'], 20)
        self.assertEqual(report['visibility']['mention_rate']['value'], 0.0)


class DashboardEndpointTests(unittest.TestCase):
    """End to end over HTTP, with the exact query string the browser sends."""

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def setUp(self):
        self.username = f'dash_route_user_{self.id().rsplit(".", 1)[-1]}'
        self.user_id = make_user(self.username)
        self.workspace_id = create_workspace(
            user_id=self.user_id, domain=f'{self.username}.example', brand_name='DashRoute')
        self.perplexity_id = engine_id_for('perplexity')
        self.openai_id = ensure_engine('openai', 'OpenAI')
        self.openrouter_id = ensure_engine('openrouter', 'OpenRouter')
        seed_scan(self.workspace_id, when=datetime.utcnow(), run_type='on_demand',
                  answers=[('OpenRouter', True, 1, False)])

    def get(self, query_string):
        with server_pg.app.test_client() as client:
            self.login(client, self.username)
            return client.get(
                f'/api/analytics/projects/{self.workspace_id}/report{query_string}')

    def test_comma_separated_engine_ids_is_200_not_400(self):
        ids = f'{self.perplexity_id},{self.openai_id},{self.openrouter_id}'
        response = self.get(f'?range=today&engine_ids={ids}')
        self.assertEqual(response.status_code, 200,
                         'the comma form is what filters.js sends')
        self.assertEqual(response.get_json()['scan_summary']['total'], 1)

    def test_repeated_engine_ids_is_200(self):
        query = (f'?range=today&engine_ids={self.perplexity_id}'
                 f'&engine_ids={self.openai_id}&engine_ids={self.openrouter_id}')
        response = self.get(query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['scan_summary']['total'], 1)

    def test_both_forms_agree(self):
        ids = f'{self.perplexity_id},{self.openai_id},{self.openrouter_id}'
        comma = self.get(f'?range=today&engine_ids={ids}').get_json()
        repeated = self.get(
            f'?range=today&engine_ids={self.perplexity_id}'
            f'&engine_ids={self.openai_id}&engine_ids={self.openrouter_id}').get_json()
        self.assertEqual(comma['visibility']['n'], repeated['visibility']['n'])
        self.assertEqual(comma['scan_summary'], repeated['scan_summary'])

    def test_the_on_demand_scan_is_visible_over_http(self):
        body = self.get('?range=today').get_json()
        self.assertEqual(body['scan_summary']['total'], 1)
        self.assertEqual(body['visibility']['n'], 1)
        self.assertEqual(body['visibility']['source'], 'live_scan_evidence')

    def test_available_filters_still_come_back(self):
        body = self.get('').get_json()
        self.assertIn('regions', body['available_filters'])
        self.assertIn('engines', body['available_filters'])

    def test_a_genuinely_malformed_engine_id_is_still_a_400(self):
        response = self.get('?engine_ids=perplexity')
        self.assertEqual(response.status_code, 400)


def rollup_threshold():
    from app.stats import MIN_ANSWERS_FOR_SCORE
    return MIN_ANSWERS_FOR_SCORE


if __name__ == '__main__':
    unittest.main()
