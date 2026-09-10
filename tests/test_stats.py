"""T11: Wilson intervals, the delta rule, and the three empty states."""

import os
import unittest

os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'stats-test-secret'

from app import stats  # noqa: E402


class WilsonIntervalTests(unittest.TestCase):
    def test_wilson_matches_known_values(self):
        """40/100 at 95%.

        SPRINT states 0.307-0.501. The exact Wilson score interval is
        0.3094-0.4980; 0.307-0.501 is neither plain Wilson nor the
        continuity-corrected form (0.3048-0.5030). Asserting the exact value,
        since 'Wilson or nothing' is the rule and the arithmetic is not negotiable.
        """
        low, high = stats.wilson_interval(40, 100)
        self.assertAlmostEqual(low, 0.3094, places=4)
        self.assertAlmostEqual(high, 0.4980, places=4)
        # And it is in the neighbourhood SPRINT describes.
        self.assertTrue(0.30 < low < 0.31 and 0.49 < high < 0.51)

    def test_zero_trials_returns_none(self):
        self.assertEqual(stats.wilson_interval(0, 0), (None, None))

    def test_interval_stays_inside_zero_and_one(self):
        for successes, trials in ((0, 5), (5, 5), (0, 1), (1, 1), (0, 12)):
            low, high = stats.wilson_interval(successes, trials)
            self.assertGreaterEqual(low, 0.0)
            self.assertLessEqual(high, 1.0)

    def test_zero_successes_still_has_width(self):
        """The reason Wilson is mandatory: the normal approximation gives 0 width
        at p=0, which would claim certainty from 12 observations."""
        low, high = stats.wilson_interval(0, 12)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.2, 'zero of twelve is not a confident zero')

    def test_smaller_samples_give_wider_intervals(self):
        narrow = stats.wilson_interval(40, 100)
        wide = stats.wilson_interval(4, 10)
        self.assertGreater(wide[1] - wide[0], narrow[1] - narrow[0])


class MetricEnvelopeTests(unittest.TestCase):
    def test_every_metric_carries_value_low_high_and_n(self):
        envelope = stats.metric(40, 100)
        self.assertEqual(set(envelope), {'value', 'low', 'high', 'n'})
        self.assertEqual(envelope['n'], 100)
        self.assertAlmostEqual(envelope['value'], 0.40)

    def test_unmeasured_metric_has_null_bounds_not_zero(self):
        envelope = stats.metric(0, 0)
        self.assertIsNone(envelope['value'])
        self.assertIsNone(envelope['low'])
        self.assertIsNone(envelope['high'])
        self.assertEqual(envelope['n'], 0)


class DeltaRuleTests(unittest.TestCase):
    def test_delta_inside_interval_is_no_measurable_change(self):
        # 40/100: interval is about 0.309-0.498, half-width about 0.094.
        current = stats.metric(40, 100)
        previous = {'value': current['value'] - 0.01}
        result = stats.describe_delta(current, previous)
        self.assertEqual(result['state'], 'no_measurable_change')

    def test_delta_larger_than_interval_has_a_direction(self):
        current = stats.metric(40, 100)
        previous = {'value': current['value'] - 0.30}
        self.assertEqual(stats.describe_delta(current, previous)['state'], 'up')
        previous = {'value': current['value'] + 0.30}
        self.assertEqual(stats.describe_delta(current, previous)['state'], 'down')

    def test_delta_without_a_baseline_is_unknown_not_zero(self):
        self.assertEqual(
            stats.describe_delta(stats.metric(40, 100), None)['state'], 'unknown')


class EmptyStateTests(unittest.TestCase):
    def test_three_empty_states_are_distinguishable(self):
        not_yet_run = stats.visibility_state(
            answer_count=0, mentioned=0, has_completed_run=False)
        insufficient = stats.visibility_state(
            answer_count=12, mentioned=3, has_completed_run=True)
        absent = stats.visibility_state(
            answer_count=50, mentioned=0, has_completed_run=True)
        ok = stats.visibility_state(
            answer_count=50, mentioned=20, has_completed_run=True)

        self.assertEqual(not_yet_run, stats.STATE_NOT_YET_RUN)
        self.assertEqual(insufficient, stats.STATE_INSUFFICIENT)
        self.assertEqual(absent, stats.STATE_ABSENT)
        self.assertEqual(ok, stats.STATE_OK)
        self.assertEqual(len({not_yet_run, insufficient, absent, ok}), 4,
                         'the three empty states must never collapse into one')

    def test_ran_and_absent_is_not_the_same_as_never_ran(self):
        self.assertNotEqual(
            stats.visibility_state(answer_count=50, mentioned=0, has_completed_run=True),
            stats.visibility_state(answer_count=0, mentioned=0, has_completed_run=False),
        )


class ScoreSuppressionTests(unittest.TestCase):
    def test_score_hidden_below_twenty_answers(self):
        row = {'answer_count': 19, 'mention_rate': 0.5, 'citation_rate': 0.1,
               'visibility_score': 44.5}
        payload = stats.score_envelope(row)
        self.assertEqual(payload['state'], stats.STATE_INSUFFICIENT)
        self.assertIsNone(payload['visibility_score'],
                          'no score below the threshold, however tempting')
        self.assertEqual(payload['n'], 19)
        self.assertEqual(payload['threshold'], 20)

    def test_score_shown_at_the_threshold(self):
        row = {'answer_count': 20, 'mention_rate': 0.5, 'citation_rate': 0.1,
               'visibility_score': 44.5}
        payload = stats.score_envelope(row)
        self.assertEqual(payload['state'], stats.STATE_OK)
        self.assertEqual(payload['visibility_score']['value'], 44.5)
        self.assertEqual(payload['visibility_score']['n'], 20)
        self.assertIsNotNone(payload['visibility_score']['low'])

    def test_no_row_at_all_is_not_yet_run(self):
        payload = stats.score_envelope(None)
        self.assertEqual(payload['state'], stats.STATE_NOT_YET_RUN)
        self.assertIsNone(payload['visibility_score'])

    def test_every_rate_in_the_payload_carries_a_sample_size(self):
        row = {'answer_count': 40, 'mention_rate': 0.5, 'citation_rate': 0.25,
               'visibility_score': 50.0}
        payload = stats.score_envelope(row)
        for key in ('mention_rate', 'citation_rate', 'visibility_score'):
            self.assertIn('n', payload[key], f'{key} must carry its sample size')
            self.assertEqual(payload[key]['n'], 40)


if __name__ == '__main__':
    unittest.main()
