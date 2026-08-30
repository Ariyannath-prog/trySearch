"""Site audits, job status and the RAG endpoint."""

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
import re

from app.auth import analytics_user_id
from app.db import engine
from app.jobs import create_analytics_job, latest_site_audit
from app.models import analytics_audit_jobs, analytics_site_audits
from app.tenancy import require_job, require_workspace
from app.rag.answers import create_rag_insight
from app.rag.index import rag_index_summary
from app.rag.ranking import retrieve_audit_chunks
from app.utils import row_to_dict, to_iso

audit_bp = Blueprint('audit', __name__)

@audit_bp.route('/api/analytics/projects/<int:workspace_id>/audits', methods=['POST'])
def start_site_audit(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    user_id, project = access.user_id, access.workspace
    with engine.connect() as conn:
        active = conn.execute(select(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.workspace_id == workspace_id) &
            (analytics_audit_jobs.c.job_type == 'site_audit') &
            (analytics_audit_jobs.c.status.in_(['queued', 'running']))
        ).order_by(desc(analytics_audit_jobs.c.created_at)).limit(1)).mappings().first()
    if active:
        return jsonify({'status': 'accepted', 'job': row_to_dict(active)}), 202
    # Enqueue only; the CLI worker runs the crawl.
    job_id = create_analytics_job(project, 'site_audit')
    return jsonify({'status': 'accepted', 'job_id': job_id}), 202

@audit_bp.route('/api/analytics/jobs/<int:job_id>', methods=['GET'])
def analytics_job_status(job_id):
    _access, job, error = require_job(job_id)
    if error:
        return error
    return jsonify({'job': row_to_dict(job)})

@audit_bp.route('/api/analytics/projects/<int:workspace_id>/audit', methods=['GET'])
def site_audit_report_endpoint(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    user_id, project = access.user_id, access.workspace
    audit = latest_site_audit(workspace_id)
    with engine.connect() as conn:
        active_job = conn.execute(select(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.workspace_id == workspace_id) &
            (analytics_audit_jobs.c.job_type == 'site_audit') &
            (analytics_audit_jobs.c.status.in_(['queued', 'running']))
        ).order_by(desc(analytics_audit_jobs.c.created_at)).limit(1)).mappings().first()
    return jsonify({
        'project': row_to_dict(project), 'audit': audit,
        'active_job': row_to_dict(active_job) if active_job else None,
    })

def rag_audit_for_project(workspace_id, audit_id=None):
    with engine.connect() as conn:
        statement = select(analytics_site_audits).where(
            analytics_site_audits.c.workspace_id == workspace_id
        )
        if audit_id is not None:
            statement = statement.where(analytics_site_audits.c.id == audit_id)
        return conn.execute(
            statement.order_by(desc(analytics_site_audits.c.created_at)).limit(1)
        ).mappings().first()

def rag_retrieval_payload(rows):
    payload = []
    for row in rows:
        excerpt = re.sub(r'\s+', ' ', row.get('content_text') or '').strip()
        if len(excerpt) > 1200:
            excerpt = excerpt[:1197].rstrip() + '...'
        payload.append({
            'evidence_ref': row['evidence_ref'], 'score': row['score'],
            'url': row.get('document_url'), 'title': row.get('document_title'),
            'chunk_index': row.get('chunk_index'), 'excerpt': excerpt,
        })
    return payload

@audit_bp.route('/api/v1/analytics/projects/<int:workspace_id>/rag', methods=['GET', 'POST'])
def analytics_rag_endpoint(workspace_id):
    """Retrieve or synthesize crawl-grounded insights without changing measured metrics."""
    access, error = require_workspace(workspace_id)
    if error:
        return error
    project = access.workspace
    data = (request.get_json(silent=True) or {}) if request.method == 'POST' else {}
    if not isinstance(data, dict):
        return jsonify({'error': 'The request body must be a JSON object.'}), 400
    raw_audit_id = data.get('audit_id') if request.method == 'POST' else request.args.get('audit_id')
    try:
        audit_id = int(raw_audit_id) if raw_audit_id is not None and raw_audit_id != '' else None
    except (TypeError, ValueError):
        return jsonify({'error': 'audit_id must be an integer.'}), 400
    audit = rag_audit_for_project(workspace_id, audit_id)
    if not audit:
        return jsonify({'error': 'Run a successful website audit before using crawl-grounded RAG.'}), 404
    summary = rag_index_summary(audit['id'])
    if not summary['chunks_indexed']:
        return jsonify({'error': 'The selected audit contains no indexable public page copy.', 'rag': summary}), 409

    if request.method == 'POST':
        question = re.sub(r'\s+', ' ', str(data.get('question') or '')).strip()
        if not question:
            return jsonify({'error': 'Enter a question about the audited website.'}), 400
        if len(question) > 1000:
            return jsonify({'error': 'The RAG question must be 1,000 characters or fewer.'}), 400
        insight = create_rag_insight(dict(project), audit['id'], question)
        if not insight:
            return jsonify({'error': 'No relevant crawl evidence was found for that question.', 'rag': summary}), 422
        return jsonify({
            'status': 'success', 'audit': {
                'id': audit['id'], 'created_at': to_iso(audit['created_at']),
                'source_type': audit['source_type'],
            },
            'rag': {**rag_index_summary(audit['id']), 'generated_insight': insight},
        }), 201

    query = re.sub(r'\s+', ' ', request.args.get('query', '')).strip()
    if len(query) > 1000:
        return jsonify({'error': 'The retrieval query must be 1,000 characters or fewer.'}), 400
    retrieval = retrieve_audit_chunks(audit['id'], query) if query else []
    return jsonify({
        'project': row_to_dict(project),
        'audit': {
            'id': audit['id'], 'status': audit['status'],
            'created_at': to_iso(audit['created_at']), 'source_type': audit['source_type'],
        },
        'rag': {**summary, 'query': query or None, 'retrieval': rag_retrieval_payload(retrieval)},
    })
