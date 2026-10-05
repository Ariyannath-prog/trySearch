"""Competitors page: app/metrics.py::competitor_intelligence() and the
competitor CRUD routes in app/routes/prompts.py.

competitor_intelligence() scores the brand and every tracked competitor with
the exact same formula (rollup.score_from_counts / visibility_score) and
cohort (workspace-scoped, run_type='scheduled' only) the brand's own official
Visibility Score is computed with, so this file's expected numbers are worked
out by hand against that same formula rather than re-deriving a parallel one.
"""

import os
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'competitors-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert, select  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import metrics  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    analytics_answer_sources,
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_tracked_prompts,
    competitors,
    extractions,
    mentions,
    users,
)

PASSWORD = 'competitors-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


def seed_answer(conn, *, scan_id, prompt_id, prompt_text, when,
                 brand_mentioned, brand_rank, brand_cited,
                 competitor_id=None, competitor_rank=None,
                 competitor_cited_domain=None):
    answer_id = conn.execute(insert(analytics_provider_answers).values(
        scan_run_id=scan_id, prompt_id=prompt_id, prompt_text=prompt_text,
        prompt_intent='Discovery', topic_name=None, provider='Perplexity',
        model='m', status='ok', search_request_id=None, answer_request_id=None,
        answer_text='text', raw_response='{}', latency_ms=1, error=None,
        created_at=when, completed_at=when,
    )).inserted_primary_key[0]
    extraction_id = conn.execute(insert(extractions).values(
        answer_id=answer_id, extractor_version='t-competitors', is_current=True,
        brand_mentioned=brand_mentioned, brand_rank=brand_rank if brand_mentioned else None,
        brand_cited=brand_cited, created_at=when,
    )).inserted_primary_key[0]
    if brand_mentioned:
        conn.execute(insert(mentions).values(
            extraction_id=extraction_id, entity_type='brand', competitor_id=None,
            rank=brand_rank, char_offset=0,
        ))
    if competitor_id is not None and competitor_rank is not None:
        conn.execute(insert(mentions).values(
            extraction_id=extraction_id, entity_type='competitor', competitor_id=competitor_id,
            rank=competitor_rank, char_offset=20,
        ))
    if brand_cited:
        conn.execute(insert(analytics_answer_sources).values(
            answer_id=answer_id, rank=1, source_kind='search_result', title='t',
            url='https://acme3.example/x', domain='acme3.example', snippet=None,
            published_at=None, category='own'))
    if competitor_cited_domain:
        conn.execute(insert(analytics_answer_sources).values(
            answer_id=answer_id, rank=1, source_kind='search_result', title='t',
            url=f'https://{competitor_cited_domain}/y', domain=competitor_cited_domain,
            snippet=None, published_at=None, category='competitor'))
    return answer_id


