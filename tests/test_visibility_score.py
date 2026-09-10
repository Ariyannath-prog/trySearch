"""T10: the PRD §13 Visibility Score and metrics_daily.

test_prd_worked_example is the anchor for the whole build. If it returns 44.4 or
45.0, something in T9's extraction is wrong - fix T9, do not adjust the formula.
"""

import os
import unittest
from datetime import date, datetime, timezone

os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'visibility-score-test-secret'

import server_pg  # noqa: E402,F401
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert, select  # noqa: E402

from app import rollup  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_tracked_prompts,
    extractions,
    metrics_daily,
)


def seed_answers(workspace_id, spec, *, run_type='scheduled', provider='Perplexity',
                 when=None):
    """Create a run and one answer + extraction per entry in spec.

    spec entries are (mentioned, rank, cited) triples.
    """
    now = when or datetime.utcnow()
    with engine.begin() as conn:
        prompt_id = conn.execute(insert(analytics_tracked_prompts).values(
            workspace_id=workspace_id, topic_id=None, prompt='best tools',
            intent='Discovery', active=True, created_at=now, updated_at=now,
        )).inserted_primary_key[0]
        scan_id = conn.execute(insert(analytics_prompt_scan_runs).values(
            workspace_id=workspace_id, job_id=None, provider=provider, model='m',
            region=None, competitor_snapshot='[]', status='succeeded',
            run_type=run_type, prompt_count=len(spec), completed_count=len(spec),
            mention_rate=None, citation_rate=None, source_presence_rate=None,
            share_of_voice=None, recommendation_summary=None, error=None,
            created_at=now, completed_at=now,
        )).inserted_primary_key[0]
        for mentioned, rank, cited in spec:
            answer_id = conn.execute(insert(analytics_provider_answers).values(
                scan_run_id=scan_id, prompt_id=prompt_id, prompt_text='best tools',
                prompt_intent='Discovery', topic_name=None, provider=provider,
                model='m', status='ok', search_request_id=None,
                answer_request_id=None, answer_text='text', raw_response='{}',
                latency_ms=1, error=None, created_at=now, completed_at=now,
            )).inserted_primary_key[0]
            conn.execute(insert(extractions).values(
                answer_id=answer_id, extractor_version='test', is_current=True,
                brand_mentioned=mentioned, brand_rank=rank, brand_cited=cited,
                created_at=now,
            ))
    return scan_id


class WorkedExampleTests(unittest.TestCase):
    """The anchor test. PRD §13's worked example, to one decimal place."""

    def test_prd_worked_example(self):
        # 100 answers; mentioned in 40; among those rank 1 in 20, rank 2 in 12,
        # rank 3 in 8; own domain cited in 15.
        reciprocal = 20 * (1 / 1) + 12 * (1 / 2) + 8 * (1 / 3)
        metrics = rollup.score_from_counts(
            total_answers=100, mentioned=40,
            reciprocal_rank_sum=reciprocal, cited=15)

        self.assertAlmostEqual(metrics['mention_rate'], 0.40, places=3)
        self.assertAlmostEqual(metrics['position_score'], 0.717, places=3)
        self.assertAlmostEqual(metrics['citation_rate'], 0.15, places=3)
        self.assertEqual(round(metrics['visibility_score'], 1), 44.5)

    def test_worked_example_end_to_end_through_the_rollup(self):
        """The same numbers, but derived from extraction rows rather than counts."""
        workspace_id = create_workspace(user_id=95001, domain='anchor.example',
                                        brand_name='Anchor')
        spec = []
        spec += [(True, 1, False)] * 20
        spec += [(True, 2, False)] * 12
        spec += [(True, 3, False)] * 8
        spec += [(False, None, False)] * 60
        # 15 of the 100 carry an own-domain citation.
        spec = [(m, r, i < 15) for i, (m, r, _c) in enumerate(spec)]
        self.assertEqual(len(spec), 100)
        self.assertEqual(sum(1 for m, _r, _c in spec if m), 40)
        self.assertEqual(sum(1 for _m, _r, c in spec if c), 15)

        seed_answers(workspace_id, spec)
        blended = rollup.rollup_workspace_day(workspace_id, rollup.utc_today())
        self.assertEqual(blended['answer_count'], 100)
        self.assertEqual(round(blended['visibility_score'], 1), 44.5)


