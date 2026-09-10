"""Confidence intervals and the honest-number envelope.

The product's stated differentiator: no bare numbers. Every rate carries its 95%
Wilson interval and its sample size, and a movement smaller than the interval is
reported as no measurable change rather than as an arrow.

Wilson, not the normal approximation. `p ± 1.96·sqrt(p(1-p)/n)` breaks at small n
and near 0 or 1 - it produces bounds outside [0,1] and collapses to zero width when
p is 0 or 1 - and small n near the extremes is exactly this product's regime. A
brand mentioned in 0 of 12 answers must not get an interval of zero width.
"""

import math

Z_95 = 1.96

# Below this many answers in the period the Visibility Score is not shown at all.
MIN_ANSWERS_FOR_SCORE = 20

# The three empty states. They are different facts and users act on them
# differently, so they are never collapsed into one "no data" message.
STATE_OK = 'ok'
STATE_NOT_YET_RUN = 'not_yet_run'      # no completed scan
STATE_ABSENT = 'absent'                # ran, brand never mentioned
STATE_INSUFFICIENT = 'insufficient'    # ran, too few answers to say


def wilson_interval(successes, trials, z=Z_95):
    """95% Wilson score interval for a proportion.

    Returns (None, None) when trials is 0: an unmeasured rate has no interval, and
    an interval of (0, 0) would read as a confident zero.
    """
    if not trials or trials <= 0:
        return None, None
    if successes < 0 or successes > trials:
        raise ValueError('successes must be between 0 and trials')

    proportion = successes / trials
    denominator = 1 + (z * z) / trials
    centre = (proportion + (z * z) / (2 * trials)) / denominator
    margin = (z / denominator) * math.sqrt(
        proportion * (1 - proportion) / trials + (z * z) / (4 * trials * trials)
    )
    # Clamp: Wilson stays inside [0,1] analytically, but floating point at the
    # extremes can step a hair outside.
    return max(0.0, centre - margin), min(1.0, centre + margin)


def metric(successes, trials, *, value=None, scale=1.0):
    """The envelope every rate is rendered as: {value, low, high, n}.

    scale=100 expresses the interval in the same units as a 0-100 score.
    `value` overrides the point estimate for a derived number (a Visibility Score
    is not itself a proportion, but it inherits its cohort's sample size).
    """
    low, high = wilson_interval(successes, trials)
    point = value
    if point is None:
        point = (successes / trials) if trials else None
    return {
        'value': point,
        'low': None if low is None else low * scale,
        'high': None if high is None else high * scale,
        'n': trials,
    }


def interval_width(envelope):
    if envelope.get('low') is None or envelope.get('high') is None:
        return None
    return envelope['high'] - envelope['low']


def describe_delta(current, previous):
    """Classify a period-over-period movement against the interval.

    A delta smaller than half the interval width is noise, and is reported as
    'no_measurable_change' with no direction, no colour and no percentage. This is
    the rule that stops the product claiming a 2-point improvement over 14 answers.
    """
    if current is None or previous is None:
        return {'delta': None, 'state': 'unknown'}

    current_value = current.get('value') if isinstance(current, dict) else current
    previous_value = previous.get('value') if isinstance(previous, dict) else previous
    if current_value is None or previous_value is None:
        return {'delta': None, 'state': 'unknown'}

    delta = current_value - previous_value
    width = interval_width(current) if isinstance(current, dict) else None
    if width is None:
        return {'delta': delta, 'state': 'unknown'}

    if abs(delta) < width / 2:
        return {'delta': delta, 'state': 'no_measurable_change'}
    return {'delta': delta, 'state': 'up' if delta > 0 else 'down'}


def visibility_state(*, answer_count, mentioned, has_completed_run):
    """Which of the three empty states, or ok.

    Order matters: a workspace that never ran is not the same as one that ran and
    was absent, which is not the same as one with too few answers to say.
    """
    if not has_completed_run or not answer_count:
        return STATE_NOT_YET_RUN
    if answer_count < MIN_ANSWERS_FOR_SCORE:
        return STATE_INSUFFICIENT
    if not mentioned:
        return STATE_ABSENT
    return STATE_OK


def score_envelope(row, *, has_completed_run=True):
    """Turn a metrics_daily row into the rendered Visibility Score payload.

    Below MIN_ANSWERS_FOR_SCORE the score is withheld entirely and the payload says
    how far off the threshold the workspace is, rather than showing a number that
    12 answers cannot support.
    """
    answer_count = (row or {}).get('answer_count') or 0
    mention_rate = (row or {}).get('mention_rate')
    mentioned = int(round((mention_rate or 0) * answer_count))

    state = visibility_state(
        answer_count=answer_count, mentioned=mentioned,
        has_completed_run=has_completed_run and bool(row))

    payload = {
        'state': state,
        'n': answer_count,
        'threshold': MIN_ANSWERS_FOR_SCORE,
        'visibility_score': None,
        'mention_rate': metric(mentioned, answer_count),
        'citation_rate': metric(
            int(round(((row or {}).get('citation_rate') or 0) * answer_count)),
            answer_count),
    }

    if state == STATE_OK:
        # The band on the Visibility Score is the Wilson interval of the *mention
        # rate*, expressed on the 0-100 scale. A Wilson interval is defined for a
        # proportion, and VS is a weighted combination of three of them, so it has
        # no exact Wilson interval of its own. MentionRate carries half the weight
        # and shares the same denominator, which makes it the honest stand-in for
        # "how much could this move if we measured again". Stated on the
        # methodology page rather than implied.
        payload['visibility_score'] = metric(
            mentioned, answer_count,
            value=(row or {}).get('visibility_score'), scale=100.0)
    return payload
