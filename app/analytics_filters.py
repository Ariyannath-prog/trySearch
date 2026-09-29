"""Shared date-range/region/engine filter parsing for analytics endpoints.

Every analytics read function already joins through analytics_prompt_scan_runs
to reach workspace_id; this module turns validated request args into one more
condition ANDed onto that same join, so no function re-implements filter
parsing, validation, or the engine_id -> provider-name lookup independently.

Regions and engines are never hard-coded here: available_regions() reads the
distinct values actually stored on this workspace's own scan runs, and engine
filtering resolves engine_ids against the existing `engines` table/registry.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import select, true

from app.db import engine
from app.models import (
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    engines as engines_table,
)


class FilterError(ValueError):
    """Raised for a malformed filter value; routes turn this into a 400."""


NAMED_RANGES = ('today', '7d', '30d', '90d')


def parse_date_range(args):
    """range=today|7d|30d|90d|custom, or bare start_date/end_date.

    No range and no dates at all means "no date filter" - (None, None).
    """
    range_key = (args.get('range') or '').strip().lower()
    today = datetime.utcnow().date()
    named = {
        'today': (today, today),
        '7d': (today - timedelta(days=6), today),
        '30d': (today - timedelta(days=29), today),
        '90d': (today - timedelta(days=89), today),
    }
    if range_key and range_key != 'custom':
        if range_key not in named:
            raise FilterError(f"Unrecognised range '{range_key}'.")
        return named[range_key]

    start_raw, end_raw = args.get('start_date'), args.get('end_date')
    if not start_raw and not end_raw:
        return None, None
    try:
        start_date = date.fromisoformat(start_raw) if start_raw else None
        end_date = date.fromisoformat(end_raw) if end_raw else None
    except ValueError:
        raise FilterError('start_date/end_date must be YYYY-MM-DD.')
    if start_date and end_date and end_date < start_date:
        raise FilterError('end_date must not be before start_date.')
    return start_date, end_date


def parse_engine_ids(args):
    """engine_ids, repeated (?engine_ids=1&engine_ids=2), comma-separated
    (?engine_ids=1,2), or both at once - from either a MultiDict or a plain
    dict-like in tests. None means "no engine filter".

    The two forms are not alternatives to choose between. On a real
    MultiDict, getlist() returns ['1,2'] for the comma form: one truthy
    element, so an `if not raw:` fallback never fires and int('1,2') blows
    up. app/static/js/components/filters.js only ever emits the comma form,
    which made every filtered dashboard request a 400. So: take whatever
    getlist() gives, then split each value on commas regardless.
    """
    if hasattr(args, 'getlist'):
        raw = args.getlist('engine_ids')
    else:
        single = args.get('engine_ids')
        raw = [single] if single else []

    parts = [
        part.strip()
        for value in raw if value is not None
        for part in str(value).split(',')
    ]
    # Both forms combined can repeat an id; keep first-seen order.
    unique = list(dict.fromkeys(part for part in parts if part))
    if not unique:
        return None
    try:
        return [int(part) for part in unique]
    except (TypeError, ValueError):
        raise FilterError('engine_ids must be integers.')


def parse_filters(args):
    """The one thing every analytics route calls to turn request.args into
    a validated filter dict. Raises FilterError on anything malformed -
    routes catch that and return 400."""
    start_date, end_date = parse_date_range(args)
    region = (args.get('region') or '').strip() or None
    if region and region.lower() in ('all', 'all regions'):
        region = None
    return {
        'start_date': start_date, 'end_date': end_date,
        'region': region, 'engine_ids': parse_engine_ids(args),
    }


def engine_providers_for_ids(engine_ids, conn=None):
    """engine_ids (engines.id values) -> their display_name strings - the
    value analytics_prompt_scan_runs.provider actually stores. Resolved
    once per request, not once per row."""
    if not engine_ids:
        return None
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        return list(conn.execute(
            select(engines_table.c.display_name).where(engines_table.c.id.in_(engine_ids))
        ).scalars())
    finally:
        if own_conn:
            conn.close()


def scan_run_filter_clause(filters, *, providers=None):
    """One AND-able boolean expression over analytics_prompt_scan_runs.

    `providers` is the already-resolved provider-name list (see
    engine_providers_for_ids) - callers resolve engine_ids to names once,
    not inside a loop. Returns sqlalchemy.true() (a real no-op boolean
    expression, safe to AND against anything) when no filter applies.
    """
    from sqlalchemy import func

    conditions = []
    if filters.get('start_date'):
        conditions.append(func.date(analytics_prompt_scan_runs.c.created_at) >= filters['start_date'])
    if filters.get('end_date'):
        conditions.append(func.date(analytics_prompt_scan_runs.c.created_at) <= filters['end_date'])
    if filters.get('region'):
        conditions.append(analytics_prompt_scan_runs.c.region == filters['region'])
    if providers:
        # analytics_prompt_scan_runs.provider is a single legacy label from the
        # one-engine era; a multi-engine run stores only the first engine there
        # while its answers carry the real per-engine provider. Matching the run
        # label alone hides a run that genuinely answered on the selected engine,
        # so a run also qualifies when any of its answers came from one.
        conditions.append(
            analytics_prompt_scan_runs.c.provider.in_(providers)
            | select(analytics_provider_answers.c.id).where(
                (analytics_provider_answers.c.scan_run_id == analytics_prompt_scan_runs.c.id)
                & (analytics_provider_answers.c.provider.in_(providers))
            ).exists()
        )
    if not conditions:
        return true()
    result = conditions[0]
    for condition in conditions[1:]:
        result = result & condition
    return result


def available_regions(workspace_id, conn=None):
    """Distinct regions actually used in this workspace's own scan history -
    never a hard-coded list, per the product requirement."""
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        rows = conn.execute(
            select(analytics_prompt_scan_runs.c.region)
            .where(
                (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
                & analytics_prompt_scan_runs.c.region.isnot(None)
            )
            .distinct()
        ).scalars().all()
    finally:
        if own_conn:
            conn.close()
    return sorted(rows)
