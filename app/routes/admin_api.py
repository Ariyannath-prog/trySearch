"""Platform admin API."""

from datetime import datetime

from flask import Blueprint, jsonify, session, request
from sqlalchemy import func, insert, select, text, update

from app.admin_auth import require_platform_admin_api
from app.admin_auth import get_platform_admin
from app.admin_audit import write_admin_audit
from app.db import engine
from app.engines.registry import adapter_for, registered_keys
from app.provider_credentials import decrypt_secret, encrypt_secret, secret_hint
from app.models import (
    admin_audit_logs,
    engines,
    memberships,
    organizations,
    provider_credentials,
    providers,
    users,
    workspaces,
)

admin_api_bp = Blueprint('admin_api', __name__, url_prefix='/api/admin')


def _count(conn, table):
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


@admin_api_bp.route('/me', methods=['GET'])
def admin_me():
    error = require_platform_admin_api()
    if error:
        return error

    return jsonify({
        'authenticated': True,
        'user_id': session.get('user_id'),
    })


@admin_api_bp.route('/overview', methods=['GET'])
def admin_overview():
    error = require_platform_admin_api()
    if error:
        return error

    with engine.connect() as conn:
        database_ok = False
        database_error = None

        try:
            conn.execute(text('SELECT 1'))
            database_ok = True
        except Exception as exc:  # noqa: BLE001
            database_error = str(exc)[:500]

        payload = {
            'users': _count(conn, users),
            'active_users': conn.execute(
                select(func.count()).select_from(users).where(users.c.is_active.is_(True))
            ).scalar_one(),
            'platform_admins': conn.execute(
                select(func.count()).select_from(users).where(
                    users.c.is_platform_admin.is_(True)
                )
            ).scalar_one(),
            'organizations': _count(conn, organizations),
            'workspaces': conn.execute(
                select(func.count()).select_from(workspaces).where(
                    workspaces.c.status == 'active'
                )
            ).scalar_one(),
            'engines': _count(conn, engines),
            'enabled_engines': conn.execute(
                select(func.count()).select_from(engines).where(
                    engines.c.enabled.is_(True)
                )
            ).scalar_one(),
            'audit_events': _count(conn, admin_audit_logs),
            'database': {
                'ok': database_ok,
                'error': database_error,
            },
        }

    return jsonify(payload)




@admin_api_bp.route('/workspaces', methods=['GET'])
def admin_workspaces():
    error = require_platform_admin_api()
    if error:
        return error

    search = (request.args.get('search') or '').strip()
    status = (request.args.get('status') or 'all').strip().lower()

    stmt = select(
        workspaces.c.id,
        workspaces.c.org_id,
        workspaces.c.brand_name,
        workspaces.c.domains,
        workspaces.c.geo,
        workspaces.c.language,
        workspaces.c.kind,
        workspaces.c.status,
        workspaces.c.deleted_at,
        workspaces.c.created_at,
        workspaces.c.domain,
        workspaces.c.website_url,
        workspaces.c.industry,
        workspaces.c.updated_at,
        organizations.c.name.label('organization_name'),
        func.count(func.distinct(memberships.c.user_id)).label('member_count'),
    ).select_from(
        workspaces
        .join(organizations, organizations.c.id == workspaces.c.org_id)
        .outerjoin(memberships, memberships.c.org_id == workspaces.c.org_id)
    ).group_by(
        workspaces.c.id,
        workspaces.c.org_id,
        workspaces.c.brand_name,
        workspaces.c.domains,
        workspaces.c.geo,
        workspaces.c.language,
        workspaces.c.kind,
        workspaces.c.status,
        workspaces.c.deleted_at,
        workspaces.c.created_at,
        workspaces.c.domain,
        workspaces.c.website_url,
        workspaces.c.industry,
        workspaces.c.updated_at,
        organizations.c.name,
    )

    if search:
        needle = f'%{search}%'
        stmt = stmt.where(
            (workspaces.c.brand_name.ilike(needle)) |
            (workspaces.c.domain.ilike(needle)) |
            (workspaces.c.website_url.ilike(needle)) |
            (organizations.c.name.ilike(needle))
        )

    if status in {'active', 'soft_deleted'}:
        stmt = stmt.where(workspaces.c.status == status)

    stmt = stmt.order_by(workspaces.c.id.asc())

    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(stmt).mappings().all()]

    return jsonify({
        'workspaces': rows,
        'count': len(rows),
    })


@admin_api_bp.route('/workspaces/<int:workspace_id>', methods=['GET'])
def admin_workspace_detail(workspace_id):
    error = require_platform_admin_api()
    if error:
        return error

    with engine.connect() as conn:
        workspace = conn.execute(
            select(
                workspaces.c.id,
                workspaces.c.org_id,
                workspaces.c.brand_name,
                workspaces.c.domains,
                workspaces.c.geo,
                workspaces.c.language,
                workspaces.c.kind,
                workspaces.c.status,
                workspaces.c.deleted_at,
                workspaces.c.created_at,
                workspaces.c.domain,
                workspaces.c.website_url,
                workspaces.c.industry,
                workspaces.c.updated_at,
                organizations.c.name.label('organization_name'),
            )
            .join(organizations, organizations.c.id == workspaces.c.org_id)
            .where(workspaces.c.id == workspace_id)
        ).mappings().first()

        if not workspace:
            return jsonify({'error': 'Workspace not found.'}), 404

        member_rows = conn.execute(
            select(
                users.c.id,
                users.c.username,
                users.c.email,
                users.c.is_active,
                memberships.c.role,
            )
            .join(memberships, memberships.c.user_id == users.c.id)
            .where(memberships.c.org_id == workspace['org_id'])
            .order_by(users.c.id.asc())
        ).mappings().all()

    return jsonify({
        'workspace': dict(workspace),
        'members': [dict(row) for row in member_rows],
    })



