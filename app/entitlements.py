"""Plan -> entitlements -> application limits. The single authority.

Nothing in the application may branch on a plan name. There is no `if plan ==
'growth'` anywhere, and adding one is the bug this module exists to prevent: a plan
is data, so changing what a plan allows must be an admin action, never a deploy.

Three rules:

* **The registry below is the whole vocabulary.** An admin can only store a key
  declared here. A typo'd key would be a limit nobody enforces - a silent
  commercial bug, which is worse than a loud one - so unknown keys are rejected at
  the API boundary rather than saved and ignored.
* **Resolution order is organization override -> plan entitlement -> registry
  default.** Every organization in production carries plan_id = NULL today, so the
  default path is the *only* path on the day this ships. It therefore has to be
  correct and backward compatible, not aspirational.
* **The backend decides.** Callers ask this module; the browser is told the answer
  so it can render honestly, and is never trusted to enforce it.
"""

from collections import namedtuple

from sqlalchemy import select

from app.config import ANALYTICS_MAX_TRACKED_PROMPTS
from app.db import engine
from app.models import organizations, plan_entitlements, plans

INT = 'int'
BOOL = 'bool'
STRING = 'string'
LIST = 'list'

VALUE_TYPES = (INT, BOOL, STRING, LIST)

# Unlimited is represented by this sentinel rather than by None, so "no limit" and
# "not configured" can never be confused by a caller doing an arithmetic comparison.
UNLIMITED = -1

Entitlement = namedtuple('Entitlement', 'key value_type default customer_visible description')


def _entitlement(key, value_type, default, *, customer_visible=True, description=''):
    return Entitlement(key, value_type, default, customer_visible, description)


# The declared vocabulary. Defaults apply to an organization with no plan assigned.
#
# They are deliberately the most restrictive *workable* value, with one exception:
# prompt_limit. Every existing organization has plan_id = NULL, and tracked prompts
# have been capped at ANALYTICS_MAX_TRACKED_PROMPTS (100) by app/config.py since
# before plans existed. Introducing the entitlement layer must not quietly cut an
# existing workspace from 100 to a smaller number, so the no-plan default *is* that
# existing constant - one source of truth, and no behaviour change until a plan is
# actually assigned.
REGISTRY = {
    entitlement.key: entitlement
    for entitlement in (
        _entitlement(
            'workspace_limit', INT, 1,
            description='Workspaces (projects) the organization may have active.'),
        _entitlement(
            'client_limit', INT, 0,
            description='Client viewers an agency may invite.'),
        _entitlement(
            'team_member_limit', INT, 1,
            description='Members of the organization, excluding client viewers.'),
        _entitlement(
            'prompt_limit', INT, ANALYTICS_MAX_TRACKED_PROMPTS,
            description='Active tracked prompts per workspace.'),
        _entitlement(
            'scan_frequency', STRING, 'monthly',
            description='Most frequent scheduled scan allowed.'),
        _entitlement(
            'engine_access', LIST, ['perplexity'],
            description='Engine keys the organization may select.'),
        _entitlement(
            'engine_limit', INT, 1,
            description='How many engines may be selected per workspace.'),
        _entitlement('api_access', BOOL, False, description='Public API access.'),
        _entitlement('reports', BOOL, False, description='Shareable client reports.'),
        _entitlement('alerts', BOOL, False, description='Visibility change alerts.'),
        _entitlement('integrations', BOOL, False,
                     description='Third-party integrations such as Search Console.'),
        _entitlement('white_label', BOOL, False,
                     description='Remove trySearch branding from reports.'),
    )
}

# Scheduled-scan cadence, least to most frequent. The vocabulary is fixed by
# scanning.next_schedule_time(), which only understands these three; inventing a
# fourth here would be an entitlement the scheduler cannot honour.
SCAN_FREQUENCY_ORDER = ('monthly', 'weekly', 'daily')


class EntitlementError(ValueError):
    """An entitlement key or value that must never reach the database."""


def registry_payload():
    """The declared vocabulary, for the admin plan editor to render."""
    return [
        {
            'key': item.key,
            'value_type': item.value_type,
            'default': item.default,
            'customer_visible': item.customer_visible,
            'description': item.description,
        }
        for item in sorted(REGISTRY.values(), key=lambda item: item.key)
    ]


def coerce_value(key, value):
    """Validate one entitlement against the registry, returning a storable value.

    Raises EntitlementError rather than coercing silently: an admin who typed a
    word into a numeric limit needs to be told, not to have it become zero.
    """
    item = REGISTRY.get(key)
    if item is None:
        raise EntitlementError(f'Unknown entitlement key: {key}')

    if item.value_type == BOOL:
        if not isinstance(value, bool):
            raise EntitlementError(f'{key} must be true or false.')
        return value

    if item.value_type == INT:
        # bool is an int subclass in Python; accepting True as 1 here would let a
        # checkbox silently become a limit.
        if isinstance(value, bool) or not isinstance(value, int):
            raise EntitlementError(f'{key} must be a whole number ({UNLIMITED} for unlimited).')
        if value < UNLIMITED:
            raise EntitlementError(f'{key} cannot be below {UNLIMITED}.')
        return value

    if item.value_type == STRING:
        if not isinstance(value, str) or not value.strip():
            raise EntitlementError(f'{key} must be a non-empty string.')
        cleaned = value.strip()
        if key == 'scan_frequency' and cleaned not in SCAN_FREQUENCY_ORDER:
            raise EntitlementError(
                f'scan_frequency must be one of {", ".join(SCAN_FREQUENCY_ORDER)}.')
        return cleaned

    # LIST
    if not isinstance(value, list) or not all(isinstance(item_, str) for item_ in value):
        raise EntitlementError(f'{key} must be a list of strings.')
    # Order-preserving de-duplication, so an admin saving the same engine twice
    # does not inflate a count derived from this list.
    return list(dict.fromkeys(entry.strip() for entry in value if entry.strip()))


