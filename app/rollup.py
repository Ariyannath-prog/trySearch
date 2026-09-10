"""metrics_daily: the only read path for dashboards.

CLAUDE.md invariant 2 - dashboards read this table, never analytics_provider_answers.
Everything here is computed in SQL/Python from stored evidence (extractions,
mentions, analytics_answer_sources). No model is asked for a number.

PRD §13 is implemented literally. The weights below are the specification; changing
one requires recomputing history, which is possible only because answers are
immutable.
"""

from datetime import datetime, timezone

from sqlalchemy import func, insert, select, update

from app.db import engine
from app.models import (
    engines as engines_table,
    analytics_answer_sources,
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    extractions,
    mentions as mentions_table,
    metrics_daily,
)

# PRD §13. Shown on the in-app methodology page. Changing any of these requires
# recomputing history - do not tune them to make a demo look better.
WEIGHT_MENTION_RATE = 0.5
WEIGHT_POSITION_SCORE = 0.3
WEIGHT_CITATION_RATE = 0.2

# PRD §13: on-demand runs are excluded, because they happen while someone is
# actively optimising and would bias the series upward.
SCHEDULED_RUN_TYPE = 'scheduled'


def utc_today():
    """Today in UTC, never the local date.

    Runs are stamped with datetime.utcnow(), so rolling up by date.today() puts a
    scan on the wrong day - or loses it entirely - for anyone whose local date has
    already rolled over. CLAUDE.md: timestamps are always UTC.
    """
    return datetime.now(timezone.utc).date()


def visibility_score(mention_rate, position_score, citation_rate):
    """VS = 100 × (0.5·MentionRate + 0.3·PositionScore + 0.2·CitationRate).

    Returns None when the period has no measured answers. A workspace that was
    never measured did not score zero (CLAUDE.md).
    """
    if mention_rate is None:
        return None
    return 100.0 * (
        WEIGHT_MENTION_RATE * mention_rate
        + WEIGHT_POSITION_SCORE * (position_score or 0.0)
        + WEIGHT_CITATION_RATE * citation_rate
    )


def score_from_counts(*, total_answers, mentioned, reciprocal_rank_sum, cited,
                      brand_mentions=None, competitor_mentions=None):
    """Turn raw counts into the PRD §13 metrics. No rounding happens here.

    reciprocal_rank_sum is the sum of 1/rank over *mentioned* answers only.
    """
    if not total_answers:
        # Empty denominator is NULL, never 0, everywhere - including the blend.
        return {
            'answer_count': 0, 'mention_rate': None, 'position_score': None,
            'citation_rate': None, 'visibility_score': None, 'sov': None,
            'sentiment_index': None,
        }

    mention_rate = mentioned / total_answers
    # Mean reciprocal rank over mentioned answers only. Never mentioned → 0, which
    # is a real measurement, unlike the None above.
    position_score = (reciprocal_rank_sum / mentioned) if mentioned else 0.0
    citation_rate = cited / total_answers

    sov = None
    if brand_mentions is not None and competitor_mentions is not None:
        denominator = brand_mentions + competitor_mentions
        sov = (brand_mentions / denominator) if denominator else None

    return {
        'answer_count': total_answers,
        'mention_rate': mention_rate,
        'position_score': position_score,
        'citation_rate': citation_rate,
        'visibility_score': visibility_score(mention_rate, position_score, citation_rate),
        'sov': sov,
        # Sentiment is deliberately outside VS in v1 (PRD §13) and not yet extracted.
        'sentiment_index': None,
    }


def blend(per_engine):
    """Blend per-engine metrics as a simple average of per-engine VS.

    Not weighted by answer count, deliberately: a low-volume engine must not be
    drowned out by a high-volume one, because the question is "how visible are we
    on each engine", not "how visible are we per answer".
    """
    scored = [m for m in per_engine if m.get('visibility_score') is not None]
    if not scored:
        return {
            'answer_count': sum(m.get('answer_count') or 0 for m in per_engine),
            'mention_rate': None, 'position_score': None, 'citation_rate': None,
            'visibility_score': None, 'sov': None, 'sentiment_index': None,
        }

    def mean(key):
        values = [m[key] for m in scored if m.get(key) is not None]
        return (sum(values) / len(values)) if values else None

    return {
        'answer_count': sum(m.get('answer_count') or 0 for m in per_engine),
        'mention_rate': mean('mention_rate'),
        'position_score': mean('position_score'),
        'citation_rate': mean('citation_rate'),
        'visibility_score': mean('visibility_score'),
        'sov': mean('sov'),
        'sentiment_index': mean('sentiment_index'),
    }


