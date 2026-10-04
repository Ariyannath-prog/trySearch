"""Signup validation and email-verification tokens.

Separated from the route layer so the rules are testable without HTTP, and so the
route stays a thin translation between JSON and these functions.

Token handling, stated once so it is not re-derived at each call site:

* The raw token is `secrets.token_urlsafe(32)` and exists only in the email. The
  database stores `sha256(raw)`, so a database read cannot be replayed as a
  credential.
* Consumption is a single conditional UPDATE. "Still unused and unexpired" is
  decided by the database in the same statement that marks it used, so two
  simultaneous clicks cannot both succeed.
* Issuing a new token invalidates the account's outstanding ones. A user who
  clicks resend twice should not be left guessing which of two links works.

Username: the schema has `users.username` NOT NULL UNIQUE and predates this flow,
and the signup journey never asks for one. Rather than migrate a column that
existing code reads, a username is derived from the email local part and
de-duplicated. Callers may still pass one explicitly.
"""

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, insert, select, update

from app.db import engine
from app.models import email_verification_tokens, users

TOKEN_BYTES = 32
PURPOSE_VERIFY = 'email_verify'
PURPOSE_RESET = 'password_reset'

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200
MAX_EMAIL_LENGTH = 254
USERNAME_MAX_LENGTH = 150

# The terms revision a signup is recorded against, imported from the single source
# of truth in app/terms.py rather than duplicated. A second constant here would be
# the thing that drifts, leaving accounts recorded against a version of the text
# nobody can produce.
from app.terms import VERSION as CURRENT_TERMS_VERSION  # noqa: E402

# Deliberately permissive. Real deliverability is proven by the verification mail
# itself, so an over-strict regex here only rejects valid unusual addresses.
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$')


def reset_ttl_hours():
    """Password-reset links live hours, not days.

    Shorter than verification on purpose: a reset link is a live credential for
    changing a password, whereas a verification link only proves an address works.
    """
    try:
        value = int(os.environ.get('PASSWORD_RESET_TTL_HOURS', '2'))
    except (TypeError, ValueError):
        value = 2
    return max(1, min(value, 24))


def verification_ttl_hours():
    try:
        value = int(os.environ.get('EMAIL_VERIFICATION_TTL_HOURS', '24'))
    except (TypeError, ValueError):
        value = 24
    return max(1, min(value, 168))


class SignupError(ValueError):
    """A validation failure with a message intended for the end user."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.message = message
        self.field = field


def normalise_email(raw):
    email = (raw or '').strip().lower()
    if not email:
        raise SignupError('Enter your email address.', 'email')
    if len(email) > MAX_EMAIL_LENGTH:
        raise SignupError('That email address is too long.', 'email')
    if not EMAIL_PATTERN.match(email):
        raise SignupError('Enter a valid email address.', 'email')
    return email


def validate_new_password(password, confirmation):
    """Same rules as signup, so a reset cannot weaken an account's password."""
    return validate_password(password, confirmation)


def validate_password(password, confirmation):
    password = password or ''
    if not password:
        raise SignupError('Choose a password.', 'password')
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SignupError(
            f'Use at least {MIN_PASSWORD_LENGTH} characters.', 'password')
    if len(password) > MAX_PASSWORD_LENGTH:
        # werkzeug hashes anything, but an unbounded field is a cheap DoS vector.
        raise SignupError('That password is too long.', 'password')
    if confirmation is None:
        raise SignupError('Confirm your password.', 'password_confirmation')
    if password != confirmation:
        raise SignupError('Those passwords do not match.', 'password_confirmation')
    return password


def validate_terms(accepted):
    # Only an explicit boolean true counts. A truthy string from a form would mean
    # a missing checkbox could read as acceptance.
    if accepted is not True:
        raise SignupError('You must accept the terms to continue.', 'terms_accepted')
    return True


