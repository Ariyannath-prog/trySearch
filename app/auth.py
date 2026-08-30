"""Session auth: register, login, logout, me."""

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
from werkzeug.security import generate_password_hash, check_password_hash

from app.db import engine
from app.models import users
from app.utils import row_to_dict

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid payload'}), 400
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not email or not password:
        return jsonify({'error': 'username, email and password required'}), 400

    password_hash = generate_password_hash(password)
    created_at = datetime.utcnow()
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(users).values(username=username, email=email, password_hash=password_hash, created_at=created_at)
            )
    except IntegrityError:
        return jsonify({'error': 'User with that username or email already exists.'}), 400

    return jsonify({'status': 'success', 'message': 'User registered.'}), 201

@auth_bp.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid payload'}), 400
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    remember = bool(data.get('remember'))

    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400

    with engine.connect() as conn:
        stmt = select(users.c.id, users.c.username, users.c.password_hash).where((users.c.username == username) | (users.c.email == username)).limit(1)
        row = conn.execute(stmt).mappings().first()
        if not row:
            return jsonify({'error': 'Invalid credentials'}), 401
        user = dict(row)
        if not check_password_hash(user['password_hash'], password):
            return jsonify({'error': 'Invalid credentials'}), 401

    # login success
    session.clear()
    session['user_id'] = user['id']
    session['username'] = user['username']
    session.permanent = remember
    return jsonify({'status': 'success', 'message': 'Logged in', 'username': user['username']})

@auth_bp.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'status': 'success', 'message': 'Logged out'})

@auth_bp.route('/api/me', methods=['GET'])
def api_me():
    user_id = session.get('user_id')
    if user_id:
        with engine.connect() as conn:
            stmt = select(users.c.id, users.c.username, users.c.email, users.c.created_at).where(
                users.c.id == user_id
            ).limit(1)
            row = conn.execute(stmt).mappings().first()
        if row:
            return jsonify({'logged_in': True, 'user': row_to_dict(row)})
        session.clear()
    return jsonify({'logged_in': False})

def analytics_user_id():
    """Return the current account id, or an API response for unauthenticated calls."""
    user_id = session.get('user_id')
    if not user_id:
        return None, (jsonify({'error': 'Sign in to use AI Search Analytics.'}), 401)
    return user_id, None
