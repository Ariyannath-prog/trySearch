"""Google Search Console: OAuth, token vault, sync."""

from flask import Blueprint
from datetime import date, datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory, abort, session, redirect
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Boolean,
    Float,
    Integer,
    String,
    Text,
    DateTime,
    UniqueConstraint,
    select,
    insert,
    update,
    desc,
    func,
    text,
)
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
import os
import secrets

from app.auth import analytics_user_id
from app.db import engine
from app.http_client import ProviderAPIError, external_json_request
from app.models import memberships, workspaces, gsc_connections, gsc_properties, gsc_query_rows, gsc_sync_runs
from app.tenancy import current_user_id, require_workspace, workspace_for_member
from app.utils import normalise_domain, row_to_dict

gsc_bp = Blueprint('gsc', __name__)

def oauth_token_cipher():
    key = os.environ.get('OAUTH_TOKEN_ENCRYPTION_KEY')
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode('utf-8'))
    except (ImportError, ValueError) as error:
        raise RuntimeError('OAUTH_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.') from error

def encrypt_oauth_token(token):
    if not token:
        return None
    cipher = oauth_token_cipher()
    if not cipher:
        raise RuntimeError('OAuth token encryption is not configured.')
    return cipher.encrypt(token.encode('utf-8')).decode('utf-8')

def decrypt_oauth_token(token):
    if not token:
        return None
    cipher = oauth_token_cipher()
    if not cipher:
        raise RuntimeError('OAuth token encryption is not configured.')
    try:
        return cipher.decrypt(token.encode('utf-8')).decode('utf-8')
    except Exception as error:
        raise RuntimeError('The stored OAuth token could not be decrypted.') from error

GOOGLE_WEBMASTERS_SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'

def google_search_console_configured():
    has_settings = bool(
        os.environ.get('GOOGLE_CLIENT_ID') and
        os.environ.get('GOOGLE_CLIENT_SECRET') and
        os.environ.get('OAUTH_TOKEN_ENCRYPTION_KEY')
    )
    if not has_settings:
        return False
    try:
        return oauth_token_cipher() is not None
    except RuntimeError:
        return False

def gsc_connection_for_project(workspace_id, user_id):
    """T5 dropped gsc_connections.user_id; access follows org membership instead."""
    with engine.connect() as conn:
        row = conn.execute(
            select(gsc_connections)
            .join(workspaces, workspaces.c.id == gsc_connections.c.workspace_id)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (gsc_connections.c.workspace_id == workspace_id)
                & (memberships.c.user_id == user_id)
            )
        ).mappings().first()
    return dict(row) if row else None

def google_redirect_uri():
    return os.environ.get('GOOGLE_OAUTH_REDIRECT_URI') or request.url_root.rstrip('/') + '/api/analytics/integrations/google/callback'

def refresh_google_access_token(connection):
    expires_at = connection.get('token_expires_at')
    if connection.get('encrypted_access_token') and expires_at and expires_at > datetime.utcnow() + timedelta(minutes=5):
        return decrypt_oauth_token(connection['encrypted_access_token'])
    refresh_token = decrypt_oauth_token(connection.get('encrypted_refresh_token'))
    if not refresh_token:
        raise ProviderAPIError('Google authorization has no refresh token. Reconnect Search Console.')
    token_payload = external_json_request(
        'https://oauth2.googleapis.com/token', method='POST', form={
            'client_id': os.environ['GOOGLE_CLIENT_ID'],
            'client_secret': os.environ['GOOGLE_CLIENT_SECRET'],
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        },
    )
    access_token = token_payload.get('access_token')
    if not access_token:
        raise ProviderAPIError('Google did not return an access token.')
    expires = datetime.utcnow() + timedelta(seconds=max(int(token_payload.get('expires_in', 3600)) - 30, 60))
    with engine.begin() as conn:
        conn.execute(update(gsc_connections).where(gsc_connections.c.id == connection['id']).values(
            encrypted_access_token=encrypt_oauth_token(access_token), token_expires_at=expires,
            status='connected', last_error=None, updated_at=datetime.utcnow(),
        ))
    return access_token