@admin_api_bp.route('/engines', methods=['GET'])
def admin_engines():
    error = require_platform_admin_api()
    if error:
        return error

    search = (request.args.get('search') or '').strip()
    status = (request.args.get('status') or 'all').strip().lower()
    registered = set(registered_keys())

    stmt = select(
        engines.c.id,
        engines.c.key,
        engines.c.display_name,
        engines.c.source_type,
        engines.c.adapter_version,
        engines.c.enabled,
    )

    if search:
        needle = f'%{search}%'
        stmt = stmt.where(
            (engines.c.key.ilike(needle)) |
            (engines.c.display_name.ilike(needle)) |
            (engines.c.source_type.ilike(needle))
        )

    if status == 'enabled':
        stmt = stmt.where(engines.c.enabled.is_(True))
    elif status == 'disabled':
        stmt = stmt.where(engines.c.enabled.is_(False))

    stmt = stmt.order_by(engines.c.id.asc())

    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(stmt).mappings().all()]

    for row in rows:
        adapter = adapter_for(row['key'])
        row['adapter_registered'] = row['key'] in registered
        row['adapter_available'] = adapter is not None
        row['supports_citations'] = bool(getattr(adapter, 'supports_citations', False)) if adapter else False
        row['supports_regions'] = bool(getattr(adapter, 'supports_regions', False)) if adapter else False

    return jsonify({
        'engines': rows,
        'count': len(rows),
        'registered_keys': sorted(registered),
    })


@admin_api_bp.route('/engines/<int:engine_id>', methods=['GET'])
def admin_engine_detail(engine_id):
    error = require_platform_admin_api()
    if error:
        return error

    with engine.connect() as conn:
        row = conn.execute(
            select(
                engines.c.id,
                engines.c.key,
                engines.c.display_name,
                engines.c.source_type,
                engines.c.adapter_version,
                engines.c.enabled,
            ).where(engines.c.id == engine_id)
        ).mappings().first()

    if not row:
        return jsonify({'error': 'Engine not found.'}), 404

    row = dict(row)
    adapter = adapter_for(row['key'])
    row['adapter_registered'] = row['key'] in set(registered_keys())
    row['adapter_available'] = adapter is not None
    row['supports_citations'] = bool(getattr(adapter, 'supports_citations', False)) if adapter else False
    row['supports_regions'] = bool(getattr(adapter, 'supports_regions', False)) if adapter else False
    row['estimated_unit_cost_usd'] = str(
        adapter.estimate_cost('') if adapter else '0'
    )

    return jsonify({'engine': row})


@admin_api_bp.route('/engines/<int:engine_id>', methods=['PATCH'])
def admin_update_engine(engine_id):
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    data = request.get_json(silent=True) or {}

    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object.'}), 400

    allowed = {'enabled'}
    unknown = set(data) - allowed
    if unknown:
        return jsonify({
            'error': 'Unsupported fields.',
            'fields': sorted(unknown),
        }), 400

    if 'enabled' not in data or not isinstance(data['enabled'], bool):
        return jsonify({'error': 'enabled must be boolean.'}), 400

    requested_enabled = data['enabled']

    with engine.begin() as conn:
        target = conn.execute(
            select(
                engines.c.id,
                engines.c.key,
                engines.c.display_name,
                engines.c.enabled,
            ).where(engines.c.id == engine_id)
        ).mappings().first()

        if not target:
            return jsonify({'error': 'Engine not found.'}), 404

        target = dict(target)
        before = {'enabled': target['enabled']}
        after = {'enabled': requested_enabled}

        if before == after:
            return jsonify({
                'status': 'unchanged',
                'engine': target,
            })

        if requested_enabled is False and before['enabled'] is True:
            enabled_count = conn.execute(
                select(func.count()).select_from(engines).where(
                    engines.c.enabled.is_(True)
                )
            ).scalar_one()

            if enabled_count <= 1:
                return jsonify({
                    'error': 'At least one engine must remain enabled.'
                }), 400

        conn.execute(
            update(engines)
            .where(engines.c.id == engine_id)
            .values(enabled=requested_enabled)
        )

        conn.execute(
            insert(admin_audit_logs).values(
                actor_user_id=actor['id'],
                action='engine.updated',
                target_type='engine',
                target_id=str(engine_id),
                details={
                    'key': target['key'],
                    'display_name': target['display_name'],
                    'before': before,
                    'after': after,
                },
                ip_address=request.headers.get('CF-Connecting-IP') or request.remote_addr,
                user_agent=(request.user_agent.string or '')[:2000],
                created_at=datetime.utcnow(),
            )
        )

        updated = conn.execute(
            select(
                engines.c.id,
                engines.c.key,
                engines.c.display_name,
                engines.c.source_type,
                engines.c.adapter_version,
                engines.c.enabled,
            ).where(engines.c.id == engine_id)
        ).mappings().first()

    return jsonify({
        'status': 'updated',
        'engine': dict(updated),
    })



