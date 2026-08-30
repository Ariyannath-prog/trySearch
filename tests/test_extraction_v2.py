"""T9: versioned extraction, rank, and citation categories."""

import json
import os
import unittest
from datetime import datetime

os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'extraction-v2-test-secret'

import server_pg  # noqa: E402,F401
from conftest import create_workspace  # noqa: E402

from sqlalchemy import func, insert, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.db import engine  # noqa: E402
from app.extraction.mentions import alias_offsets, text_mentions_alias  # noqa: E402
from app.extraction.pipeline import (  # noqa: E402
    EXTRACTOR_VERSION,
    extract_for_answer,
    reextract_workspace,
)
from app.extraction.rank import rank_entities  # noqa: E402
from app.models import (  # noqa: E402
    analytics_answer_sources,
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_tracked_prompts,
    competitors as competitors_table,
    extractions,
    mentions as mentions_table,
    workspaces,
)

BRAND = 'Alpha'


def make_workspace(user_id, domain='alpha.example'):
    return create_workspace(user_id=user_id, domain=domain, brand_name=BRAND)


def add_competitors(workspace_id, names):
    now = datetime.utcnow()
    ids = {}
    with engine.begin() as conn:
        for name in names:
            ids[name] = conn.execute(insert(competitors_table).values(
                workspace_id=workspace_id, name=name,
                domains=[f'{name.lower()}.example'], aliases=[],
                created_at=now,
            )).inserted_primary_key[0]
    return ids


def save_answer(workspace_id, answer_text, *, sources=(), status='ok'):
    """Persist a scan run, a prompt and one answer, returning the answer id."""
    now = datetime.utcnow()
    with engine.begin() as conn:
        prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
            workspace_id=workspace_id, topic_id=None, prompt='best tools',
            intent='Discovery', active=True, created_at=now, updated_at=now,
        )).inserted_primary_key[0]
        scan_id = conn.execute(insert(analytics_prompt_scan_runs).values(
            workspace_id=workspace_id, job_id=None, provider='Perplexity',
            model='m', region=None, competitor_snapshot='[]', status='succeeded',
            prompt_count=1, completed_count=1, mention_rate=None, citation_rate=None,
            source_presence_rate=None, share_of_voice=None,
            recommendation_summary=None, error=None, created_at=now, completed_at=now,
        )).inserted_primary_key[0]
        answer_id = conn.execute(insert(analytics_provider_answers).values(
            scan_run_id=scan_id, prompt_id=prompt_id, prompt_text='best tools',
            prompt_intent='Discovery', topic_name=None, provider='Perplexity',
            model='m', status=status, search_request_id=None, answer_request_id=None,
            answer_text=answer_text,
            raw_response=json.dumps({'search': None, 'answer': {'t': answer_text}}),
            latency_ms=1, error=None, created_at=now, completed_at=now,
        )).inserted_primary_key[0]
        for rank, url in enumerate(sources, start=1):
            conn.execute(insert(analytics_answer_sources).values(
                answer_id=answer_id, rank=rank, source_kind='search_result',
                title='t', url=url, domain=url.split('/')[2], snippet=None,
                published_at=None,
            ))
    return answer_id


def workspace_row(workspace_id):
    with engine.connect() as conn:
        return dict(conn.execute(select(workspaces).where(
            workspaces.c.id == workspace_id)).mappings().first())


def current_extraction(answer_id):
    with engine.connect() as conn:
        row = conn.execute(select(extractions).where(
            (extractions.c.answer_id == answer_id) & (extractions.c.is_current)
        )).mappings().first()
    return dict(row) if row else None


def mention_rows(extraction_id):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(
            select(mentions_table)
            .where(mentions_table.c.extraction_id == extraction_id)
            .order_by(mentions_table.c.rank)
        ).mappings().all()]


class AliasMatchingTests(unittest.TestCase):
    def test_alias_matches_on_word_boundary_only(self):
        # The single most important property in the module: "Aspire" must not
        # match "Aspireship", or every mention rate is inflated.
        self.assertIsNone(alias_offsets('Try Aspireship today', ['Aspire']))
        self.assertIsNotNone(alias_offsets('Try Aspire today', ['Aspire']))
        self.assertFalse(text_mentions_alias('Aspireship', ['Aspire']))

    def test_offset_is_the_earliest_match(self):
        self.assertEqual(alias_offsets('xx Gamma yy Alpha', ['Alpha', 'Gamma']), 3)

    def test_text_mentions_alias_is_defined_by_offsets(self):
        for text in ('Alpha wins', 'nothing here', '', 'Alphabet soup'):
            self.assertEqual(
                text_mentions_alias(text, ['Alpha']),
                alias_offsets(text, ['Alpha']) is not None,
                'the two must never disagree - there is one regex',
            )


