"""app/analytics_filters.py - the shared date-range/region/engine filter
parser every analytics endpoint (starting with scan_history(), see
tests/test_scan_history.py) reuses rather than re-implementing its own
validation and engine_id -> provider-name lookup.
"""

import os
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'analytics-filters-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert, true  # noqa: E402

from app import analytics_filters as af  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import analytics_prompt_scan_runs  # noqa: E402


class FakeArgs(dict):
    """A plain dict stands in for request.args in these unit tests - no
    getlist(), exercising the comma-separated fallback path deliberately."""


class DateRangeParsingTests(unittest.TestCase):
    def test_named_ranges_resolve_relative_to_today(self):
        today = datetime.utcnow().date()
        start, end = af.parse_date_range(FakeArgs({'range': '7d'}))
        self.assertEqual(end, today)
        self.assertEqual(start, today - timedelta(days=6))

    def test_today_range_is_a_single_day(self):
        today = datetime.utcnow().date()
        start, end = af.parse_date_range(FakeArgs({'range': 'today'}))
        self.assertEqual((start, end), (today, today))

    def test_unrecognised_range_raises(self):
        with self.assertRaises(af.FilterError):
            af.parse_date_range(FakeArgs({'range': 'last-quarter'}))

    def test_custom_range_reads_explicit_dates(self):
        start, end = af.parse_date_range(FakeArgs({
            'range': 'custom', 'start_date': '2026-01-01', 'end_date': '2026-01-31',
        }))
        self.assertEqual(start.isoformat(), '2026-01-01')
        self.assertEqual(end.isoformat(), '2026-01-31')

    def test_bare_dates_work_without_range_custom(self):
        start, end = af.parse_date_range(FakeArgs({'start_date': '2026-02-01', 'end_date': '2026-02-05'}))
        self.assertEqual(start.isoformat(), '2026-02-01')

    def test_malformed_date_raises(self):
        with self.assertRaises(af.FilterError):
            af.parse_date_range(FakeArgs({'start_date': 'not-a-date'}))

    def test_end_before_start_raises(self):
        with self.assertRaises(af.FilterError):
            af.parse_date_range(FakeArgs({'start_date': '2026-02-05', 'end_date': '2026-02-01'}))

    def test_no_range_and_no_dates_means_unfiltered(self):
        self.assertEqual(af.parse_date_range(FakeArgs({})), (None, None))


class EngineIdParsingTests(unittest.TestCase):
    def test_comma_separated_form(self):
        self.assertEqual(af.parse_engine_ids(FakeArgs({'engine_ids': '1,2,3'})), [1, 2, 3])

    def test_absent_is_none(self):
        self.assertIsNone(af.parse_engine_ids(FakeArgs({})))

    def test_non_integer_raises(self):
        with self.assertRaises(af.FilterError):
            af.parse_engine_ids(FakeArgs({'engine_ids': 'perplexity'}))


class RegionNormalisationTests(unittest.TestCase):
    def test_all_regions_normalises_to_none(self):
        self.assertIsNone(af.parse_filters(FakeArgs({'region': 'All Regions'}))['region'])
        self.assertIsNone(af.parse_filters(FakeArgs({'region': 'all'}))['region'])

    def test_a_real_region_is_kept(self):
        self.assertEqual(af.parse_filters(FakeArgs({'region': 'US'}))['region'], 'US')

    def test_empty_filters_dict_has_every_key(self):
        parsed = af.parse_filters(FakeArgs({}))
        self.assertEqual(set(parsed.keys()), {'start_date', 'end_date', 'region', 'engine_ids'})


class FilterClauseTests(unittest.TestCase):
    def test_no_filters_is_a_true_no_op(self):
        clause = af.scan_run_filter_clause({})
        # A no-op clause must still be safely AND-able with a real condition.
        combined = (analytics_prompt_scan_runs.c.workspace_id == 1) & clause
        self.assertIsNotNone(combined)

    def test_region_and_provider_conditions_compose(self):
        clause = af.scan_run_filter_clause({'region': 'US'}, providers=['Perplexity'])
        compiled = str(clause.compile(compile_kwargs={'literal_binds': True}))
        self.assertIn('region', compiled)
        self.assertIn('provider', compiled)


class ResolutionAgainstTheDatabaseTests(unittest.TestCase):
    def test_available_regions_is_distinct_and_workspace_scoped(self):
        workspace_id = create_workspace(user_id=98900, domain='filterregions.example',
                                        brand_name='FilterRegions')
        other_workspace_id = create_workspace(user_id=98901, domain='otherfilter.example',
                                              brand_name='OtherFilter')
        now = datetime.utcnow()
        with engine.begin() as conn:
            for region in ('US', 'US', 'GB', None):
                conn.execute(insert(analytics_prompt_scan_runs).values(
                    workspace_id=workspace_id, job_id=None, provider='Perplexity', model='m',
                    region=region, competitor_snapshot='[]', status='succeeded', run_type='scheduled',
                    prompt_count=1, completed_count=1, mention_rate=None, citation_rate=None,
                    source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                    error=None, created_at=now, completed_at=now,
                ))
            conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=other_workspace_id, job_id=None, provider='Perplexity', model='m',
                region='DE', competitor_snapshot='[]', status='succeeded', run_type='scheduled',
                prompt_count=1, completed_count=1, mention_rate=None, citation_rate=None,
                source_presence_rate=None, share_of_voice=None, recommendation_summary=None,
                error=None, created_at=now, completed_at=now,
            ))
        regions = af.available_regions(workspace_id)
        self.assertEqual(regions, ['GB', 'US'])  # distinct, sorted, no None, no DE from the other workspace

    def test_engine_providers_for_ids_resolves_against_the_engines_table(self):
        with engine.connect() as conn:
            from app.models import engines as engines_table
            from sqlalchemy import select
            perplexity_id = conn.execute(select(engines_table.c.id).where(
                engines_table.c.key == 'perplexity')).scalar_one()
        providers = af.engine_providers_for_ids([perplexity_id])
        self.assertEqual(providers, ['Perplexity'])

    def test_no_engine_ids_resolves_to_none(self):
        self.assertIsNone(af.engine_providers_for_ids(None))
        self.assertIsNone(af.engine_providers_for_ids([]))


if __name__ == '__main__':
    unittest.main()