@admin_api_bp.route('/api-keys', methods=['GET'])
def admin_api_keys():
    error = require_platform_admin_api()
    if error:
        return error

    search = (request.args.get('search') or '').strip()
    status = (request.args.get('status') or 'all').strip().lower()

    stmt = select(
        provider_credentials.c.id,
        provider_credentials.c.provider_id,
        provider_credentials.c.provider,
        provider_credentials.c.engine_id,
        provider_credentials.c.label,
        provider_credentials.c.secret_hint,
        provider_credentials.c.enabled,
        provider_credentials.c.last_tested_at,
        provider_credentials.c.last_error,
        provider_credentials.c.created_at,
        provider_credentials.c.updated_at,
        providers.c.key.label('provider_key'),
        providers.c.display_name.label('provider_display_name'),
        providers.c.auth_type.label('provider_auth_type'),
        engines.c.key.label('engine_key'),
        engines.c.display_name.label('engine_display_name'),
    ).select_from(
        provider_credentials
        .outerjoin(providers, providers.c.id == provider_credentials.c.provider_id)
        .outerjoin(engines, engines.c.id == provider_credentials.c.engine_id)
    )

    if search:
        needle = f'%{search}%'
        stmt = stmt.where(
            (provider_credentials.c.provider.ilike(needle)) |
            (provider_credentials.c.label.ilike(needle)) |
            (providers.c.key.ilike(needle)) |
            (providers.c.display_name.ilike(needle)) |
            (engines.c.key.ilike(needle)) |
            (engines.c.display_name.ilike(needle))
        )

    if status == 'enabled':
        stmt = stmt.where(provider_credentials.c.enabled.is_(True))
    elif status == 'disabled':
        stmt = stmt.where(provider_credentials.c.enabled.is_(False))

    stmt = stmt.order_by(provider_credentials.c.id.asc())

    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(stmt).mappings().all()]

    return jsonify({
        'credentials': rows,
        'count': len(rows),
    })


@admin_api_bp.route('/api-keys', methods=['POST'])
def admin_create_api_key():
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    data = request.get_json(silent=True) or {}

    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object.'}), 400

    provider_id = data.get('provider_id')
    label = str(data.get('label') or '').strip()
    secret = data.get('secret')
    engine_id = data.get('engine_id')
    enabled = data.get('enabled', True)

    if isinstance(provider_id, bool) or not isinstance(provider_id, int):
        return jsonify({'error': 'provider_id must be an integer.'}), 400

    if not label or len(label) > 160:
        return jsonify({'error': 'label is required and must be 160 characters or fewer.'}), 400

    if not isinstance(secret, str) or not secret.strip():
        return jsonify({'error': 'secret is required.'}), 400

    if not isinstance(enabled, bool):
        return jsonify({'error': 'enabled must be boolean.'}), 400

    if engine_id is not None:
        if isinstance(engine_id, bool) or not isinstance(engine_id, int):
            return jsonify({'error': 'engine_id must be an integer or null.'}), 400

    encrypted = encrypt_secret(secret.strip())
    hint = secret_hint(secret.strip())

    with engine.begin() as conn:
        provider_row = conn.execute(
            select(
                providers.c.id,
                providers.c.key,
                providers.c.display_name,
                providers.c.auth_type,
                providers.c.enabled,
            ).where(providers.c.id == provider_id)
        ).mappings().first()

        if not provider_row:
            return jsonify({'error': 'Provider not found.'}), 404

        provider_row = dict(provider_row)

        if provider_row['auth_type'] != 'api_key':
            return jsonify({
                'error': f"{provider_row['display_name']} uses {provider_row['auth_type']} authentication, not an API key."
            }), 400

        if not provider_row['enabled']:
            return jsonify({'error': 'This provider is disabled.'}), 400

        if engine_id is not None:
            engine_row = conn.execute(
                select(engines.c.id, engines.c.provider_id).where(engines.c.id == engine_id)
            ).mappings().first()

            if not engine_row:
                return jsonify({'error': 'Engine not found.'}), 404

            if engine_row['provider_id'] not in (None, provider_id):
                return jsonify({
                    'error': 'Selected engine belongs to a different provider.'
                }), 400

        duplicate = conn.execute(
            select(provider_credentials.c.id).where(
                provider_credentials.c.provider_id == provider_id,
                provider_credentials.c.label == label,
            )
        ).scalar_one_or_none()

        if duplicate is not None:
            return jsonify({
                'error': 'A credential with this provider and label already exists.',
                'credential_id': duplicate,
            }), 409

        row = conn.execute(
            insert(provider_credentials).values(
                provider_id=provider_id,
                provider=provider_row['key'],
                engine_id=engine_id,
                label=label,
                encrypted_secret=encrypted,
                secret_hint=hint,
                enabled=enabled,
                last_tested_at=None,
                last_error=None,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ).returning(
                provider_credentials.c.id,
                provider_credentials.c.provider_id,
                provider_credentials.c.provider,
                provider_credentials.c.engine_id,
                provider_credentials.c.label,
                provider_credentials.c.secret_hint,
                provider_credentials.c.enabled,
                provider_credentials.c.created_at,
                provider_credentials.c.updated_at,
            )
        ).mappings().first()

        conn.execute(
            insert(admin_audit_logs).values(
                actor_user_id=actor['id'],
                action='provider_credential.created',
                target_type='provider_credential',
                target_id=str(row['id']),
                details={
                    'provider_id': provider_id,
                    'provider': provider_row['key'],
                    'label': label,
                    'engine_id': engine_id,
                    'enabled': enabled,
                    'secret_changed': True,
                },
                ip_address=request.headers.get('CF-Connecting-IP') or request.remote_addr,
                user_agent=(request.user_agent.string or '')[:2000],
                created_at=datetime.utcnow(),
            )
        )

    return jsonify({
        'status': 'created',
        'credential': dict(row),
    }), 201



