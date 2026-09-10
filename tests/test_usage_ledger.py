"""T8: usage_ledger and spend ceilings.

Covers the three acceptance criteria:
  - a scan of N prompts across E engines writes exactly N x E engine rows plus
    extraction rows
  - a forced provider failure still writes a row
  - an org over its ceiling is refused before any paid call is made
"""

import os
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'usage-ledger-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert, select, update  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import costs, scanning  # noqa: E402
from app.engines import perplexity as perplexity_engine  # noqa: E402
from app.db import engine  # noqa: E402
from app.http_client import ProviderAPIError  # noqa: E402
from app.models import (  # noqa: E402
    analytics_tracked_prompts,
    organizations,
    usage_ledger,
    users,
    workspaces,
)

PASSWORD = 'ledger-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


def add_prompts(workspace_id, count, label):
    now = datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(insert(analytics_tracked_prompts), [
            {'workspace_id': workspace_id, 'topic_id': None,
             'prompt': f'{label} tooling number {i}', 'intent': 'Discovery',
             'active': True, 'created_at': now, 'updated_at': now}
            for i in range(count)
        ])


def org_of(workspace_id):
    with engine.connect() as conn:
        return conn.execute(select(workspaces.c.org_id).where(
            workspaces.c.id == workspace_id)).scalar_one()


def ledger_rows(org_id, category=None):
    query = select(usage_ledger).where(usage_ledger.c.org_id == org_id)
    if category:
        query = query.where(usage_ledger.c.category == category)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(query).mappings().all()]


def search_payload(prompt, region=None):
    return {'id': 's', 'results': [{'title': 'Example', 'url': 'https://example.com/a',
                                    'snippet': 'Example'}]}


def answer_payload(prompt):
    return {'id': 'a', 'status': 'completed', 'output_text': 'Example is good.',
            'output': [], 'model': 'sonar'}


def run_scan(client, workspace_id, *, search=None, answer=None):
    env = {'PERPLEXITY_API_KEY': 'test-key', 'PERPLEXITY_REQUEST_DELAY_SECONDS': '0'}
    with patch.dict(os.environ, env, clear=False), \
         patch.object(scanning, 'retry_delay', lambda attempt: 0), \
         patch.object(perplexity_engine, 'call_perplexity_search',
                      side_effect=search or search_payload), \
         patch.object(perplexity_engine, 'call_perplexity_answer',
                      side_effect=answer or answer_payload):
        body = client.post(f'/api/analytics/projects/{workspace_id}/prompt-scans',
                           json={}).get_json()
        job_id = body.get('job_id') or body['job']['id']
        scanning.run_prompt_scan_job(job_id)
    return job_id


class CostEstimationTests(unittest.TestCase):
    def test_prices_are_decimal_not_float(self):
        for value in costs.PROVIDER_COSTS.values():
            self.assertIsInstance(value, Decimal, 'money is numeric, never float')

    def test_perplexity_matches_the_prd_estimate(self):
        self.assertEqual(costs.estimate_cost('engine_query', 'Perplexity'),
                         Decimal('0.006000'))

    def test_extraction_is_free_because_it_is_a_regex(self):
        self.assertEqual(costs.estimate_cost('extraction', 'regex'), Decimal('0.000000'))

    def test_unknown_provider_falls_back_rather_than_costing_nothing(self):
        cost = costs.estimate_cost('engine_query', 'SomeNewEngine')
        self.assertGreater(cost, Decimal('0'),
                           'an unmetered engine must not look free')


class LedgerRowTests(unittest.TestCase):
    """N prompts x E engines writes N x E engine rows, plus extraction rows."""

    @classmethod
    def setUpClass(cls):
        cls.user = make_user('ledger_rows')
        cls.workspace = create_workspace(user_id=cls.user, domain='rows.example',
                                         brand_name='Rows')
        cls.org = org_of(cls.workspace)
        add_prompts(cls.workspace, 3, 'rows')

    def test_scan_writes_one_engine_row_per_prompt_per_engine(self):
        with server_pg.app.test_client() as client:
            client.post('/api/login', json={'username': 'ledger_rows', 'password': PASSWORD})
            run_scan(client, self.workspace)

        engine_rows = ledger_rows(self.org, 'engine_query')
        extraction = ledger_rows(self.org, 'extraction')
        agent_rows = ledger_rows(self.org, 'agent')

        # N=3 prompts, E=1 engine (Perplexity).
        self.assertEqual(len(engine_rows), 3, 'exactly N x E engine_query rows')
        self.assertEqual(len(extraction), 3, 'one extraction row per answer')
        # T12: one adapter run is one engine query. Perplexity's search+agent pair
        # is now an implementation detail behind the adapter boundary, not two
        # separately billable categories - which makes N x E literal.
        self.assertEqual(len(agent_rows), 0,
                         'the adapter reports one engine query per prompt')

        for row in engine_rows:
            self.assertEqual(row['provider'], 'Perplexity')
            self.assertEqual(row['workspace_id'], self.workspace)
            self.assertEqual(row['org_id'], self.org, 'org_id is denormalised onto the row')
            self.assertGreater(Decimal(str(row['cost_usd'])), Decimal('0'))

    def test_extraction_rows_cost_nothing(self):
        for row in ledger_rows(self.org, 'extraction'):
            self.assertEqual(Decimal(str(row['cost_usd'])), Decimal('0'),
                             're-running extraction over stored answers is free')


