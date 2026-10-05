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
    conn=None,
):
    """Persist one platform-admin action.

    Pass `conn` to write the audit row inside the caller's open transaction, which
    is what commercially sensitive changes need: the change and the record of who
    made it either both land or neither does. Omitting it keeps the original
    behaviour of committing on its own connection, so existing callers are
    unaffected.
    """
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

    statement = insert(admin_audit_logs).values(**values)

    if conn is not None:
        conn.execute(statement)
        return

    with engine.begin() as own_conn:
        own_conn.execute(statement)