@admin_api_bp.route('/api-keys/<int:credential_id>/test', methods=['POST'])
def admin_test_api_key(credential_id):
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()

    # Import inside the route so the admin API remains decoupled from
    # the engine package at module import time.
    from app.engines.registry import adapter_for

    with engine.begin() as conn:
        row = conn.execute(
            select(
                provider_credentials.c.id,
                provider_credentials.c.provider_id,
                provider_credentials.c.provider,
                provider_credentials.c.engine_id,
                provider_credentials.c.label,
                provider_credentials.c.encrypted_secret,
                provider_credentials.c.enabled,
                providers.c.key.label('provider_key'),
                providers.c.display_name.label('provider_display_name'),
                providers.c.auth_type.label('provider_auth_type'),
                engines.c.key.label('engine_key'),
                engines.c.display_name.label('engine_display_name'),
                engines.c.enabled.label('engine_enabled'),
            )
            .select_from(
                provider_credentials
                .outerjoin(
                    providers,
                    providers.c.id == provider_credentials.c.provider_id
                )
                .outerjoin(
                    engines,
                    engines.c.id == provider_credentials.c.engine_id
                )
            )
            .where(provider_credentials.c.id == credential_id)
        ).mappings().first()

        if not row:
            return jsonify({
                'error': 'Provider credential not found.'
            }), 404

        row = dict(row)

        if row['provider_auth_type'] != 'api_key':
            message = (
                f"{row['provider_display_name'] or row['provider']} "
                f"does not use API-key authentication."
            )

            conn.execute(
                update(provider_credentials)
                .where(provider_credentials.c.id == credential_id)
                .values(
                    last_tested_at=datetime.utcnow(),
                    last_error=message[:2000],
                    updated_at=datetime.utcnow(),
                )
            )

            return jsonify({
                'status': 'failed',
                'error': message,
            }), 400

        if not row['enabled']:
            message = 'This provider credential is disabled.'

            conn.execute(
                update(provider_credentials)
                .where(provider_credentials.c.id == credential_id)
                .values(
                    last_tested_at=datetime.utcnow(),
                    last_error=message,
                    updated_at=datetime.utcnow(),
                )
            )

            return jsonify({
                'status': 'failed',
                'error': message,
            }), 400

        if row['engine_id'] is None or not row['engine_key']:
            message = 'Assign an engine to this credential before testing.'

            conn.execute(
                update(provider_credentials)
                .where(provider_credentials.c.id == credential_id)
                .values(
                    last_tested_at=datetime.utcnow(),
                    last_error=message,
                    updated_at=datetime.utcnow(),
                )
            )

            return jsonify({
                'status': 'failed',
                'error': message,
            }), 400

        adapter = adapter_for(row['engine_key'])

        if adapter is None:
            message = (
                f"No registered adapter is available for engine "
                f"'{row['engine_key']}'."
            )

            conn.execute(
                update(provider_credentials)
                .where(provider_credentials.c.id == credential_id)
                .values(
                    last_tested_at=datetime.utcnow(),
                    last_error=message[:2000],
                    updated_at=datetime.utcnow(),
                )
            )

            return jsonify({
                'status': 'failed',
                'error': message,
            }), 400

        try:
            credential = decrypt_secret(row['encrypted_secret'])
        except Exception as exc:
            message = f"Credential decryption failed: {type(exc).__name__}"

            conn.execute(
                update(provider_credentials)
                .where(provider_credentials.c.id == credential_id)
                .values(
                    last_tested_at=datetime.utcnow(),
                    last_error=message[:2000],
                    updated_at=datetime.utcnow(),
                )
            )

            return jsonify({
                'status': 'failed',
                'error': message,
            }), 500

        try:
            result = adapter.run(
                'Reply with exactly: CONNECTION_OK',
                region=None,
                timeout_s=90,
                credential=credential,
            )
        except Exception as exc:
            # The adapter boundary should normally prevent this, but keep the
            # admin endpoint safe if an unexpected integration error escapes.
            message = (
                f"Unexpected adapter error: "
                f"{type(exc).__name__}: {str(exc)}"
            )[:2000]

            now = datetime.utcnow()

            conn.execute(
                update(provider_credentials)
                .where(provider_credentials.c.id == credential_id)
                .values(
                    last_tested_at=now,
                    last_error=message,
                    updated_at=now,
                )
            )

            conn.execute(
                insert(admin_audit_logs).values(
                    actor_user_id=actor['id'],
                    action='provider_credential.tested',
                    target_type='provider_credential',
                    target_id=str(credential_id),
                    details={
                        'provider_id': row['provider_id'],
                        'provider': row['provider_key'] or row['provider'],
                        'engine_id': row['engine_id'],
                        'engine': row['engine_key'],
                        'status': 'failed',
                        'model_version': None,
                        'latency_ms': None,
                        'cost_usd': None,
                        'citation_count': 0,
                        'error': message,
                    },
                    ip_address=(
                        request.headers.get('CF-Connecting-IP')
                        or request.remote_addr
                    ),
                    user_agent=(request.user_agent.string or '')[:2000],
                    created_at=now,
                )
            )

            return jsonify({
                'status': 'failed',
                'error': message,
            }), 502

        now = datetime.utcnow()

        error_message = (
            str(result.error)[:2000]
            if result.error
            else None
        )

        cost_value = None
        if result.cost_usd is not None:
            try:
                cost_value = float(result.cost_usd)
            except (TypeError, ValueError):
                cost_value = None

        citation_count = len(result.citations or [])

        conn.execute(
            update(provider_credentials)
            .where(provider_credentials.c.id == credential_id)
            .values(
                last_tested_at=now,
                last_error=error_message,
                updated_at=now,
            )
        )

        conn.execute(
            insert(admin_audit_logs).values(
                actor_user_id=actor['id'],
                action='provider_credential.tested',
                target_type='provider_credential',
                target_id=str(credential_id),
                details={
                    'provider_id': row['provider_id'],
                    'provider': row['provider_key'] or row['provider'],
                    'engine_id': row['engine_id'],
                    'engine': row['engine_key'],
                    'status': result.status,
                    'model_version': result.model_version,
                    'latency_ms': result.latency_ms,
                    'cost_usd': cost_value,
                    'citation_count': citation_count,
                    'error': error_message,
                },
                ip_address=(
                    request.headers.get('CF-Connecting-IP')
                    or request.remote_addr
                ),
                user_agent=(request.user_agent.string or '')[:2000],
                created_at=now,
            )
        )

        provider_name = (
            row['provider_display_name']
            or row['provider_key']
            or row['provider']
        )

        engine_name = (
            row['engine_display_name']
            or row['engine_key']
        )

    if result.status == 'ok':
        return jsonify({
            'status': 'ok',
            'message': 'Connection successful.',
            'provider': provider_name,
            'engine': engine_name,
            'model_version': result.model_version,
            'latency_ms': result.latency_ms,
            'citation_count': citation_count,
            'cost_usd': cost_value,
        })

    return jsonify({
        'status': result.status,
        'message': 'Connection test completed but the engine did not return a successful result.',
        'provider': provider_name,
        'engine': engine_name,
        'model_version': result.model_version,
        'latency_ms': result.latency_ms,
        'citation_count': citation_count,
        'cost_usd': cost_value,
        'error': error_message or 'The provider returned a non-successful result.',
    })


