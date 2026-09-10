import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import insert, select


os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'analytics-truth-contract-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402
from app import db  # noqa: E402
from app.crawler import fetch  # noqa: E402
from app import jobs  # noqa: E402
from app import metrics  # noqa: E402
from app import models  # noqa: E402
from app import tenancy  # noqa: E402
from app import scanning  # noqa: E402
from app.engines import perplexity as perplexity_engine  # noqa: E402
from app.engines.base import Citation, EngineResult  # noqa: E402


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AnalyticsTruthContractTests(unittest.TestCase):
    def create_project(self, *, user_id, domain='example.com', brand_name='Example'):
        return create_workspace(user_id=user_id, domain=domain, brand_name=brand_name,
                                created_at=utc_now())

    def create_scan(self, workspace_id, *, region='IN', competitors=None, prompt_count=1):
        now = utc_now()
        with db.engine.begin() as conn:
            return conn.execute(insert(models.analytics_prompt_scan_runs).values(
                workspace_id=workspace_id,
                job_id=None,
                provider='Perplexity',
                model='provider/model',
                region=region,
                competitor_snapshot=json.dumps(competitors or []),
                status='partial',
                prompt_count=prompt_count,
                completed_count=prompt_count,
                mention_rate=None,
                citation_rate=None,
                source_presence_rate=None,
                share_of_voice=None,
                recommendation_summary=None,
                error=None,
                created_at=now,
                completed_at=now,
            )).inserted_primary_key[0]

    def test_partial_provider_results_keep_unmeasured_fields_null(self):
        workspace_id = self.create_project(user_id=91001)
        scan_id = self.create_scan(workspace_id, prompt_count=2)
        project = {'id': workspace_id, 'brand_name': 'Example',
                   'domain': 'example.com', 'domains': ['example.com']}

        scanning.persist_provider_answer(
            scan_id,
            {
                'id': 91001,
                'prompt': 'Which service should a buyer choose?',
                'intent': 'Discovery',
                'topic_name': 'Platforms',
            },
            project,
            EngineResult(
                status='empty',
                answer_text='',
                citations=(Citation(position=1, url='https://example.com/evidence',
                                    title='Example evidence'),),
                raw_response={'search': {'id': 'search-only'}, 'answer': None},
            ),
            ['Agent API: unavailable'],
            20,
        )
        scanning.persist_provider_answer(
            scan_id,
            {
                'id': 91002,
                'prompt': 'What does Example provide?',
                'intent': 'Discovery',
                'topic_name': 'Platforms',
            },
            project,
            EngineResult(
                status='ok',
                answer_text='Example provides an evidence workflow.',
                citations=(),
                raw_response={'search': None, 'answer': {'id': 'answer-only'}},
                model_version='provider/model',
            ),
            ['Search API: unavailable'],
            20,
        )

        rows = {row['prompt_id']: row for row in metrics.provider_evidence_rows(scan_id)}
        search_only = rows[91001]
        self.assertEqual(search_only['status'], 'partial')
        self.assertIsNone(search_only['brand_mentioned'])
        self.assertIsNone(search_only['brand_cited'])
        self.assertTrue(search_only['source_present'])
        self.assertEqual(search_only['best_source_rank'], 1)

        answer_only = rows[91002]
        # T12: an engine that returns text but no citations returned a *complete*
        # answer - an empty citation list is a fact about the source, not a partial
        # result. The unmeasured-fields assertions below are the point of this test
        # and are unchanged.
        self.assertEqual(answer_only['status'], 'succeeded')
        self.assertTrue(answer_only['brand_mentioned'])
        self.assertFalse(answer_only['brand_cited'])
        self.assertIsNone(answer_only['source_present'])
        self.assertIsNone(answer_only['best_source_rank'])

    def test_comparison_cohort_tracks_prompts_competitors_and_region(self):
        workspace_id = self.create_project(user_id=91002)
        common_competitors = [
            {'name': 'Acme', 'domain': 'acme.com'},
            {'name': 'Rival', 'domain': 'rival.com'},
        ]
        run_specs = [
            ('IN', common_competitors, 'Which platform is best?'),
            ('IN', list(reversed(common_competitors)), 'Which platform is best?'),
            ('US', common_competitors, 'Which platform is best?'),
            ('IN', common_competitors, 'Which platform has stronger evidence?'),
        ]
        scan_ids = []
        now = utc_now()
        with db.engine.begin() as conn:
            for index, (region, competitors, prompt_text) in enumerate(run_specs, 1):
                scan_id = conn.execute(insert(models.analytics_prompt_scan_runs).values(
                    workspace_id=workspace_id,
                    job_id=None,
                    provider='Perplexity',
                    model='provider/model',
                    region=region,
                    competitor_snapshot=json.dumps(competitors),
                    status='succeeded',
                    prompt_count=1,
                    completed_count=1,
                    mention_rate=100,
                    citation_rate=0,
                    source_presence_rate=0,
                    share_of_voice=50,
                    recommendation_summary=None,
                    error=None,
                    created_at=now,
                    completed_at=now,
                )).inserted_primary_key[0]
                scan_ids.append(scan_id)
                conn.execute(insert(models.analytics_provider_answers).values(
                    scan_run_id=scan_id,
                    prompt_id=92000 + index,
                    prompt_text=prompt_text,
                    prompt_intent='Discovery',
                    topic_name='Platforms',
                    provider='Perplexity',
                    model='provider/model',
                    status='succeeded',
                    answer_text='Example is mentioned.',
                    raw_response='{}',
                    latency_ms=10,
                    error=None,
                    created_at=now,
                    completed_at=now,
                ))

        history = {
            row['id']: row for row in metrics.latest_prompt_evidence(workspace_id)['history']
        }
        first = history[scan_ids[0]]['cohort_id']
        reordered = history[scan_ids[1]]['cohort_id']
        different_region = history[scan_ids[2]]['cohort_id']
        different_prompt = history[scan_ids[3]]['cohort_id']

        self.assertEqual(first, reordered)
        self.assertNotEqual(first, different_region)
        self.assertNotEqual(first, different_prompt)

    def test_comparison_cohort_preserves_duplicate_prompt_multiplicity(self):
        workspace_id = self.create_project(user_id=91004)
        now = utc_now()
        scan_ids = []
        with db.engine.begin() as conn:
            for prompt_count in (1, 2):
                scan_id = conn.execute(insert(models.analytics_prompt_scan_runs).values(
                    workspace_id=workspace_id,
                    job_id=None,
                    provider='Perplexity',
                    model='provider/model',
                    region='IN',
                    competitor_snapshot='[]',
                    status='succeeded',
                    prompt_count=prompt_count,
                    completed_count=prompt_count,
                    mention_rate=100,
                    citation_rate=0,
                    source_presence_rate=0,
                    share_of_voice=None,
                    recommendation_summary=None,
                    error=None,
                    created_at=now,
                    completed_at=now,
                )).inserted_primary_key[0]
                scan_ids.append(scan_id)
                for prompt_index in range(prompt_count):
                    conn.execute(insert(models.analytics_provider_answers).values(
                        scan_run_id=scan_id,
                        prompt_id=93000 + prompt_count * 10 + prompt_index,
                        prompt_text='Which platform is best?',
                        prompt_intent='Discovery',
                        topic_name='Platforms',
                        provider='Perplexity',
                        model='provider/model',
                        status='succeeded',
                        answer_text='Example is mentioned.',
                        raw_response='{}',
                                        latency_ms=10,
                        error=None,
                        created_at=now,
                        completed_at=now,
                    ))

        history = {
            row['id']: row for row in metrics.latest_prompt_evidence(workspace_id)['history']
        }
        self.assertNotEqual(
            history[scan_ids[0]]['cohort_id'],
            history[scan_ids[1]]['cohort_id'],
        )

    def test_prompt_scan_aggregates_only_saved_provider_evidence(self):
        workspace_id = self.create_project(user_id=91003)
        now = utc_now()
        with db.engine.begin() as conn:
            conn.execute(insert(models.competitors).values(
                workspace_id=workspace_id,
                name='Acme',
                domains=['acme.com'],
                aliases=[],
                created_at=now,
            ))
            conn.execute(insert(models.analytics_tracked_prompts), [
                {
                    'workspace_id': workspace_id,
                    'topic_id': None,
                    'prompt': 'Compare Example with Acme for analytics',
                    'intent': 'Comparison',
                    'active': True,
                    'created_at': now,
                    'updated_at': now,
                },
                {
                    'workspace_id': workspace_id,
                    'topic_id': None,
                    'prompt': 'Which analytics platform is established?',
                    'intent': 'Discovery',
                    'active': True,
                    'created_at': now,
                    'updated_at': now,
                },
            ])
        project = tenancy.workspace_for_member(workspace_id, 91003)
        job_id = jobs.create_analytics_job(project, 'prompt_scan', provider='Perplexity')
        search_payloads = [
            {
                'id': 'search-1',
                'results': [
                    {'title': 'Acme', 'url': 'https://acme.com/guide', 'snippet': 'Acme guide'},
                    {'title': 'Example', 'url': 'https://example.com/proof', 'snippet': 'Example proof'},
                ],
            },
            {
                'id': 'search-2',
                'results': [
                    {'title': 'Acme', 'url': 'https://acme.com/research', 'snippet': 'Acme research'},
                ],
            },
        ]
        answer_payloads = [
            {
                'id': 'answer-1',
                'status': 'completed',
                'model': 'provider/model',
                'output_text': 'Example and Acme are relevant options.',
                'citations': [{'title': 'Example proof', 'url': 'https://example.com/proof'}],
            },
            {
                'id': 'answer-2',
                'status': 'completed',
                'model': 'provider/model',
                'output_text': 'Acme is an established option.',
                'citations': [{'title': 'Acme research', 'url': 'https://acme.com/research'}],
            },
        ]
        with patch.dict(os.environ, {
            'PERPLEXITY_API_KEY': 'test-key',
            'PERPLEXITY_REQUEST_DELAY_SECONDS': '0',
            'HF_TOKEN': '',
            'OLLAMA_BASE_URL': '',
        }, clear=False), patch.object(
            perplexity_engine, 'call_perplexity_search', side_effect=search_payloads,
        ), patch.object(
            perplexity_engine, 'call_perplexity_answer', side_effect=answer_payloads,
        ):
            scanning.run_prompt_scan_job(job_id)

        with db.engine.connect() as conn:
            scan = conn.execute(select(models.analytics_prompt_scan_runs).where(
                models.analytics_prompt_scan_runs.c.job_id == job_id
            )).mappings().one()
        self.assertEqual(scan['status'], 'succeeded')
        self.assertEqual(scan['completed_count'], 2)
        self.assertEqual(scan['mention_rate'], 50)
        self.assertEqual(scan['citation_rate'], 50)
        self.assertEqual(scan['source_presence_rate'], 50)
        self.assertEqual(scan['share_of_voice'], 33.33)

    def test_mixed_public_and_private_dns_answers_are_rejected(self):
        addresses = [
            (2, 1, 6, '', ('93.184.216.34', 443)),
            (2, 1, 6, '', ('127.0.0.1', 443)),
        ]
        with patch.object(fetch.socket, 'getaddrinfo', return_value=addresses):
            with self.assertRaisesRegex(ValueError, 'publicly routable'):
                fetch.validate_public_web_url('https://example.com/')


if __name__ == '__main__':
    unittest.main()
