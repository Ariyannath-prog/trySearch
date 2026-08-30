"""Topics, competitors, tracked prompts and scan schedules."""

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
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import os
import re

from app.config import ANALYTICS_MAX_TRACKED_PROMPTS
from app.db import engine
from app.llm import open_model_settings
from app.models import competitors, analytics_scan_schedules, analytics_topics, analytics_tracked_prompts
from app.tenancy import require_workspace
from app.scanning import next_schedule_time
from app.utils import normalise_domain, row_to_dict

prompts_bp = Blueprint('prompts', __name__)

def analytics_tracking_payload(workspace_id):
    with engine.connect() as conn:
        topics = [row_to_dict(row) for row in conn.execute(select(analytics_topics).where(
            analytics_topics.c.workspace_id == workspace_id
        ).order_by(analytics_topics.c.name)).mappings().all()]
        competitor_rows = [row_to_dict(row) for row in conn.execute(select(competitors).where(
            competitors.c.workspace_id == workspace_id
        ).order_by(competitors.c.name)).mappings().all()]
        prompt_rows = conn.execute(select(
            analytics_tracked_prompts,
            analytics_topics.c.name.label('topic_name'),
        ).outerjoin(
            analytics_topics, analytics_tracked_prompts.c.topic_id == analytics_topics.c.id
        ).where(analytics_tracked_prompts.c.workspace_id == workspace_id)
            .order_by(desc(analytics_tracked_prompts.c.active), analytics_tracked_prompts.c.created_at)).mappings().all()
        prompts = [row_to_dict(row) for row in prompt_rows]
        schedule = conn.execute(select(analytics_scan_schedules).where(
            analytics_scan_schedules.c.workspace_id == workspace_id
        )).mappings().first()
    open_model = open_model_settings()
    return {
        'topics': topics, 'competitors': competitor_rows, 'prompts': prompts,
        'schedule': row_to_dict(schedule) if schedule else None,
        'providers': {
            'perplexity': {
                'configured': bool(os.environ.get('PERPLEXITY_API_KEY')),
                'model': f"Agent preset: {os.environ.get('PERPLEXITY_AGENT_PRESET', 'low')}",
            },
            'open_model': {
                'configured': open_model['configured'],
                'provider': open_model['provider'],
                'model': open_model['model'],
                'purpose': 'Evidence-grounded opportunity summaries only',
            },
        },
    }

