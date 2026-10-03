"""Platform-admin plan and entitlement management, plus the customer plan list.

Deliberately a separate module from admin_api.py, which is already 1700+ lines; the
blueprint shares the /api/admin prefix so the URL surface stays one namespace.

Three rules this module enforces and does not delegate:

* **Plans are never deleted.** Archive or deactivate. A plan referenced by an
  organization cannot even be archived, and `ON DELETE RESTRICT` on
  organizations.plan_id means the database would refuse a delete regardless.
* **Entitlement keys come from app/entitlements.py.** An undeclared key is a 400,
  not a stored row nobody reads.
* **Every mutation is audited through the existing write_admin_audit()**, in the
  same transaction as the change, with a before/after diff.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from sqlalchemy import delete, func, insert, select, update

from app import entitlements as ent
from app.admin_audit import write_admin_audit
from app.admin_auth import get_platform_admin, require_platform_admin_api
from app.db import engine
from app.models import organizations, plan_entitlements, plans
from app.tenancy import current_user_id
from app.utils import row_to_dict

admin_plans_bp = Blueprint('admin_plans', __name__, url_prefix='/api/admin')
plans_bp = Blueprint('plans', __name__)

ACCOUNT_TYPES = ('brand', 'agency')
BILLING_INTERVALS = ('monthly', 'annual')

# Fields an admin may write, and how each is validated.
TEXT_FIELDS = ('name', 'description')
MONEY_FIELDS = ('price_monthly', 'price_annual', 'monthly_cost_ceiling_usd')


def _plan_payload(row, entitlement_rows=None):
    """Full admin view of a plan."""
    payload = row_to_dict(row)
    for field in MONEY_FIELDS:
        if payload.get(field) is not None:
            payload[field] = str(payload[field])
    payload['account_types'] = list(payload.get('account_types') or [])
    if entitlement_rows is not None:
        resolved = ent.defaults()
        stored = {}
        for entitlement in entitlement_rows:
            if entitlement['key'] in ent.REGISTRY:
                resolved[entitlement['key']] = entitlement['value']
                stored[entitlement['key']] = entitlement['value']
        # `entitlements` is what the plan actually grants once defaults are layered
        # in; `entitlements_stored` is only what an admin explicitly set, so the
        # editor can show which values are inherited rather than chosen.
        payload['entitlements'] = resolved
        payload['entitlements_stored'] = stored
    return payload


def _slugify(value):
    cleaned = []
    for char in (value or '').strip().lower():
        if char.isalnum():
            cleaned.append(char)
        elif char in ' -_' and cleaned and cleaned[-1] != '-':
            cleaned.append('-')
    return ''.join(cleaned).strip('-')


def _parse_money(value, field):
    """Money as Decimal, never float. None clears the value."""
    if value is None or value == '':
        return None, None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None, f'{field} must be a decimal amount.'
    if amount < 0:
        return None, f'{field} cannot be negative.'
    if amount.as_tuple().exponent < -2:
        return None, f'{field} cannot have more than two decimal places.'
    return amount, None


def _validate_plan_fields(data, *, partial):
    """Return (values, error). Shared by create and update."""
    values = {}

    if 'name' in data or not partial:
        name = str(data.get('name') or '').strip()
        if not name:
            return None, 'name is required.'
        if len(name) > 160:
            return None, 'name must be 160 characters or fewer.'
        values['name'] = name

    if 'description' in data:
        description = data.get('description')
        if description is None:
            description = ''
        if not isinstance(description, str):
            return None, 'description must be a string.'
        values['description'] = description.strip()

    if 'slug' in data:
        slug = _slugify(data.get('slug'))
        if not slug:
            return None, 'slug must contain at least one alphanumeric character.'
        values['slug'] = slug

    if 'active' in data:
        if not isinstance(data['active'], bool):
            return None, 'active must be boolean.'
        values['active'] = data['active']

    if 'display_order' in data:
        order = data['display_order']
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            return None, 'display_order must be a non-negative whole number.'
        values['display_order'] = order

    if 'currency' in data:
        currency = str(data.get('currency') or '').strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            return None, 'currency must be a three-letter code such as USD.'
        values['currency'] = currency

    if 'billing_interval' in data:
        interval = str(data.get('billing_interval') or '').strip().lower()
        if interval not in BILLING_INTERVALS:
            return None, f'billing_interval must be one of {", ".join(BILLING_INTERVALS)}.'
        values['billing_interval'] = interval

    for field in MONEY_FIELDS:
        if field in data:
            amount, error = _parse_money(data[field], field)
            if error:
                return None, error
            values[field] = amount

    if 'trial_days' in data:
        trial = data['trial_days']
        if isinstance(trial, bool) or not isinstance(trial, int) or trial < 0:
            return None, 'trial_days must be a non-negative whole number (0 for no trial).'
        values['trial_days'] = trial

    if 'account_types' in data or not partial:
        raw = data.get('account_types')
        if raw is None:
            raw = []
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            return None, 'account_types must be a list of strings.'
        selected = list(dict.fromkeys(item.strip().lower() for item in raw if item.strip()))
        unknown = [item for item in selected if item not in ACCOUNT_TYPES]
        if unknown:
            return None, f'Unsupported account types: {", ".join(sorted(unknown))}.'
        values['account_types'] = selected

    return values, None


def _validate_entitlements(raw):
    """Validate a complete entitlement map against the registry."""
    if not isinstance(raw, dict):
        return None, 'entitlements must be an object.'
    unknown = sorted(set(raw) - set(ent.REGISTRY))
    if unknown:
        return None, f'Unknown entitlement keys: {", ".join(unknown)}.'
    cleaned = {}
    for key, value in raw.items():
        try:
            cleaned[key] = ent.coerce_value(key, value)
        except ent.EntitlementError as error:
            return None, str(error)
    return cleaned, None


def _write_entitlements(conn, plan_id, cleaned, now):
    """Replace the plan's stored entitlements with `cleaned`, as a full snapshot.

    A snapshot rather than a merge: the editor always submits the complete set, so
    a key the admin removed must actually disappear instead of lingering as an
    invisible override.
    """
    conn.execute(delete(plan_entitlements).where(plan_entitlements.c.plan_id == plan_id))
    for key, value in sorted(cleaned.items()):
        conn.execute(insert(plan_entitlements).values(
            plan_id=plan_id, key=key, value_type=ent.REGISTRY[key].value_type,
            value=value, created_at=now, updated_at=now,
        ))


def _organizations_on_plan(conn, plan_id):
    return conn.execute(
        select(func.count()).select_from(organizations)
        .where(organizations.c.plan_id == plan_id)
    ).scalar_one()


# --- admin: entitlement vocabulary -------------------------------------------

@admin_plans_bp.route('/entitlement-keys', methods=['GET'])
def admin_entitlement_keys():
    """The declared entitlement vocabulary, so the editor renders only real keys."""
    error = require_platform_admin_api()
    if error:
        return error
    return jsonify({
        'keys': ent.registry_payload(),
        'value_types': list(ent.VALUE_TYPES),
        'scan_frequencies': list(ent.SCAN_FREQUENCY_ORDER),
        'unlimited': ent.UNLIMITED,
    })


# --- admin: plans ------------------------------------------------------------

@admin_plans_bp.route('/plans', methods=['GET'])
def admin_list_plans():
    error = require_platform_admin_api()
    if error:
        return error

    include_archived = (request.args.get('include_archived') or '').lower() in ('1', 'true', 'yes')

    stmt = select(
        plans,
        func.count(organizations.c.id).label('organization_count'),
    ).select_from(
        plans.outerjoin(organizations, organizations.c.plan_id == plans.c.id)
    ).group_by(plans.c.id).order_by(plans.c.display_order.asc(), plans.c.id.asc())

    if not include_archived:
        stmt = stmt.where(plans.c.archived_at.is_(None))

    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()

    return jsonify({
        'plans': [_plan_payload(row) for row in rows],
        'count': len(rows),
    })


@admin_plans_bp.route('/plans', methods=['POST'])
def admin_create_plan():
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object.'}), 400

    values, message = _validate_plan_fields(data, partial=False)
    if message:
        return jsonify({'error': message}), 400

    cleaned_entitlements, message = _validate_entitlements(data.get('entitlements') or {})
    if message:
        return jsonify({'error': message}), 400

    values.setdefault('slug', _slugify(values['name']))
    if not values['slug']:
        return jsonify({'error': 'Could not derive a slug from the name; supply one.'}), 400
    # A new plan is a draft unless the caller explicitly activates it, so nothing
    # becomes purchasable by accident.
    values.setdefault('active', False)

    now = datetime.utcnow()
    values.update(created_at=now, updated_at=now)

    with engine.begin() as conn:
        clash = conn.execute(
            select(plans.c.id).where(plans.c.slug == values['slug'])
        ).scalar_one_or_none()
        if clash is not None:
            return jsonify({
                'error': f"A plan with slug '{values['slug']}' already exists.",
                'plan_id': clash,
            }), 409

        plan_id = conn.execute(insert(plans).values(**values)).inserted_primary_key[0]
        _write_entitlements(conn, plan_id, cleaned_entitlements, now)

        row = conn.execute(select(plans).where(plans.c.id == plan_id)).mappings().first()
        entitlement_rows = conn.execute(
            select(plan_entitlements.c.key, plan_entitlements.c.value)
            .where(plan_entitlements.c.plan_id == plan_id)
        ).mappings().all()

        write_admin_audit(
            actor['id'], 'plan.created', target_type='plan', target_id=plan_id,
            details={
                'slug': values['slug'],
                'name': values['name'],
                'active': values['active'],
                'after': _audit_safe(values),
                'entitlements': cleaned_entitlements,
            },
            request=request, conn=conn,
        )

    return jsonify({'status': 'created', 'plan': _plan_payload(row, entitlement_rows)}), 201


@admin_plans_bp.route('/plans/<int:plan_id>', methods=['GET'])
def admin_plan_detail(plan_id):
    error = require_platform_admin_api()
    if error:
        return error

    with engine.connect() as conn:
        row = conn.execute(select(plans).where(plans.c.id == plan_id)).mappings().first()
        if not row:
            return jsonify({'error': 'Plan not found.'}), 404
        entitlement_rows = conn.execute(
            select(plan_entitlements.c.key, plan_entitlements.c.value)
            .where(plan_entitlements.c.plan_id == plan_id)
        ).mappings().all()
        organization_count = _organizations_on_plan(conn, plan_id)

    payload = _plan_payload(row, entitlement_rows)
    payload['organization_count'] = organization_count
    # Surfaced so the UI can explain why archive is unavailable before it is tried.
    payload['can_archive'] = organization_count == 0 and row['archived_at'] is None
    return jsonify({'plan': payload})


@admin_plans_bp.route('/plans/<int:plan_id>', methods=['PATCH'])
def admin_update_plan(plan_id):
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object.'}), 400

    allowed = {
        'name', 'description', 'slug', 'active', 'display_order', 'currency',
        'billing_interval', 'price_monthly', 'price_annual', 'trial_days',
        'account_types', 'monthly_cost_ceiling_usd', 'entitlements',
    }
    unknown = set(data) - allowed
    if unknown:
        return jsonify({'error': 'Unsupported fields.', 'fields': sorted(unknown)}), 400
    if not data:
        return jsonify({'error': 'No supported changes supplied.'}), 400

    values, message = _validate_plan_fields(data, partial=True)
    if message:
        return jsonify({'error': message}), 400

    cleaned_entitlements = None
    if 'entitlements' in data:
        cleaned_entitlements, message = _validate_entitlements(data['entitlements'])
        if message:
            return jsonify({'error': message}), 400

    now = datetime.utcnow()

    with engine.begin() as conn:
        before_row = conn.execute(
            select(plans).where(plans.c.id == plan_id)).mappings().first()
        if not before_row:
            return jsonify({'error': 'Plan not found.'}), 404
        before_row = dict(before_row)

        if before_row['archived_at'] is not None:
            return jsonify({
                'error': 'This plan is archived. Restore it before editing.',
            }), 409

        if 'slug' in values and values['slug'] != before_row['slug']:
            clash = conn.execute(
                select(plans.c.id).where(
                    (plans.c.slug == values['slug']) & (plans.c.id != plan_id))
            ).scalar_one_or_none()
            if clash is not None:
                return jsonify({
                    'error': f"A plan with slug '{values['slug']}' already exists.",
                    'plan_id': clash,
                }), 409

        # Activating a plan that cannot actually be sold is a configuration error
        # worth catching here rather than letting a customer see a priceless plan.
        will_be_active = values.get('active', before_row['active'])
        if will_be_active:
            price = values.get('price_monthly', before_row['price_monthly'])
            account_types = values.get('account_types', list(before_row['account_types'] or []))
            if price is None:
                return jsonify({
                    'error': 'An active plan needs a monthly price. Set price_monthly, '
                             'or leave the plan inactive.',
                }), 400
            if not account_types:
                return jsonify({
                    'error': 'An active plan must be available to at least one account '
                             'type (brand or agency).',
                }), 400

        before_diff = {key: before_row.get(key) for key in values}

        if values:
            conn.execute(update(plans).where(plans.c.id == plan_id).values(
                **values, updated_at=now))
        else:
            conn.execute(update(plans).where(plans.c.id == plan_id).values(updated_at=now))

        entitlements_before = None
        if cleaned_entitlements is not None:
            entitlements_before = {
                row['key']: row['value']
                for row in conn.execute(
                    select(plan_entitlements.c.key, plan_entitlements.c.value)
                    .where(plan_entitlements.c.plan_id == plan_id)
                ).mappings()
            }
            _write_entitlements(conn, plan_id, cleaned_entitlements, now)

        details = {
            'slug': before_row['slug'],
            'before': _audit_safe(before_diff),
            'after': _audit_safe(values),
        }
        if cleaned_entitlements is not None:
            details['entitlements_before'] = entitlements_before
            details['entitlements_after'] = cleaned_entitlements

        # One action per concern, so an audit reader searching for a price change
        # does not have to open every generic plan.updated row.
        action = 'plan.updated'
        if set(values) == {'active'}:
            action = 'plan.activated' if values['active'] else 'plan.deactivated'
        elif cleaned_entitlements is not None and not values:
            action = 'plan.entitlements.updated'

        write_admin_audit(
            actor['id'], action, target_type='plan', target_id=plan_id,
            details=details, request=request, conn=conn,
        )

        row = conn.execute(select(plans).where(plans.c.id == plan_id)).mappings().first()
        entitlement_rows = conn.execute(
            select(plan_entitlements.c.key, plan_entitlements.c.value)
            .where(plan_entitlements.c.plan_id == plan_id)
        ).mappings().all()

    return jsonify({'status': 'updated', 'plan': _plan_payload(row, entitlement_rows)})


@admin_plans_bp.route('/plans/<int:plan_id>/archive', methods=['POST'])
def admin_archive_plan(plan_id):
    """Retire a plan. There is no delete endpoint, by design."""
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    now = datetime.utcnow()

    with engine.begin() as conn:
        row = conn.execute(select(plans).where(plans.c.id == plan_id)).mappings().first()
        if not row:
            return jsonify({'error': 'Plan not found.'}), 404
        row = dict(row)
        if row['archived_at'] is not None:
            return jsonify({'status': 'unchanged', 'plan': _plan_payload(row)})

        referenced = _organizations_on_plan(conn, plan_id)
        if referenced:
            return jsonify({
                'error': 'This plan is still assigned to organizations. Move them to '
                         'another plan first.',
                'organization_count': referenced,
            }), 409

        # Archiving also deactivates: an archived plan must never stay purchasable.
        conn.execute(update(plans).where(plans.c.id == plan_id).values(
            archived_at=now, active=False, updated_at=now))

        write_admin_audit(
            actor['id'], 'plan.archived', target_type='plan', target_id=plan_id,
            details={'slug': row['slug'], 'name': row['name'],
                     'before': {'active': row['active'], 'archived_at': None}},
            request=request, conn=conn,
        )

        updated = conn.execute(select(plans).where(plans.c.id == plan_id)).mappings().first()

    return jsonify({'status': 'archived', 'plan': _plan_payload(updated)})


@admin_plans_bp.route('/plans/<int:plan_id>/restore', methods=['POST'])
def admin_restore_plan(plan_id):
    """Undo an archive. The plan returns as a draft, never straight to active."""
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    now = datetime.utcnow()

    with engine.begin() as conn:
        row = conn.execute(select(plans).where(plans.c.id == plan_id)).mappings().first()
        if not row:
            return jsonify({'error': 'Plan not found.'}), 404
        row = dict(row)
        if row['archived_at'] is None:
            return jsonify({'status': 'unchanged', 'plan': _plan_payload(row)})

        conn.execute(update(plans).where(plans.c.id == plan_id).values(
            archived_at=None, active=False, updated_at=now))

        write_admin_audit(
            actor['id'], 'plan.restored', target_type='plan', target_id=plan_id,
            details={'slug': row['slug'], 'name': row['name']},
            request=request, conn=conn,
        )

        updated = conn.execute(select(plans).where(plans.c.id == plan_id)).mappings().first()

    return jsonify({'status': 'restored', 'plan': _plan_payload(updated)})


def _audit_safe(values):
    """Decimal and datetime are not JSON; the audit row is JSONB."""
    safe = {}
    for key, value in values.items():
        if isinstance(value, Decimal):
            safe[key] = str(value)
        elif isinstance(value, datetime):
            safe[key] = value.isoformat()
        else:
            safe[key] = value
    return safe


# --- customer-facing plan list ----------------------------------------------

@plans_bp.route('/api/plans', methods=['GET'])
def list_available_plans():
    """Active, non-archived plans a given account type may buy.

    Returns only what a customer is entitled to see: the plan's commercial terms and
    its customer-visible entitlements. Annual pricing is withheld because only
    monthly is sold in this phase, and monthly_cost_ceiling_usd is withheld because
    it is infrastructure cost control, not a product feature.

    Authenticated, because plan availability depends on the caller's account type and
    this is consumed by onboarding. It is not a secret - the public pricing page is
    separate - but there is no reason to serve it anonymously either.
    """
    user_id, error = current_user_id()
    if error:
        return error

    account_type = (request.args.get('account_type') or '').strip().lower()
    if account_type and account_type not in ACCOUNT_TYPES:
        return jsonify({
            'error': f'account_type must be one of {", ".join(ACCOUNT_TYPES)}.'}), 400

    stmt = select(plans).where(
        plans.c.active.is_(True),
        plans.c.archived_at.is_(None),
    ).order_by(plans.c.display_order.asc(), plans.c.id.asc())

    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
        resolved = {
            row['id']: ent.entitlements_for_plan(row['id'], conn=conn) for row in rows
        }

    payload = []
    for row in rows:
        available_to = list(row['account_types'] or [])
        if account_type and account_type not in available_to:
            continue
        payload.append({
            'id': row['id'],
            'slug': row['slug'],
            'name': row['name'],
            'description': row['description'],
            'display_order': row['display_order'],
            'currency': row['currency'],
            'billing_interval': 'monthly',
            'price_monthly': str(row['price_monthly']) if row['price_monthly'] is not None else None,
            'trial_days': row['trial_days'],
            'account_types': available_to,
            'entitlements': ent.customer_payload(resolved[row['id']]),
        })

    return jsonify({
        'plans': payload,
        'count': len(payload),
        'account_type': account_type or None,
    })
