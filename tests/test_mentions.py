"""Mentions / AI Answer Intelligence page (/mentions) and
app/metrics.py::mention_listing() - a workspace-wide, all-runs answer
listing that reuses answer_derivations() for the brand fields (already
covered by tests/test_visibility_score.py and friends) and only adds new
logic for the context snippet and competitor attribution, both read from
the already-populated mentions table.
"""

import os
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'mentions-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import metrics  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    analytics_answer_sources,
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_topics,
    analytics_tracked_prompts,
    competitors,
    extractions,
    mentions,
    users,
)

PASSWORD = 'mentions-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


class ContextSnippetTests(unittest.TestCase):
    def test_snippet_is_a_real_window_around_the_offset(self):
        text_value = 'x' * 100 + 'Acme' + 'y' * 100
        snippet = metrics._context_snippet(text_value, 100, radius=20)
        self.assertIn('Acme', snippet)
        self.assertTrue(snippet.startswith('…'))
        self.assertTrue(snippet.endswith('…'))

    def test_no_offset_is_none(self):
        self.assertIsNone(metrics._context_snippet('some text', None))

    def test_no_text_is_none(self):
        self.assertIsNone(metrics._context_snippet(None, 5))


class MentionListingTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.workspace_id = create_workspace(user_id=99101, domain='mentionsco.example',
                                            brand_name='Acme')
        older = datetime(2026, 2, 1, 9, 0, 0)
        newer = datetime(2026, 2, 3, 9, 0, 0)
        with engine.begin() as conn:
            cls.competitor_id = conn.execute(insert(competitors).values(
                workspace_id=cls.workspace_id, name='RivalX', domains=['rivalx.example'],
                aliases=[], created_at=older,
            )).inserted_primary_key[0]
            topic_id = conn.execute(insert(analytics_topics).values(
                workspace_id=cls.workspace_id, name='Pricing', created_at=older,
            )).inserted_primary_key[0]
            prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
                workspace_id=cls.workspace_id, topic_id=topic_id, prompt='best crm for startups',
                intent='Discovery', active=True, created_at=older, updated_at=older,
            )).inserted_primary_key[0]
            scan1 = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=cls.workspace_id, job_id=None, provider='Perplexity', model='m',
                region=None, competitor_snapshot='[]', status='succeeded', run_type='scheduled',
                prompt_count=2, completed_count=2, mention_rate=None, citation_rate=None,
                source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                error=None, created_at=older, completed_at=older,
            )).inserted_primary_key[0]
            scan2 = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=cls.workspace_id, job_id=None, provider='OpenAI', model='m',
                region=None, competitor_snapshot='[]', status='succeeded', run_type='on_demand',
                prompt_count=1, completed_count=1, mention_rate=None, citation_rate=None,
                source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                error=None, created_at=newer, completed_at=newer,
            )).inserted_primary_key[0]

            prefix = 'Looking at solutions, '
            answer1_text = prefix + 'Acme stands out for enterprise teams evaluating this space.'
            brand_offset = len(prefix)
            answer1 = conn.execute(insert(analytics_provider_answers).values(
                scan_run_id=scan1, prompt_id=prompt_id, prompt_text='best crm for startups',
                prompt_intent='Discovery', topic_name=None, provider='Perplexity', model='m',
                status='ok', search_request_id=None, answer_request_id=None,
                answer_text=answer1_text, raw_response='{}', latency_ms=1, error=None,
                created_at=older, completed_at=older,
            )).inserted_primary_key[0]
            extraction1 = conn.execute(insert(extractions).values(
                answer_id=answer1, extractor_version='t-mentions', is_current=True,
                brand_mentioned=True, brand_rank=2, brand_cited=True, created_at=older,
            )).inserted_primary_key[0]
            conn.execute(insert(mentions).values(
                extraction_id=extraction1, entity_type='brand', competitor_id=None,
                rank=2, char_offset=brand_offset))
            conn.execute(insert(mentions).values(
                extraction_id=extraction1, entity_type='competitor',
                competitor_id=cls.competitor_id, rank=1, char_offset=0))
            conn.execute(insert(analytics_answer_sources).values(
                answer_id=answer1, rank=1, source_kind='search_result', title='t',
                url='https://mentionsco.example/x', domain='mentionsco.example', snippet=None,
                published_at=None, category='own'))

            answer2 = conn.execute(insert(analytics_provider_answers).values(
                scan_run_id=scan1, prompt_id=prompt_id, prompt_text='best crm for startups',
                prompt_intent='Discovery', topic_name=None, provider='Perplexity', model='m',
                status='ok', search_request_id=None, answer_request_id=None,
                answer_text='No relevant brand named here at all.', raw_response='{}',
                latency_ms=1, error=None, created_at=older, completed_at=older,
            )).inserted_primary_key[0]
            conn.execute(insert(extractions).values(
                answer_id=answer2, extractor_version='t-mentions', is_current=True,
                brand_mentioned=False, brand_rank=None, brand_cited=False, created_at=older,
            ))

            answer3 = conn.execute(insert(analytics_provider_answers).values(
                scan_run_id=scan2, prompt_id=prompt_id, prompt_text='best crm for startups',
                prompt_intent='Discovery', topic_name=None, provider='OpenAI', model='m',
                status='ok', search_request_id=None, answer_request_id=None,
                answer_text='Acme leads, RivalX trails behind in this comparison.',
                raw_response='{}', latency_ms=1, error=None, created_at=newer, completed_at=newer,
            )).inserted_primary_key[0]
            extraction3 = conn.execute(insert(extractions).values(
                answer_id=answer3, extractor_version='t-mentions', is_current=True,
                brand_mentioned=True, brand_rank=1, brand_cited=False, created_at=newer,
            )).inserted_primary_key[0]
            conn.execute(insert(mentions).values(
                extraction_id=extraction3, entity_type='brand', competitor_id=None,
                rank=1, char_offset=0))
            conn.execute(insert(mentions).values(
                extraction_id=extraction3, entity_type='competitor',
                competitor_id=cls.competitor_id, rank=2, char_offset=13))

        cls.answer1, cls.answer2, cls.answer3 = answer1, answer2, answer3
        cls.listing = metrics.mention_listing(cls.workspace_id)

    def by_id(self, answer_id):
        return {row['id']: row for row in self.listing}[answer_id]

    def test_lists_every_answer_across_both_runs(self):
        self.assertEqual(len(self.listing), 3)

    def test_ordered_newest_first(self):
        self.assertEqual(self.listing[0]['id'], self.answer3)

    def test_reuses_answer_derivations_for_brand_fields(self):
        row = self.by_id(self.answer1)
        self.assertTrue(row['brand_mentioned'])
        self.assertEqual(row['brand_rank'], 2)
        self.assertTrue(row['brand_cited'])
        self.assertTrue(row['source_present'])
        self.assertEqual(row['best_source_rank'], 1)

        absent = self.by_id(self.answer2)
        self.assertFalse(absent['brand_mentioned'])
        self.assertIsNone(absent['brand_rank'])

    def test_context_snippet_is_read_from_the_stored_offset(self):
        row = self.by_id(self.answer1)
        self.assertIsNotNone(row['context'])
        self.assertIn('Acme', row['context'])

    def test_competitor_attribution_includes_name_and_rank(self):
        row = self.by_id(self.answer1)
        self.assertEqual(len(row['competitors']), 1)
        self.assertEqual(row['competitors'][0]['name'], 'RivalX')
        self.assertEqual(row['competitors'][0]['rank'], 1)

    def test_topic_resolved_via_tracked_prompt(self):
        row = self.by_id(self.answer1)
        self.assertEqual(row['topic_name'], 'Pricing')

    def test_answer_preview_present_and_answer_text_not_leaked_raw(self):
        row = self.by_id(self.answer1)
        self.assertIn('answer_preview', row)
        self.assertNotIn('answer_text', row)

    def test_includes_on_demand_runs_unlike_the_scored_metrics(self):
        """Unlike collect_counts()/competitor_intelligence(), this is a raw
        evidence browser - an on-demand run's answers are still real
        evidence and must not be silently dropped."""
        row = self.by_id(self.answer3)
        self.assertEqual(row['run_type'], 'on_demand')


class MentionsEndpointTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_get_returns_project_and_mentions(self):
        user_id = make_user('mentions_route_user')
        workspace_id = create_workspace(user_id=user_id, domain='mentionsroute.example',
                                        brand_name='MentionsRoute')
        with server_pg.app.test_client() as client:
            self.login(client, 'mentions_route_user')
            response = client.get(f'/api/analytics/projects/{workspace_id}/mentions')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn('project', body)
        self.assertEqual(body['mentions'], [])

    def test_endpoint_is_workspace_scoped(self):
        with server_pg.app.test_client() as client:
            response = client.get('/api/analytics/projects/1/mentions')
        self.assertEqual(response.status_code, 401)


class MentionsPageTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_mentions_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/mentions')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_mentions_returns_200_for_logged_in_user(self):
        make_user('pages_mentions_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_mentions_user')
            response = client.get('/mentions')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


if __name__ == '__main__':
    unittest.main()