def engine_id_for_provider(provider, conn):
    """Resolve a provider label to an engines.id.

    T12 made this a real lookup, so per-engine metrics_daily rows now appear with
    no schema change - which was the point of keeping it a function.
    """
    return conn.execute(
        select(engines_table.c.id).where(engines_table.c.display_name == provider)
    ).scalar_one_or_none()


def collect_counts(workspace_id, day, conn):
    """Counts per engine for one workspace-day, read from evidence only.

    Never touches analytics_provider_answers for the *values* - only to reach the
    answers of the day through their run. The measured facts all come from
    extractions, mentions and analytics_answer_sources.
    """
    rows = conn.execute(
        select(
            analytics_provider_answers.c.provider,
            extractions.c.brand_mentioned,
            extractions.c.brand_rank,
            extractions.c.brand_cited,
        )
        .select_from(analytics_provider_answers)
        .join(analytics_prompt_scan_runs,
              analytics_prompt_scan_runs.c.id
              == analytics_provider_answers.c.scan_run_id)
        .join(extractions,
              (extractions.c.answer_id == analytics_provider_answers.c.id)
              & extractions.c.is_current)
        .where(
            (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
            & (analytics_prompt_scan_runs.c.run_type == SCHEDULED_RUN_TYPE)
            & (func.date(analytics_prompt_scan_runs.c.created_at) == day)
        )
    ).mappings().all()

    by_provider = {}
    for row in rows:
        bucket = by_provider.setdefault(row['provider'], {
            'total_answers': 0, 'mentioned': 0, 'reciprocal_rank_sum': 0.0, 'cited': 0,
        })
        bucket['total_answers'] += 1
        if row['brand_mentioned']:
            bucket['mentioned'] += 1
            if row['brand_rank']:
                bucket['reciprocal_rank_sum'] += 1.0 / row['brand_rank']
        if row['brand_cited']:
            bucket['cited'] += 1
    return by_provider


def upsert_row(workspace_id, day, engine_id, values, conn):
    """Write one metrics_daily row. Idempotent - recompute overwrites, never adds."""
    now = datetime.utcnow()
    where = (
        (metrics_daily.c.workspace_id == workspace_id)
        & (metrics_daily.c.date == day)
        & (metrics_daily.c.engine_id.is_(None) if engine_id is None
           else metrics_daily.c.engine_id == engine_id)
    )
    updated = conn.execute(
        update(metrics_daily).where(where).values(**values, updated_at=now)
    ).rowcount
    if not updated:
        conn.execute(insert(metrics_daily).values(
            workspace_id=workspace_id, date=day, engine_id=engine_id,
            **values, created_at=now, updated_at=now,
        ))


def rollup_workspace_day(workspace_id, day=None):
    """Recompute metrics_daily for one workspace and date. Idempotent.

    Defaults to the UTC date, never the local one: a caller passing
    date.today() from a machine already past local midnight would roll up an
    empty day and blank the score.
    """
    day = day or utc_today()
    with engine.begin() as conn:
        by_provider = collect_counts(workspace_id, day, conn)

        per_engine = []
        for provider, counts in sorted(by_provider.items()):
            metrics = score_from_counts(**counts)
            per_engine.append(metrics)
            resolved = engine_id_for_provider(provider, conn)
            if resolved is not None:
                upsert_row(workspace_id, day, resolved, metrics, conn)

        blended = blend(per_engine) if per_engine else score_from_counts(
            total_answers=0, mentioned=0, reciprocal_rank_sum=0.0, cited=0)
        upsert_row(workspace_id, day, None, blended, conn)
        return blended


def latest_metrics(workspace_id, *, engine_id=None, limit=90):
    """Read path for dashboards. Reads metrics_daily and nothing else."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(metrics_daily)
            .where(
                (metrics_daily.c.workspace_id == workspace_id)
                & (metrics_daily.c.engine_id.is_(None) if engine_id is None
                   else metrics_daily.c.engine_id == engine_id)
            )
            .order_by(metrics_daily.c.date.desc())
            .limit(limit)
        ).mappings().all()
    return [dict(row) for row in rows]


def latest_metrics_all_engines(workspace_id, *, limit=90):
    """Every metrics_daily row for the workspace, blended and per-engine."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(metrics_daily)
            .where(metrics_daily.c.workspace_id == workspace_id)
            .order_by(metrics_daily.c.date.desc())
            .limit(limit)
        ).mappings().all()
    return [dict(row) for row in rows]
