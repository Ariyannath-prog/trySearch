"""Phase A: CSRF enforcement, PostgreSQL rate limiting, and auth hardening.

These use conftest.raw_client() where the point is that a request is *refused*,
because the suite's default test client attaches a CSRF token automatically.
"""

import os
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'security-foundations-test-secret'

import server_pg  # noqa: E402
from conftest import raw_client  # noqa: E402

from sqlalchemy import delete, insert, select, update  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import ratelimit  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import rate_limit_counters, users  # noqa: E402
from app.security import EXEMPT_PATHS, TOKEN_HEADER  # noqa: E402

PASSWORD = 'security-password-123'


def make_user(username, *, active=True, verified=False):
    with engine.begin() as conn:
        existing = conn.execute(
            select(users.c.id).where(users.c.username == username)).scalar_one_or_none()
        values = {
            'is_active': active,
            'email_verified_at': datetime.utcnow() if verified else None,
        }
        if existing is not None:
            conn.execute(update(users).where(users.c.id == existing).values(**values))
            return existing
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@security.example',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(), **values,
        )).inserted_primary_key[0]


def clear_rate_limits():
    with engine.begin() as conn:
        conn.execute(delete(rate_limit_counters))


class CSRFEnforcementTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        make_user('csrf_user')

    def setUp(self):
        clear_rate_limits()

    def test_a_mutating_api_request_without_a_token_is_refused(self):
        client = raw_client(server_pg.app)
        response = client.post('/api/logout')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'csrf_invalid')

    def test_the_same_request_with_a_valid_token_is_accepted(self):
        client = raw_client(server_pg.app)
        token = client.get('/api/csrf-token').get_json()['csrf_token']
        response = client.post('/api/logout', headers={TOKEN_HEADER: token})
        self.assertEqual(response.status_code, 200)

    def test_a_wrong_token_is_refused(self):
        client = raw_client(server_pg.app)
        client.get('/api/csrf-token')
        response = client.post('/api/logout', headers={TOKEN_HEADER: 'not-the-token'})
        self.assertEqual(response.status_code, 403)

    def test_a_token_from_a_different_session_is_refused(self):
        """A synchronizer token is only valid for the session that holds it."""
        other = raw_client(server_pg.app)
        stolen = other.get('/api/csrf-token').get_json()['csrf_token']

        client = raw_client(server_pg.app)
        client.get('/api/csrf-token')
        response = client.post('/api/logout', headers={TOKEN_HEADER: stolen})
        self.assertEqual(response.status_code, 403)

    def test_get_requests_are_unaffected(self):
        client = raw_client(server_pg.app)
        self.assertEqual(client.get('/api/me').status_code, 200)
        self.assertEqual(client.get('/api/health').status_code, 200)

    def test_the_token_can_be_supplied_in_the_json_body(self):
        client = raw_client(server_pg.app)
        token = client.get('/api/csrf-token').get_json()['csrf_token']
        response = client.post('/api/logout', json={'csrf_token': token})
        self.assertEqual(response.status_code, 200)

    def test_the_public_contact_form_stays_exempt(self):
        """index.html is static and must not be modified, so this must keep working."""
        self.assertIn('/api/contacts', EXEMPT_PATHS)
        client = raw_client(server_pg.app)
        response = client.post('/api/contacts', json={
            'name': 'CSRF Exempt', 'email': 'exempt@example.com', 'message': 'hello'})
        self.assertEqual(response.status_code, 201)

    def test_login_rotates_the_token(self):
        """Session fixation: the pre-login token must not survive the privilege change."""
        client = raw_client(server_pg.app)
        before = client.get('/api/csrf-token').get_json()['csrf_token']
        response = client.post('/api/login',
                               json={'username': 'csrf_user', 'password': PASSWORD},
                               headers={TOKEN_HEADER: before})
        self.assertEqual(response.status_code, 200)
        after = client.get('/api/csrf-token').get_json()['csrf_token']
        self.assertNotEqual(before, after)

    def test_me_carries_a_token_for_anonymous_callers(self):
        """The login form needs one before a user exists."""
        client = raw_client(server_pg.app)
        body = client.get('/api/me').get_json()
        self.assertFalse(body['logged_in'])
        self.assertTrue(body['csrf_token'])

    def test_every_mutating_api_route_is_guarded_or_deliberately_exempt(self):
        """Refuse-by-default, read off the real url_map.

        A new mutating endpoint is covered the moment it is registered, so one
        cannot ship unprotected by forgetting a decorator.
        """
        from app.security import _is_guarded

        unguarded = []
        for rule in server_pg.app.url_map.iter_rules():
            if not rule.rule.startswith('/api/'):
                continue
            for method in rule.methods - {'HEAD', 'OPTIONS', 'GET'}:
                if not _is_guarded(rule.rule, method) and rule.rule not in EXEMPT_PATHS:
                    unguarded.append(f'{method} {rule.rule}')
        self.assertEqual(unguarded, [], f'unguarded mutating routes: {unguarded}')


