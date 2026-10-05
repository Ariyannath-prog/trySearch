"""Brand sentiment: the report endpoint and the classification trigger."""

from flask import Blueprint, jsonify, request
from sqlalchemy import desc, select

from app.costs import ceiling_status, refusal_payload
from app.db import engine
from app.jobs import create_analytics_job
from app.metrics import sentiment_intelligence
from app.models import analytics_audit_jobs
from app.tenancy import require_workspace
from app.utils import row_to_dict

sentiment_bp = Blueprint('sentiment', __name__)


def _active_sentiment_job(workspace_id, conn):
    return conn.execute(select(analytics_audit_jobs).where(
        (analytics_audit_jobs.c.workspace_id == workspace_id) &
        (analytics_audit_jobs.c.job_type == 'sentiment_classification') &
        (analytics_audit_jobs.c.status.in_(['queued', 'running']))
    ).order_by(desc(analytics_audit_jobs.c.created_at)).limit(1)).mappings().first()


@sentiment_bp.route('/api/analytics/projects/<int:workspace_id>/sentiment', methods=['GET'])
def sentiment_report_endpoint(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    with engine.connect() as conn:
        active_job = _active_sentiment_job(workspace_id, conn)
    return jsonify({
        'project': row_to_dict(access.workspace),
        'sentiment': sentiment_intelligence(workspace_id),
        'active_job': row_to_dict(active_job) if active_job else None,
    })


@sentiment_bp.route('/api/analytics/projects/<int:workspace_id>/sentiment/classify', methods=['POST'])
def start_sentiment_classification(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    project = access.workspace
    with engine.connect() as conn:
        active = _active_sentiment_job(workspace_id, conn)
    if active:
        return jsonify({'status': 'accepted', 'job': row_to_dict(active)}), 202
    state, spend, ceiling = ceiling_status(access.org_id)
    if state == 'exceeded':
        return jsonify(refusal_payload(spend, ceiling)), 402
    job_id = create_analytics_job(project, 'sentiment_classification')
    return jsonify({'status': 'accepted', 'job_id': job_id}), 202