class FailureStillMeteredTests(unittest.TestCase):
    """A forced provider failure still writes a row - that is the whole point."""

    @classmethod
    def setUpClass(cls):
        cls.user = make_user('ledger_failure')
        cls.workspace = create_workspace(user_id=cls.user, domain='failure.example',
                                         brand_name='Failure')
        cls.org = org_of(cls.workspace)
        add_prompts(cls.workspace, 2, 'failure')

    def test_failed_calls_are_still_metered(self):
        def always_fails(prompt, region=None):
            raise ProviderAPIError('provider down')

        with server_pg.app.test_client() as client:
            client.post('/api/login',
                        json={'username': 'ledger_failure', 'password': PASSWORD})
            with patch.object(scanning, 'ANSWER_RETRY_ATTEMPTS', 2):
                run_scan(client, self.workspace,
                         search=always_fails,
                         answer=lambda prompt: (_ for _ in ()).throw(
                             ProviderAPIError('provider down')))

        engine_rows = ledger_rows(self.org, 'engine_query')
        self.assertEqual(len(engine_rows), 2,
                         'a failed provider call still writes a ledger row')
        self.assertGreater(sum(Decimal(str(r['cost_usd'])) for r in engine_rows),
                           Decimal('0'),
                           'failed calls cost money and must be counted')


class CeilingTests(unittest.TestCase):
    """An org over its ceiling is refused before any paid call is made.

    Each test builds its own org: spend accumulates in the ledger, so sharing one
    would make the result depend on method ordering.
    """

    def setUp(self):
        name = f'ledger_ceiling_{self.id().rsplit(".", 1)[-1]}'
        self.user = make_user(name)
        self.username = name
        self.workspace = create_workspace(user_id=self.user, domain='ceiling.example',
                                          brand_name='Ceiling')
        self.org = org_of(self.workspace)
        add_prompts(self.workspace, 2, 'ceiling')
        with engine.begin() as conn:
            conn.execute(update(organizations)
                         .where(organizations.c.id == self.org)
                         .values(monthly_cost_ceiling_usd=Decimal('1.00')))

    def spend(self, amount):
        costs.record_usage(workspace_id=self.workspace, org_id=self.org,
                           category='engine_query', provider='Perplexity',
                           cost_usd=Decimal(amount))

    def test_states_move_ok_to_alert_to_exceeded(self):
        state, _spend, ceiling = costs.ceiling_status(self.org)
        self.assertEqual(ceiling, Decimal('1.00'))
        self.assertEqual(state, 'ok')

        self.spend('0.65')          # 65% -> alert at 60%
        state, _s, _c = costs.ceiling_status(self.org)
        self.assertEqual(state, 'alert')

        self.spend('0.40')          # 105% -> exceeded
        state, _s, _c = costs.ceiling_status(self.org)
        self.assertEqual(state, 'exceeded')

    def test_over_ceiling_is_refused_before_any_paid_call(self):
        # Push the org over its ceiling.
        self.spend('5.00')
        calls = []

        def counting_search(prompt, region=None):
            calls.append(prompt)
            return search_payload(prompt)

        env = {'PERPLEXITY_API_KEY': 'test-key', 'PERPLEXITY_REQUEST_DELAY_SECONDS': '0'}
        with server_pg.app.test_client() as client:
            client.post('/api/login',
                        json={'username': self.username, 'password': PASSWORD})
            with patch.dict(os.environ, env, clear=False), \
                 patch.object(perplexity_engine, 'call_perplexity_search', side_effect=counting_search), \
                 patch.object(perplexity_engine, 'call_perplexity_answer', side_effect=answer_payload):
                response = client.post(
                    f'/api/analytics/projects/{self.workspace}/prompt-scans', json={})

        self.assertEqual(response.status_code, 402,
                         'an org over its ceiling must be refused')
        self.assertEqual(calls, [], 'no provider call may be made after refusal')

    def test_worker_also_refuses_so_a_queued_job_cannot_slip_through(self):
        """The route check is not enough: a job queued before the ceiling was hit
        would otherwise still spend when the worker picked it up."""
        self.spend('5.00')
        calls = []

        def counting_search(prompt, region=None):
            calls.append(prompt)
            return search_payload(prompt)

        # Queue a job directly, bypassing the route's refusal.
        from app.jobs import create_analytics_job
        with engine.connect() as conn:
            workspace = dict(conn.execute(select(workspaces).where(
                workspaces.c.id == self.workspace)).mappings().first())
        job_id = create_analytics_job(workspace, 'prompt_scan', provider='Perplexity')

        env = {'PERPLEXITY_API_KEY': 'test-key', 'PERPLEXITY_REQUEST_DELAY_SECONDS': '0'}
        with patch.dict(os.environ, env, clear=False), \
             patch.object(perplexity_engine, 'call_perplexity_search', side_effect=counting_search), \
             patch.object(perplexity_engine, 'call_perplexity_answer', side_effect=answer_payload):
            scanning.run_prompt_scan_job(job_id)

        self.assertEqual(calls, [], 'the worker must refuse an over-ceiling job too')


if __name__ == '__main__':
    unittest.main()
