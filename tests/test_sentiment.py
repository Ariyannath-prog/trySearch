"""Sentiment Intelligence: the classifier (app/sentiment.py), the rollup's
new sentiment_index (app/rollup.py), the job (app/jobs.py::
run_sentiment_classification_job), the report (app/metrics.py::
sentiment_intelligence), and the routes/page. extractions.sentiment/
sentiment_conf have existed since T9 but were always written NULL - this
is the first thing that ever computes them.
"""

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'sentiment-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert, select  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import jobs as jobs_module  # noqa: E402
from app import sentiment as sentiment_module  # noqa: E402
from app import metrics  # noqa: E402
from app.db import engine  # noqa: E402
from app.http_client import ProviderAPIError  # noqa: E402
from app.models import (  # noqa: E402
    analytics_audit_jobs,
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_topics,
    analytics_tracked_prompts,
    extractions,
    users,
)
from app.rollup import score_from_counts, sentiment_index_from_labels  # noqa: E402

PASSWORD = 'sentiment-password-123'
OPEN_MODEL_ENV = {'HF_TOKEN': '', 'OLLAMA_BASE_URL': 'http://127.0.0.1:11434/v1',
                  'OLLAMA_MODEL': 'test-open-model'}


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


def seed_answer(conn, *, scan_id, prompt_id, when, brand_mentioned=True,
                topic_name=None, sentiment=None, sentiment_conf=None,
                answer_text='Acme is a strong choice for this use case.'):
    answer_id = conn.execute(insert(analytics_provider_answers).values(
        scan_run_id=scan_id, prompt_id=prompt_id, prompt_text='best crm for startups',
        prompt_intent='Discovery', topic_name=topic_name, provider='Perplexity',
        model='m', status='ok', search_request_id=None, answer_request_id=None,
        answer_text=answer_text, raw_response='{}', latency_ms=1, error=None,
        created_at=when, completed_at=when,
    )).inserted_primary_key[0]
    extraction_id = conn.execute(insert(extractions).values(
        answer_id=answer_id, extractor_version='t-sentiment', is_current=True,
        brand_mentioned=brand_mentioned, brand_rank=1 if brand_mentioned else None,
        brand_cited=False, sentiment=sentiment, sentiment_conf=sentiment_conf,
        created_at=when,
    )).inserted_primary_key[0]
    return answer_id, extraction_id


class SentimentIndexTests(unittest.TestCase):
    def test_average_of_the_three_label_scores(self):
        self.assertAlmostEqual(
            sentiment_index_from_labels(['positive', 'neutral', 'negative']), 50.0)

    def test_unclassified_answers_are_excluded_not_coerced_to_neutral(self):
        self.assertAlmostEqual(sentiment_index_from_labels(['positive', None, None]), 100.0)

    def test_no_labels_at_all_is_none(self):
        self.assertIsNone(sentiment_index_from_labels([]))
        self.assertIsNone(sentiment_index_from_labels([None, None]))

    def test_score_from_counts_passes_sentiment_index_through_unchanged(self):
        result = score_from_counts(
            total_answers=10, mentioned=5, reciprocal_rank_sum=2.0, cited=1,
            sentiment_index=75.0)
        self.assertEqual(result['sentiment_index'], 75.0)
        # Sentiment never leaks into the score PRD §13 defines.
        result_without = score_from_counts(
            total_answers=10, mentioned=5, reciprocal_rank_sum=2.0, cited=1)
        self.assertEqual(result['visibility_score'], result_without['visibility_score'])


class ClassifierTests(unittest.TestCase):
    def test_not_configured_returns_none(self):
        with patch.dict(os.environ, {'HF_TOKEN': '', 'OLLAMA_BASE_URL': ''}, clear=False):
            self.assertIsNone(sentiment_module.classify_answer_sentiment('Acme is great.', 'Acme'))

    def test_valid_response_is_parsed(self):
        payload = {'choices': [{'message': {'content': '{"sentiment": "positive", "confidence": 0.9}'}}]}
        with patch.dict(os.environ, OPEN_MODEL_ENV, clear=False), patch.object(
            sentiment_module, 'external_json_request', return_value=payload,
        ):
            result = sentiment_module.classify_answer_sentiment('Acme is excellent.', 'Acme')
        self.assertEqual(result['sentiment'], 'positive')
        self.assertAlmostEqual(result['confidence'], 0.9)

    def test_unrecognised_label_raises(self):
        payload = {'choices': [{'message': {'content': '{"sentiment": "mixed", "confidence": 0.5}'}}]}
        with patch.dict(os.environ, OPEN_MODEL_ENV, clear=False), patch.object(
            sentiment_module, 'external_json_request', return_value=payload,
        ):
            with self.assertRaises(ProviderAPIError):
                sentiment_module.classify_answer_sentiment('Acme is fine.', 'Acme')

    def test_confidence_is_clamped_into_zero_one(self):
        payload = {'choices': [{'message': {'content': '{"sentiment": "negative", "confidence": 5}'}}]}
        with patch.dict(os.environ, OPEN_MODEL_ENV, clear=False), patch.object(
            sentiment_module, 'external_json_request', return_value=payload,
        ):
            result = sentiment_module.classify_answer_sentiment('Acme disappointed.', 'Acme')
        self.assertEqual(result['confidence'], 1.0)