def gsc_report(workspace_id, user_id):
    connection = gsc_connection_for_project(workspace_id, user_id)
    if not connection:
        return {
            'configured': google_search_console_configured(), 'status': 'disconnected',
            'property': None, 'properties': [], 'last_sync': None, 'metrics': None, 'queries': [],
        }
    with engine.connect() as conn:
        properties = [row_to_dict(row) for row in conn.execute(select(gsc_properties).where(
            gsc_properties.c.connection_id == connection['id']
        ).order_by(desc(gsc_properties.c.selected), gsc_properties.c.site_url)).mappings().all()]
        sync = conn.execute(select(gsc_sync_runs).where(
            gsc_sync_runs.c.connection_id == connection['id']
        ).order_by(desc(gsc_sync_runs.c.created_at)).limit(1)).mappings().first()
        rows = []
        metric_rows = []
        if sync and sync['status'] == 'succeeded':
            rows = [row_to_dict(row) for row in conn.execute(select(gsc_query_rows).where(
                gsc_query_rows.c.sync_run_id == sync['id']
            ).order_by(desc(gsc_query_rows.c.clicks), desc(gsc_query_rows.c.impressions)).limit(100)).mappings().all()]
            metric_rows = conn.execute(select(
                gsc_query_rows.c.clicks, gsc_query_rows.c.impressions, gsc_query_rows.c.position,
            ).where(gsc_query_rows.c.sync_run_id == sync['id'])).mappings().all()
    metrics = None
    if metric_rows:
        clicks = sum(float(row['clicks']) for row in metric_rows)
        impressions = sum(float(row['impressions']) for row in metric_rows)
        weighted_position = sum(float(row['position']) * float(row['impressions']) for row in metric_rows)
        metrics = {
            'clicks': round(clicks, 2), 'impressions': round(impressions, 2),
            'ctr': round(clicks / impressions * 100, 2) if impressions else 0,
            'position': round(weighted_position / impressions, 2) if impressions else None,
            'rows_in_view': len(rows), 'rows_saved': len(metric_rows),
        }
    return {
        'configured': google_search_console_configured(), 'status': connection['status'],
        'property': connection.get('selected_property'), 'properties': properties,
        'last_error': connection.get('last_error'),
        'last_sync': row_to_dict(sync) if sync else None, 'metrics': metrics, 'queries': rows,
    }

@gsc_bp.route('/api/analytics/integrations/google/start', methods=['GET'])
def start_google_search_console_oauth():
    # Authentication first, so an anonymous caller still gets 401 rather than a
    # 400 about a query argument they were never entitled to use.
    _user_id, error = current_user_id()
    if error:
        return error
    try:
        workspace_id = int(request.args.get('workspace_id', ''))
    except ValueError:
        return jsonify({'error': 'A valid workspace_id is required.'}), 400
    # Starting an OAuth flow grants this workspace access to a Google account, so
    # it is a write even though the request is a GET.
    access, error = require_workspace(workspace_id, write=True)
    if error:
        return error
    user_id = access.user_id
    if not google_search_console_configured():
        return jsonify({'error': 'Google Search Console is not configured on this server.'}), 503
    try:
        oauth_token_cipher()
    except RuntimeError as error:
        return jsonify({'error': str(error)}), 503
    state = secrets.token_urlsafe(32)
    session['gsc_oauth_state'] = state
    session['gsc_oauth_project_id'] = workspace_id
    params = {
        'client_id': os.environ['GOOGLE_CLIENT_ID'], 'redirect_uri': google_redirect_uri(),
        'response_type': 'code', 'scope': GOOGLE_WEBMASTERS_SCOPE,
        'access_type': 'offline', 'include_granted_scopes': 'true', 'prompt': 'consent',
        'state': state,
    }
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params))

