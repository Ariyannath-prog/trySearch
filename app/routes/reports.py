"""Report endpoints.

The share link is deliberately its own blueprint with no tenancy guard, because
it authenticates by token rather than by session. That makes it the one surface
where a mistake leaks another tenant's data, so it is GET-only, it resolves the
workspace *from the token* and never from user input, and it reaches no write
path at all.
"""

import json

from flask import Blueprint, jsonify, render_template, request

from app import reports as report_service
from app.db import engine
from app.models import workspace_branding
from app.tenancy import require_workspace
from datetime import datetime
from sqlalchemy import insert, select, update

reports_bp = Blueprint('reports', __name__)


@reports_bp.route('/api/analytics/projects/<int:workspace_id>/report/preview',
                  methods=['GET'])
def report_preview(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    sections = request.args.getlist('section') or None
    report = report_service.build_report(workspace_id, sections=sections)
    if report is None:
        return jsonify({'error': 'Workspace not found.'}), 404
    return jsonify({'report': report})


@reports_bp.route('/api/analytics/projects/<int:workspace_id>/report/branding',
                  methods=['GET', 'PUT'])
def report_branding(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error

    if request.method == 'GET':
        with engine.connect() as conn:
            return jsonify({'branding': report_service.branding_for(workspace_id, conn)})

    data = request.get_json(silent=True) or {}
    now = datetime.utcnow()
    values = {
        'display_name': (data.get('display_name') or None),
        'logo_url': (data.get('logo_url') or None),
        'accent_colour': (data.get('accent_colour') or None),
        # Not client-settable: the plan decides whether the mark can be hidden.
        'updated_at': now,
    }
    with engine.begin() as conn:
        exists = conn.execute(
            select(workspace_branding.c.workspace_id)
            .where(workspace_branding.c.workspace_id == workspace_id)
        ).scalar_one_or_none()
        if exists:
            conn.execute(update(workspace_branding)
                         .where(workspace_branding.c.workspace_id == workspace_id)
                         .values(**values))
        else:
            conn.execute(insert(workspace_branding).values(
                workspace_id=workspace_id, hide_trysearch_mark=False,
                created_at=now, **values))

    with engine.connect() as conn:
        return jsonify({'branding': report_service.branding_for(workspace_id, conn)})


@reports_bp.route('/api/analytics/projects/<int:workspace_id>/report/shares',
                  methods=['POST'])
def create_report_share(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    sections = data.get('sections') or None
    share = report_service.create_share(
        workspace_id, sections=sections, days=int(data.get('days') or 30))
    share['url'] = f"/reports/shared/{share['token']}"
    return jsonify({'share': share}), 201


@reports_bp.route('/api/analytics/projects/<int:workspace_id>/report/shares/<token>',
                  methods=['DELETE'])
def revoke_report_share(workspace_id, token):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    share = report_service.resolve_share(token)
    if not share or share['workspace_id'] != workspace_id:
        return jsonify({'error': 'Share link not found.'}), 404
    report_service.revoke_share(token)
    return jsonify({'status': 'revoked'})


# --- the public surface ------------------------------------------------------

@reports_bp.route('/api/reports/shared/<token>', methods=['GET'])
def shared_report(token):
    """Read-only, no session. GET only, and the workspace comes from the token.

    Nothing here accepts a workspace id from the caller, so a token can only ever
    reach the one workspace it was minted for.
    """
    share = report_service.resolve_share(token)
    if not share:
        # Same answer for missing, revoked and expired: a live token must not be
        # distinguishable from a dead one by probing.
        return jsonify({'error': 'This report link is not available.'}), 404

    sections = json.loads(share['sections']) if share.get('sections') else None
    report = report_service.build_report(share['workspace_id'], sections=sections)
    if report is None:
        return jsonify({'error': 'This report link is not available.'}), 404

    report['shared'] = True
    return jsonify({'report': report})


@reports_bp.route('/reports/shared/<token>', methods=['GET'])
def shared_report_page(token):
    """The rendered report. This is the artifact that actually gets sent.

    PRD §6b: the share link IS the pitch, so it has to look right unbranded. Same
    read-only rules as the JSON surface - GET only, workspace from the token.
    """
    share = report_service.resolve_share(token)
    if not share:
        return render_template('report_unavailable.html'), 404

    sections = json.loads(share['sections']) if share.get('sections') else None
    report = report_service.build_report(share['workspace_id'], sections=sections)
    if report is None:
        return render_template('report_unavailable.html'), 404

    title = (report['branding'].get('display_name')
             or report['workspace'].get('brand_name') or 'Report')
    return render_template('report.html', report=report, title=title)