class CompetitorIntelligenceTests(unittest.TestCase):
    """20 scheduled answers across two days - exactly MIN_ANSWERS_FOR_SCORE,
    so the visibility score is shown, not suppressed."""

    @classmethod
    def setUpClass(cls):
        cls.workspace_id = create_workspace(user_id=98001, domain='acme3.example',
                                            brand_name='Acme3')
        cls.day1 = datetime(2026, 1, 1, 12, 0, 0)
        cls.day2 = datetime(2026, 1, 4, 12, 0, 0)
        with engine.begin() as conn:
            cls.competitor_id = conn.execute(insert(competitors).values(
                workspace_id=cls.workspace_id, name='RivalCo', domains=['rival3.example'],
                aliases=[], created_at=cls.day1,
            )).inserted_primary_key[0]
            prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
                workspace_id=cls.workspace_id, topic_id=None, prompt='best crm for startups',
                intent='Discovery', active=True, created_at=cls.day1, updated_at=cls.day1,
            )).inserted_primary_key[0]
            scan1 = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=cls.workspace_id, job_id=None, provider='Perplexity', model='m',
                region=None, competitor_snapshot='[]', status='succeeded', run_type='scheduled',
                prompt_count=10, completed_count=10, mention_rate=None, citation_rate=None,
                source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                error=None, created_at=cls.day1, completed_at=cls.day1,
            )).inserted_primary_key[0]
            scan2 = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=cls.workspace_id, job_id=None, provider='Perplexity', model='m',
                region=None, competitor_snapshot='[]', status='succeeded', run_type='scheduled',
                prompt_count=10, completed_count=10, mention_rate=None, citation_rate=None,
                source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                error=None, created_at=cls.day2, completed_at=cls.day2,
            )).inserted_primary_key[0]

            # Day 1 (10 answers): brand rank 2 when mentioned (1-6), absent (7-10).
            # Competitor beats the brand in 1-4 (rank 1 vs 2) and is the only
            # mention in 7-9 (brand absent) - 7 outperform candidates on day 1,
            # 4 of which pass the "competitor ranked ahead" test explicitly.
            for i in range(1, 7):  # 1-6: brand mentioned, rank 2
                seed_answer(conn, scan_id=scan1, prompt_id=prompt_id,
                           prompt_text='best crm for startups', when=cls.day1,
                           brand_mentioned=True, brand_rank=2, brand_cited=(i <= 3),
                           competitor_id=cls.competitor_id if i <= 4 else None,
                           competitor_rank=1 if i <= 4 else None,
                           competitor_cited_domain='rival3.example' if i <= 2 else None)
            for i in range(7, 11):  # 7-10: brand absent
                seed_answer(conn, scan_id=scan1, prompt_id=prompt_id,
                           prompt_text='best crm for startups', when=cls.day1,
                           brand_mentioned=False, brand_rank=None, brand_cited=False,
                           competitor_id=cls.competitor_id if i <= 9 else None,
                           competitor_rank=1 if i <= 9 else None)

            # Day 2 (10 answers): brand mentioned in all 10 (rank 1 for 11-15,
            # rank 3 for 16-20). Competitor mentioned once (11), rank 2 - worse
            # than the brand's rank 1 there, so NOT an outperform case.
            for i in range(11, 16):  # 11-15: brand rank 1
                seed_answer(conn, scan_id=scan2, prompt_id=prompt_id,
                           prompt_text='best crm for startups', when=cls.day2,
                           brand_mentioned=True, brand_rank=1, brand_cited=(i <= 12),
                           competitor_id=cls.competitor_id if i == 11 else None,
                           competitor_rank=2 if i == 11 else None)
            for i in range(16, 21):  # 16-20: brand rank 3
                seed_answer(conn, scan_id=scan2, prompt_id=prompt_id,
                           prompt_text='best crm for startups', when=cls.day2,
                           brand_mentioned=True, brand_rank=3, brand_cited=False)

        cls.intel = metrics.competitor_intelligence(cls.workspace_id)

    def entity(self, entity_id):
        return {e['id']: e for e in self.intel['entities']}[entity_id]

    def test_measured_answer_count_and_threshold(self):
        self.assertEqual(self.intel['measured_answer_count'], 20)
        self.assertEqual(self.intel['threshold'], 20)

    def test_brand_metrics(self):
        brand = self.entity('brand')
        self.assertEqual(brand['tracked'], True)
        self.assertAlmostEqual(brand['mention_rate']['value'], 0.8)
        self.assertEqual(brand['mention_rate']['n'], 20)
        self.assertAlmostEqual(brand['citation_rate']['value'], 0.25)
        self.assertAlmostEqual(brand['average_rank'], 2.0)
        self.assertIsNotNone(brand['visibility_score'])
        self.assertAlmostEqual(brand['visibility_score'], 63.125, places=2)

    def test_competitor_metrics(self):
        rival = self.entity(self.competitor_id)
        self.assertEqual(rival['tracked'], False)
        self.assertEqual(rival['name'], 'RivalCo')
        self.assertAlmostEqual(rival['mention_rate']['value'], 0.4)
        self.assertAlmostEqual(rival['citation_rate']['value'], 0.1)
        self.assertAlmostEqual(rival['average_rank'], 1.125, places=1)
        self.assertAlmostEqual(rival['visibility_score'], 50.125, places=2)

    def test_share_of_voice_sums_across_brand_and_competitor(self):
        brand = self.entity('brand')
        rival = self.entity(self.competitor_id)
        self.assertAlmostEqual(brand['share_of_voice'], 16 / 24, places=4)
        self.assertAlmostEqual(rival['share_of_voice'], 8 / 24, places=4)

    def test_trend_has_one_point_per_measured_day(self):
        brand_trend = {row['date']: row for row in self.intel['trend']['brand']}
        self.assertEqual(set(brand_trend.keys()), {'2026-01-01', '2026-01-04'})
        self.assertAlmostEqual(brand_trend['2026-01-01']['visibility_score'], 51.0, places=1)
        self.assertAlmostEqual(brand_trend['2026-01-04']['visibility_score'], 74.0, places=1)

        rival_trend = {row['date']: row for row in self.intel['trend'][str(self.competitor_id)]}
        self.assertAlmostEqual(rival_trend['2026-01-01']['visibility_score'], 69.0, places=1)
        self.assertAlmostEqual(rival_trend['2026-01-04']['visibility_score'], 20.0, places=1)

    def test_outperforms_only_lists_answers_where_competitor_ranked_ahead(self):
        outperforms = self.intel['outperforms']
        self.assertEqual(len(outperforms), 7)
        for row in outperforms:
            self.assertEqual(row['competitor_name'], 'RivalCo')
            if row['brand_rank'] is not None:
                self.assertLess(row['competitor_rank'], row['brand_rank'])