class RateLimitTests(unittest.TestCase):

    def setUp(self):
        clear_rate_limits()

    def test_hits_accumulate_and_then_refuse(self):
        limit, _window = ratelimit.POLICIES['signup']
        for attempt in range(1, limit + 1):
            allowed, hits, reported, _retry = ratelimit.check('signup', '10.0.0.1')
            self.assertTrue(allowed, f'attempt {attempt} should be allowed')
            self.assertEqual(hits, attempt)
            self.assertEqual(reported, limit)

        allowed, hits, _limit, retry_after = ratelimit.check('signup', '10.0.0.1')
        self.assertFalse(allowed)
        self.assertEqual(hits, limit + 1)
        self.assertGreater(retry_after, 0)

    def test_different_callers_have_independent_buckets(self):
        limit, _window = ratelimit.POLICIES['signup']
        for _ in range(limit + 1):
            ratelimit.check('signup', '10.0.0.2')
        allowed, _hits, _limit, _retry = ratelimit.check('signup', '10.0.0.3')
        self.assertTrue(allowed, 'one caller being limited must not limit everyone')

    def test_different_subjects_have_independent_buckets(self):
        limit, _window = ratelimit.POLICIES['login']
        for _ in range(limit + 1):
            ratelimit.check('login', '10.0.0.4', subject='victim@example.com')
        allowed, *_ = ratelimit.check('login', '10.0.0.4', subject='other@example.com')
        self.assertTrue(allowed)

    def test_a_later_window_starts_fresh(self):
        limit, window = ratelimit.POLICIES['signup']
        now = datetime(2026, 1, 1, 12, 0, 0)
        for _ in range(limit + 1):
            ratelimit.check('signup', '10.0.0.5', now=now)
        allowed, hits, _limit, _retry = ratelimit.check(
            'signup', '10.0.0.5', now=now + timedelta(seconds=window + 1))
        self.assertTrue(allowed)
        self.assertEqual(hits, 1)

    def test_the_bucket_key_does_not_store_the_raw_identity(self):
        """The table must not become a log of who tried to sign in."""
        ratelimit.check('login', '203.0.113.9', subject='someone@example.com')
        with engine.connect() as conn:
            keys = [row[0] for row in conn.execute(
                select(rate_limit_counters.c.bucket_key)).all()]
        joined = ' '.join(keys)
        self.assertNotIn('203.0.113.9', joined)
        self.assertNotIn('someone@example.com', joined)

    def test_enforce_raises_and_produces_a_429_with_retry_after(self):
        limit, _window = ratelimit.POLICIES['signup']
        for _ in range(limit):
            ratelimit.enforce('signup', '10.0.0.6')
        with self.assertRaises(ratelimit.RateLimitExceeded) as caught:
            ratelimit.enforce('signup', '10.0.0.6')
        with server_pg.app.test_request_context():
            response, status = ratelimit.refusal_response(caught.exception)
        self.assertEqual(status, 429)
        self.assertIn('Retry-After', response.headers)

    def test_prune_removes_only_old_windows(self):
        now = datetime.utcnow()
        ratelimit.check('signup', '10.0.0.7', now=now - timedelta(days=2))
        ratelimit.check('signup', '10.0.0.8', now=now)
        removed = ratelimit.prune(now=now)
        self.assertGreaterEqual(removed, 1)
        with engine.connect() as conn:
            remaining = conn.execute(
                select(rate_limit_counters.c.bucket_key)).scalars().all()
        self.assertEqual(len(remaining), 1)

    def test_repeated_failed_logins_are_rate_limited(self):
        make_user('ratelimited_user')
        limit, _window = ratelimit.POLICIES['login']
        with server_pg.app.test_client() as client:
            statuses = [
                client.post('/api/login', json={
                    'username': 'ratelimited_user', 'password': 'wrong-password',
                }).status_code
                for _ in range(limit + 2)
            ]
        self.assertIn(429, statuses,
                      'repeated failed logins must eventually be rate limited')

    def test_repeated_SUCCESSFUL_logins_are_not_rate_limited(self):
        """A real person signing in from several devices must not be locked out.

        Only failures are charged against the limit; a guessing script produces
        nothing but failures, so this costs no security.
        """
        make_user('frequent_user')
        limit, _window = ratelimit.POLICIES['login']
        statuses = []
        for _ in range(limit + 3):
            with server_pg.app.test_client() as client:
                statuses.append(client.post('/api/login', json={
                    'username': 'frequent_user', 'password': PASSWORD}).status_code)
        self.assertEqual(
            set(statuses), {200},
            f'successful logins must never be rate limited, got {statuses}')

    def test_a_successful_login_forgives_earlier_failures(self):
        make_user('forgiven_user')
        limit, _window = ratelimit.POLICIES['login']
        with server_pg.app.test_client() as client:
            for _ in range(limit - 1):
                client.post('/api/login', json={
                    'username': 'forgiven_user', 'password': 'wrong'})
            self.assertEqual(client.post('/api/login', json={
                'username': 'forgiven_user', 'password': PASSWORD}).status_code, 200)
            # The bucket was cleared, so there is budget for mistakes again.
            for _ in range(limit - 1):
                response = client.post('/api/login', json={
                    'username': 'forgiven_user', 'password': 'wrong'})
            self.assertEqual(
                response.status_code, 401,
                'a successful login should reset the failure budget')

    def test_peek_does_not_record_an_attempt(self):
        clear_rate_limits()
        for _ in range(50):
            allowed, hits, _limit, _retry = ratelimit.peek('login', '10.0.0.99')
            self.assertTrue(allowed)
            self.assertEqual(hits, 0)