def candidate_username(email, requested=None):
    """A username to store, derived from the email unless one was supplied."""
    if requested:
        cleaned = re.sub(r'\s+', '', str(requested).strip())
        if not cleaned:
            raise SignupError('Enter a username.', 'username')
        if len(cleaned) > USERNAME_MAX_LENGTH:
            raise SignupError('That username is too long.', 'username')
        return cleaned
    local = email.split('@', 1)[0]
    base = re.sub(r'[^a-z0-9._-]+', '', local.lower()).strip('._-')
    return (base or 'user')[:40]


def allocate_username(conn, base):
    """First free variant of `base`. Uniqueness is still enforced by the column."""
    taken = conn.execute(
        select(users.c.username).where(users.c.username.ilike(f'{base}%'))
    ).scalars().all()
    lowered = {value.lower() for value in taken}
    if base.lower() not in lowered:
        return base
    for suffix in range(2, 1000):
        candidate = f'{base}{suffix}'
        if candidate.lower() not in lowered:
            return candidate
    # Astronomically unlikely; a random tail is better than a loop that gives up.
    return f'{base}{secrets.token_hex(4)}'


def hash_token(raw_token):
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def issue_token(user_id, *, purpose=PURPOSE_VERIFY, ip=None, now=None,
                ttl_hours=None, conn=None):
    """Create a token, returning the raw value. Only its hash is stored.

    Supersedes the user's outstanding tokens of the same purpose, so exactly one
    link is live at a time.
    """
    now = now or datetime.utcnow()
    ttl = ttl_hours or verification_ttl_hours()
    raw = secrets.token_urlsafe(TOKEN_BYTES)

    def _write(connection):
        connection.execute(
            update(email_verification_tokens)
            .where(
                (email_verification_tokens.c.user_id == user_id)
                & (email_verification_tokens.c.purpose == purpose)
                & (email_verification_tokens.c.used_at.is_(None))
            )
            .values(used_at=now)
        )
        connection.execute(insert(email_verification_tokens).values(
            user_id=user_id, purpose=purpose, token_hash=hash_token(raw),
            expires_at=now + timedelta(hours=ttl), used_at=None,
            created_at=now, requested_ip=ip,
        ))

    if conn is not None:
        _write(conn)
    else:
        with engine.begin() as connection:
            _write(connection)
    return raw, ttl


# Outcomes of consume_token, kept as constants so routes do not match on prose.
VERIFY_OK = 'verified'
VERIFY_ALREADY = 'already_verified'
VERIFY_INVALID = 'invalid'
VERIFY_EXPIRED = 'expired'
VERIFY_USED = 'used'


def consume_token(raw_token, *, purpose=PURPOSE_VERIFY, now=None):
    """Spend a token once. Returns (outcome, user_id_or_None).

    The UPDATE carries the unused/unexpired predicate, so the database decides
    whether this token was still spendable. A second concurrent request updates
    zero rows and is reported as already used rather than succeeding twice.
    """
    now = now or datetime.utcnow()
    if not raw_token or not isinstance(raw_token, str):
        return VERIFY_INVALID, None

    token_hash = hash_token(raw_token.strip())

    with engine.begin() as conn:
        row = conn.execute(
            select(
                email_verification_tokens.c.id,
                email_verification_tokens.c.user_id,
                email_verification_tokens.c.used_at,
                email_verification_tokens.c.expires_at,
            ).where(
                (email_verification_tokens.c.token_hash == token_hash)
                & (email_verification_tokens.c.purpose == purpose)
            )
        ).mappings().first()

        if not row:
            return VERIFY_INVALID, None

        already = conn.execute(
            select(users.c.email_verified_at).where(users.c.id == row['user_id'])
        ).scalar_one_or_none()

        if row['used_at'] is not None:
            # A link clicked twice by an already-verified user is a success for
            # them, not an error; only report 'used' when it achieved nothing.
            return (VERIFY_ALREADY if already else VERIFY_USED), row['user_id']
        if row['expires_at'] <= now:
            return VERIFY_EXPIRED, row['user_id']

        spent = conn.execute(
            update(email_verification_tokens)
            .where(
                (email_verification_tokens.c.id == row['id'])
                & (email_verification_tokens.c.used_at.is_(None))
                & (email_verification_tokens.c.expires_at > now)
            )
            .values(used_at=now)
        )
        if spent.rowcount != 1:
            return (VERIFY_ALREADY if already else VERIFY_USED), row['user_id']

        if already is None:
            conn.execute(update(users).where(users.c.id == row['user_id'])
                         .values(email_verified_at=now))
        return VERIFY_OK, row['user_id']