class FormulaEdgeCaseTests(unittest.TestCase):
    def test_never_mentioned_gives_zero_position_score(self):
        metrics = rollup.score_from_counts(
            total_answers=10, mentioned=0, reciprocal_rank_sum=0.0, cited=0)
        self.assertEqual(metrics['position_score'], 0.0,
                         'never mentioned is a measured 0, not NULL')
        self.assertEqual(metrics['mention_rate'], 0.0)
        self.assertEqual(metrics['visibility_score'], 0.0)

    def test_empty_denominator_is_null_not_zero(self):
        metrics = rollup.score_from_counts(
            total_answers=0, mentioned=0, reciprocal_rank_sum=0.0, cited=0)
        for key in ('mention_rate', 'position_score', 'citation_rate',
                    'visibility_score'):
            self.assertIsNone(metrics[key],
                              f'{key} must be NULL when nothing was measured')

    def test_blend_is_simple_average(self):
        """A 1-answer engine moves the blend as much as a 100-answer engine."""
        big = rollup.score_from_counts(
            total_answers=100, mentioned=100, reciprocal_rank_sum=100.0, cited=100)
        small = rollup.score_from_counts(
            total_answers=1, mentioned=0, reciprocal_rank_sum=0.0, cited=0)
        blended = rollup.blend([big, small])

        self.assertEqual(big['visibility_score'], 100.0)
        self.assertEqual(small['visibility_score'], 0.0)
        self.assertEqual(blended['visibility_score'], 50.0,
                         'simple average, not weighted by answer count')
        self.assertEqual(blended['answer_count'], 101)

    def test_blend_of_nothing_is_null(self):
        empty = rollup.score_from_counts(
            total_answers=0, mentioned=0, reciprocal_rank_sum=0.0, cited=0)
        self.assertIsNone(rollup.blend([empty])['visibility_score'])

    def test_weights_match_the_prd(self):
        self.assertEqual(
            (rollup.WEIGHT_MENTION_RATE, rollup.WEIGHT_POSITION_SCORE,
             rollup.WEIGHT_CITATION_RATE),
            (0.5, 0.3, 0.2), 'PRD §13 weights - changing these recomputes history')


class RollupBehaviourTests(unittest.TestCase):
    def test_on_demand_runs_are_excluded(self):
        workspace_id = create_workspace(user_id=95002, domain='ondemand.example',
                                        brand_name='OnDemand')
        # A perfect on-demand run, which must not count at all.
        seed_answers(workspace_id, [(True, 1, True)] * 10, run_type='on_demand')
        blended = rollup.rollup_workspace_day(workspace_id, rollup.utc_today())
        self.assertIsNone(blended['visibility_score'],
                          'on-demand answers must not reach metrics_daily')
        self.assertEqual(blended['answer_count'], 0)

        # The same answers as a scheduled run do count.
        seed_answers(workspace_id, [(True, 1, True)] * 10, run_type='scheduled')
        blended = rollup.rollup_workspace_day(workspace_id, rollup.utc_today())
        self.assertEqual(blended['answer_count'], 10)
        self.assertEqual(blended['visibility_score'], 100.0)

    @staticmethod
    def _row_count(workspace_id):
        with engine.connect() as conn:
            return len(conn.execute(select(metrics_daily).where(
                metrics_daily.c.workspace_id == workspace_id)).mappings().all())

    def test_rollup_is_idempotent(self):
        workspace_id = create_workspace(user_id=95003, domain='idem.example',
                                        brand_name='Idem')
        seed_answers(workspace_id, [(True, 1, True), (False, None, False)])

        first = rollup.rollup_workspace_day(workspace_id, rollup.utc_today())
        after_one = self._row_count(workspace_id)
        second = rollup.rollup_workspace_day(workspace_id, rollup.utc_today())
        self.assertEqual(first, second)

        # T12 seeded the engines table, so a day now yields a blended row *and* a
        # per-engine row. Idempotency is that recomputing does not add more.
        self.assertEqual(self._row_count(workspace_id), after_one,
                         'recomputing a date overwrites, never duplicates')


