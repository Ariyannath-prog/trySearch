"""Recommendations / Opportunities page (/recommendations) and
app/metrics.py::recommendation_intelligence() - a read-only merge of the
only two things in this backend that generate an actual recommendation:
analytics_content_opportunities (app/scanning.py) and
analytics_audit_findings (via the existing latest_site_audit(), untouched
here). No new recommendation-generation logic is added by this file or the
function it tests.
"""

import os
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'recommendations-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import metrics  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    analytics_audit_findings,
    analytics_audit_pages,
    analytics_content_opportunities,
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_site_audits,
    analytics_topics,
    analytics_tracked_prompts,
    users,
)

PASSWORD = 'recommendations-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


class RecommendationIntelligenceTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.workspace_id = create_workspace(user_id=98700, domain='recs.example',
                                            brand_name='Recs')
        older = datetime(2026, 3, 1, 9, 0, 0)
        newer = datetime(2026, 3, 5, 9, 0, 0)
        with engine.begin() as conn:
            topic_id = conn.execute(insert(analytics_topics).values(
                workspace_id=cls.workspace_id, name='Pricing', created_at=older,
            )).inserted_primary_key[0]
            prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
                workspace_id=cls.workspace_id, topic_id=topic_id, prompt='best crm for startups',
                intent='Discovery', active=True, created_at=older, updated_at=older,
            )).inserted_primary_key[0]
            scan_id = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=cls.workspace_id, job_id=None, provider='Perplexity', model='m',
                region=None, competitor_snapshot='[]', status='succeeded', run_type='scheduled',
                prompt_count=1, completed_count=1, mention_rate=None, citation_rate=None,
                source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                error=None, created_at=older, completed_at=older,
            )).inserted_primary_key[0]
            answer_id = conn.execute(insert(analytics_provider_answers).values(
                scan_run_id=scan_id, prompt_id=prompt_id, prompt_text=None,
                prompt_intent='Discovery', topic_name=None, provider='Perplexity', model='m',
                status='ok', search_request_id=None, answer_request_id=None,
                answer_text='text', raw_response='{}', latency_ms=1, error=None,
                created_at=older, completed_at=older,
            )).inserted_primary_key[0]

            # A low-priority content opportunity (older) and a high-priority
            # one (newer) - tests both priority ordering and newest-first
            # within a tier.
            conn.execute(insert(analytics_content_opportunities).values(
                workspace_id=cls.workspace_id, scan_run_id=scan_id, source='stored-evidence rules',
                title='Protect and deepen measured coverage', rationale='Coverage exists; add proof.',
                evidence_refs=f'answer:{answer_id}', priority='low', created_at=older,
            ))
            cls.high_opportunity_id = conn.execute(insert(analytics_content_opportunities).values(
                workspace_id=cls.workspace_id, scan_run_id=scan_id, source='stored-evidence rules',
                title='Build a direct answer for an unmentioned prompt',
                rationale=f'Recs was absent from the stored answer to: best crm for startups',
                evidence_refs=f'answer:{answer_id}', priority='high', created_at=newer,
            )).inserted_primary_key[0]

            audit_id = conn.execute(insert(analytics_site_audits).values(
                workspace_id=cls.workspace_id, job_id=None, status='succeeded',
                source_type='website_crawl', start_url='https://recs.example/',
                final_url='https://recs.example/', pages_discovered=1, pages_audited=1,
                pages_failed=0, readiness_score=60, metadata_score=70, content_score=55,
                crawlability_score=80, structured_data_score=40, summary='ok',
                created_at=newer, completed_at=newer,
            )).inserted_primary_key[0]
            page_id = conn.execute(insert(analytics_audit_pages).values(
                audit_id=audit_id, url='https://recs.example/pricing', final_url=None,
                fetched=True, http_status=200, title='Pricing', description=None,
                headings_count=2, word_count=300, schema_blocks=0, canonical=None,
                noindex=False, language='en', internal_links=3, external_links=1,
                readiness_score=60, metadata_score=70, content_score=55,
                crawlability_score=80, structured_data_score=40, issues_count=1,
                error=None, fetched_at=newer,
            )).inserted_primary_key[0]
            cls.finding_id = conn.execute(insert(analytics_audit_findings).values(
                audit_id=audit_id, page_id=page_id, code='missing_schema',
                area='Structured data', severity='medium',
                evidence='No JSON-LD blocks were found.',
                recommendation='Add valid JSON-LD describing the page.',
            )).inserted_primary_key[0]

        cls.recommendations = metrics.recommendation_intelligence(cls.workspace_id)

    def test_merges_both_sources(self):
        kinds = {r['kind'] for r in self.recommendations}
        self.assertEqual(kinds, {'content_opportunity', 'site_finding'})
        self.assertEqual(len(self.recommendations), 3)

    def test_high_priority_sorts_before_medium_before_low(self):
        priorities = [r['priority'] for r in self.recommendations]
        rank = {'high': 0, 'medium': 1, 'low': 2}
        self.assertEqual(priorities, sorted(priorities, key=lambda p: rank[p]))
        self.assertEqual(priorities[0], 'high')
        self.assertEqual(priorities[-1], 'low')

    def test_content_opportunity_resolves_its_evidence(self):
        item = next(r for r in self.recommendations if r['id'] == f'opportunity:{self.high_opportunity_id}')
        self.assertEqual(item['area'], 'AI Visibility')
        self.assertEqual(item['link'], '/mentions')
        self.assertEqual(len(item['evidence']), 1)
        self.assertEqual(item['evidence'][0]['prompt'], 'best crm for startups')
        self.assertEqual(item['evidence'][0]['topic_name'], 'Pricing')
        self.assertEqual(item['evidence'][0]['provider'], 'Perplexity')

    def test_site_finding_maps_severity_to_priority_and_resolves_its_page(self):
        item = next(r for r in self.recommendations if r['id'] == f'finding:{self.finding_id}')
        self.assertEqual(item['priority'], 'medium')  # severity 'medium' -> priority 'medium'
        self.assertEqual(item['area'], 'Structured data')
        self.assertEqual(item['rationale'], 'Add valid JSON-LD describing the page.')
        self.assertEqual(item['link'], '/site-audit')
        self.assertEqual(item['evidence'][0]['url'], 'https://recs.example/pricing')

    def test_no_status_field_is_invented(self):
        for item in self.recommendations:
            self.assertNotIn('status', item)
            self.assertNotIn('done', item)


class EmptyWorkspaceTests(unittest.TestCase):
    def test_fresh_workspace_has_no_recommendations(self):
        workspace_id = create_workspace(user_id=98701, domain='norec.example', brand_name='NoRec')
        self.assertEqual(metrics.recommendation_intelligence(workspace_id), [])


class RecommendationsRouteTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_get_returns_project_and_recommendations(self):
        user_id = make_user('recommendations_route_user')
        workspace_id = create_workspace(user_id=user_id, domain='recroute.example',
                                        brand_name='RecRoute')
        with server_pg.app.test_client() as client:
            self.login(client, 'recommendations_route_user')
            response = client.get(f'/api/analytics/projects/{workspace_id}/recommendations')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn('project', body)
        self.assertEqual(body['recommendations'], [])

    def test_endpoint_is_workspace_scoped(self):
        with server_pg.app.test_client() as client:
            response = client.get('/api/analytics/projects/1/recommendations')
        self.assertEqual(response.status_code, 401)


class RecommendationsPageTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_recommendations_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/recommendations')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_recommendations_returns_200_for_logged_in_user(self):
        make_user('pages_recommendations_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_recommendations_user')
            response = client.get('/recommendations')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


if __name__ == '__main__':
    unittest.main()
