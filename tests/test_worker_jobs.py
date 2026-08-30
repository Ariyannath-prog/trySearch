"""T7: execution belongs to the CLI worker, not the web process.

Covers the four acceptance criteria:
  - an on-demand scan returns immediately with a job id and completes in the worker
  - a 300-prompt workspace scans without a 409
  - killing the worker mid-run leaves the job recoverable, not lost
  - a forced single-answer failure produces status='partial', not failed
"""

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'worker-jobs-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert, select, update  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import scanning, worker  # noqa: E402
from app.db import engine  # noqa: E402
from app.http_client import ProviderAPIError  # noqa: E402
from app.models import (  # noqa: E402
    analytics_audit_jobs,
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_tracked_prompts,
    users,
)

PASSWORD = 'worker-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


def add_prompts(workspace_id, count, *, label='x'):
    now = datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(insert(analytics_tracked_prompts), [
            {
                'workspace_id': workspace_id,
                'topic_id': None,
                'prompt': f'{label} tooling number {i}',
                'intent': 'Discovery',
                'active': True,
                'created_at': now,
                'updated_at': now,
            }
            for i in range(count)
        ])


def enqueue_scan(client, workspace_id):
    """Return the job id, whether a new job was created or an active one reused."""
    body = client.post(f'/api/analytics/projects/{workspace_id}/prompt-scans',
                       json={}).get_json()
    return body.get('job_id') or body['job']['id']


def search_payload(prompt, region=None):
    return {'id': 'search-1', 'results': [
        {'title': 'Example', 'url': 'https://example.com/a', 'snippet': 'Example wins'},
    ]}


def answer_payload(prompt):
    return {
        'id': 'answer-1', 'status': 'completed',
        'output_text': 'Example is a strong option.',
        'output': [], 'model': 'sonar',
    }


class NoThreadingTests(unittest.TestCase):
    def test_threading_is_gone_from_the_app(self):
        """The web process must not execute jobs in-process."""
        import pathlib
        offenders = []
        for path in pathlib.Path('app').rglob('*.py'):
            text = path.read_text()
            if 'threading' in text or 'start_background_analytics_job' in text:
                offenders.append(str(path))
        self.assertEqual(offenders, [], 'job execution must live in the CLI worker')


class OnDemandScanTests(unittest.TestCase):
    """An on-demand scan returns a job id immediately; the worker completes it."""

    @classmethod
    def setUpClass(cls):
        cls.user = make_user('worker_ondemand')
        cls.workspace = create_workspace(user_id=cls.user, domain='ondemand.example',
                                         brand_name='Ondemand')
        add_prompts(cls.workspace, 2, label='ondemand')

    def login(self, client):
        client.post('/api/login', json={'username': 'worker_ondemand', 'password': PASSWORD})

    def test_request_returns_job_id_without_running_the_scan(self):
        with patch.dict(os.environ, {'PERPLEXITY_API_KEY': 'test-key'}, clear=False), \
             patch.object(scanning, 'call_perplexity_search') as search:
            with server_pg.app.test_client() as client:
                self.login(client)
                response = client.post(
                    f'/api/analytics/projects/{self.workspace}/prompt-scans', json={})
        self.assertEqual(response.status_code, 202)
        self.assertIn('job_id', response.get_json())
        # The web request must not have called the provider at all.
        search.assert_not_called()

        with engine.connect() as conn:
            status = conn.execute(select(analytics_audit_jobs.c.status).where(
                analytics_audit_jobs.c.id == response.get_json()['job_id'])).scalar_one()
        self.assertEqual(status, 'queued')

    def test_worker_completes_the_queued_job(self):
        with patch.dict(os.environ, {'PERPLEXITY_API_KEY': 'test-key',
                                     'PERPLEXITY_REQUEST_DELAY_SECONDS': '0'}, clear=False), \
             patch.object(scanning, 'call_perplexity_search', side_effect=search_payload), \
             patch.object(scanning, 'call_perplexity_answer', side_effect=answer_payload):
            with server_pg.app.test_client() as client:
                self.login(client)
                job_id = enqueue_scan(client, self.workspace)
            scanning.run_prompt_scan_job(job_id)

        with engine.connect() as conn:
            status = conn.execute(select(analytics_audit_jobs.c.status).where(
                analytics_audit_jobs.c.id == job_id)).scalar_one()
        self.assertEqual(status, 'succeeded')