class SentimentClassificationJobTests(unittest.TestCase):

    def test_job_classifies_only_unclassified_mentioned_scheduled_answers(self):
        workspace_id = create_workspace(user_id=97501, domain='jobrun.example', brand_name='JobRun')
        now = datetime.utcnow()
        with engine.begin() as conn:
            prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
                workspace_id=workspace_id, topic_id=None, prompt='best crm for startups',
                intent='Discovery', active=True, created_at=now, updated_at=now,
            )).inserted_primary_key[0]
            scan_id = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=workspace_id, job_id=None, provider='Perplexity', model='m',
                region=None, competitor_snapshot='[]', status='succeeded', run_type='scheduled',
                prompt_count=3, completed_count=3, mention_rate=None, citation_rate=None,
                source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                error=None, created_at=now, completed_at=now,
            )).inserted_primary_key[0]
            already_id, already_ext_id = seed_answer(
                conn, scan_id=scan_id, prompt_id=prompt_id, when=now,
                sentiment='negative', sentiment_conf=0.4)
            pending_id, pending_ext_id = seed_answer(
                conn, scan_id=scan_id, prompt_id=prompt_id, when=now)
            absent_id, absent_ext_id = seed_answer(
                conn, scan_id=scan_id, prompt_id=prompt_id, when=now, brand_mentioned=False)

            job_id = conn.execute(insert(analytics_audit_jobs).values(
                workspace_id=workspace_id, run_type='scheduled', job_type='sentiment_classification',
                provider=None, status='queued', progress=0, total_items=0, completed_items=0,
                error=None, created_at=now, started_at=None, completed_at=None,
            )).inserted_primary_key[0]

        payload = {'choices': [{'message': {'content': '{"sentiment": "positive", "confidence": 0.8}'}}]}
        with patch.dict(os.environ, OPEN_MODEL_ENV, clear=False), patch.object(
            sentiment_module, 'external_json_request', return_value=payload,
        ):
            jobs_module.run_sentiment_classification_job(job_id)

        with engine.connect() as conn:
            job = conn.execute(select(analytics_audit_jobs).where(
                analytics_audit_jobs.c.id == job_id)).mappings().first()
            already = conn.execute(select(extractions).where(
                extractions.c.id == already_ext_id)).mappings().first()
            pending = conn.execute(select(extractions).where(
                extractions.c.id == pending_ext_id)).mappings().first()
            absent = conn.execute(select(extractions).where(
                extractions.c.id == absent_ext_id)).mappings().first()

        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['completed_items'], 1)  # only the pending one was classified
        # Already-classified answer is untouched - re-running costs nothing extra.
        self.assertEqual(already['sentiment'], 'negative')
        self.assertAlmostEqual(already['sentiment_conf'], 0.4)
        # The pending answer got classified, in place - same extraction row.
        self.assertEqual(pending['sentiment'], 'positive')
        self.assertAlmostEqual(pending['sentiment_conf'], 0.8)
        # An answer where the brand was never mentioned is never classified.
        self.assertIsNone(absent['sentiment'])

    def test_malformed_model_response_leaves_the_answer_unclassified(self):
        workspace_id = create_workspace(user_id=97502, domain='malformed.example', brand_name='Malformed')
        now = datetime.utcnow()
        with engine.begin() as conn:
            prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
                workspace_id=workspace_id, topic_id=None, prompt='best crm for startups',
                intent='Discovery', active=True, created_at=now, updated_at=now,
            )).inserted_primary_key[0]
            scan_id = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=workspace_id, job_id=None, provider='Perplexity', model='m',
                region=None, competitor_snapshot='[]', status='succeeded', run_type='scheduled',
                prompt_count=1, completed_count=1, mention_rate=None, citation_rate=None,
                source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                error=None, created_at=now, completed_at=now,
            )).inserted_primary_key[0]
            _, ext_id = seed_answer(conn, scan_id=scan_id, prompt_id=prompt_id, when=now)
            job_id = conn.execute(insert(analytics_audit_jobs).values(
                workspace_id=workspace_id, run_type='scheduled', job_type='sentiment_classification',
                provider=None, status='queued', progress=0, total_items=0, completed_items=0,
                error=None, created_at=now, started_at=None, completed_at=None,
            )).inserted_primary_key[0]

        payload = {'choices': [{'message': {'content': '{"sentiment": "??", "confidence": 0.5}'}}]}
        with patch.dict(os.environ, OPEN_MODEL_ENV, clear=False), patch.object(
            sentiment_module, 'external_json_request', return_value=payload,
        ):
            jobs_module.run_sentiment_classification_job(job_id)

        with engine.connect() as conn:
            job = conn.execute(select(analytics_audit_jobs).where(
                analytics_audit_jobs.c.id == job_id)).mappings().first()
            ext = conn.execute(select(extractions).where(
                extractions.c.id == ext_id)).mappings().first()
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['completed_items'], 0)
        self.assertIsNone(ext['sentiment'])