def defaults():
    """The resolved entitlement set for an organization with no plan."""
    return {item.key: item.default for item in REGISTRY.values()}


def entitlements_for_plan(plan_id, *, conn=None):
    """Resolved entitlements for one plan: stored rows layered over the defaults."""
    resolved = defaults()
    if plan_id is None:
        return resolved

    def _read(connection):
        return connection.execute(
            select(plan_entitlements.c.key, plan_entitlements.c.value)
            .where(plan_entitlements.c.plan_id == plan_id)
        ).mappings().all()

    rows = _read(conn) if conn is not None else _read_with_new_connection(_read)

    for row in rows:
        # A key that is no longer declared is ignored rather than surfaced: the
        # registry, not the table, decides what the application enforces.
        if row['key'] in REGISTRY:
            resolved[row['key']] = row['value']
    return resolved


def _read_with_new_connection(reader):
    with engine.connect() as connection:
        return reader(connection)


# Plan state that means "this organization's plan limits are in force". A canceled
# or past_due organization falls back to the no-plan defaults rather than keeping
# entitlements it is no longer paying for. 'none' covers every organization that
# predates plans.
ACTIVE_PLAN_STATUSES = frozenset({'trialing', 'active'})


def organization_plan(org_id, *, conn=None):
    """Return (plan_row_or_None, org_row). Both are plain dicts or None."""
    def _read(connection):
        org = connection.execute(
            select(
                organizations.c.id,
                organizations.c.name,
                organizations.c.account_type,
                organizations.c.plan_id,
                organizations.c.plan_status,
                organizations.c.trial_ends_at,
                organizations.c.plan_started_at,
                organizations.c.monthly_cost_ceiling_usd,
            ).where(organizations.c.id == org_id)
        ).mappings().first()
        if not org:
            return None, None
        org = dict(org)
        if org['plan_id'] is None:
            return None, org
        plan = connection.execute(
            select(plans).where(plans.c.id == org['plan_id'])
        ).mappings().first()
        return (dict(plan) if plan else None), org

    if conn is not None:
        return _read(conn)
    with engine.connect() as connection:
        return _read(connection)


def entitlements_for_org(org_id, *, conn=None):
    """The authoritative entitlement set for an organization.

    Resolution: organization override -> plan entitlement -> registry default.
    An organization with no plan, or whose plan is not currently in force, gets the
    defaults - which for prompt_limit is the pre-existing 100, so nothing an
    existing customer could already do stops working.
    """
    plan, org = organization_plan(org_id, conn=conn)
    if org is None:
        return defaults()

    if plan is None or org['plan_status'] not in ACTIVE_PLAN_STATUSES:
        resolved = defaults()
    else:
        resolved = entitlements_for_plan(plan['id'], conn=conn)

    return resolved


def effective_limit(org_id, key, *, conn=None):
    """One entitlement value, resolved. Raises on an undeclared key."""
    if key not in REGISTRY:
        raise EntitlementError(f'Unknown entitlement key: {key}')
    return entitlements_for_org(org_id, conn=conn)[key]


def allows(org_id, key, *, conn=None):
    """True when a boolean capability is granted."""
    item = REGISTRY.get(key)
    if item is None or item.value_type != BOOL:
        raise EntitlementError(f'{key} is not a boolean entitlement.')
    return bool(effective_limit(org_id, key, conn=conn))


def is_unlimited(value):
    return value == UNLIMITED


def within_limit(org_id, key, current_count, *, conn=None, adding=1):
    """Would `current_count + adding` stay inside the limit?

    Returns (allowed, limit). The limit is returned so a caller can put the real
    number in the error message instead of a vague refusal.
    """
    limit = effective_limit(org_id, key, conn=conn)
    if is_unlimited(limit):
        return True, limit
    return (current_count + adding) <= limit, limit


def limit_payload(key, limit, current_count):
    """A consistent, actionable 402 body for a limit refusal."""
    return {
        'error': 'This plan does not allow that.',
        'entitlement': key,
        'limit': limit,
        'current': current_count,
        'upgrade_required': True,
    }


def allows_scan_frequency(org_id, frequency, *, conn=None):
    """Is `frequency` at or below the organization's permitted cadence?"""
    if frequency not in SCAN_FREQUENCY_ORDER:
        return False, effective_limit(org_id, 'scan_frequency', conn=conn)
    permitted = effective_limit(org_id, 'scan_frequency', conn=conn)
    if permitted not in SCAN_FREQUENCY_ORDER:
        # A stored value outside the vocabulary is treated as the most restrictive
        # option rather than being honoured as something the scheduler cannot run.
        permitted = SCAN_FREQUENCY_ORDER[0]
    return (SCAN_FREQUENCY_ORDER.index(frequency)
            <= SCAN_FREQUENCY_ORDER.index(permitted)), permitted


def customer_payload(entitlements):
    """Strip entitlements a customer has no business seeing.

    Everything in the registry is currently customer-visible; the plan's
    monthly_cost_ceiling_usd is intentionally *not* an entitlement and therefore
    never reaches this payload. The filter exists so adding an internal key later
    cannot leak by default.
    """
    return {
        key: value for key, value in entitlements.items()
        if key in REGISTRY and REGISTRY[key].customer_visible
    }