class CompetitorScoreSuppressionTests(unittest.TestCase):
    """Below MIN_ANSWERS_FOR_SCORE, visibility_score is withheld - same rule
    the brand's own score follows - but mention/citation rate are still shown."""

    def test_visibility_score_is_none_below_threshold(self):
        workspace_id = create_workspace(user_id=98002, domain='smallsample.example',
                                        brand_name='SmallSample')
        with engine.begin() as conn:
            competitor_id = conn.execute(insert(competitors).values(
                workspace_id=workspace_id, name='TinyRival', domains=[], aliases=[],
                created_at=datetime.utcnow(),
            )).inserted_primary_key[0]
            prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
                workspace_id=workspace_id, topic_id=None, prompt='best crm for startups',
                intent='Discovery', active=True, created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )).inserted_primary_key[0]
            scan_id = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=workspace_id, job_id=None, provider='Perplexity', model='m',
                region=None, competitor_snapshot='[]', status='succeeded', run_type='scheduled',
                prompt_count=3, completed_count=3, mention_rate=None, citation_rate=None,
                source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                error=None, created_at=datetime.utcnow(), completed_at=datetime.utcnow(),
            )).inserted_primary_key[0]
            for _ in range(3):
                seed_answer(conn, scan_id=scan_id, prompt_id=prompt_id,
                           prompt_text='best crm for startups', when=datetime.utcnow(),
                           brand_mentioned=True, brand_rank=1, brand_cited=True)
        intel = metrics.competitor_intelligence(workspace_id)
        brand = {e['id']: e for e in intel['entities']}['brand']
        self.assertEqual(intel['measured_answer_count'], 3)
        self.assertIsNone(brand['visibility_score'])
        self.assertAlmostEqual(brand['mention_rate']['value'], 1.0)

    def test_no_competitors_still_returns_the_brand_alone(self):
        workspace_id = create_workspace(user_id=98003, domain='nocompetitors.example',
                                        brand_name='NoCompetitors')
        intel = metrics.competitor_intelligence(workspace_id)
        self.assertEqual(intel['measured_answer_count'], 0)
        self.assertEqual(len(intel['entities']), 1)
        self.assertTrue(intel['entities'][0]['tracked'])
        self.assertEqual(intel['outperforms'], [])


class CompetitorRouteTests(unittest.TestCase):
    """CRUD + the intelligence GET, at the shared /competitors path."""

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def new_workspace(self, tag, user_id):
        return create_workspace(user_id=user_id, domain=f'{tag}.example', brand_name=tag)

    def test_get_returns_the_intelligence_payload(self):
        user_id = make_user('competitors_get_user')
        workspace_id = self.new_workspace('competitorsget', user_id)
        with server_pg.app.test_client() as client:
            self.login(client, 'competitors_get_user')
            response = client.get(f'/api/analytics/projects/{workspace_id}/competitors')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn('intelligence', body)
        self.assertIn('entities', body['intelligence'])
        self.assertTrue(body['intelligence']['entities'][0]['tracked'])

    def test_create_then_patch_then_delete(self):
        user_id = make_user('competitors_crud_user')
        workspace_id = self.new_workspace('competitorscrud', user_id)
        with server_pg.app.test_client() as client:
            self.login(client, 'competitors_crud_user')
            create = client.post(f'/api/analytics/projects/{workspace_id}/competitors',
                                 json={'name': 'FirstName', 'domain': 'first.example'})
            self.assertEqual(create.status_code, 201)
            competitor_id = create.get_json()['competitor']['id']

            patch = client.patch(
                f'/api/analytics/projects/{workspace_id}/competitors/{competitor_id}',
                json={'name': 'RenamedName'})
            self.assertEqual(patch.status_code, 200)
            self.assertEqual(patch.get_json()['competitor']['name'], 'RenamedName')

            get_after_patch = client.get(f'/api/analytics/projects/{workspace_id}/competitors')
            names = [e['name'] for e in get_after_patch.get_json()['intelligence']['entities']]
            self.assertIn('RenamedName', names)

            delete = client.delete(
                f'/api/analytics/projects/{workspace_id}/competitors/{competitor_id}')
            self.assertEqual(delete.status_code, 200)

            get_after_delete = client.get(f'/api/analytics/projects/{workspace_id}/competitors')
            names_after = [e['name'] for e in get_after_delete.get_json()['intelligence']['entities']]
            self.assertNotIn('RenamedName', names_after)

    def test_patch_rejects_unknown_competitor(self):
        user_id = make_user('competitors_patch404_user')
        workspace_id = self.new_workspace('competitorspatch404', user_id)
        with server_pg.app.test_client() as client:
            self.login(client, 'competitors_patch404_user')
            response = client.patch(
                f'/api/analytics/projects/{workspace_id}/competitors/999999',
                json={'name': 'Whoever'})
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