class SentimentIntelligenceTests(unittest.TestCase):

    def test_brand_only_report_shape(self):
        workspace_id = create_workspace(user_id=97600, domain='sentimentreport.example',
                                        brand_name='SentimentReport')
        now = datetime.utcnow()
        with engine.begin() as conn:
            topic_id = conn.execute(insert(analytics_topics).values(
                workspace_id=workspace_id, name='Pricing', created_at=now,
            )).inserted_primary_key[0]
            prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
                workspace_id=workspace_id, topic_id=topic_id, prompt='best crm for startups',
                intent='Discovery', active=True, created_at=now, updated_at=now,
            )).inserted_primary_key[0]
            scan_id = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=workspace_id, job_id=None, provider='Perplexity', model='m',
                region=None, competitor_snapshot='[]', status='succeeded', run_type='scheduled',
                prompt_count=2, completed_count=2, mention_rate=None, citation_rate=None,
                source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                error=None, created_at=now, completed_at=now,
            )).inserted_primary_key[0]
            seed_answer(conn, scan_id=scan_id, prompt_id=prompt_id, when=now,
                       sentiment='positive', sentiment_conf=0.9)
            seed_answer(conn, scan_id=scan_id, prompt_id=prompt_id, when=now,
                       sentiment='negative', sentiment_conf=0.7)

        report = metrics.sentiment_intelligence(workspace_id)
        self.assertEqual(report['mentioned_count'], 2)
        self.assertEqual(report['classified_count'], 2)
        self.assertEqual(report['distribution'], {'positive': 1, 'neutral': 0, 'negative': 1})
        self.assertAlmostEqual(report['overall_sentiment_index'], 50.0)
        self.assertEqual(len(report['topics']), 1)
        self.assertEqual(report['topics'][0]['topic'], 'Pricing')
        self.assertEqual(len(report['evidence']), 2)


class SentimentRouteTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_get_returns_empty_shape_for_fresh_workspace(self):
        user_id = make_user('sentiment_route_user')
        workspace_id = create_workspace(user_id=user_id, domain='sentimentroute.example',
                                        brand_name='SentimentRoute')
        with server_pg.app.test_client() as client:
            self.login(client, 'sentiment_route_user')
            response = client.get(f'/api/analytics/projects/{workspace_id}/sentiment')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn('project', body)
        self.assertEqual(body['sentiment']['mentioned_count'], 0)
        self.assertIsNone(body['active_job'])

    def test_classify_queues_a_job_and_is_idempotent_while_active(self):
        user_id = make_user('sentiment_classify_user')
        workspace_id = create_workspace(user_id=user_id, domain='sentimentclassify.example',
                                        brand_name='SentimentClassify')
        with server_pg.app.test_client() as client:
            self.login(client, 'sentiment_classify_user')
            first = client.post(f'/api/analytics/projects/{workspace_id}/sentiment/classify', json={})
            second = client.post(f'/api/analytics/projects/{workspace_id}/sentiment/classify', json={})
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.get_json()['job_id'], second.get_json()['job']['id'])

    def test_endpoint_is_workspace_scoped(self):
        with server_pg.app.test_client() as client:
            response = client.get('/api/analytics/projects/1/sentiment')
        self.assertEqual(response.status_code, 401)


class SentimentPageTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_sentiment_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/sentiment')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_sentiment_returns_200_for_logged_in_user(self):
        make_user('pages_sentiment_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_sentiment_user')
            response = client.get('/sentiment')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


if __name__ == '__main__':
    unittest.main()
