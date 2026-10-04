"""PostgreSQL-backed fixed-window rate limiting.

Counting in the database rather than in the process, because TrySearch runs under
gunicorn: a process-local counter resets on every restart and is simply wrong once a
second worker exists, which would make the limit a suggestion. No Redis is
introduced for this - Postgres is already a hard dependency and is sufficient.

The hot path is one statement:

    INSERT ... ON CONFLICT (bucket_key, window_start) DO UPDATE
      SET hits = rate_limit_counters.hits + 1 RETURNING hits

That is atomic, so two workers racing the same bucket cannot both read "1". Fixed
windows keep the table bounded at one row per key per window; a per-event table
would grow with traffic and need aggressive pruning to stay fast.

Fail-open is deliberate: if the limiter itself errors, the request proceeds. A
broken meter must not take down signup, the same reasoning costs.record_usage()
already applies to the usage ledger.
"""

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import delete, text

from app.db import engine
from app.models import rate_limit_counters

# name -> (max_hits, window_seconds). Tuned to be generous for a real person and
# hostile to a script. Applied per client identity (see _bucket_key).
#
# 'login' counts FAILED attempts only (see peek/clear and app/auth.py). A
# successful sign-in costs nothing, so a real person signing in repeatedly is never
# locked out, while a password-guessing run - which produces only failures - is.
#
# Two buckets guard account creation, because one alone is bypassable:
#   'signup'    is keyed per (caller, email) and stops one address being
#               mail-bombed by repeated signups.
#   'signup_ip' is keyed per caller only and caps how many accounts one source
#               can create at all - without it, varying the email defeats the
#               limit entirely. Set higher than 'signup' so a shared NAT is not
#               punished for a handful of genuine sign-ups.
# 'resend_verification' / 'resend_ip' split the same way.
POLICIES = {
    'signup': (5, 3600),
    'signup_ip': (15, 3600),
    'login': (10, 900),
    'email_verify': (10, 3600),
    'resend_verification': (3, 3600),
    'resend_ip': (12, 3600),
    'password_reset': (5, 3600),
}

# Windows older than this are dead weight. Pruned by the CLI worker.
PRUNE_AFTER_SECONDS = 24 * 3600


class RateLimitExceeded(Exception):
    """Raised by enforce() when a bucket is over its policy."""

    def __init__(self, policy, limit, window_seconds, retry_after):
        super().__init__(f'{policy} rate limit exceeded')
        self.policy = policy
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after = retry_after


def client_identity(request):
    """Best available caller identity.

    CF-Connecting-IP first, matching what admin_audit already trusts on this
    deployment, then X-Forwarded-For's left-most entry, then the socket address.
    """
    forwarded = request.headers.get('CF-Connecting-IP')
    if not forwarded:
        chain = request.headers.get('X-Forwarded-For') or ''
        forwarded = chain.split(',')[0].strip() if chain else None
    return forwarded or request.remote_addr or 'unknown'


def _bucket_key(policy, identity, subject=None):
    """A short, non-reversible key.

    The identity and any subject are hashed rather than stored: the table would
    otherwise become a log of who tried to sign in and with which email address,
    which is personal data this feature has no need to retain.
    """
    raw = f'{policy}|{identity}|{subject or ""}'.encode('utf-8')
    return f'{policy}:{hashlib.sha256(raw).hexdigest()[:40]}'


def _window_start(window_seconds, now):
    epoch_seconds = int(now.timestamp())
    return datetime.utcfromtimestamp(epoch_seconds - (epoch_seconds % window_seconds))


def _verdict(policy, hits, window_start, window_seconds, now):
    limit, _ = POLICIES[policy]
    if hits > limit:
        retry_after = int(
            (window_start + timedelta(seconds=window_seconds) - now).total_seconds())
        return False, hits, limit, max(retry_after, 1)
    return True, hits, limit, 0


