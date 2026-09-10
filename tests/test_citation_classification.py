"""T15: citation classification and the most-cited-domains view."""

import contextlib
import os
import pathlib
import unittest
from datetime import datetime

os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'citation-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import event, insert, select  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import metrics  # noqa: E402
from app.db import engine  # noqa: E402
from app.extraction import citations  # noqa: E402
from app.models import (  # noqa: E402
    analytics_answer_sources,
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_tracked_prompts,
    extractions,
    users,
)

OWN = ['acme.com']
RIVALS = ['rival.com']


@contextlib.contextmanager
def count_queries():
    """Count SELECTs issued while the block runs."""
    seen = []

    def before(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            seen.append(statement)

    event.listen(engine, 'before_cursor_execute', before)
    try:
        yield seen
    finally:
        event.remove(engine, 'before_cursor_execute', before)


class ClassificationOrderTests(unittest.TestCase):
    def classify(self, url, **kwargs):
        return citations.classify_citation(
            url, own_domains=kwargs.get('own', OWN),
            competitor_domains=kwargs.get('rivals', RIVALS))

    def test_subdomain_of_own_domain_counts_as_own(self):
        self.assertEqual(self.classify('https://docs.acme.com/guide'), 'own')
        self.assertEqual(self.classify('https://www.acme.com/'), 'own')
        self.assertEqual(self.classify('https://acme.com/pricing'), 'own')

    def test_own_beats_competitor_when_both_match(self):
        """A page on your domain that also names a rival is still your citation."""
        got = citations.classify_citation(
            'https://acme.com/compare/rival',
            own_domains=['acme.com'], competitor_domains=['acme.com', 'rival.com'])
        self.assertEqual(got, 'own')

    def test_classification_is_first_match_wins(self):
        # github.com is in the developer list. Claimed as own, own must win.
        self.assertEqual(
            citations.classify_citation('https://github.com/acme/sdk',
                                        own_domains=['github.com'],
                                        competitor_domains=[]),
            'own')
        # Claimed as a competitor, competitor beats developer.
        self.assertEqual(
            citations.classify_citation('https://github.com/acme/sdk',
                                        own_domains=[],
                                        competitor_domains=['github.com']),
            'competitor')
        # Claimed by nobody, the curated list decides.
        self.assertEqual(
            citations.classify_citation('https://github.com/acme/sdk',
                                        own_domains=[], competitor_domains=[]),
            'developer')

    def test_category_order_is_the_documented_one(self):
        self.assertEqual(
            citations.CATEGORY_ORDER,
            ('own', 'competitor', 'editorial', 'social', 'forum', 'developer', 'other'))

    def test_matching_is_never_a_substring(self):
        """notreddit.com is not Reddit, and a suffix attack is not GitHub."""
        for url in ('https://notreddit.com/r/saas',
                    'https://reddit.com.evil.net/phish',
                    'https://fakegithub.com/acme',
                    'https://acme.com.attacker.net/'):
            with self.subTest(url=url):
                self.assertEqual(self.classify(url), 'other')

    def test_unclassified_is_other_not_a_silent_drop(self):
        self.assertEqual(self.classify('https://some-blog.example/post'), 'other')

    def test_curated_lists_are_loaded(self):
        self.assertGreaterEqual(len(citations.curated_domains('editorial')), 200,
                                'SPRINT asks the editorial list to start at 200-300')
        for category in ('social', 'forum', 'developer'):
            self.assertGreater(len(citations.curated_domains(category)), 0)


class RedirectResolutionTests(unittest.TestCase):
    def test_redirect_chain_resolves_to_final_domain(self):
        """A shortener names the wrong publisher if taken at face value."""

        class FakeResponse:
            url = 'https://www.g2.com/products/acme/reviews'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeOpener:
            def open(self, url, timeout=None):
                return FakeResponse()

        host = citations.resolve_final_host('https://bit.ly/xyz', opener=FakeOpener())
        self.assertEqual(host, 'g2.com', 'www. stripped, final URL used')
        self.assertEqual(
            citations.classify_citation('https://bit.ly/xyz', own_domains=OWN,
                                        competitor_domains=RIVALS, host=host),
            'editorial')

    def test_unreachable_link_falls_back_to_the_url_as_given(self):
        class ExplodingOpener:
            def open(self, url, timeout=None):
                raise OSError('unreachable')

        host = citations.resolve_final_host('https://www.g2.com/x',
                                            opener=ExplodingOpener())
        self.assertEqual(host, 'g2.com', 'an unreachable link is still a citation')


class DomainListPrivacyTests(unittest.TestCase):
    def test_domain_list_never_appears_in_an_api_response(self):
        """The list is the moat. It must not leak through any endpoint."""
        sample = sorted(citations.curated_domains('editorial'))[:40]

        with server_pg.app.test_client() as client:
            with engine.begin() as conn:
                conn.execute(insert(users).values(
                    username='citation_user', email='cit@example.com',
                    password_hash=generate_password_hash('citation-password-1'),
                    created_at=datetime.utcnow()))
            client.post('/api/login', json={'username': 'citation_user',
                                            'password': 'citation-password-1'})
            bodies = []
            for rule in server_pg.app.url_map.iter_rules():
                if not rule.rule.startswith('/api') or rule.arguments:
                    continue
                for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
                    response = client.open(rule.rule, method=method, json={})
                    bodies.append(response.get_data(as_text=True))

        blob = '\n'.join(bodies)
        leaked = [d for d in sample if d in blob]
        self.assertEqual(leaked, [], f'curated domains leaked into an API response: {leaked}')

    def test_citations_endpoint_returns_categories_not_the_list(self):
        """The leak test above skips routes with path args, so the one endpoint
        that actually touches the lists is checked directly."""
        workspace_id = create_workspace(user_id=97009, domain='leak.example',
                                        brand_name='Leak')
        with engine.begin() as conn:
            conn.execute(insert(users).values(
                username='leak_user', email='leak@example.com',
                password_hash=generate_password_hash('leak-password-1'),
                created_at=datetime.utcnow()))
        with server_pg.app.test_client() as client:
            client.post('/api/login', json={'username': 'leak_user',
                                            'password': 'leak-password-1'})
            body = client.get(
                f'/api/analytics/projects/{workspace_id}/citations'
            ).get_data(as_text=True)

        sample = sorted(citations.curated_domains('editorial'))[:60]
        leaked = [d for d in sample if d in body]
        self.assertEqual(leaked, [], f'citations endpoint leaked the list: {leaked}')

    def test_domain_list_is_not_in_the_js_bundle(self):
        repo = pathlib.Path(__file__).resolve().parent.parent
        sample = sorted(citations.curated_domains('editorial'))[:40]
        for js in repo.glob('*.js'):
            text = js.read_text(errors='replace')
            leaked = [d for d in sample if d in text]
            self.assertEqual(leaked, [], f'{js.name} ships curated domains: {leaked}')


class TopDomainsQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace_id = create_workspace(user_id=97001, domain='acme.com',
                                            brand_name='Acme')
        now = datetime.utcnow()
        rows = [
            ('https://acme.com/a', 'acme.com', 'own', True),
            ('https://acme.com/b', 'acme.com', 'own', True),
            ('https://g2.com/x', 'g2.com', 'editorial', True),
            ('https://g2.com/y', 'g2.com', 'editorial', False),
            ('https://reddit.com/r/x', 'reddit.com', 'forum', False),
            ('https://rival.com/z', 'rival.com', 'competitor', False),
            ('https://techcrunch.com/p', 'techcrunch.com', 'editorial', False),
        ]
        with engine.begin() as conn:
            prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
                workspace_id=cls.workspace_id, topic_id=None, prompt='best crm',
                intent='Discovery', active=True, created_at=now, updated_at=now,
            )).inserted_primary_key[0]
            scan_id = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=cls.workspace_id, job_id=None, provider='Perplexity',
                model='m', region=None, competitor_snapshot='[]', status='succeeded',
                run_type='scheduled', prompt_count=len(rows), completed_count=len(rows),
                mention_rate=None, citation_rate=None, source_presence_rate=None,
                share_of_voice=None, recommendation_summary=None, error=None,
                created_at=now, completed_at=now,
            )).inserted_primary_key[0]
            for rank, (url, domain, category, brand_mentioned) in enumerate(rows, 1):
                answer_id = conn.execute(insert(analytics_provider_answers).values(
                    scan_run_id=scan_id, prompt_id=prompt_id, prompt_text='best crm',
                    prompt_intent='Discovery', topic_name=None, provider='Perplexity',
                    model='m', status='ok', search_request_id=None,
                    answer_request_id=None, answer_text='text', raw_response='{}',
                    latency_ms=1, error=None, created_at=now, completed_at=now,
                )).inserted_primary_key[0]
                conn.execute(insert(extractions).values(
                    answer_id=answer_id, extractor_version='t15', is_current=True,
                    brand_mentioned=brand_mentioned, brand_rank=1 if brand_mentioned else None,
                    brand_cited=category == 'own', created_at=now))
                conn.execute(insert(analytics_answer_sources).values(
                    answer_id=answer_id, rank=rank, source_kind='search_result',
                    title='t', url=url, domain=domain, snippet=None,
                    published_at=None, category=category))

    def test_top_domains_is_a_single_query(self):
        with count_queries() as seen:
            rollup = metrics.citation_domain_rollup(self.workspace_id)
        self.assertEqual(len(seen), 1,
                         f'expected one GROUP BY, saw {len(seen)} SELECTs')
        self.assertEqual(rollup['total_citations'], 7)

    def test_rollup_splits_own_competitor_and_third_party(self):
        rollup = metrics.citation_domain_rollup(self.workspace_id)
        buckets = {}
        for row in rollup['domains']:
            buckets[row['bucket']] = buckets.get(row['bucket'], 0) + row['citations']
        self.assertEqual(buckets['own'], 2)
        self.assertEqual(buckets['competitor'], 1)
        self.assertEqual(buckets['third_party'], 4)

    def test_shares_sum_to_one(self):
        rollup = metrics.citation_domain_rollup(self.workspace_id)
        self.assertAlmostEqual(sum(r['share'] for r in rollup['domains']), 1.0)

    def test_competitor_gaps_exclude_domains_that_cite_you(self):
        """g2.com cites you in one answer, so it is not a gap. reddit is."""
        gaps = {row['domain'] for row in
                metrics.competitor_citation_gaps(self.workspace_id)}
        self.assertNotIn('g2.com', gaps, 'g2 already cites you in one answer')
        self.assertNotIn('acme.com', gaps, 'your own domain is never a gap')
        self.assertNotIn('rival.com', gaps, 'a rival domain is not a gap')
        self.assertIn('reddit.com', gaps)
        self.assertIn('techcrunch.com', gaps)

    def test_competitor_gaps_is_a_single_query(self):
        with count_queries() as seen:
            metrics.competitor_citation_gaps(self.workspace_id)
        self.assertEqual(len(seen), 1, f'expected one query, saw {len(seen)}')


if __name__ == '__main__':
    unittest.main()