@admin_api_bp.route('/api-keys/<int:credential_id>', methods=['GET'])
def admin_api_key_detail(credential_id):
    error = require_platform_admin_api()
    if error:
        return error

    with engine.connect() as conn:
        row = conn.execute(
            select(
                provider_credentials.c.id,
                provider_credentials.c.provider_id,
                provider_credentials.c.provider,
                provider_credentials.c.engine_id,
                provider_credentials.c.label,
                provider_credentials.c.secret_hint,
                provider_credentials.c.enabled,
                provider_credentials.c.last_tested_at,
                provider_credentials.c.last_error,
                provider_credentials.c.created_at,
                provider_credentials.c.updated_at,
                providers.c.key.label('provider_key'),
                providers.c.display_name.label('provider_display_name'),
                providers.c.auth_type.label('provider_auth_type'),
                engines.c.key.label('engine_key'),
                engines.c.display_name.label('engine_display_name'),
            )
            .select_from(
                provider_credentials
                .outerjoin(providers, providers.c.id == provider_credentials.c.provider_id)
                .outerjoin(engines, engines.c.id == provider_credentials.c.engine_id)
            )
            .where(provider_credentials.c.id == credential_id)
        ).mappings().first()

    if not row:
        return jsonify({'error': 'Provider credential not found.'}), 404

    return jsonify({
        'credential': dict(row),
        'secret': None,
        'secret_exposed': False,
    })


@admin_api_bp.route('/api-keys/<int:credential_id>', methods=['PATCH'])
def admin_update_api_key(credential_id):
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    data = request.get_json(silent=True) or {}

    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object.'}), 400

    allowed = {'label', 'engine_id', 'enabled', 'secret'}
    unknown = set(data) - allowed

    if unknown:
        return jsonify({
            'error': 'Unsupported fields.',
            'fields': sorted(unknown),
        }), 400

    if not data:
        return jsonify({'error': 'No changes supplied.'}), 400

    with engine.begin() as conn:
        target = conn.execute(
            select(
                provider_credentials.c.id,
                provider_credentials.c.provider_id,
                provider_credentials.c.provider,
                provider_credentials.c.engine_id,
                provider_credentials.c.label,
                provider_credentials.c.enabled,
            ).where(provider_credentials.c.id == credential_id)
        ).mappings().first()

        if not target:
            return jsonify({'error': 'Provider credential not found.'}), 404

        target = dict(target)
        requested = {}

        if 'label' in data:
            label = str(data['label'] or '').strip()

            if not label or len(label) > 160:
                return jsonify({'error': 'label must be 1-160 characters.'}), 400

            requested['label'] = label

        if 'engine_id' in data:
            engine_id = data['engine_id']

            if engine_id is not None and (
                isinstance(engine_id, bool) or not isinstance(engine_id, int)
            ):
                return jsonify({'error': 'engine_id must be an integer or null.'}), 400

            if engine_id is not None:
                engine_row = conn.execute(
                    select(
                        engines.c.id,
                        engines.c.provider_id,
                    ).where(engines.c.id == engine_id)
                ).mappings().first()

                if not engine_row:
                    return jsonify({'error': 'Engine not found.'}), 404

                if engine_row['provider_id'] not in (None, target['provider_id']):
                    return jsonify({
                        'error': 'Selected engine belongs to a different provider.'
                    }), 400

            requested['engine_id'] = engine_id

        if 'enabled' in data:
            if not isinstance(data['enabled'], bool):
                return jsonify({'error': 'enabled must be boolean.'}), 400

            requested['enabled'] = data['enabled']

        secret_changed = False

        if 'secret' in data:
            if not isinstance(data['secret'], str) or not data['secret'].strip():
                return jsonify({'error': 'secret must be a non-empty string.'}), 400

            new_secret = data['secret'].strip()
            requested['encrypted_secret'] = encrypt_secret(new_secret)
            requested['secret_hint'] = secret_hint(new_secret)
            requested['last_tested_at'] = None
            requested['last_error'] = None
            secret_changed = True

        if not requested:
            return jsonify({'error': 'No supported changes supplied.'}), 400

        next_label = requested.get('label', target['label'])
        next_provider_id = target['provider_id']

        provider_row = conn.execute(
            select(
                providers.c.id,
                providers.c.key,
                providers.c.display_name,
                providers.c.auth_type,
                providers.c.enabled,
            ).where(providers.c.id == next_provider_id)
        ).mappings().first()

        if not provider_row:
            return jsonify({'error': 'Linked provider not found.'}), 409

        if provider_row['auth_type'] != 'api_key':
            return jsonify({
                'error': f"{provider_row['display_name']} does not use API-key authentication."
            }), 400

        duplicate = conn.execute(
            select(provider_credentials.c.id).where(
                provider_credentials.c.provider_id == next_provider_id,
                provider_credentials.c.label == next_label,
                provider_credentials.c.id != credential_id,
            )
        ).scalar_one_or_none()

        if duplicate is not None:
            return jsonify({
                'error': 'A credential with this provider and label already exists.',
                'credential_id': duplicate,
            }), 409

        requested['provider'] = provider_row['key']
        requested['updated_at'] = datetime.utcnow()

        conn.execute(
            update(provider_credentials)
            .where(provider_credentials.c.id == credential_id)
            .values(**requested)
        )

        conn.execute(
            insert(admin_audit_logs).values(
                actor_user_id=actor['id'],
                action='provider_credential.updated',
                target_type='provider_credential',
                target_id=str(credential_id),
                details={
                    'provider_id': target['provider_id'],
                    'provider': target['provider'],
                    'before': {
                        'label': target['label'],
                        'engine_id': target['engine_id'],
                        'enabled': target['enabled'],
                    },
                    'after': {
                        'label': requested.get('label', target['label']),
                        'engine_id': requested.get('engine_id', target['engine_id']),
                        'enabled': requested.get('enabled', target['enabled']),
                        'secret_changed': secret_changed,
                    },
                },
                ip_address=request.headers.get('CF-Connecting-IP') or request.remote_addr,
                user_agent=(request.user_agent.string or '')[:2000],
                created_at=datetime.utcnow(),
            )
        )

        updated = conn.execute(
            select(
                provider_credentials.c.id,
                provider_credentials.c.provider_id,
                provider_credentials.c.provider,
                provider_credentials.c.engine_id,
                provider_credentials.c.label,
                provider_credentials.c.secret_hint,
                provider_credentials.c.enabled,
                provider_credentials.c.last_tested_at,
                provider_credentials.c.last_error,
                provider_credentials.c.created_at,
                provider_credentials.c.updated_at,
            ).where(provider_credentials.c.id == credential_id)
        ).mappings().first()

    return jsonify({
        'status': 'updated',
        'credential': dict(updated),
        'secret_changed': secret_changed,
    })