class BatchingTests(unittest.TestCase):
    """A 300-prompt workspace scans in batches instead of being refused."""

    @classmethod
    def setUpClass(cls):
        cls.user = make_user('worker_batching')
        cls.workspace = create_workspace(user_id=cls.user, domain='batching.example',
                                         brand_name='Batching')
        add_prompts(cls.workspace, 300, label='batching')

    def test_batched_helper_splits_300_into_12_batches_of_25(self):
        batches = list(scanning.batched(list(range(300)), 25))
        self.assertEqual(len(batches), 12)
        self.assertEqual({len(b) for b in batches}, {25})

    def test_300_prompts_do_not_get_a_409(self):
        with patch.dict(os.environ, {'PERPLEXITY_API_KEY': 'test-key'}, clear=False):
            with server_pg.app.test_client() as client:
                client.post('/api/login',
                            json={'username': 'worker_batching', 'password': PASSWORD})
                response = client.post(
                    f'/api/analytics/projects/{self.workspace}/prompt-scans', json={})
        self.assertEqual(
            response.status_code, 202,
            f'300 prompts must be batched, not refused. body: {response.get_data(as_text=True)[:200]}')

    def test_all_300_prompts_are_scanned_in_one_run(self):
        calls = []

        def counting_search(prompt, region=None):
            calls.append(prompt)
            return search_payload(prompt)

        with patch.dict(os.environ, {'PERPLEXITY_API_KEY': 'test-key',
                                     'PERPLEXITY_REQUEST_DELAY_SECONDS': '0'}, clear=False), \
             patch.object(scanning, 'call_perplexity_search', side_effect=counting_search), \
             patch.object(scanning, 'call_perplexity_answer', side_effect=answer_payload):
            with server_pg.app.test_client() as client:
                client.post('/api/login',
                            json={'username': 'worker_batching', 'password': PASSWORD})
                job_id = enqueue_scan(client, self.workspace)
            scanning.run_prompt_scan_job(job_id)

        self.assertEqual(len(calls), 300, 'every prompt must be scanned across the batches')


class PartialResultTests(unittest.TestCase):
    """A forced single-answer failure marks the run partial, never failed."""

    @classmethod
    def setUpClass(cls):
        cls.user = make_user('worker_partial')
        cls.workspace = create_workspace(user_id=cls.user, domain='partial.example',
                                         brand_name='Partial')
        add_prompts(cls.workspace, 3, label='partial')

    def test_one_failing_answer_yields_partial(self):
        seen = []

        def flaky_search(prompt, region=None):
            seen.append(prompt)
            # Fail every attempt for exactly one prompt.
            if 'number 1' in prompt:
                raise ProviderAPIError('forced search failure')
            return search_payload(prompt)

        def flaky_answer(prompt):
            if 'number 1' in prompt:
                raise ProviderAPIError('forced answer failure')
            return answer_payload(prompt)

        with patch.dict(os.environ, {'PERPLEXITY_API_KEY': 'test-key',
                                     'PERPLEXITY_REQUEST_DELAY_SECONDS': '0',
                                     'ANSWER_RETRY_ATTEMPTS': '2'}, clear=False), \
             patch.object(scanning, 'ANSWER_RETRY_ATTEMPTS', 2), \
             patch.object(scanning, 'retry_delay', lambda attempt: 0), \
             patch.object(scanning, 'call_perplexity_search', side_effect=flaky_search), \
             patch.object(scanning, 'call_perplexity_answer', side_effect=flaky_answer):
            with server_pg.app.test_client() as client:
                client.post('/api/login',
                            json={'username': 'worker_partial', 'password': PASSWORD})
                job_id = enqueue_scan(client, self.workspace)
            scanning.run_prompt_scan_job(job_id)

        with engine.connect() as conn:
            run = conn.execute(select(analytics_prompt_scan_runs).where(
                analytics_prompt_scan_runs.c.job_id == job_id)).mappings().first()
            job_status = conn.execute(select(analytics_audit_jobs.c.status).where(
                analytics_audit_jobs.c.id == job_id)).scalar_one()

        self.assertEqual(run['status'], 'partial',
                         'one failed answer must not fail the whole run')
        self.assertEqual(job_status, 'succeeded',
                         'the job completed; the run is simply partial')

    def test_failing_answer_is_retried_before_being_given_up_on(self):
        attempts = []

        def always_fails(prompt, region=None):
            attempts.append(prompt)
            raise ProviderAPIError('always down')

        with patch.object(scanning, 'retry_delay', lambda attempt: 0):
            payload, error = scanning.call_with_retries(always_fails, 'a prompt', attempts=3)

        self.assertIsNone(payload)
        self.assertIn('after 3 attempts', error)
        self.assertEqual(len(attempts), 3, 'a failing answer retries 3 times')


