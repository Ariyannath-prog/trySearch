"""Platform-admin authorization helpers."""

from flask import jsonify, redirect, session
from sqlalchemy import select

from app.db import engine
from app.models import users


def get_platform_admin():
    """Return the current active platform-admin user, or None."""
    user_id = session.get('user_id')
    if not user_id:
        return None

    with engine.connect() as conn:
        row = conn.execute(
            select(
                users.c.id,
                users.c.username,
                users.c.email,
                users.c.is_platform_admin,
                users.c.is_active,
            )
            .where(users.c.id == user_id)
            .limit(1)
        ).mappings().first()

    if not row:
        return None

    user = dict(row)

    if not user['is_active'] or not user['is_platform_admin']:
        return None

    return user


def require_platform_admin_page():
    """Return a Flask response when access is denied, otherwise None."""
    if not session.get('user_id'):
        return redirect('/login')

    if get_platform_admin() is None:
        return redirect('/')

    return None


def require_platform_admin_api():
    """Return a Flask response when access is denied, otherwise None."""
    if not session.get('user_id'):
        return jsonify({'error': 'Authentication required.'}), 401

    if get_platform_admin() is None:
        return jsonify({'error': 'Platform administrator access required.'}), 403

    return None
