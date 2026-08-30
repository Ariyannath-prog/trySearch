import os
import unittest
from datetime import datetime
from unittest.mock import patch


os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'rag-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402
from app.rag import chunking  # noqa: E402
from app import db  # noqa: E402
from app import http_client  # noqa: E402
from app import jobs  # noqa: E402
from app import models  # noqa: E402
from app.rag import answers as rag_answers  # noqa: E402
from app.rag import ranking  # noqa: E402
from sqlalchemy import func, insert, select  # noqa: E402


class RagPipelineTests(unittest.TestCase):
    def test_visible_copy_chunking_is_bounded_and_overlapping(self):
        content = ' '.join(f'word{index}' for index in range(55))
        chunks = chunking.chunk_visible_text(
            content, chunk_words=20, overlap_words=5, max_chunks=3,
        )
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].split()[-5:], chunks[1].split()[:5])
        self.assertEqual(chunks[1].split()[-5:], chunks[2].split()[:5])

    def test_sparse_retrieval_prefers_relevant_grounded_chunk(self):
        rows = [
            {
                'id': 1, 'chunk_index': 0, 'document_title': 'Technical guide',
                'content_text': 'JSON-LD structured data schema markup helps parsers understand an organisation.',
            },
            {
                'id': 2, 'chunk_index': 0, 'document_title': 'Pricing',
                'content_text': 'Monthly pricing plans include a starter account and team account.',
            },
        ]
        ranked = ranking.rank_rag_chunks('Which page explains structured data schema?', rows)
        self.assertEqual(ranked[0]['id'], 1)
        self.assertEqual(ranked[0]['evidence_ref'], 'chunk:1')

    def test_model_synthesis_must_cite_a_retrieved_chunk(self):
        project = {'brand_name': 'Example', 'domain': 'example.com'}
        chunks = [{
            'id': 7, 'evidence_ref': 'chunk:7', 'document_title': 'Research',
            'document_url': 'https://example.com/research',
            'content_text': 'Example publishes a dated benchmark methodology.',
        }]
        valid_payload = {'choices': [{'message': {'content': (
            '{"answer":"The research page exposes a dated methodology.",'
            '"evidence_refs":["chunk:7"]}'
        )}}]}
        provider_env = {
            'HF_TOKEN': '', 'OLLAMA_BASE_URL': 'http://127.0.0.1:11434/v1',
            'OLLAMA_MODEL': 'test-open-model',
        }
        with patch.dict(os.environ, provider_env, clear=False), patch.object(
            rag_answers, 'external_json_request', return_value=valid_payload,
        ):
            result = rag_answers.rag_model_answer(project, 'What proof is present?', chunks)
        self.assertEqual(result['evidence_refs'], ['chunk:7'])

        invalid_payload = {'choices': [{'message': {'content': (
            '{"answer":"Unsupported answer.","evidence_refs":["chunk:999"]}'
        )}}]}
        with patch.dict(os.environ, provider_env, clear=False), patch.object(
            rag_answers, 'external_json_request', return_value=invalid_payload,
        ):
            with self.assertRaises(http_client.ProviderAPIError):
                rag_answers.rag_model_answer(project, 'What proof is present?', chunks)

    def test_site_audit_persists_retrievable_public_page_copy(self):
        now = datetime.utcnow()
        workspace_id = create_workspace(user_id=1, created_at=now)
        page = {
            'fetched': True, 'requested_url': 'https://example.com/research',
            'url': 'https://example.com/research', 'http_status': 200,
            'title': 'Original research', 'description': 'A benchmark study.',
            'headings': ['Benchmark methodology'], 'word_count': 12,
            'schema_blocks': 1, 'canonical': 'https://example.com/research',
            'noindex': False, 'language': 'en', 'internal_links': 2,
            'external_links': 1, 'readiness_score': 90, 'metadata_score': 100,
            'content_score': 70, 'crawlability_score': 100,
            'structured_data_score': 100, 'findings': [], 'fetched_at': now,
            'content_text': (
                'Our benchmark methodology reviews one hundred evidence-backed answers. '
                'The report includes publication dates, sources, and limitations.'
            ),
        }
        crawl = {
            'status': 'succeeded', 'start_url': 'https://example.com/',
            'final_url': 'https://example.com/', 'pages_discovered': 1,
            'pages_audited': 1, 'pages_failed': 0, 'pages': [page],
            'sitemaps': [], 'summary': 'One page audited.', 'readiness_score': 90,
            'metadata_score': 100, 'content_score': 70,
            'crawlability_score': 100, 'structured_data_score': 100,
        }
        audit_id = jobs.persist_site_audit(workspace_id, None, crawl)
        with db.engine.connect() as conn:
            document_count = conn.execute(select(func.count()).select_from(
                models.analytics_rag_documents
            ).where(models.analytics_rag_documents.c.audit_id == audit_id)).scalar_one()
            chunk_count = conn.execute(select(func.count()).select_from(
                models.analytics_rag_chunks
            ).where(models.analytics_rag_chunks.c.audit_id == audit_id)).scalar_one()
        self.assertEqual(document_count, 1)
        self.assertGreaterEqual(chunk_count, 1)
        retrieved = ranking.retrieve_audit_chunks(audit_id, 'benchmark methodology sources')
        self.assertEqual(retrieved[0]['document_url'], 'https://example.com/research')

        with server_pg.app.test_client() as client:
            with client.session_transaction() as session:
                session['user_id'] = 1
            response = client.get(
                f'/api/v1/analytics/projects/{workspace_id}/rag',
                query_string={'query': 'benchmark methodology sources'},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload['rag']['measurement_scope'], 'content_analysis_only')
            self.assertEqual(payload['rag']['retrieval'][0]['url'], 'https://example.com/research')

            with patch.dict(os.environ, {'HF_TOKEN': '', 'OLLAMA_BASE_URL': ''}, clear=False):
                response = client.post(
                    f'/api/v1/analytics/projects/{workspace_id}/rag',
                    json={'question': 'What does the benchmark methodology include?'},
                )
            self.assertEqual(response.status_code, 201)
            generated = response.get_json()['rag']['generated_insight']
            self.assertEqual(generated['provider'], 'trySearch local retrieval')
            self.assertTrue(generated['evidence_refs'][0].startswith('chunk:'))


if __name__ == '__main__':
    unittest.main()