class WorkerRecoveryTests(unittest.TestCase):
    """Killing the worker mid-run leaves the job recoverable, not lost."""

    @classmethod
    def setUpClass(cls):
        cls.user = make_user('worker_recovery')
        cls.workspace = create_workspace(user_id=cls.user, domain='recovery.example',
                                         brand_name='Recovery')
        add_prompts(cls.workspace, 4, label='recovery')

    def test_stale_running_job_is_requeued_and_resumes_without_recharging(self):
        calls = []

        def counting_search(prompt, region=None):
            if prompt.startswith('recovery'):
                calls.append(prompt)
            return search_payload(prompt)

        env = {'PERPLEXITY_API_KEY': 'test-key', 'PERPLEXITY_REQUEST_DELAY_SECONDS': '0'}

        # Run the scan, then simulate the worker dying after two answers by
        # deleting the rest and putting the job back into a stale 'running' lease.
        with patch.dict(os.environ, env, clear=False), \
             patch.object(scanning, 'call_perplexity_search', side_effect=counting_search), \
             patch.object(scanning, 'call_perplexity_answer', side_effect=answer_payload):
            with server_pg.app.test_client() as client:
                client.post('/api/login',
                            json={'username': 'worker_recovery', 'password': PASSWORD})
                job_id = enqueue_scan(client, self.workspace)
            scanning.run_prompt_scan_job(job_id)

        first_pass_calls = len(calls)
        self.assertEqual(first_pass_calls, 4)

        with engine.connect() as conn:
            scan_id = conn.execute(select(analytics_prompt_scan_runs.c.id).where(
                analytics_prompt_scan_runs.c.job_id == job_id)).scalar_one()
            answer_ids = [r[0] for r in conn.execute(select(analytics_provider_answers.c.id)
                          .where(analytics_provider_answers.c.scan_run_id == scan_id)
                          .order_by(analytics_provider_answers.c.id)).all()]
        # Drop the last two answers: the state a worker killed mid-run leaves behind.
        with engine.begin() as conn:
            conn.execute(analytics_provider_answers.delete().where(
                analytics_provider_answers.c.id.in_(answer_ids[2:])))
            conn.execute(update(analytics_prompt_scan_runs).where(
                analytics_prompt_scan_runs.c.id == scan_id).values(
                    status='running', completed_at=None))
            conn.execute(update(analytics_audit_jobs).where(
                analytics_audit_jobs.c.id == job_id).values(
                    status='running', completed_at=None,
                    started_at=datetime.utcnow() - timedelta(minutes=90)))

        # The 45-minute stale lease must requeue it rather than lose it.
        with patch.dict(os.environ, env, clear=False), \
             patch.object(scanning, 'call_perplexity_search', side_effect=counting_search), \
             patch.object(scanning, 'call_perplexity_answer', side_effect=answer_payload):
            worker.run_scheduled_analytics_command()

        with engine.connect() as conn:
            final_status = conn.execute(select(analytics_audit_jobs.c.status).where(
                analytics_audit_jobs.c.id == job_id)).scalar_one()
            answer_count = len(conn.execute(select(analytics_provider_answers.c.id).where(
                analytics_provider_answers.c.scan_run_id == scan_id)).all())

        self.assertEqual(final_status, 'succeeded', 'the recovered job must complete')
        self.assertEqual(answer_count, 4, 'the run must end with every prompt answered')
        # Only the two missing answers should have been bought again.
        self.assertEqual(
            len(calls) - first_pass_calls, 2,
            'a resumed run must not re-submit answers it already paid for',
        )


if __name__ == '__main__':
    unittest.main()