@admin_api_bp.route('/providers', methods=['GET'])
def admin_providers():
    error = require_platform_admin_api()
    if error:
        return error

    search = (request.args.get('search') or '').strip()
    status = (request.args.get('status') or 'all').strip().lower()

    stmt = select(
        providers.c.id,
        providers.c.key,
        providers.c.display_name,
        providers.c.category,
        providers.c.auth_type,
        providers.c.base_url,
        providers.c.docs_url,
        providers.c.config,
        providers.c.enabled,
        providers.c.created_at,
        providers.c.updated_at,
        func.count(func.distinct(provider_credentials.c.id)).label('credential_count'),
        func.count(func.distinct(engines.c.id)).label('engine_count'),
    ).select_from(
        providers
        .outerjoin(
            provider_credentials,
            provider_credentials.c.provider == providers.c.key,
        )
        .outerjoin(
            engines,
            engines.c.provider_id == providers.c.id,
        )
    ).group_by(
        providers.c.id,
        providers.c.key,
        providers.c.display_name,
        providers.c.category,
        providers.c.auth_type,
        providers.c.base_url,
        providers.c.docs_url,
        providers.c.config,
        providers.c.enabled,
        providers.c.created_at,
        providers.c.updated_at,
    )

    if search:
        needle = f'%{search}%'
        stmt = stmt.where(
            (providers.c.key.ilike(needle)) |
            (providers.c.display_name.ilike(needle)) |
            (providers.c.category.ilike(needle))
        )

    if status == 'enabled':
        stmt = stmt.where(providers.c.enabled.is_(True))
    elif status == 'disabled':
        stmt = stmt.where(providers.c.enabled.is_(False))

    stmt = stmt.order_by(providers.c.id.asc())

    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(stmt).mappings().all()]

    return jsonify({
        'providers': rows,
        'count': len(rows),
    })


@admin_api_bp.route('/providers/<int:provider_id>', methods=['GET'])
def admin_provider_detail(provider_id):
    error = require_platform_admin_api()
    if error:
        return error

    with engine.connect() as conn:
        provider = conn.execute(
            select(
                providers.c.id,
                providers.c.key,
                providers.c.display_name,
                providers.c.category,
                providers.c.auth_type,
                providers.c.base_url,
                providers.c.docs_url,
                providers.c.config,
                providers.c.enabled,
                providers.c.created_at,
                providers.c.updated_at,
            ).where(providers.c.id == provider_id)
        ).mappings().first()

        if not provider:
            return jsonify({'error': 'Provider not found.'}), 404

        credential_rows = conn.execute(
            select(
                provider_credentials.c.id,
                provider_credentials.c.label,
                provider_credentials.c.secret_hint,
                provider_credentials.c.enabled,
                provider_credentials.c.last_tested_at,
                provider_credentials.c.last_error,
                provider_credentials.c.created_at,
                provider_credentials.c.updated_at,
                provider_credentials.c.engine_id,
            )
            .where(provider_credentials.c.provider == provider['key'])
            .order_by(provider_credentials.c.id.asc())
        ).mappings().all()

        engine_rows = conn.execute(
            select(
                engines.c.id,
                engines.c.key,
                engines.c.display_name,
                engines.c.source_type,
                engines.c.adapter_version,
                engines.c.enabled,
            )
            .where(engines.c.provider_id == provider_id)
            .order_by(engines.c.id.asc())
        ).mappings().all()

    return jsonify({
        'provider': dict(provider),
        'credentials': [dict(row) for row in credential_rows],
        'engines': [dict(row) for row in engine_rows],
    })


