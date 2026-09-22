"""Helpers for recording platform-admin actions."""

from datetime import datetime

from app.db import engine
from app.models import admin_audit_logs
from sqlalchemy import insert


def write_admin_audit(
    actor_user_id,
    action,
    *,
    target_type=None,
    target_id=None,
    details=None,
    request=None,
):
    """Persist one platform-admin action."""
    values = {
        'actor_user_id': actor_user_id,
        'action': action,
        'target_type': target_type,
        'target_id': str(target_id) if target_id is not None else None,
        'details': details or {},
        'created_at': datetime.utcnow(),
    }

    if request is not None:
        values['ip_address'] = request.headers.get('CF-Connecting-IP') or request.remote_addr
        values['user_agent'] = (request.user_agent.string or '')[:2000]

    with engine.begin() as conn:
        conn.execute(insert(admin_audit_logs).values(**values))
