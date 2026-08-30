import os
import unittest
from unittest.mock import patch


# Never let this helper-level test module attach to a developer or production DB.
os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'analytics-test-secret'

import server_pg  # noqa: E402
from app.crawler import fetch  # noqa: E402
from app.engines import perplexity  # noqa: E402
from app import recommendations  # noqa: E402
from app.crawler import scoring  # noqa: E402


class AnalyticsPipelineTests(unittest.TestCase):
    def test_private_and_nonstandard_urls_are_rejected(self):
        for url in (
            'http://127.0.0.1/',
            'http://localhost/',
            'http://10.0.0.8/',
            'https://example.com:8443/',
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    fetch.validate_public_web_url(url)

    def test_crawl_urls_stay_on_site_and_drop_tracking_parameters(self):
        allowed_hosts = {'example.com'}
        self.assertEqual(
            fetch.canonicalise_crawl_url(
                'https://example.com/start',
                '/guide?utm_source=newsletter&topic=aeo#section',
                allowed_hosts,
            ),
            'https://example.com/guide?topic=aeo',
        )
        self.assertIsNone(
            fetch.canonicalise_crawl_url(
                'https://example.com/start',
                'https://other.example/guide',
                allowed_hosts,
            )
        )

    def test_fetch_failures_do_not_become_measured_zero_scores(self):
        scored = scoring.score_website_snapshot({
            'fetched': False,
            'error': 'HTTP 503',
        })
        self.assertIsNone(scored['readiness_score'])
        self.assertIsNone(scored['metadata_score'])
        self.assertEqual(scored['findings'][0]['code'], 'fetch_failed')

    def test_current_agent_response_keeps_text_citations_and_sources(self):
        payload = {
            'id': 'resp-test',
            'status': 'completed',
            'model': 'provider/model',
            'output': [
                {
                    'type': 'search_results',
                    'results': [{
                        'title': 'Example evidence',
                        'url': 'https://example.com/evidence',
                        'snippet': 'A source snippet.',
                    }],
                },
                {
                    'type': 'message',
                    'content': [{
                        'type': 'output_text',
                        'text': 'Example is present in the saved answer.',
                        'annotations': [{
                            'type': 'url_citation',
                            'title': 'Example evidence',
                            'url': 'https://example.com/evidence',
                        }],
                    }],
                },
            ],
        }
        self.assertEqual(
            perplexity.perplexity_answer_text(payload),
            'Example is present in the saved answer.',
        )
        self.assertEqual(
            perplexity.perplexity_answer_citations(payload)[0]['url'],
            'https://example.com/evidence',
        )
        sources = perplexity.normalise_perplexity_sources({}, payload)
        self.assertEqual(
            {source['source_kind'] for source in sources},
            {'agent_search_result', 'answer_citation'},
        )

    def test_open_model_actions_must_reference_saved_evidence(self):
        project = {'brand_name': 'Example', 'domain': 'example.com'}
        evidence = [{
            'id': 42,
            'prompt': 'Which platform is best?',
            'brand_mentioned': False,
            'brand_cited': False,
            'source_present': False,
            'best_source_rank': None,
            'answer_text': 'A saved provider answer.',
        }]
        provider_payload = {
            'choices': [{
                'message': {
                    'content': '{"opportunities":[{"title":"Publish proof","rationale":"The saved answer lacks first-party support.","evidence_refs":"answer:42","priority":"high"}]}'
                }
            }]
        }
        with patch.dict(os.environ, {
            'HF_TOKEN': '',
            'OLLAMA_BASE_URL': 'http://127.0.0.1:11434/v1',
            'OLLAMA_MODEL': 'gpt-oss:20b',
        }, clear=False), patch.object(
            recommendations,
            'external_json_request',
            return_value=provider_payload,
        ):
            actions = recommendations.open_model_evidence_opportunities(project, evidence)
        self.assertEqual(actions[0]['evidence_refs'], 'answer:42')

        provider_payload['choices'][0]['message']['content'] = (
            '{"opportunities":[{"title":"Unsupported","rationale":"No saved record.",'
            '"evidence_refs":"answer:999","priority":"high"}]}'
        )
        with patch.dict(os.environ, {
            'HF_TOKEN': '',
            'OLLAMA_BASE_URL': 'http://127.0.0.1:11434/v1',
            'OLLAMA_MODEL': 'gpt-oss:20b',
        }, clear=False), patch.object(
            recommendations,
            'external_json_request',
            return_value=provider_payload,
        ):
            self.assertIsNone(recommendations.open_model_evidence_opportunities(project, evidence))


if __name__ == '__main__':
    unittest.main()