class AuthHardeningTests(unittest.TestCase):

    def setUp(self):
        clear_rate_limits()

    def test_a_deactivated_user_cannot_log_in(self):
        """The admin deactivate control previously did not stop session login."""
        make_user('deactivated_user', active=False)
        with server_pg.app.test_client() as client:
            response = client.post('/api/login', json={
                'username': 'deactivated_user', 'password': PASSWORD})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'account_inactive')

    def test_a_deactivated_user_with_a_wrong_password_still_gets_401(self):
        """The 403 must not become an oracle for which accounts are suspended."""
        make_user('deactivated_user_2', active=False)
        with server_pg.app.test_client() as client:
            response = client.post('/api/login', json={
                'username': 'deactivated_user_2', 'password': 'wrong'})
        self.assertEqual(response.status_code, 401)

    def test_an_active_user_can_still_log_in(self):
        make_user('active_user')
        with server_pg.app.test_client() as client:
            response = client.post('/api/login', json={
                'username': 'active_user', 'password': PASSWORD})
        self.assertEqual(response.status_code, 200)

    def test_last_login_at_is_recorded(self):
        user_id = make_user('timestamp_user')
        with engine.begin() as conn:
            conn.execute(update(users).where(users.c.id == user_id)
                         .values(last_login_at=None))
        with server_pg.app.test_client() as client:
            client.post('/api/login', json={
                'username': 'timestamp_user', 'password': PASSWORD})
        with engine.connect() as conn:
            recorded = conn.execute(select(users.c.last_login_at)
                                    .where(users.c.id == user_id)).scalar_one()
        self.assertIsNotNone(recorded, 'last_login_at existed but was never written')

    def test_login_by_email_is_case_insensitive(self):
        make_user('case_user')
        with server_pg.app.test_client() as client:
            response = client.post('/api/login', json={
                'username': 'CASE_USER@SECURITY.EXAMPLE', 'password': PASSWORD})
        self.assertEqual(
            response.status_code, 200,
            'email login must match the unique index on lower(email)')

    def test_a_session_that_outlives_deactivation_stops_working(self):
        user_id = make_user('revoked_user')
        with server_pg.app.test_client() as client:
            self.assertEqual(client.post('/api/login', json={
                'username': 'revoked_user', 'password': PASSWORD}).status_code, 200)
            with engine.begin() as conn:
                conn.execute(update(users).where(users.c.id == user_id)
                             .values(is_active=False))
            body = client.get('/api/me').get_json()
        self.assertFalse(body['logged_in'],
                         'deactivating a user must invalidate their live session')

    def test_login_reports_verification_state(self):
        make_user('unverified_user', verified=False)
        make_user('verified_user', verified=True)
        with server_pg.app.test_client() as client:
            unverified = client.post('/api/login', json={
                'username': 'unverified_user', 'password': PASSWORD}).get_json()
        with server_pg.app.test_client() as client:
            verified = client.post('/api/login', json={
                'username': 'verified_user', 'password': PASSWORD}).get_json()
        # Login still works unverified by design; onboarding is what Phase C gates.
        self.assertFalse(unverified['email_verified'])
        self.assertTrue(verified['email_verified'])

    def test_register_normalises_the_email_address(self):
        with server_pg.app.test_client() as client:
            response = client.post('/api/register', json={
                'username': 'MixedCaseReg', 'email': 'MiXeD@Security.Example',
                'password': PASSWORD})
        self.assertEqual(response.status_code, 201)
        with engine.connect() as conn:
            stored = conn.execute(select(users.c.email)
                                  .where(users.c.username == 'MixedCaseReg')).scalar_one()
        self.assertEqual(stored, 'mixed@security.example')

    def test_a_case_variant_email_cannot_register_twice(self):
        with server_pg.app.test_client() as client:
            first = client.post('/api/register', json={
                'username': 'dupe_one', 'email': 'dupe@security.example',
                'password': PASSWORD})
            self.assertEqual(first.status_code, 201)
            second = client.post('/api/register', json={
                'username': 'dupe_two', 'email': 'DUPE@SECURITY.EXAMPLE',
                'password': PASSWORD})
        self.assertEqual(
            second.status_code, 400,
            'lower(email) uniqueness must prevent a second identity for one address')


if __name__ == '__main__':
    unittest.main()
