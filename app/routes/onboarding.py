"""Onboarding: domain in, reviewed prompt set out.

Two endpoints on purpose. `preview` generates and returns a profile and writes
nothing; `approve` persists what the user actually approved. Nothing scans in
either - the first run is a separate, explicit action, because an unapproved
prompt set is a bad first impression and a scan costs money.
"""

from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import insert, select

from app import onboarding as onboarding_service
from app.db import engine
from app.engines.gemini import call_gemini_text
from app.http_client import ProviderAPIError
from app.models import (
    analytics_tracked_prompts,
    brand_aliases,
    competitors as competitors_table,
    workspaces,
)
from app.tenancy import current_user_id, default_org_for_user, require_workspace
from app.utils import normalise_domain, row_to_dict

onboarding_bp = Blueprint('onboarding', __name__)


@onboarding_bp.route('/api/onboarding/preview', methods=['POST'])
def preview_onboarding_profile():
    """Generate a profile for review. Writes nothing, scans nothing."""
    user_id, error = current_user_id()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    raw_domain = (data.get('domain') or '').strip()
    domain = normalise_domain(raw_domain)
    if not domain:
        return jsonify({'error': 'Enter a valid website domain.'}), 400

    try:
        html = onboarding_service.fetch_homepage(f'https://{domain}')
    except Exception as fetch_error:  # noqa: BLE001 - any fetch failure is the same
        return jsonify({
            'error': f'Could not read {domain}: {fetch_error}',
            'fallback': 'manual',
        }), 502

    try:
        profile = onboarding_service.generate_profile(
            domain, onboarding_service.visible_text(html),
            call_model=call_gemini_text,
        )
    except (onboarding_service.OnboardingError, ProviderAPIError) as error:
        # Manual entry is the fallback, not a silent half-profile.
        return jsonify({'error': str(error), 'fallback': 'manual'}), 502

    profile['domain'] = domain
    return jsonify({'profile': profile})


@onboarding_bp.route('/api/onboarding/approve', methods=['POST'])
def approve_onboarding_profile():
    """Persist the reviewed profile. Still does not scan."""
    user_id, error = current_user_id()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    profile = data.get('profile') or {}
    domain = normalise_domain(profile.get('domain') or data.get('domain') or '')
    brand_name = (profile.get('brand_name') or '').strip()
    prompts = profile.get('prompts') or []

    if not domain or not brand_name:
        return jsonify({'error': 'A domain and brand name are required.'}), 400
    if not prompts:
        return jsonify({'error': 'Approve at least one prompt.'}), 400

    workspace_id = data.get('workspace_id')
    if workspace_id:
        access, guard_error = require_workspace(workspace_id)
        if guard_error:
            return guard_error
        workspace_id = access.workspace['id']
    else:
        org_id = default_org_for_user(user_id)
        now = datetime.utcnow()
        with engine.begin() as conn:
            workspace_id = conn.execute(insert(workspaces).values(
                org_id=org_id, brand_name=brand_name[:150],
                domains=[domain] + list(profile.get('domains') or []),
                geo='US', language='en', kind='project', status='active',
                created_at=now, updated_at=now,
                domain=domain, website_url=f'https://{domain}/',
                industry=(profile.get('industry') or 'General')[:150],
            )).inserted_primary_key[0]

    now = datetime.utcnow()
    with engine.begin() as conn:
        for alias in onboarding_service.drop_substring_aliases(
                brand_name, profile.get('aliases') or []):
            conn.execute(insert(brand_aliases).values(
                workspace_id=workspace_id, alias=alias))

        for competitor in (profile.get('competitors') or [])[:onboarding_service.MAX_COMPETITORS]:
            name = (competitor.get('name') or '').strip()
            if not name:
                continue
            conn.execute(insert(competitors_table).values(
                workspace_id=workspace_id, name=name,
                domains=list(competitor.get('domains') or []),
                aliases=list(competitor.get('aliases') or []),
                created_at=now,
            ))

        for prompt in prompts:
            text = onboarding_service.normalise_prompt(prompt.get('text'))
            if not text:
                continue
            conn.execute(insert(analytics_tracked_prompts).values(
                workspace_id=workspace_id, topic_id=None, prompt=text,
                intent=(prompt.get('category') or 'discovery').title()[:80],
                active=True, created_at=now, updated_at=now,
            ))

    with engine.connect() as conn:
        stored = conn.execute(select(workspaces).where(
            workspaces.c.id == workspace_id)).mappings().first()
        prompt_count = len(conn.execute(select(analytics_tracked_prompts.c.id).where(
            analytics_tracked_prompts.c.workspace_id == workspace_id)).all())

    return jsonify({
        'status': 'approved',
        'workspace': row_to_dict(stored),
        'prompt_count': prompt_count,
        # Explicitly not started. The client asks for the first scan separately.
        'scan_started': False,
    }), 201
