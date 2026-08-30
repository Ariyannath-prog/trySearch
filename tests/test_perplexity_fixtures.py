"""Replay recorded Perplexity responses through the parsers.

These are the contract tests for the Perplexity engine: they assert that the shapes
the provider actually returned still parse. When Perplexity changes its format,
re-record with scripts/record_fixture.py and these tests tell you what broke.

No network, no API key, no cost — the fixtures are committed JSON.
"""

import unittest

from conftest import load_fixture

from app.engines.perplexity import (
    normalise_perplexity_sources,
    perplexity_answer_citations,
    perplexity_answer_text,
)


class PerplexitySearchFixtureTests(unittest.TestCase):
    def setUp(self):
        self.payload = load_fixture('perplexity', 'search_basic')

    def test_recorded_search_has_ranked_results_with_urls(self):
        results = self.payload['results']
        self.assertGreater(len(results), 0)
        for result in results:
            self.assertIn('url', result)
            self.assertTrue(result['url'].startswith('http'))
            self.assertIn('title', result)


class PerplexityAnswerFixtureTests(unittest.TestCase):
    def setUp(self):
        self.payload = load_fixture('perplexity', 'answer_basic')

    def test_recorded_answer_completed(self):
        self.assertEqual(self.payload.get('status'), 'completed')

    def test_answer_text_is_extracted(self):
        text = perplexity_answer_text(self.payload)
        self.assertIsInstance(text, str)
        self.assertGreater(len(text.strip()), 0)

    def test_citations_are_urls_or_empty(self):
        # An engine returns citations where the provider supplies them and an empty
        # list where it does not. Both are valid; a non-list is not.
        citations = perplexity_answer_citations(self.payload)
        self.assertIsInstance(citations, list)
        for citation in citations:
            self.assertIsInstance(citation, str)

    def test_sources_normalise_from_both_payloads(self):
        search_payload = load_fixture('perplexity', 'search_basic')
        sources = normalise_perplexity_sources(search_payload, self.payload)
        self.assertIsInstance(sources, list)
        self.assertGreater(len(sources), 0)
        for source in sources:
            self.assertIn('url', source)
            self.assertIn('domain', source)
            self.assertTrue(source['url'].startswith('http'))
            self.assertGreaterEqual(source['rank'], 1)

    def test_search_and_agent_sources_are_labelled_separately(self):
        # Sources come from two origins and their ranks are per-origin, not global:
        # the Search API ranks 1..N once, while each of the agent's search blocks
        # restarts at 1. Keeping them labelled is what lets rank mean something.
        search_payload = load_fixture('perplexity', 'search_basic')
        sources = normalise_perplexity_sources(search_payload, self.payload)

        kinds = {source['source_kind'] for source in sources}
        self.assertEqual(kinds, {'search_result', 'agent_search_result'})

        search_ranks = [s['rank'] for s in sources if s['source_kind'] == 'search_result']
        self.assertEqual(
            search_ranks, list(range(1, len(search_ranks) + 1)),
            'Search API sources must stay ranked 1..N in order',
        )


class NoNetworkGuardTests(unittest.TestCase):
    def test_guard_blocks_real_connections(self):
        """The guard itself must work, or 'no network' proves nothing."""
        import socket

        from conftest import NetworkAccessDenied

        with self.assertRaises(NetworkAccessDenied):
            socket.create_connection(('example.com', 80), timeout=1)


if __name__ == '__main__':
    unittest.main()