@gsc_bp.route('/api/analytics/integrations/google/callback', methods=['GET'])
def google_search_console_oauth_callback():
    _user_id, error = current_user_id()
    if error:
        return error
    # State is validated before the workspace lookup: the workspace id comes from
    # the session, not the query string, so a forged callback cannot name one.
    expected_state = session.pop('gsc_oauth_state', None)
    workspace_id = session.pop('gsc_oauth_project_id', None)
    if not expected_state or not secrets.compare_digest(request.args.get('state', ''), expected_state):
        return jsonify({'error': 'Google OAuth state validation failed.'}), 400
    # Membership is re-checked here rather than trusted from the start of the flow:
    # a user can be removed from the org between the redirect out and the return.
    access, error = require_workspace(workspace_id, write=True)
    if error:
        return error
    user_id, project = access.user_id, access.workspace
    if request.args.get('error'):
        return redirect(f'/analytics?project={workspace_id}&gsc=denied')
    code = request.args.get('code')
    if not code:
        return jsonify({'error': 'Google did not return an authorization code.'}), 400
    try:
        token_payload = external_json_request(
            'https://oauth2.googleapis.com/token', method='POST', form={
                'code': code, 'client_id': os.environ['GOOGLE_CLIENT_ID'],
                'client_secret': os.environ['GOOGLE_CLIENT_SECRET'],
                'redirect_uri': google_redirect_uri(), 'grant_type': 'authorization_code',
            },
        )
        access_token = token_payload.get('access_token')
        if not access_token:
            raise ProviderAPIError('Google did not return an access token.')
        site_payload = external_json_request(
            'https://www.googleapis.com/webmasters/v3/sites',
            headers={'Authorization': f'Bearer {access_token}'},
        )
        sites = site_payload.get('siteEntry') or []
        now = datetime.utcnow()
        existing = gsc_connection_for_project(workspace_id, user_id)
        refresh_token = token_payload.get('refresh_token')
        encrypted_refresh = encrypt_oauth_token(refresh_token) if refresh_token else (existing or {}).get('encrypted_refresh_token')
        if not encrypted_refresh:
            raise ProviderAPIError('Google did not return offline access. Reconnect and approve consent.')
        selected_property = None
        for site in sites:
            site_url = site.get('siteUrl') or ''
            site_domain = normalise_domain(site_url.replace('sc-domain:', ''))
            if site_domain == project['domain']:
                selected_property = site_url
                break
        if not selected_property and len(sites) == 1:
            selected_property = sites[0].get('siteUrl')
        expires = now + timedelta(seconds=max(int(token_payload.get('expires_in', 3600)) - 30, 60))
        with engine.begin() as conn:
            values = dict(
                encrypted_refresh_token=encrypted_refresh,
                encrypted_access_token=encrypt_oauth_token(access_token), token_expires_at=expires,
                granted_scopes=token_payload.get('scope') or GOOGLE_WEBMASTERS_SCOPE,
                selected_property=selected_property, status='connected', last_error=None, updated_at=now,
            )
            if existing:
                conn.execute(update(gsc_connections).where(gsc_connections.c.id == existing['id']).values(**values))
                connection_id = existing['id']
                conn.execute(gsc_properties.delete().where(gsc_properties.c.connection_id == connection_id))
            else:
                result = conn.execute(insert(gsc_connections).values(
                    workspace_id=workspace_id, created_at=now, **values,
                ))
                connection_id = result.inserted_primary_key[0]
            for site in sites:
                site_url = (site.get('siteUrl') or '')[:2048]
                if site_url:
                    conn.execute(insert(gsc_properties).values(
                        connection_id=connection_id, site_url=site_url,
                        permission_level=(site.get('permissionLevel') or 'unknown')[:80],
                        selected=site_url == selected_property,
                    ))
        return redirect(f'/analytics?project={workspace_id}&gsc=connected')
    except (ProviderAPIError, RuntimeError) as error:
        return redirect(f'/analytics?project={workspace_id}&gsc=error&message={quote(str(error)[:180])}')

