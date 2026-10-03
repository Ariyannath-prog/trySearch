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
from app.ratelimit import (
    RateLimitExceeded,
    check as record_rate_limit_hit,
    clear as clear_rate_limit,
    client_identity,
    enforce,
    enforce_peek,
    refusal_response,
)
from app.security import issue_token, rotate_token
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

    # Normalised once, here, so it matches the unique index on lower(email). Without
    # this, 'A@x.com' and 'a@x.com' were two accounts; now the second attempt is a
    # clean duplicate error instead of a second identity.
    email = email.lower()

    try:
        enforce('signup', client_identity(request), subject=email)
    except RateLimitExceeded as error:
        return refusal_response(error)

    password_hash = generate_password_hash(password)
    created_at = datetime.utcnow()
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(users).values(username=username, email=email, password_hash=password_hash, created_at=created_at)
            )
    except IntegrityError:
        return jsonify({'error': 'User with that username or email already exists.'}), 400

    # Phase A keeps this endpoint's existing contract. Email verification, terms
    # capture and the verified-only onboarding gate arrive in Phase C, against the
    # columns and token table the Phase A migrations add.
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

    # Rate limited per caller *and* per submitted identifier, so one address cannot
    # be ground through a password list and one client cannot spray many accounts.
    #
    # Only failures are counted. The check here does not record an attempt; a hit is
    # recorded below when the credentials are wrong, and the bucket is cleared on
    # success. Charging a correct sign-in against the limit would lock out a real
    # person using several devices while doing nothing extra to slow a guessing
    # script, which produces nothing but failures.
    identity = client_identity(request)
    subject = username.lower()
    try:
        enforce_peek('login', identity, subject=subject)
    except RateLimitExceeded as error:
        return refusal_response(error)

    def reject_credentials():
        record_rate_limit_hit('login', identity, subject=subject)
        return jsonify({'error': 'Invalid credentials'}), 401

    with engine.connect() as conn:
        stmt = select(
            users.c.id,
            users.c.username,
            users.c.password_hash,
            users.c.is_active,
            users.c.email_verified_at,
        ).where(
            # Email comparison is case-insensitive, matching the unique index on
            # lower(email); username stays exact.
            (users.c.username == username) | (func.lower(users.c.email) == username.lower())
        ).limit(1)
        row = conn.execute(stmt).mappings().first()
        if not row:
            return reject_credentials()
        user = dict(row)
        if not check_password_hash(user['password_hash'], password):
            return reject_credentials()

    # A deactivated account must not be able to sign in. Without this check the
    # admin "deactivate user" control only hid the user from admin_auth, while the
    # ordinary session login still worked - the control was not actually enforcing
    # anything. Deliberately checked after the password, so the response does not
    # reveal which accounts exist but are suspended.
    if not user['is_active']:
        return jsonify({
            'error': 'This account has been deactivated. Contact support.',
            'code': 'account_inactive',
        }), 403

    # login success: the password was right, so prior failures are forgiven.
    clear_rate_limit('login', identity, subject=subject)

    session.clear()
    session['user_id'] = user['id']
    session['username'] = user['username']
    session.permanent = remember
    # A new session gets a new CSRF token: the pre-login token must not stay valid
    # across the privilege change (session fixation).
    rotate_token()

    with engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == user['id']).values(
            last_login_at=datetime.utcnow()))

    return jsonify({
        'status': 'success', 'message': 'Logged in', 'username': user['username'],
        # The client needs to know where to send the user next. Login still works
        # for an unverified account by design; onboarding is what is gated.
        'email_verified': user['email_verified_at'] is not None,
    })

@auth_bp.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    # The next request issues a fresh token; the old one dies with the session.
    return jsonify({'status': 'success', 'message': 'Logged out'})

@auth_bp.route('/api/me', methods=['GET'])
def api_me():
    """Identity plus the CSRF token.

    Every authenticated page already calls this on load, so it is where the browser
    picks up its token - one round trip rather than a second bootstrap request.
    `csrf_token` is returned for anonymous callers too, because the login and signup
    forms need one before a user exists.
    """
    token = issue_token()
    user_id = session.get('user_id')
    if user_id:
        with engine.connect() as conn:
            stmt = select(
                users.c.id,
                users.c.username,
                users.c.email,
                users.c.created_at,
                users.c.email_verified_at,
                users.c.is_active,
                users.c.is_platform_admin,
            ).where(users.c.id == user_id).limit(1)
            row = conn.execute(stmt).mappings().first()
        if row:
            user = row_to_dict(row)
            # A session that outlived a deactivation must not keep working.
            if not user.get('is_active'):
                session.clear()
                return jsonify({'logged_in': False, 'csrf_token': issue_token()})
            return jsonify({
                'logged_in': True,
                'user': user,
                'email_verified': user.get('email_verified_at') is not None,
                'csrf_token': token,
            })
        session.clear()
    return jsonify({'logged_in': False, 'csrf_token': token})


@auth_bp.route('/api/csrf-token', methods=['GET'])
def api_csrf_token():
    """Standalone token fetch, for a client that has not called /api/me."""
    return jsonify({'csrf_token': issue_token()})

def analytics_user_id():
    """Return the current account id, or an API response for unauthenticated calls."""
    user_id = session.get('user_id')
    if not user_id:
        return None, (jsonify({'error': 'Sign in to use AI Search Analytics.'}), 401)
    return user_id, None