@prompts_bp.route('/api/analytics/projects/<int:workspace_id>/tracking', methods=['GET'])
def analytics_tracking_endpoint(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    project = access.workspace
    return jsonify({'project': row_to_dict(project), 'tracking': analytics_tracking_payload(workspace_id)})

@prompts_bp.route('/api/analytics/projects/<int:workspace_id>/topics', methods=['POST'])
def create_analytics_topic(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    name = ((request.get_json(silent=True) or {}).get('name') or '').strip()
    if not name or len(name) > 180:
        return jsonify({'error': 'Enter a topic between 1 and 180 characters.'}), 400
    try:
        with engine.begin() as conn:
            result = conn.execute(insert(analytics_topics).values(
                workspace_id=workspace_id, name=name, created_at=datetime.utcnow(),
            ))
    except IntegrityError:
        return jsonify({'error': 'That topic is already tracked.'}), 409
    with engine.connect() as conn:
        row = conn.execute(select(analytics_topics).where(analytics_topics.c.id == result.inserted_primary_key[0])).mappings().first()
    return jsonify({'topic': row_to_dict(row)}), 201

@prompts_bp.route('/api/analytics/projects/<int:workspace_id>/topics/<int:topic_id>', methods=['DELETE'])
def delete_analytics_topic(workspace_id, topic_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    with engine.begin() as conn:
        exists = conn.execute(select(analytics_topics.c.id).where(
            (analytics_topics.c.id == topic_id) & (analytics_topics.c.workspace_id == workspace_id)
        )).scalar_one_or_none()
        if not exists:
            return jsonify({'error': 'Topic not found.'}), 404
        conn.execute(update(analytics_tracked_prompts).where(
            analytics_tracked_prompts.c.topic_id == topic_id
        ).values(topic_id=None, updated_at=datetime.utcnow()))
        conn.execute(analytics_topics.delete().where(analytics_topics.c.id == topic_id))
    return jsonify({'status': 'success'})

@prompts_bp.route('/api/analytics/projects/<int:workspace_id>/competitors', methods=['POST'])
def create_analytics_competitor(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    domain_value = (data.get('domain') or '').strip()
    domain = normalise_domain(domain_value) if domain_value else None
    if not name or len(name) > 180:
        return jsonify({'error': 'Enter a competitor name between 1 and 180 characters.'}), 400
    if domain_value and not domain:
        return jsonify({'error': 'Enter a valid competitor domain or leave it blank.'}), 400
    try:
        with engine.begin() as conn:
            result = conn.execute(insert(competitors).values(
                workspace_id=workspace_id, name=name,
                domains=[domain] if domain else [], aliases=[],
                created_at=datetime.utcnow(),
            ))
    except IntegrityError:
        return jsonify({'error': 'That competitor is already tracked.'}), 409
    with engine.connect() as conn:
        row = conn.execute(select(competitors).where(
            competitors.c.id == result.inserted_primary_key[0]
        )).mappings().first()
    return jsonify({'competitor': row_to_dict(row)}), 201

@prompts_bp.route('/api/analytics/projects/<int:workspace_id>/competitors/<int:competitor_id>', methods=['DELETE'])
def delete_analytics_competitor(workspace_id, competitor_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    with engine.begin() as conn:
        result = conn.execute(competitors.delete().where(
            (competitors.c.id == competitor_id) &
            (competitors.c.workspace_id == workspace_id)
        ))
    if not result.rowcount:
        return jsonify({'error': 'Competitor not found.'}), 404
    return jsonify({'status': 'success'})

@prompts_bp.route('/api/analytics/projects/<int:workspace_id>/tracked-prompts', methods=['POST'])
def create_analytics_tracked_prompt(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    intent = (data.get('intent') or 'Discovery').strip()[:80] or 'Discovery'
    try:
        topic_id = int(data['topic_id']) if data.get('topic_id') else None
    except (TypeError, ValueError):
        return jsonify({'error': 'Choose a valid topic.'}), 400
    if len(prompt) < 8 or len(prompt) > 1000:
        return jsonify({'error': 'Enter a prompt between 8 and 1,000 characters.'}), 400
    if topic_id:
        with engine.connect() as conn:
            topic = conn.execute(select(analytics_topics.c.id).where(
                (analytics_topics.c.id == topic_id) & (analytics_topics.c.workspace_id == workspace_id)
            )).scalar_one_or_none()
        if not topic:
            return jsonify({'error': 'The selected topic does not belong to this project.'}), 400
    now = datetime.utcnow()
    with engine.begin() as conn:
        prompt_count = conn.execute(select(func.count()).select_from(analytics_tracked_prompts).where(
            analytics_tracked_prompts.c.workspace_id == workspace_id
        )).scalar_one()
        if prompt_count >= ANALYTICS_MAX_TRACKED_PROMPTS:
            return jsonify({'error': f'This project has reached its {ANALYTICS_MAX_TRACKED_PROMPTS}-prompt storage limit.'}), 409
        result = conn.execute(insert(analytics_tracked_prompts).values(
            workspace_id=workspace_id, topic_id=topic_id, prompt=prompt, intent=intent,
            active=True, created_at=now, updated_at=now,
        ))
    return jsonify({'prompt_id': result.inserted_primary_key[0]}), 201

@prompts_bp.route('/api/analytics/projects/<int:workspace_id>/tracked-prompts/<int:prompt_id>', methods=['PATCH', 'DELETE'])
def update_analytics_tracked_prompt(workspace_id, prompt_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    with engine.connect() as conn:
        prompt = conn.execute(select(analytics_tracked_prompts).where(
            (analytics_tracked_prompts.c.id == prompt_id) &
            (analytics_tracked_prompts.c.workspace_id == workspace_id)
        )).mappings().first()
    if not prompt:
        return jsonify({'error': 'Prompt not found.'}), 404
    if request.method == 'DELETE':
        with engine.begin() as conn:
            conn.execute(analytics_tracked_prompts.delete().where(analytics_tracked_prompts.c.id == prompt_id))
        return jsonify({'status': 'success'})
    data = request.get_json(silent=True) or {}
    values = {'updated_at': datetime.utcnow()}
    if 'active' in data:
        values['active'] = bool(data['active'])
    if 'prompt' in data:
        prompt_text = (data.get('prompt') or '').strip()
        if len(prompt_text) < 8 or len(prompt_text) > 1000:
            return jsonify({'error': 'Enter a prompt between 8 and 1,000 characters.'}), 400
        values['prompt'] = prompt_text
    with engine.begin() as conn:
        conn.execute(update(analytics_tracked_prompts).where(analytics_tracked_prompts.c.id == prompt_id).values(**values))
    return jsonify({'status': 'success'})

@prompts_bp.route('/api/analytics/projects/<int:workspace_id>/scan-schedule', methods=['PUT'])
def update_analytics_scan_schedule(workspace_id):
    access, error = require_workspace(workspace_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled'))
    frequency = (data.get('frequency') or 'weekly').lower()
    region = (data.get('region') or '').strip().upper()[:2] or None
    if frequency not in {'daily', 'weekly', 'monthly'}:
        return jsonify({'error': 'Frequency must be daily, weekly, or monthly.'}), 400
    if region and not re.fullmatch(r'[A-Z]{2}', region):
        return jsonify({'error': 'Region must be a two-letter country code.'}), 400
    now = datetime.utcnow()
    with engine.begin() as conn:
        existing = conn.execute(select(analytics_scan_schedules.c.id).where(
            analytics_scan_schedules.c.workspace_id == workspace_id
        )).scalar_one_or_none()
        values = dict(
            enabled=enabled, frequency=frequency, region=region,
            next_run_at=next_schedule_time(frequency, now) if enabled else None,
            updated_at=now,
        )
        if existing:
            conn.execute(update(analytics_scan_schedules).where(analytics_scan_schedules.c.id == existing).values(**values))
        else:
            conn.execute(insert(analytics_scan_schedules).values(
                workspace_id=workspace_id, last_run_at=None, created_at=now, **values,
            ))
    return jsonify({'tracking': analytics_tracking_payload(workspace_id)})