def check(policy, identity, *, subject=None, now=None, cost=1):
    """Record one attempt and report the outcome.

    Returns (allowed, hits, limit, retry_after_seconds). Never raises: a limiter
    failure returns allowed, because refusing real users because the meter broke is
    worse than briefly not limiting.
    """
    limit, window_seconds = POLICIES[policy]
    now = now or datetime.utcnow()
    window_start = _window_start(window_seconds, now)
    bucket = _bucket_key(policy, identity, subject)

    try:
        with engine.begin() as conn:
            hits = conn.execute(
                text(
                    'INSERT INTO rate_limit_counters '
                    '  (bucket_key, window_start, hits, updated_at) '
                    'VALUES (:bucket, :window_start, :cost, :now) '
                    'ON CONFLICT (bucket_key, window_start) DO UPDATE '
                    '  SET hits = rate_limit_counters.hits + :cost, updated_at = :now '
                    'RETURNING hits'
                ),
                {'bucket': bucket, 'window_start': window_start, 'cost': cost, 'now': now},
            ).scalar_one()
    except Exception:  # noqa: BLE001 - a broken meter must not break signup
        return True, 0, limit, 0

    return _verdict(policy, hits, window_start, window_seconds, now)


def peek(policy, identity, *, subject=None, now=None):
    """Report the outcome WITHOUT recording an attempt.

    This is what lets login count only *failures*. Charging a successful sign-in
    against the limit would lock out a real person who legitimately signs in from
    several devices, while doing nothing extra to slow a password-guessing script -
    which by definition is generating failures.
    """
    limit, window_seconds = POLICIES[policy]
    now = now or datetime.utcnow()
    window_start = _window_start(window_seconds, now)
    bucket = _bucket_key(policy, identity, subject)

    try:
        with engine.connect() as conn:
            hits = conn.execute(
                text(
                    'SELECT hits FROM rate_limit_counters '
                    'WHERE bucket_key = :bucket AND window_start = :window_start'
                ),
                {'bucket': bucket, 'window_start': window_start},
            ).scalar_one_or_none() or 0
    except Exception:  # noqa: BLE001 - a broken meter must not break login
        return True, 0, limit, 0

    return _verdict(policy, hits, window_start, window_seconds, now)


def clear(policy, identity, *, subject=None, now=None):
    """Forget the current window for one bucket.

    Called after a successful authentication, so a user who mistyped a few times
    and then got it right is not left carrying those failures.
    """
    _limit, window_seconds = POLICIES[policy]
    now = now or datetime.utcnow()
    window_start = _window_start(window_seconds, now)
    bucket = _bucket_key(policy, identity, subject)
    try:
        with engine.begin() as conn:
            conn.execute(
                delete(rate_limit_counters).where(
                    (rate_limit_counters.c.bucket_key == bucket)
                    & (rate_limit_counters.c.window_start == window_start)
                )
            )
    except Exception:  # noqa: BLE001
        return


def enforce(policy, identity, *, subject=None, now=None, cost=1):
    """check(), raising RateLimitExceeded when over the limit."""
    allowed, _hits, limit, retry_after = check(
        policy, identity, subject=subject, now=now, cost=cost)
    if not allowed:
        _limit, window_seconds = POLICIES[policy]
        raise RateLimitExceeded(policy, limit, window_seconds, retry_after)


def enforce_peek(policy, identity, *, subject=None, now=None):
    """Raise if the bucket is already over its limit, without recording anything."""
    allowed, _hits, limit, retry_after = peek(
        policy, identity, subject=subject, now=now)
    if not allowed:
        _limit, window_seconds = POLICIES[policy]
        raise RateLimitExceeded(policy, limit, window_seconds, retry_after)


def refusal_response(error):
    """A 429 with Retry-After, as a tuple a Flask route can return directly."""
    from flask import jsonify

    response = jsonify({
        'error': 'Too many attempts. Please wait and try again.',
        'code': 'rate_limited',
        'retry_after_seconds': error.retry_after,
    })
    response.headers['Retry-After'] = str(error.retry_after)
    return response, 429


def prune(*, older_than_seconds=PRUNE_AFTER_SECONDS, now=None):
    """Delete expired windows. Called by the CLI worker; safe to run concurrently."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(seconds=older_than_seconds)
    with engine.begin() as conn:
        result = conn.execute(
            delete(rate_limit_counters).where(rate_limit_counters.c.window_start < cutoff)
        )
    return result.rowcount