class RankAlgorithmTests(unittest.TestCase):
    COMPETITORS = [
        {'id': 1, 'name': 'Beta', 'domains': [], 'aliases': []},
        {'id': 2, 'name': 'Gamma', 'domains': [], 'aliases': []},
    ]

    def test_rank_orders_by_first_mention(self):
        text = 'Beta is popular, Alpha is precise, and Gamma is cheap.'
        brand_rank, rows = rank_entities(
            text, brand_aliases=[BRAND], competitors=self.COMPETITORS)
        self.assertEqual(brand_rank, 2)
        self.assertEqual(len(rows), 3)
        offsets = [row['char_offset'] for row in rows]
        self.assertEqual(offsets, sorted(offsets), 'mentions ascend by offset')
        self.assertEqual([r['rank'] for r in rows], [1, 2, 3])

    def test_brand_only_mention_is_rank_one(self):
        brand_rank, rows = rank_entities(
            'Alpha is the only one named.', brand_aliases=[BRAND],
            competitors=self.COMPETITORS)
        self.assertEqual(brand_rank, 1)
        self.assertEqual(len(rows), 1)

    def test_absent_brand_has_null_rank(self):
        brand_rank, rows = rank_entities(
            'Beta and Gamma are options.', brand_aliases=[BRAND],
            competitors=self.COMPETITORS)
        self.assertIsNone(brand_rank, 'absent must be NULL, never 0')
        self.assertNotIn(0, [r['rank'] for r in rows])
        self.assertEqual(len(rows), 2, 'competitor mentions are still recorded')


class ExtractionPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace_id = make_workspace(94001)
        cls.workspace = workspace_row(cls.workspace_id)
        add_competitors(cls.workspace_id, ['Beta', 'Gamma'])

    def test_extraction_records_rank_and_mentions(self):
        answer_id = save_answer(
            self.workspace_id, 'Beta leads, then Alpha, then Gamma.')
        extract_for_answer({'id': answer_id, 'answer_text':
                            'Beta leads, then Alpha, then Gamma.'}, self.workspace)
        row = current_extraction(answer_id)
        self.assertEqual(row['brand_rank'], 2)
        self.assertTrue(row['brand_mentioned'])
        self.assertEqual(row['extractor_version'], EXTRACTOR_VERSION)
        self.assertEqual(len(mention_rows(row['id'])), 3)

    def test_own_citation_sets_brand_cited_and_category(self):
        text = 'Alpha is good.'
        answer_id = save_answer(self.workspace_id, text, sources=[
            'https://alpha.example/proof', 'https://beta.example/rival',
        ])
        extract_for_answer({'id': answer_id, 'answer_text': text}, self.workspace)
        self.assertTrue(current_extraction(answer_id)['brand_cited'])
        with engine.connect() as conn:
            categories = {
                r['url']: r['category'] for r in conn.execute(
                    select(analytics_answer_sources).where(
                        analytics_answer_sources.c.answer_id == answer_id)
                ).mappings()
            }
        self.assertEqual(categories['https://alpha.example/proof'], 'own')
        self.assertEqual(categories['https://beta.example/rival'], 'competitor')

    def test_reextraction_writes_new_version_and_flips_is_current(self):
        text = 'Alpha only.'
        answer_id = save_answer(self.workspace_id, text)
        first = extract_for_answer({'id': answer_id, 'answer_text': text},
                                   self.workspace)
        second = extract_for_answer({'id': answer_id, 'answer_text': text},
                                    self.workspace)
        self.assertNotEqual(first, second, 'a re-run inserts a new row')

        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(select(extractions).where(
                extractions.c.answer_id == answer_id)).mappings().all()]
        self.assertEqual(len(rows), 2)
        current = [r for r in rows if r['is_current']]
        self.assertEqual(len(current), 1, 'exactly one current extraction')
        self.assertEqual(current[0]['id'], second)

    def test_two_current_extractions_are_rejected_by_the_database(self):
        """The partial unique index, not application code, is what enforces this."""
        text = 'Alpha again.'
        answer_id = save_answer(self.workspace_id, text)
        extract_for_answer({'id': answer_id, 'answer_text': text}, self.workspace)
        with self.assertRaises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(insert(extractions).values(
                    answer_id=answer_id, extractor_version='forced',
                    is_current=True, brand_mentioned=True, brand_rank=1,
                    brand_cited=False, created_at=datetime.utcnow(),
                ))


class ReextractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace_id = make_workspace(94002, domain='alpha2.example')
        cls.workspace = workspace_row(cls.workspace_id)
        add_competitors(cls.workspace_id, ['Beta'])
        cls.answers = [
            save_answer(cls.workspace_id, 'Alpha then Beta.'),
            save_answer(cls.workspace_id, 'Beta only here.'),
        ]

    def test_reextraction_makes_zero_provider_calls(self):
        # The autouse no_network fixture fails the test on any socket, so simply
        # completing proves no provider was called.
        count = reextract_workspace(self.workspace_id)
        self.assertEqual(count, 2)

    def test_current_extractions_equal_ok_answers(self):
        reextract_workspace(self.workspace_id)
        with engine.connect() as conn:
            current = conn.execute(
                select(func.count()).select_from(extractions)
                .where(extractions.c.answer_id.in_(self.answers)
                       & extractions.c.is_current)
            ).scalar_one()
        self.assertEqual(current, len(self.answers))

    def test_reextraction_changes_no_provider_answer_row(self):
        """raw_response is immutable. That is what makes replay possible."""
        with engine.connect() as conn:
            before = [dict(r) for r in conn.execute(
                select(analytics_provider_answers).where(
                    analytics_provider_answers.c.id.in_(self.answers))
            ).mappings().all()]
        reextract_workspace(self.workspace_id)
        with engine.connect() as conn:
            after = [dict(r) for r in conn.execute(
                select(analytics_provider_answers).where(
                    analytics_provider_answers.c.id.in_(self.answers))
            ).mappings().all()]
        self.assertEqual(before, after, 'no answer row may change on re-extraction')


if __name__ == '__main__':
    unittest.main()
