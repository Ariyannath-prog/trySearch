"""usage_ledger writes, per-provider cost estimation, and spend ceilings.

CLAUDE.md's third invariant: every provider call writes a usage_ledger row, success
or failure. Ceilings count usage rows rather than run rows, because a retry storm
writes no runs but burns money.

Prices are the PRD §6a per-answer estimates. They are *estimates*, recorded per call
so the ledger stays a complete audit trail even before real invoices land. Re-read
§6a whenever a provider reprices.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, insert, select

from app.db import engine
from app.models import organizations, usage_ledger

# PRD §6a, per answer. Money is Decimal, never float.
PROVIDER_COSTS = {
    ('engine_query', 'Perplexity'): Decimal('0.006'),
    ('agent', 'Perplexity'): Decimal('0.006'),
    ('engine_query', 'OpenAI'): Decimal('0.012'),
    ('engine_query', 'Anthropic'): Decimal('0.012'),
    # Gemini is free for the first 5,000 grounded prompts per GCP project per
    # month, then $14/1k. The paid rate is recorded; the free tier is not modelled
    # per-call because it is a project-level allowance, not a per-request price.
    ('engine_query', 'Gemini'): Decimal('0.015'),
    ('content', 'OpenModel'): Decimal('0.002'),
}

# Extraction is a word-boundary regex (CLAUDE.md), so it makes no provider call and
# costs nothing. The row is still written: it keeps the ledger a complete record of
# what was derived from which answer, and it becomes non-zero the day extraction
# uses a model. It is also what makes "re-running extraction costs $0" auditable
# rather than merely asserted.
ZERO = Decimal('0.000000')

DEFAULT_UNIT_COST = Decimal('0.010')

ALERT_FRACTION = Decimal('0.60')

CATEGORIES = ('engine_query', 'extraction', 'agent', 'content', 'crawl')


def default_ceiling():
    """Monthly USD ceiling for an org with no explicit plan limit."""
    raw = os.environ.get('DEFAULT_MONTHLY_COST_CEILING_USD', '50')
    try:
        return Decimal(raw)
    except (ArithmeticError, ValueError):
        return Decimal('50')


def estimate_cost(category, provider, units=1):
    """Estimated USD for one provider call, as a Decimal."""
    if category == 'extraction':
        return ZERO
    unit = PROVIDER_COSTS.get((category, provider), DEFAULT_UNIT_COST)
    return (unit * Decimal(units)).quantize(Decimal('0.000001'))


def record_usage(*, workspace_id, org_id, category, provider, units=1, cost_usd=None,
                 occurred_at=None):
    """Write one ledger row. Called for every provider call, including failures.

    Never raises past its own boundary: losing the scan because the meter failed
    would be worse than losing the meter reading.
    """
    if category not in CATEGORIES:
        raise ValueError(f'unknown usage category: {category}')
    now = occurred_at or datetime.utcnow()
    amount = cost_usd if cost_usd is not None else estimate_cost(category, provider, units)
    try:
        with engine.begin() as conn:
            conn.execute(insert(usage_ledger).values(
                workspace_id=workspace_id, org_id=org_id, date=now.date(),
                category=category, provider=provider, units=units,
                cost_usd=amount, created_at=now,
            ))
    except Exception:  # noqa: BLE001 - the meter must not break the scan
        return None
    return amount


def month_to_date_spend(org_id, *, today=None):
    """Total USD the org has spent since the first of the current month."""
    # UTC, to match the timestamps on the ledger rows being summed.
    today = today or datetime.now(timezone.utc).date()
    start = today.replace(day=1)
    with engine.connect() as conn:
        total = conn.execute(
            select(func.coalesce(func.sum(usage_ledger.c.cost_usd), 0))
            .where(
                (usage_ledger.c.org_id == org_id)
                & (usage_ledger.c.date >= start)
            )
        ).scalar_one()
    return Decimal(str(total))


def ceiling_for_org(org_id):
    with engine.connect() as conn:
        configured = conn.execute(
            select(organizations.c.monthly_cost_ceiling_usd)
            .where(organizations.c.id == org_id)
        ).scalar_one_or_none()
    if configured is None:
        return default_ceiling()
    return Decimal(str(configured))


def ceiling_status(org_id, *, today=None):
    """Return (state, spend, ceiling) where state is 'ok', 'alert' or 'exceeded'.

    Checked *before* dispatch. 'alert' at 60% of the ceiling, 'exceeded' at 100%.
    """
    ceiling = ceiling_for_org(org_id)
    spend = month_to_date_spend(org_id, today=today)
    if ceiling <= 0:
        return 'ok', spend, ceiling
    if spend >= ceiling:
        return 'exceeded', spend, ceiling
    if spend >= ceiling * ALERT_FRACTION:
        return 'alert', spend, ceiling
    return 'ok', spend, ceiling


def refusal_payload(spend, ceiling):
    return {
        'error': 'This organization has reached its monthly spend ceiling.',
        'spend_usd': str(spend),
        'ceiling_usd': str(ceiling),
    }