@admin_api_bp.route('/providers/<int:provider_id>', methods=['PATCH'])
def admin_update_provider(provider_id):
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    data = request.get_json(silent=True) or {}

    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object.'}), 400

    allowed = {'enabled'}
    unknown = set(data) - allowed

    if unknown:
        return jsonify({
            'error': 'Unsupported fields.',
            'fields': sorted(unknown),
        }), 400

    if 'enabled' not in data or not isinstance(data['enabled'], bool):
        return jsonify({'error': 'enabled must be boolean.'}), 400

    requested_enabled = data['enabled']

    with engine.begin() as conn:
        target = conn.execute(
            select(
                providers.c.id,
                providers.c.key,
                providers.c.display_name,
                providers.c.enabled,
            ).where(providers.c.id == provider_id)
        ).mappings().first()

        if not target:
            return jsonify({'error': 'Provider not found.'}), 404

        target = dict(target)
        before = {'enabled': target['enabled']}
        after = {'enabled': requested_enabled}

        if before == after:
            return jsonify({
                'status': 'unchanged',
                'provider': target,
            })

        conn.execute(
            update(providers)
            .where(providers.c.id == provider_id)
            .values(
                enabled=requested_enabled,
                updated_at=datetime.utcnow(),
            )
        )

        conn.execute(
            insert(admin_audit_logs).values(
                actor_user_id=actor['id'],
                action='provider.updated',
                target_type='provider',
                target_id=str(provider_id),
                details={
                    'key': target['key'],
                    'display_name': target['display_name'],
                    'before': before,
                    'after': after,
                },
                ip_address=request.headers.get('CF-Connecting-IP') or request.remote_addr,
                user_agent=(request.user_agent.string or '')[:2000],
                created_at=datetime.utcnow(),
            )
        )

        updated = conn.execute(
            select(
                providers.c.id,
                providers.c.key,
                providers.c.display_name,
                providers.c.category,
                providers.c.auth_type,
                providers.c.base_url,
                providers.c.docs_url,
                providers.c.enabled,
                providers.c.updated_at,
            ).where(providers.c.id == provider_id)
        ).mappings().first()

    return jsonify({
        'status': 'updated',
        'provider': dict(updated),
    })


@admin_api_bp.route('/organizations', methods=['GET'])
def admin_organizations():
    error = require_platform_admin_api()
    if error:
        return error

    search = (request.args.get('search') or '').strip()

    stmt = select(
        organizations.c.id,
        organizations.c.name,
        organizations.c.plan_id,
        organizations.c.stripe_customer_id,
        organizations.c.monthly_cost_ceiling_usd,
        organizations.c.created_at,
        func.count(func.distinct(memberships.c.user_id)).label('member_count'),
        func.count(func.distinct(workspaces.c.id)).label('workspace_count'),
    ).select_from(
        organizations
        .outerjoin(memberships, memberships.c.org_id == organizations.c.id)
        .outerjoin(workspaces, workspaces.c.org_id == organizations.c.id)
    ).group_by(
        organizations.c.id,
        organizations.c.name,
        organizations.c.plan_id,
        organizations.c.stripe_customer_id,
        organizations.c.monthly_cost_ceiling_usd,
        organizations.c.created_at,
    )

    if search:
        needle = f'%{search}%'
        stmt = stmt.where(
            (organizations.c.name.ilike(needle)) |
            (organizations.c.stripe_customer_id.ilike(needle))
        )

    stmt = stmt.order_by(organizations.c.id.asc())

    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(stmt).mappings().all()]

    for row in rows:
        if row['monthly_cost_ceiling_usd'] is not None:
            row['monthly_cost_ceiling_usd'] = float(row['monthly_cost_ceiling_usd'])

    return jsonify({
        'organizations': rows,
        'count': len(rows),
    })


@admin_api_bp.route('/organizations/<int:org_id>', methods=['GET'])
def admin_organization_detail(org_id):
    error = require_platform_admin_api()
    if error:
        return error

    with engine.connect() as conn:
        org = conn.execute(
            select(
                organizations.c.id,
                organizations.c.name,
                organizations.c.plan_id,
                organizations.c.stripe_customer_id,
                organizations.c.monthly_cost_ceiling_usd,
                organizations.c.created_at,
            ).where(organizations.c.id == org_id)
        ).mappings().first()

        if not org:
            return jsonify({'error': 'Organization not found.'}), 404

        members_rows = conn.execute(
            select(
                users.c.id,
                users.c.username,
                users.c.email,
                users.c.is_active,
                users.c.is_platform_admin,
                memberships.c.role,
            )
            .join(memberships, memberships.c.user_id == users.c.id)
            .where(memberships.c.org_id == org_id)
            .order_by(users.c.id.asc())
        ).mappings().all()

        workspace_rows = conn.execute(
            select(
                workspaces.c.id,
                workspaces.c.brand_name,
                workspaces.c.domain,
                workspaces.c.website_url,
                workspaces.c.geo,
                workspaces.c.language,
                workspaces.c.kind,
                workspaces.c.status,
                workspaces.c.created_at,
            )
            .where(workspaces.c.org_id == org_id)
            .order_by(workspaces.c.id.asc())
        ).mappings().all()

    org = dict(org)
    if org['monthly_cost_ceiling_usd'] is not None:
        org['monthly_cost_ceiling_usd'] = float(org['monthly_cost_ceiling_usd'])

    return jsonify({
        'organization': org,
        'members': [dict(row) for row in members_rows],
        'workspaces': [dict(row) for row in workspace_rows],
    })


