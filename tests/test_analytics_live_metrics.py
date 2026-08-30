import json
import os
import unittest
from datetime import datetime, timezone

from sqlalchemy import insert


os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'analytics-live-metrics-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace, set_extraction  # noqa: E402
from app import db  # noqa: E402
from app import metrics  # noqa: E402
from app import models  # noqa: E402


class AnalyticsLiveMetricTests(unittest.TestCase):
    def test_evidence_payload_derives_rankings_and_history_from_saved_records(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        workspace_id = create_workspace(user_id=1, created_at=now)
        with db.engine.begin() as conn:
            scan_id = conn.execute(insert(models.analytics_prompt_scan_runs).values(
                workspace_id=workspace_id, job_id=None, provider='Perplexity', model='provider/model',
                region='IN', competitor_snapshot=json.dumps([{'name': 'Acme', 'domain': 'acme.com'}]),
                status='succeeded', prompt_count=2, completed_count=2,
                mention_rate=50, citation_rate=50, source_presence_rate=50, share_of_voice=33.33,
                recommendation_summary=None, error=None, created_at=now, completed_at=now,
            )).inserted_primary_key[0]
            first_answer_id = conn.execute(insert(models.analytics_provider_answers).values(
                scan_run_id=scan_id, prompt_id=101, prompt_text='Compare Example and Acme',
                prompt_intent='Comparison', topic_name='Platforms', provider='Perplexity',
                model='provider/model', status='succeeded', answer_text='Example and Acme are options.',
                raw_response='{}', latency_ms=25, created_at=now, completed_at=now,
            )).inserted_primary_key[0]
            second_answer_id = conn.execute(insert(models.analytics_provider_answers).values(
                scan_run_id=scan_id, prompt_id=102, prompt_text='Which tool is established?',
                prompt_intent='Discovery', topic_name='Platforms', provider='Perplexity',
                model='provider/model', status='succeeded', answer_text='Acme is established.',
                raw_response='{}', latency_ms=25, created_at=now, completed_at=now,
            )).inserted_primary_key[0]
            conn.execute(insert(models.analytics_answer_sources), [
                {'answer_id': first_answer_id, 'rank': 1, 'source_kind': 'search_result', 'title': 'Acme', 'url': 'https://acme.com/a', 'domain': 'acme.com'},
                {'answer_id': first_answer_id, 'rank': 2, 'source_kind': 'search_result', 'title': 'Example', 'url': 'https://example.com/a', 'domain': 'example.com'},
                {'answer_id': second_answer_id, 'rank': 3, 'source_kind': 'search_result', 'title': 'Acme', 'url': 'https://acme.com/b', 'domain': 'acme.com'},
            ])
        set_extraction(first_answer_id, brand_mentioned=True, brand_cited=True, brand_rank=1)
        set_extraction(second_answer_id, brand_mentioned=False, brand_cited=False)
        from app.extraction.pipeline import categorise_sources
        from app.db import engine as _e
        from sqlalchemy import select as _sel
        from app.models import workspaces as _ws
        with _e.begin() as c:
            ws = dict(c.execute(_sel(_ws).where(_ws.c.id == workspace_id)).mappings().first())
            for aid in (first_answer_id, second_answer_id):
                categorise_sources(aid, workspace=ws, competitors=[], conn=c)

        payload = metrics.latest_prompt_evidence(workspace_id)

        self.assertEqual(payload['measurement']['source'], 'stored_provider_evidence')
        self.assertTrue(payload['measurement']['cohort_id'])
        self.assertEqual(payload['run']['average_source_position'], 2)
        self.assertEqual(payload['run']['ranked_appearance_count'], 1)
        self.assertEqual(payload['history'][0]['average_source_position'], 2)
        rankings = {item['name']: item for item in payload['brand_rankings']}
        self.assertEqual(rankings['Example']['visibility'], 50)
        self.assertEqual(rankings['Example']['share_of_voice'], 33.33)
        self.assertEqual(rankings['Example']['average_source_position'], 2)
        self.assertEqual(rankings['Acme']['visibility'], 100)
        self.assertEqual(rankings['Acme']['share_of_voice'], 66.67)
        self.assertEqual(rankings['Acme']['average_source_position'], 2)


if __name__ == '__main__':
    unittest.main()