def user_by_email(email, conn=None):
    """Case-insensitive lookup, matching the unique index on lower(email)."""
    statement = select(
        users.c.id, users.c.username, users.c.email, users.c.is_active,
        users.c.email_verified_at, users.c.created_at,
    ).where(func.lower(users.c.email) == email.lower()).limit(1)

    if conn is not None:
        row = conn.execute(statement).mappings().first()
    else:
        with engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
    return dict(row) if row else None


def is_verified(user_id):
    with engine.connect() as conn:
        return conn.execute(
            select(users.c.email_verified_at).where(users.c.id == user_id)
        ).scalar_one_or_none() is not None


def verification_url(raw_token):
    from app.mailer import app_base_url
    from urllib.parse import quote

    return f'{app_base_url()}/verify-email?token={quote(raw_token, safe="")}'


# --- password reset ---------------------------------------------------------

RESET_OK = 'reset'
RESET_INVALID = 'invalid'
RESET_EXPIRED = 'expired'
RESET_USED = 'used'


def consume_reset_token(raw_token, *, now=None):
    """Spend a password-reset token once. Returns (outcome, user_id_or_None).

    Mirrors consume_token(): the unused-and-unexpired predicate travels with the
    UPDATE, so the database decides spendability and two concurrent clicks cannot
    both succeed. Unlike verification there is no "already done" success case -
    a spent reset link is always a failure, because the holder cannot know
    whether the previous use was theirs.
    """
    now = now or datetime.utcnow()
    if not raw_token or not isinstance(raw_token, str):
        return RESET_INVALID, None

    token_hash = hash_token(raw_token.strip())

    with engine.begin() as conn:
        row = conn.execute(
            select(
                email_verification_tokens.c.id,
                email_verification_tokens.c.user_id,
                email_verification_tokens.c.used_at,
                email_verification_tokens.c.expires_at,
            ).where(
                (email_verification_tokens.c.token_hash == token_hash)
                & (email_verification_tokens.c.purpose == PURPOSE_RESET)
            )
        ).mappings().first()

        if not row:
            return RESET_INVALID, None
        if row['used_at'] is not None:
            return RESET_USED, row['user_id']
        if row['expires_at'] <= now:
            return RESET_EXPIRED, row['user_id']

        spent = conn.execute(
            update(email_verification_tokens)
            .where(
                (email_verification_tokens.c.id == row['id'])
                & (email_verification_tokens.c.used_at.is_(None))
                & (email_verification_tokens.c.expires_at > now)
            )
            .values(used_at=now)
        )
        if spent.rowcount != 1:
            return RESET_USED, row['user_id']
        return RESET_OK, row['user_id']


def apply_new_password(user_id, password_hash, *, now=None):
    """Store a new password hash and invalidate outstanding reset links.

    Also marks the address verified if it was not already: completing a reset
    proves control of the inbox, which is the same bar verification sets. This is
    what lets an account that predates verification recover without a second,
    separate confirmation step.
    """
    now = now or datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(
            update(users).where(users.c.id == user_id).values(password_hash=password_hash)
        )
        conn.execute(
            update(users)
            .where((users.c.id == user_id) & (users.c.email_verified_at.is_(None)))
            .values(email_verified_at=now)
        )
        # Any other live reset link for this account is now void.
        conn.execute(
            update(email_verification_tokens)
            .where(
                (email_verification_tokens.c.user_id == user_id)
                & (email_verification_tokens.c.purpose == PURPOSE_RESET)
                & (email_verification_tokens.c.used_at.is_(None))
            )
            .values(used_at=now)
        )


def reset_url(raw_token):
    from app.mailer import app_base_url
    from urllib.parse import quote

    return f'{app_base_url()}/reset-password?token={quote(raw_token, safe="")}'