class ReadPathTests(unittest.TestCase):
    def test_no_dashboard_read_touches_provider_answers(self):
        """CLAUDE.md invariant 2, asserted by counting queries, not by reading code."""
        workspace_id = create_workspace(user_id=95004, domain='readpath.example',
                                        brand_name='ReadPath')
        seed_answers(workspace_id, [(True, 1, True)] * 3)
        rollup.rollup_workspace_day(workspace_id, rollup.utc_today())

        statements = []
        from sqlalchemy import event
        engine_obj = engine

        def record(conn, cursor, statement, params, context, executemany):
            statements.append(statement)

        event.listen(engine_obj, 'before_cursor_execute', record)
        try:
            rollup.latest_metrics(workspace_id)
        finally:
            event.remove(engine_obj, 'before_cursor_execute', record)

        joined = ' '.join(statements).lower()
        self.assertIn('metrics_daily', joined, 'the read path must use the rollup')
        self.assertNotIn('analytics_provider_answers', joined,
                         'a dashboard read must never touch raw answers')

    def test_analytics_report_reports_site_health_separately(self):
        """The fabricated crawl-score "engines" list must not come back."""
        from app import metrics
        workspace_id = create_workspace(user_id=95005, domain='report.example',
                                        brand_name='Report')
        seed_answers(workspace_id, [(True, 1, True)] * 2)
        rollup.rollup_workspace_day(workspace_id, rollup.utc_today())

        report = metrics.analytics_report(workspace_id, 95005)
        self.assertIn('site_health', report)
        self.assertIn('visibility', report)
        for row in report['engines']:
            self.assertIsNotNone(row['engine_id'],
                                 'engines must be real engines, not crawl sub-scores')
        names = {str(v) for v in report['engines']}
        for fake in ('Metadata', 'Crawlability', 'Structured data'):
            self.assertFalse(any(fake in n for n in names),
                             f'{fake} is a crawl score, not an engine')


if __name__ == '__main__':
    unittest.main()


class UtcDayBoundaryTests(unittest.TestCase):
    """The rollup buckets by UTC, never the local date.

    Runs are stamped with datetime.utcnow(). Bucketing them with date.today() means
    that between local midnight and UTC midnight the day's scans are looked up under
    a date no run carries, collect_counts returns nothing, and metrics_daily records
    zero answers - the dashboard silently blanks overnight and no test notices,
    because CI runs in UTC where the two dates agree.
    """

    def test_rollup_day_defaults_to_utc_not_local(self):
        from datetime import date as date_cls
        self.assertEqual(rollup.utc_today(),
                         datetime.now(timezone.utc).date())
        # If this ever becomes the local date the bug is back.
        self.assertIsInstance(rollup.utc_today(), date_cls)

    def test_answers_are_found_on_the_utc_day_they_were_stamped(self):
        workspace_id = create_workspace(user_id=95009, domain='utc.example',
                                        brand_name='Utc')
        stamped = datetime.utcnow()
        seed_answers(workspace_id, [(True, 1, True)] * 4, when=stamped)

        blended = rollup.rollup_workspace_day(workspace_id)
        self.assertEqual(blended['answer_count'], 4,
                         'the default day must match how runs are stamped')

        # And the local date is only safe when it happens to agree with UTC.
        on_utc_day = rollup.rollup_workspace_day(workspace_id, stamped.date())
        self.assertEqual(on_utc_day['answer_count'], 4)