@gsc_bp.route('/api/analytics/projects/<int:workspace_id>/search-console', methods=['GET', 'DELETE'])
def search_console_connection_endpoint(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    user_id = access.user_id
    if request.method == 'DELETE':
        connection = gsc_connection_for_project(workspace_id, user_id)
        if connection:
            with engine.begin() as conn:
                sync_ids = [row[0] for row in conn.execute(select(gsc_sync_runs.c.id).where(
                    gsc_sync_runs.c.connection_id == connection['id']
                )).all()]
                if sync_ids:
                    conn.execute(gsc_query_rows.delete().where(gsc_query_rows.c.sync_run_id.in_(sync_ids)))
                conn.execute(gsc_sync_runs.delete().where(gsc_sync_runs.c.connection_id == connection['id']))
                conn.execute(gsc_properties.delete().where(gsc_properties.c.connection_id == connection['id']))
                conn.execute(gsc_connections.delete().where(gsc_connections.c.id == connection['id']))
        return jsonify({'status': 'disconnected'})
    return jsonify({'search_console': gsc_report(workspace_id, user_id)})

@gsc_bp.route('/api/analytics/projects/<int:workspace_id>/search-console/property', methods=['PUT'])
def select_search_console_property(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    user_id = access.user_id
    connection = gsc_connection_for_project(workspace_id, user_id)
    if not connection:
        return jsonify({'error': 'Connect Google Search Console first.'}), 409
    site_url = ((request.get_json(silent=True) or {}).get('site_url') or '').strip()
    with engine.connect() as conn:
        allowed = conn.execute(select(gsc_properties.c.id).where(
            (gsc_properties.c.connection_id == connection['id']) & (gsc_properties.c.site_url == site_url)
        )).scalar_one_or_none()
    if not allowed:
        return jsonify({'error': 'Select a property returned by Google Search Console.'}), 400
    with engine.begin() as conn:
        conn.execute(update(gsc_properties).where(gsc_properties.c.connection_id == connection['id']).values(selected=False))
        conn.execute(update(gsc_properties).where(gsc_properties.c.id == allowed).values(selected=True))
        conn.execute(update(gsc_connections).where(gsc_connections.c.id == connection['id']).values(
            selected_property=site_url, updated_at=datetime.utcnow(),
        ))
    return jsonify({'search_console': gsc_report(workspace_id, user_id)})

@gsc_bp.route('/api/analytics/projects/<int:workspace_id>/search-console/sync', methods=['POST'])
def sync_search_console(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    user_id = access.user_id
    connection = gsc_connection_for_project(workspace_id, user_id)
    if not connection:
        return jsonify({'error': 'Connect Google Search Console first.'}), 409
    property_url = connection.get('selected_property')
    if not property_url:
        return jsonify({'error': 'Choose a Search Console property first.'}), 409
    body = request.get_json(silent=True) or {}
    end_day = date.today() - timedelta(days=3)
    start_day = end_day - timedelta(days=27)
    try:
        requested_start = date.fromisoformat(body.get('start_date')) if body.get('start_date') else start_day
        requested_end = date.fromisoformat(body.get('end_date')) if body.get('end_date') else end_day
    except ValueError:
        return jsonify({'error': 'Search Console dates must use YYYY-MM-DD.'}), 400
    if requested_end < requested_start or (requested_end - requested_start).days > 365:
        return jsonify({'error': 'Choose a valid date range of at most 366 days.'}), 400
    now = datetime.utcnow()
    with engine.begin() as conn:
        result = conn.execute(insert(gsc_sync_runs).values(
            workspace_id=workspace_id, connection_id=connection['id'], property_url=property_url,
            status='running', start_date=requested_start.isoformat(), end_date=requested_end.isoformat(),
            rows_saved=0, data_state='final', error=None, created_at=now, completed_at=None,
        ))
        sync_id = result.inserted_primary_key[0]
    try:
        access_token = refresh_google_access_token(connection)
        row_limit = max(1, min(int(os.environ.get('GSC_ROW_LIMIT', '2500')), 25_000))
        payload = external_json_request(
            f"https://www.googleapis.com/webmasters/v3/sites/{quote(property_url, safe='')}/searchAnalytics/query",
            method='POST', headers={'Authorization': f'Bearer {access_token}'}, payload={
                'startDate': requested_start.isoformat(), 'endDate': requested_end.isoformat(),
                'dimensions': ['query', 'page'], 'type': 'web', 'aggregationType': 'auto',
                'rowLimit': row_limit, 'startRow': 0, 'dataState': 'final',
            }, timeout=45,
        )
        query_rows = []
        for row in payload.get('rows') or []:
            keys = row.get('keys') or []
            query_rows.append({
                'sync_run_id': sync_id, 'query': (keys[0] if keys else '(unknown)')[:2000],
                'page': (keys[1] if len(keys) > 1 else None),
                'clicks': float(row.get('clicks', 0)), 'impressions': float(row.get('impressions', 0)),
                'ctr': float(row.get('ctr', 0)), 'position': float(row.get('position', 0)),
            })
        with engine.begin() as conn:
            if query_rows:
                conn.execute(insert(gsc_query_rows), query_rows)
            conn.execute(update(gsc_sync_runs).where(gsc_sync_runs.c.id == sync_id).values(
                status='succeeded', rows_saved=len(query_rows), completed_at=datetime.utcnow(),
            ))
            conn.execute(update(gsc_connections).where(gsc_connections.c.id == connection['id']).values(
                status='connected', last_error=None, updated_at=datetime.utcnow(),
            ))
        return jsonify({'status': 'success', 'search_console': gsc_report(workspace_id, user_id)})
    except (ProviderAPIError, RuntimeError) as error:
        with engine.begin() as conn:
            conn.execute(update(gsc_sync_runs).where(gsc_sync_runs.c.id == sync_id).values(
                status='failed', error=str(error)[:2000], completed_at=datetime.utcnow(),
            ))
            conn.execute(update(gsc_connections).where(gsc_connections.c.id == connection['id']).values(
                status='error', last_error=str(error)[:2000], updated_at=datetime.utcnow(),
            ))
        return jsonify({'error': str(error), 'search_console': gsc_report(workspace_id, user_id)}), 502
