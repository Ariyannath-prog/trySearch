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

from app import accounts
from app.db import engine
from app import mailer
from app.onboarding_state import onboarding_state, post_login_destination
from app.mailer import send_password_reset_email, send_verification_email
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

    email_verified = user['email_verified_at'] is not None

    # Where to go next is decided here, not in the page's JavaScript: a user who
    # has not finished onboarding has no workspace for the dashboard to show, and
    # landing them on an empty /analytics is the bug this replaces.
    return jsonify({
        'status': 'success', 'message': 'Logged in', 'username': user['username'],
        # Login still works for an unverified account by design; onboarding is
        # what is gated.
        'email_verified': email_verified,
        'next': post_login_destination(user['id'], email_verified=email_verified),
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
            email_verified = user.get('email_verified_at') is not None
            payload = {
                'logged_in': True,
                'user': user,
                'email_verified': email_verified,
                'csrf_token': token,
            }
            if email_verified:
                # Same derivation the login redirect uses, so a client that
                # refreshes /api/me cannot disagree with it.
                state = onboarding_state(user_id)
                payload['onboarding'] = {
                    'stage': state['stage'],
                    'complete': state['onboarding_complete'],
                    'next': state['next'],
                    'resume': state['resume'],
                    'workspace_id': state['workspace_id'],
                }
            return jsonify(payload)
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


# --- Phase C: signup and email verification ---------------------------------
# /api/register above is kept exactly as it was, for any existing caller. New
# clients use /api/signup, which adds password confirmation, terms capture and a
# verification email.


def _signup_response_for(email):
    """The response body for a signup attempt.

    Identical whether or not the address was already registered. Returning
    "that email is taken" here would turn signup into an account-existence
    oracle; the person who genuinely owns the address learns the truth from
    their inbox instead.
    """
    return {
        'status': 'pending_verification',
        'message': 'Check your email to confirm your address.',
        'email': email,
        'next': '/verify-email',
    }


@auth_bp.route('/api/signup', methods=['POST'])
def api_signup():
    """Create an account and send a verification email."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid payload'}), 400

    try:
        email = accounts.normalise_email(data.get('email'))
    except accounts.SignupError as error:
        return jsonify({'error': error.message, 'field': error.field}), 400

    identity = client_identity(request)
    try:
        # Per-source first: varying the email address must not defeat the limit.
        enforce('signup_ip', identity)
        enforce('signup', identity, subject=email)
    except RateLimitExceeded as error:
        return refusal_response(error)

    try:
        password = accounts.validate_password(
            data.get('password'), data.get('password_confirmation'))
        accounts.validate_terms(data.get('terms_accepted'))
        username_base = accounts.candidate_username(email, data.get('username'))
    except accounts.SignupError as error:
        return jsonify({'error': error.message, 'field': error.field}), 400

    # Refuse before creating anything if we cannot actually deliver the mail.
    # Telling someone to check their inbox when no relay is configured would
    # leave them permanently stuck on an unverifiable account.
    if mailer.delivery_mode() == mailer.MODE_UNCONFIGURED:
        return jsonify({
            'error': 'Account creation is temporarily unavailable. Please try again later.',
            'code': 'email_unconfigured',
        }), 503

    now = datetime.utcnow()
    password_hash = generate_password_hash(password)
    raw_token = None

    with engine.begin() as conn:
        existing = accounts.user_by_email(email, conn=conn)
        if existing:
            # Known address. Create nothing, reveal nothing. If it is still
            # unverified, re-send so the legitimate owner can get in; if it is
            # verified, send nothing at all.
            if existing['email_verified_at'] is None and existing['is_active']:
                raw_token, ttl = accounts.issue_token(
                    existing['id'], ip=identity, now=now, conn=conn)
                resend_email = existing['email']
            else:
                resend_email = None
        else:
            username = accounts.allocate_username(conn, username_base)
            try:
                user_id = conn.execute(insert(users).values(
                    username=username, email=email, password_hash=password_hash,
                    created_at=now, is_platform_admin=False, is_active=True,
                    terms_accepted_at=now,
                    terms_version=accounts.CURRENT_TERMS_VERSION,
                )).inserted_primary_key[0]
            except IntegrityError:
                # Lost a race against a concurrent signup for the same address.
                # Same opaque response as the duplicate path above.
                return jsonify(_signup_response_for(email)), 202
            raw_token, ttl = accounts.issue_token(
                user_id, ip=identity, now=now, conn=conn)
            resend_email = email

    if raw_token and resend_email:
        ok, _mode, _error = send_verification_email(
            to=resend_email,
            verify_url=accounts.verification_url(raw_token),
            expires_hours=ttl,
        )
        if not ok:
            # The account exists but the mail failed. Say so plainly rather than
            # pointing the user at an inbox that will stay empty; /api/resend-verification
            # lets them try again without creating a second account.
            return jsonify({
                'status': 'pending_verification',
                'message': 'Account created, but the confirmation email could not be '
                           'sent. Use the resend option in a moment.',
                'email': email,
                'next': '/verify-email',
                'email_sent': False,
            }), 202

    return jsonify(_signup_response_for(email)), 202


@auth_bp.route('/api/resend-verification', methods=['POST'])
def api_resend_verification():
    """Re-send the verification email.

    Works for the signed-in user, or for an address supplied in the body so
    someone who never got the first mail is not locked out by having no session.
    The response never varies with whether the address exists.
    """
    data = request.get_json(silent=True) or {}
    identity = client_identity(request)

    email = None
    user_id = session.get('user_id')
    if user_id:
        with engine.connect() as conn:
            email = conn.execute(
                select(users.c.email).where(users.c.id == user_id)
            ).scalar_one_or_none()
    if not email:
        try:
            email = accounts.normalise_email(data.get('email'))
        except accounts.SignupError as error:
            return jsonify({'error': error.message, 'field': error.field}), 400

    try:
        # Same two-bucket rule: cap the source as well as the address, so one
        # caller cannot mail-bomb many different inboxes.
        enforce('resend_ip', identity)
        enforce('resend_verification', identity, subject=email.lower())
    except RateLimitExceeded as error:
        return refusal_response(error)

    opaque = jsonify({
        'status': 'sent',
        'message': 'If that address needs confirming, a new link is on its way.',
    })

    if mailer.delivery_mode() == mailer.MODE_UNCONFIGURED:
        return jsonify({
            'error': 'Email delivery is not configured on this server.',
            'code': 'email_unconfigured',
        }), 503

    user = accounts.user_by_email(email)
    if not user or not user['is_active'] or user['email_verified_at'] is not None:
        # Nothing to do. Same body and status as the success path.
        return opaque, 202

    raw_token, ttl = accounts.issue_token(user['id'], ip=identity)
    send_verification_email(
        to=user['email'],
        verify_url=accounts.verification_url(raw_token),
        expires_hours=ttl,
    )
    return opaque, 202


@auth_bp.route('/api/verify-email', methods=['POST'])
def api_verify_email():
    """Spend a verification token.

    On success the user is signed in, because requiring a separate login
    immediately after proving control of the address adds friction without
    adding security, and onboarding is the next step.
    """
    data = request.get_json(silent=True) or {}
    token = data.get('token')

    try:
        enforce('email_verify', client_identity(request))
    except RateLimitExceeded as error:
        return refusal_response(error)

    outcome, user_id = accounts.consume_token(token)

    if outcome == accounts.VERIFY_INVALID:
        return jsonify({
            'error': 'That confirmation link is not valid.',
            'code': outcome,
        }), 400
    if outcome == accounts.VERIFY_EXPIRED:
        return jsonify({
            'error': 'That confirmation link has expired. Request a new one.',
            'code': outcome,
            'can_resend': True,
        }), 410
    if outcome == accounts.VERIFY_USED:
        return jsonify({
            'error': 'That confirmation link has already been used. Request a new one.',
            'code': outcome,
            'can_resend': True,
        }), 410

    with engine.connect() as conn:
        user = conn.execute(
            select(users.c.id, users.c.username, users.c.is_active)
            .where(users.c.id == user_id)
        ).mappings().first()

    if not user or not user['is_active']:
        return jsonify({
            'error': 'This account is not available. Contact support.',
            'code': 'account_inactive',
        }), 403

    # Sign in and start a fresh session, with a new CSRF token for the new
    # privilege level - the same rule /api/login follows.
    session.clear()
    session['user_id'] = user['id']
    session['username'] = user['username']
    rotate_token()

    with engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == user['id'])
                     .values(last_login_at=datetime.utcnow()))

    return jsonify({
        'status': outcome,
        'message': ('Email confirmed.' if outcome == accounts.VERIFY_OK
                    else 'This email was already confirmed.'),
        'email_verified': True,
        # Verification leads into onboarding, never straight to the dashboard:
        # a workspace does not exist yet, so /analytics would have nothing to show.
        'next': '/onboarding',
    })


@auth_bp.route('/api/verification-status', methods=['GET'])
def api_verification_status():
    """Whether the signed-in user still needs to confirm their address."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Authentication required.'}), 401
    with engine.connect() as conn:
        row = conn.execute(
            select(users.c.email, users.c.email_verified_at)
            .where(users.c.id == user_id)
        ).mappings().first()
    if not row:
        session.clear()
        return jsonify({'error': 'Authentication required.'}), 401
    verified = row['email_verified_at'] is not None
    return jsonify({
        'email': row['email'],
        'email_verified': verified,
        'next': '/onboarding' if verified else '/verify-email',
    })


# --- password reset ---------------------------------------------------------
# Reuses the email_verification_tokens table via purpose='password_reset' and the
# existing rate-limit policies, so there is no second token or throttle system.


@auth_bp.route('/api/forgot-password', methods=['POST'])
def api_forgot_password():
    """Start a password reset.

    The response never varies with whether the address exists, is active or is
    verified. A "no such account" reply here would turn this endpoint into a
    membership oracle for anyone with a list of addresses.
    """
    data = request.get_json(silent=True) or {}
    identity = client_identity(request)

    try:
        email = accounts.normalise_email(data.get('email'))
    except accounts.SignupError as error:
        # Even a malformed address gets the neutral reply, so probing cannot
        # distinguish "invalid" from "unknown".
        return jsonify({
            'status': 'sent',
            'message': 'If that address has an account, a reset link is on its way.',
        }), 202

    try:
        # Per-source first, so varying the address does not defeat the limit.
        enforce('password_reset_ip', identity)
        enforce('password_reset', identity, subject=email)
    except RateLimitExceeded as error:
        return refusal_response(error)

    opaque = jsonify({
        'status': 'sent',
        'message': 'If that address has an account, a reset link is on its way.',
    })

    if mailer.delivery_mode() == mailer.MODE_UNCONFIGURED:
        return jsonify({
            'error': 'Email delivery is not configured on this server.',
            'code': 'email_unconfigured',
        }), 503

    user = accounts.user_by_email(email)
    if not user or not user['is_active']:
        return opaque, 202

    raw_token, ttl = accounts.issue_token(
        user['id'], purpose=accounts.PURPOSE_RESET, ip=identity,
        ttl_hours=accounts.reset_ttl_hours())
    send_password_reset_email(
        to=user['email'], reset_url=accounts.reset_url(raw_token), expires_hours=ttl)
    return opaque, 202


@auth_bp.route('/api/reset-password', methods=['POST'])
def api_reset_password():
    """Finish a password reset.

    Deliberately does not sign the user in. Proving control of the inbox is
    enough to set a password, but making them use it once confirms they have
    recorded it, and it keeps session creation on one path.
    """
    data = request.get_json(silent=True) or {}

    try:
        enforce('password_reset', client_identity(request), subject='__consume__')
    except RateLimitExceeded as error:
        return refusal_response(error)

    try:
        password = accounts.validate_new_password(
            data.get('password'), data.get('password_confirmation'))
    except accounts.SignupError as error:
        return jsonify({'error': error.message, 'field': error.field}), 400

    outcome, user_id = accounts.consume_reset_token(data.get('token'))

    if outcome == accounts.RESET_INVALID:
        return jsonify({'error': 'That reset link is not valid.',
                        'code': outcome}), 400
    if outcome == accounts.RESET_EXPIRED:
        return jsonify({'error': 'That reset link has expired. Request a new one.',
                        'code': outcome, 'can_retry': True}), 410
    if outcome == accounts.RESET_USED:
        return jsonify({'error': 'That reset link has already been used. Request a '
                                 'new one.', 'code': outcome, 'can_retry': True}), 410

    with engine.connect() as conn:
        user = conn.execute(
            select(users.c.id, users.c.is_active).where(users.c.id == user_id)
        ).mappings().first()
    if not user or not user['is_active']:
        return jsonify({'error': 'This account is not available. Contact support.',
                        'code': 'account_inactive'}), 403

    accounts.apply_new_password(user_id, generate_password_hash(password))

    # Drop any session this request happens to carry, so a reset performed from a
    # shared browser does not leave someone else signed in as this account.
    session.clear()

    return jsonify({
        'status': accounts.RESET_OK,
        'message': 'Your password has been changed. Sign in with it to continue.',
        'next': '/login',
    })
