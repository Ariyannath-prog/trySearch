"""Prompt scan launch and stored evidence."""

from flask import Blueprint
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
import json
import os

from app.db import engine
from app.costs import ceiling_status, refusal_payload
from app.jobs import create_analytics_job
from app.metrics import latest_prompt_evidence
from app.models import analytics_answer_sources, analytics_audit_jobs, analytics_prompt_scan_runs, analytics_provider_answers, analytics_topics, analytics_tracked_prompts
from app.tenancy import require_workspace
from app.utils import row_to_dict

evidence_bp = Blueprint('evidence', __name__)

@evidence_bp.route('/api/analytics/projects/<int:workspace_id>/prompt-scans', methods=['POST'])
def start_analytics_prompt_scan(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    user_id, project = access.user_id, access.workspace
    if not os.environ.get('PERPLEXITY_API_KEY'):
        return jsonify({'error': 'Perplexity is not configured. Add PERPLEXITY_API_KEY on the server.'}), 503
    with engine.connect() as conn:
        prompt_count = conn.execute(select(func.count()).select_from(analytics_tracked_prompts).where(
            (analytics_tracked_prompts.c.workspace_id == workspace_id) &
            (analytics_tracked_prompts.c.active.is_(True))
        )).scalar_one()
        active = conn.execute(select(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.workspace_id == workspace_id) &
            (analytics_audit_jobs.c.job_type == 'prompt_scan') &
            (analytics_audit_jobs.c.status.in_(['queued', 'running']))
        ).order_by(desc(analytics_audit_jobs.c.created_at)).limit(1)).mappings().first()
    if not prompt_count:
        return jsonify({'error': 'Add at least one active tracked prompt first.'}), 409
    # Refuse before dispatch, so an org over its ceiling never reaches a paid call.
    state, spend, ceiling = ceiling_status(access.org_id)
    if state == 'exceeded':
        return jsonify(refusal_payload(spend, ceiling)), 402
    if active:
        return jsonify({'status': 'accepted', 'job': row_to_dict(active)}), 202
    # Enqueue only. The CLI worker executes it; nothing runs inside the web
    # process, so a scan cannot block a gunicorn thread or die on deploy.
    job_id = create_analytics_job(project, 'prompt_scan', provider='Perplexity',
                             run_type='on_demand')
    return jsonify({'status': 'accepted', 'job_id': job_id}), 202

@evidence_bp.route('/api/analytics/projects/<int:workspace_id>/evidence', methods=['GET'])
def analytics_evidence_endpoint(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    project = access.workspace
    try:
        run_id = int(request.args['run_id']) if request.args.get('run_id') else None
    except ValueError:
        return jsonify({'error': 'run_id must be an integer.'}), 400
    evidence = latest_prompt_evidence(workspace_id, run_id)
    with engine.connect() as conn:
        active_job = conn.execute(select(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.workspace_id == workspace_id) &
            (analytics_audit_jobs.c.job_type == 'prompt_scan') &
            (analytics_audit_jobs.c.status.in_(['queued', 'running']))
        ).order_by(desc(analytics_audit_jobs.c.created_at)).limit(1)).mappings().first()
    return jsonify({
        'project': row_to_dict(project), 'evidence': evidence,
        'active_job': row_to_dict(active_job) if active_job else None,
    })

@evidence_bp.route('/api/analytics/projects/<int:workspace_id>/evidence/<int:answer_id>', methods=['GET'])
def analytics_evidence_detail_endpoint(workspace_id, answer_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    with engine.connect() as conn:
        answer = conn.execute(select(
            analytics_provider_answers,
            func.coalesce(analytics_provider_answers.c.prompt_text, analytics_tracked_prompts.c.prompt).label('resolved_prompt'),
            func.coalesce(analytics_provider_answers.c.prompt_intent, analytics_tracked_prompts.c.intent).label('resolved_intent'),
            func.coalesce(analytics_provider_answers.c.topic_name, analytics_topics.c.name).label('resolved_topic_name'),
        ).join(
            analytics_prompt_scan_runs, analytics_provider_answers.c.scan_run_id == analytics_prompt_scan_runs.c.id
        ).outerjoin(
            analytics_tracked_prompts, analytics_provider_answers.c.prompt_id == analytics_tracked_prompts.c.id
        ).outerjoin(
            analytics_topics, analytics_tracked_prompts.c.topic_id == analytics_topics.c.id
        ).where(
            (analytics_provider_answers.c.id == answer_id) &
            (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
        )).mappings().first()
        if not answer:
            return jsonify({'error': 'Evidence record not found.'}), 404
        sources = [row_to_dict(row) for row in conn.execute(select(analytics_answer_sources).where(
            analytics_answer_sources.c.answer_id == answer_id
        ).order_by(analytics_answer_sources.c.source_kind, analytics_answer_sources.c.rank)).mappings().all()]
    result = row_to_dict(answer)
    result['prompt'] = result.pop('resolved_prompt')
    result['intent'] = result.pop('resolved_intent')
    result['topic_name'] = result.pop('resolved_topic_name')
    try:
        result['raw_response'] = json.loads(result.get('raw_response') or '{}')
    except json.JSONDecodeError:
        result['raw_response'] = {'unparsed': result.get('raw_response')}
    result['sources'] = sources
    return jsonify({'evidence': result})