@admin_api_bp.route('/users', methods=['GET'])
def admin_users():
    error = require_platform_admin_api()
    if error:
        return error

    search = (request.args.get('search') or '').strip()
    status = (request.args.get('status') or 'all').strip().lower()

    stmt = select(
        users.c.id,
        users.c.username,
        users.c.email,
        users.c.created_at,
        users.c.is_platform_admin,
        users.c.is_active,
        users.c.last_login_at,
        func.count(func.distinct(memberships.c.org_id)).label('organization_count'),
        func.count(func.distinct(workspaces.c.id)).label('workspace_count'),
    ).select_from(
        users
        .outerjoin(memberships, memberships.c.user_id == users.c.id)
        .outerjoin(workspaces, workspaces.c.org_id == memberships.c.org_id)
    ).group_by(
        users.c.id,
        users.c.username,
        users.c.email,
        users.c.created_at,
        users.c.is_platform_admin,
        users.c.is_active,
        users.c.last_login_at,
    )

    if search:
        needle = f'%{search}%'
        stmt = stmt.where(
            (users.c.username.ilike(needle)) |
            (users.c.email.ilike(needle))
        )

    if status == 'active':
        stmt = stmt.where(users.c.is_active.is_(True))
    elif status == 'disabled':
        stmt = stmt.where(users.c.is_active.is_(False))
    elif status == 'admin':
        stmt = stmt.where(users.c.is_platform_admin.is_(True))

    stmt = stmt.order_by(users.c.id.asc())

    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(stmt).mappings().all()]

    return jsonify({
        'users': rows,
        'count': len(rows),
    })


@admin_api_bp.route('/users/<int:user_id>', methods=['GET'])
def admin_user_detail(user_id):
    error = require_platform_admin_api()
    if error:
        return error

    with engine.connect() as conn:
        row = conn.execute(
            select(
                users.c.id,
                users.c.username,
                users.c.email,
                users.c.created_at,
                users.c.is_platform_admin,
                users.c.is_active,
                users.c.last_login_at,
            ).where(users.c.id == user_id)
        ).mappings().first()

        if not row:
            return jsonify({'error': 'User not found.'}), 404

        org_rows = conn.execute(
            select(
                memberships.c.org_id,
                memberships.c.role,
                organizations.c.name.label('organization_name'),
            )
            .join(organizations, organizations.c.id == memberships.c.org_id)
            .where(memberships.c.user_id == user_id)
            .order_by(organizations.c.name.asc())
        ).mappings().all()

        workspace_rows = conn.execute(
            select(
                workspaces.c.id,
                workspaces.c.brand_name,
                workspaces.c.status,
                workspaces.c.org_id,
                organizations.c.name.label('organization_name'),
            )
            .join(organizations, organizations.c.id == workspaces.c.org_id)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                memberships.c.user_id == user_id
            )
            .order_by(workspaces.c.id.asc())
        ).mappings().all()

    return jsonify({
        'user': dict(row),
        'organizations': [dict(r) for r in org_rows],
        'workspaces': [dict(r) for r in workspace_rows],
    })


@admin_api_bp.route('/users/<int:user_id>', methods=['PATCH'])
def admin_update_user(user_id):
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    data = request.get_json(silent=True) or {}

    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object.'}), 400

    allowed = {'is_active', 'is_platform_admin'}
    unknown = set(data) - allowed
    if unknown:
        return jsonify({
            'error': 'Unsupported fields.',
            'fields': sorted(unknown),
        }), 400

    requested = {}

    for field in allowed:
        if field in data:
            if not isinstance(data[field], bool):
                return jsonify({'error': f'{field} must be boolean.'}), 400
            requested[field] = data[field]

    if not requested:
        return jsonify({'error': 'No supported changes supplied.'}), 400

    with engine.begin() as conn:
        target = conn.execute(
            select(
                users.c.id,
                users.c.username,
                users.c.email,
                users.c.is_platform_admin,
                users.c.is_active,
            ).where(users.c.id == user_id)
        ).mappings().first()

        if not target:
            return jsonify({'error': 'User not found.'}), 404

        target = dict(target)
        before = {
            'is_active': target['is_active'],
            'is_platform_admin': target['is_platform_admin'],
        }

        after = {**before, **requested}

        # Never lock the current administrator out of the control center.
        if user_id == actor['id']:
            if after['is_active'] is False:
                return jsonify({'error': 'You cannot deactivate your own account.'}), 400
            if before['is_platform_admin'] and after['is_platform_admin'] is False:
                return jsonify({'error': 'You cannot remove your own platform-admin access.'}), 400

        # Never leave the platform with zero active administrators.
        if before['is_platform_admin'] and after['is_platform_admin'] is False:
            active_admins = conn.execute(
                select(func.count()).select_from(users).where(
                    users.c.is_platform_admin.is_(True),
                    users.c.is_active.is_(True),
                )
            ).scalar_one()

            if active_admins <= 1:
                return jsonify({
                    'error': 'At least one active platform administrator must remain.'
                }), 400

        if before['is_active'] and after['is_active'] is False and before['is_platform_admin']:
            other_admins = conn.execute(
                select(func.count()).select_from(users).where(
                    users.c.id != user_id,
                    users.c.is_platform_admin.is_(True),
                    users.c.is_active.is_(True),
                )
            ).scalar_one()

            if other_admins == 0:
                return jsonify({
                    'error': 'At least one other active platform administrator must remain.'
                }), 400

        conn.execute(
            update(users)
            .where(users.c.id == user_id)
            .values(**requested)
        )

        # Audit in the same transaction as the change.
        conn.execute(
            insert(admin_audit_logs).values(
                actor_user_id=actor['id'],
                action='user.updated',
                target_type='user',
                target_id=str(user_id),
                details={
                    'username': target['username'],
                    'before': before,
                    'after': after,
                },
                ip_address=request.headers.get('CF-Connecting-IP') or request.remote_addr,
                user_agent=(request.user_agent.string or '')[:2000],
                created_at=datetime.utcnow(),
            )
        )

        updated = conn.execute(
            select(
                users.c.id,
                users.c.username,
                users.c.email,
                users.c.created_at,
                users.c.is_platform_admin,
                users.c.is_active,
                users.c.last_login_at,
            ).where(users.c.id == user_id)
        ).mappings().first()

    return jsonify({
        'status': 'updated',
        'user': dict(updated),
    })
