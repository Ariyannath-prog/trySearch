"""Phase C: signup and email verification.

Email never leaves the process here. The mailer runs in console mode under
APP_ENV=development, and these tests capture the sent message to recover the raw
token — the same path a real user takes through their inbox, without a relay.
"""

import os
import re
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'signup-verification-test-secret'
os.environ.setdefault('APP_BASE_URL', 'https://example.test')

import server_pg  # noqa: E402
from conftest import raw_client  # noqa: E402

from sqlalchemy import delete, func, insert, select, update  # noqa: E402
from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

from app import accounts, mailer  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import email_verification_tokens, rate_limit_counters, users  # noqa: E402
from app import ratelimit  # noqa: E402

GOOD_PASSWORD = 'correct-horse-battery'


def clear_rate_limits():
    with engine.begin() as conn:
        conn.execute(delete(rate_limit_counters))


def signup_payload(email, **overrides):
    payload = {
        'email': email,
        'password': GOOD_PASSWORD,
        'password_confirmation': GOOD_PASSWORD,
        'terms_accepted': True,
    }
    payload.update(overrides)
    return payload


class CapturedMail:
    """Collects messages instead of sending them."""

    def __init__(self):
        self.sent = []

    def install(self, monkey_target=mailer):
        self._original = monkey_target.send

        def fake_send(*, to, subject, text_body, html_body=None):
            self.sent.append({'to': to, 'subject': subject, 'text': text_body})
            return True, 'console', None

        monkey_target.send = fake_send
        # auth.py imported send_verification_email, which closes over mailer.send
        # at call time, so patching mailer.send is enough.
        return self

    def restore(self, monkey_target=mailer):
        monkey_target.send = self._original

    def last_token(self):
        assert self.sent, 'no email was sent'
        match = re.search(r'token=([A-Za-z0-9_\-]+)', self.sent[-1]['text'])
        assert match, f'no token in email body: {self.sent[-1]["text"][:200]}'
        return match.group(1)

    def tokens(self):
        return [re.search(r'token=([A-Za-z0-9_\-]+)', m['text']).group(1)
                for m in self.sent]


class MailCaptureTestCase(unittest.TestCase):
    def setUp(self):
        clear_rate_limits()
        self.mail = CapturedMail().install()

    def tearDown(self):
        self.mail.restore()


def delete_user(email):
    with engine.begin() as conn:
        ids = [r[0] for r in conn.execute(
            select(users.c.id).where(func.lower(users.c.email) == email.lower())).all()]
        if ids:
            conn.execute(delete(email_verification_tokens)
                         .where(email_verification_tokens.c.user_id.in_(ids)))
            conn.execute(delete(users).where(users.c.id.in_(ids)))


# --- validation -------------------------------------------------------------

class SignupValidationTests(MailCaptureTestCase):

    def post(self, payload):
        with server_pg.app.test_client() as client:
            return client.post('/api/signup', json=payload)

    def test_valid_signup_is_accepted_and_pends_verification(self):
        delete_user('valid@signup.test')
        response = self.post(signup_payload('valid@signup.test'))
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body['status'], 'pending_verification')
        self.assertEqual(body['next'], '/verify-email')
        self.assertEqual(len(self.mail.sent), 1)

    def test_invalid_email_is_rejected(self):
        for bad in ('', 'not-an-email', 'a@b', 'a b@c.com', 'x@@y.com'):
            with self.subTest(email=bad):
                response = self.post(signup_payload(bad))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json().get('field'), 'email')

    def test_short_password_is_rejected(self):
        response = self.post(signup_payload(
            'short@signup.test', password='abc', password_confirmation='abc'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'password')

    def test_mismatched_confirmation_is_rejected(self):
        response = self.post(signup_payload(
            'mismatch@signup.test', password_confirmation='something-else'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'password_confirmation')

    def test_missing_confirmation_is_rejected(self):
        payload = signup_payload('noconfirm@signup.test')
        del payload['password_confirmation']
        response = self.post(payload)
        self.assertEqual(response.status_code, 400)

    def test_terms_must_be_accepted(self):
        for value in (False, None, 'yes', 1):
            with self.subTest(terms=value):
                response = self.post(signup_payload(
                    'terms@signup.test', terms_accepted=value))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()['field'], 'terms_accepted')

    def test_email_is_normalised_and_whitespace_trimmed(self):
        delete_user('mixed@signup.test')
        response = self.post(signup_payload('  MiXeD@Signup.TEST  '))
        self.assertEqual(response.status_code, 202)
        with engine.connect() as conn:
            stored = conn.execute(select(users.c.email).where(
                func.lower(users.c.email) == 'mixed@signup.test')).scalar_one()
        self.assertEqual(stored, 'mixed@signup.test')

    def test_a_non_object_body_is_rejected(self):
        with server_pg.app.test_client() as client:
            self.assertEqual(client.post('/api/signup', json=['nope']).status_code, 400)


# --- storage and hashing ----------------------------------------------------

class SignupStorageTests(MailCaptureTestCase):

    def test_password_is_hashed_never_stored_plaintext(self):
        delete_user('hash@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('hash@signup.test'))
        with engine.connect() as conn:
            stored = conn.execute(select(users.c.password_hash).where(
                users.c.email == 'hash@signup.test')).scalar_one()
        self.assertNotEqual(stored, GOOD_PASSWORD)
        self.assertNotIn(GOOD_PASSWORD, stored)
        self.assertTrue(check_password_hash(stored, GOOD_PASSWORD),
                        'must verify with the existing werkzeug hasher')

    def test_terms_acceptance_is_recorded_with_a_version(self):
        delete_user('terms2@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('terms2@signup.test'))
        with engine.connect() as conn:
            row = conn.execute(select(users.c.terms_accepted_at, users.c.terms_version)
                               .where(users.c.email == 'terms2@signup.test')).mappings().first()
        self.assertIsNotNone(row['terms_accepted_at'])
        self.assertEqual(row['terms_version'], accounts.CURRENT_TERMS_VERSION)

    def test_new_account_starts_unverified_and_active(self):
        delete_user('fresh@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('fresh@signup.test'))
        with engine.connect() as conn:
            row = conn.execute(select(users.c.email_verified_at, users.c.is_active,
                                      users.c.is_platform_admin)
                               .where(users.c.email == 'fresh@signup.test')).mappings().first()
        self.assertIsNone(row['email_verified_at'])
        self.assertTrue(row['is_active'])
        self.assertFalse(row['is_platform_admin'],
                         'signup must never create a platform admin')

    def test_only_a_token_hash_is_stored(self):
        delete_user('tokenhash@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('tokenhash@signup.test'))
        raw = self.mail.last_token()
        with engine.connect() as conn:
            stored = conn.execute(select(email_verification_tokens.c.token_hash)
                                  .order_by(email_verification_tokens.c.id.desc())
                                  .limit(1)).scalar_one()
        self.assertNotEqual(stored, raw)
        self.assertEqual(stored, accounts.hash_token(raw))
        self.assertEqual(len(stored), 64, 'sha256 hex digest')

    def test_username_is_derived_and_deduplicated(self):
        for email in ('dupe@a.test', 'dupe@b.test'):
            delete_user(email)
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('dupe@a.test'))
            client.post('/api/signup', json=signup_payload('dupe@b.test'))
        with engine.connect() as conn:
            names = conn.execute(select(users.c.username).where(
                users.c.email.in_(['dupe@a.test', 'dupe@b.test']))).scalars().all()
        self.assertEqual(len(set(names)), 2, f'usernames must be unique: {names}')


# --- duplicate handling / enumeration ---------------------------------------

class DuplicateHandlingTests(MailCaptureTestCase):

    def test_duplicate_signup_does_not_reveal_that_the_account_exists(self):
        delete_user('dup@signup.test')
        with server_pg.app.test_client() as client:
            first = client.post('/api/signup', json=signup_payload('dup@signup.test'))
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            second = client.post('/api/signup', json=signup_payload('dup@signup.test'))
        self.assertEqual(first.status_code, second.status_code)
        self.assertEqual(first.get_json()['status'], second.get_json()['status'])
        self.assertEqual(first.get_json()['message'], second.get_json()['message'])

    def test_duplicate_signup_creates_no_second_account(self):
        delete_user('dup2@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('dup2@signup.test'))
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('dup2@signup.test'))
        with engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(users).where(
                func.lower(users.c.email) == 'dup2@signup.test')).scalar_one()
        self.assertEqual(count, 1)

    def test_duplicate_signup_does_not_overwrite_the_existing_password(self):
        delete_user('dup3@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('dup3@signup.test'))
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload(
                'dup3@signup.test', password='attacker-chosen-pass',
                password_confirmation='attacker-chosen-pass'))
        with engine.connect() as conn:
            stored = conn.execute(select(users.c.password_hash).where(
                users.c.email == 'dup3@signup.test')).scalar_one()
        self.assertTrue(check_password_hash(stored, GOOD_PASSWORD),
                        'a repeat signup must not reset the real owner password')
        self.assertFalse(check_password_hash(stored, 'attacker-chosen-pass'))

    def test_signup_for_a_verified_address_sends_nothing(self):
        delete_user('dup4@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('dup4@signup.test'))
            token = self.mail.last_token()
            client.post('/api/verify-email', json={'token': token})
        before = len(self.mail.sent)
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/signup', json=signup_payload('dup4@signup.test'))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(self.mail.sent), before,
                         'no mail should go to an already-verified address')


# --- verification -----------------------------------------------------------

class VerificationTests(MailCaptureTestCase):

    def signup(self, email):
        delete_user(email)
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload(email))
        return self.mail.last_token()

    def test_valid_token_verifies_signs_in_and_points_at_onboarding(self):
        token = self.signup('verify@signup.test')
        with server_pg.app.test_client() as client:
            response = client.post('/api/verify-email', json={'token': token})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            body = response.get_json()
            self.assertEqual(body['status'], accounts.VERIFY_OK)
            self.assertTrue(body['email_verified'])
            self.assertEqual(body['next'], '/onboarding',
                             'verification must lead to onboarding, not /analytics')
            me = client.get('/api/me').get_json()
            self.assertTrue(me['logged_in'], 'verification signs the user in')

    def test_verified_timestamp_is_written(self):
        token = self.signup('stamp@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/verify-email', json={'token': token})
        with engine.connect() as conn:
            stamp = conn.execute(select(users.c.email_verified_at).where(
                users.c.email == 'stamp@signup.test')).scalar_one()
        self.assertIsNotNone(stamp)

    def test_token_is_marked_used(self):
        token = self.signup('used@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/verify-email', json={'token': token})
        with engine.connect() as conn:
            used = conn.execute(select(email_verification_tokens.c.used_at).where(
                email_verification_tokens.c.token_hash == accounts.hash_token(token)
            )).scalar_one()
        self.assertIsNotNone(used)

    def test_an_invalid_token_is_rejected(self):
        with server_pg.app.test_client() as client:
            response = client.post('/api/verify-email', json={'token': 'not-a-real-token'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], accounts.VERIFY_INVALID)

    def test_a_missing_token_is_rejected(self):
        with server_pg.app.test_client() as client:
            self.assertEqual(client.post('/api/verify-email', json={}).status_code, 400)

    def test_a_reused_token_cannot_verify_a_second_account(self):
        """Second spend achieves nothing and is reported as already verified."""
        token = self.signup('reuse@signup.test')
        with server_pg.app.test_client() as client:
            first = client.post('/api/verify-email', json={'token': token})
        self.assertEqual(first.status_code, 200)
        with server_pg.app.test_client() as client:
            second = client.post('/api/verify-email', json={'token': token})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()['status'], accounts.VERIFY_ALREADY)

    def test_a_used_token_for_a_still_unverified_user_is_refused(self):
        """Superseded by a resend, then clicked: must not verify."""
        token = self.signup('superseded@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/resend-verification',
                        json={'email': 'superseded@signup.test'})
        with server_pg.app.test_client() as client:
            response = client.post('/api/verify-email', json={'token': token})
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.get_json()['code'], accounts.VERIFY_USED)
        self.assertTrue(response.get_json()['can_resend'])

    def test_an_expired_token_is_refused(self):
        token = self.signup('expired@signup.test')
        with engine.begin() as conn:
            conn.execute(update(email_verification_tokens)
                         .where(email_verification_tokens.c.token_hash
                                == accounts.hash_token(token))
                         .values(expires_at=datetime.utcnow() - timedelta(minutes=1)))
        with server_pg.app.test_client() as client:
            response = client.post('/api/verify-email', json={'token': token})
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.get_json()['code'], accounts.VERIFY_EXPIRED)
        with engine.connect() as conn:
            self.assertIsNone(conn.execute(select(users.c.email_verified_at).where(
                users.c.email == 'expired@signup.test')).scalar_one())

    def test_issuing_a_new_token_supersedes_the_previous_one(self):
        self.signup('supersede2@signup.test')
        with server_pg.app.test_client() as client:
            client.post('/api/resend-verification',
                        json={'email': 'supersede2@signup.test'})
        tokens = self.mail.tokens()
        self.assertGreaterEqual(len(tokens), 2)
        self.assertNotEqual(tokens[-1], tokens[-2])
        with server_pg.app.test_client() as client:
            newest = client.post('/api/verify-email', json={'token': tokens[-1]})
        self.assertEqual(newest.status_code, 200, 'the newest link must work')

    def test_verification_is_refused_for_a_deactivated_account(self):
        token = self.signup('inactive@signup.test')
        with engine.begin() as conn:
            conn.execute(update(users).where(users.c.email == 'inactive@signup.test')
                         .values(is_active=False))
        with server_pg.app.test_client() as client:
            response = client.post('/api/verify-email', json={'token': token})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'account_inactive')


# --- resend -----------------------------------------------------------------

class ResendTests(MailCaptureTestCase):

    def test_resend_for_an_unknown_address_looks_identical_to_success(self):
        with server_pg.app.test_client() as client:
            unknown = client.post('/api/resend-verification',
                                  json={'email': 'nobody@nowhere.test'})
        delete_user('known@resend.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('known@resend.test'))
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            known = client.post('/api/resend-verification',
                                json={'email': 'known@resend.test'})
        self.assertEqual(unknown.status_code, known.status_code)
        self.assertEqual(unknown.get_json(), known.get_json())

    def test_resend_sends_nothing_for_an_unknown_address(self):
        before = len(self.mail.sent)
        with server_pg.app.test_client() as client:
            client.post('/api/resend-verification', json={'email': 'ghost@nowhere.test'})
        self.assertEqual(len(self.mail.sent), before)

    def test_resend_is_rate_limited(self):
        delete_user('rl@resend.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('rl@resend.test'))
        clear_rate_limits()
        limit, _window = ratelimit.POLICIES['resend_verification']
        statuses = []
        with server_pg.app.test_client() as client:
            for _ in range(limit + 2):
                statuses.append(client.post('/api/resend-verification',
                                            json={'email': 'rl@resend.test'}).status_code)
        self.assertIn(429, statuses)

    def test_repeat_signups_for_one_address_are_rate_limited(self):
        clear_rate_limits()
        limit, _window = ratelimit.POLICIES['signup']
        statuses = []
        with server_pg.app.test_client() as client:
            for _ in range(limit + 2):
                statuses.append(client.post(
                    '/api/signup',
                    json=signup_payload('onerate@signup.test')).status_code)
        self.assertIn(429, statuses)

    def test_mass_signup_from_one_source_is_rate_limited(self):
        """Varying the email must not defeat the limit.

        Keying only on (caller, email) would let one source create unlimited
        accounts, which is the abuse signup limiting exists to prevent.
        """
        clear_rate_limits()
        limit, _window = ratelimit.POLICIES['signup_ip']
        statuses = []
        with server_pg.app.test_client() as client:
            for index in range(limit + 2):
                statuses.append(client.post('/api/signup', json=signup_payload(
                    f'mass{index}@signup.test')).status_code)
        self.assertIn(429, statuses,
                      'a single source must not be able to create unlimited accounts')

    def test_mass_resend_from_one_source_is_rate_limited(self):
        clear_rate_limits()
        limit, _window = ratelimit.POLICIES['resend_ip']
        statuses = []
        with server_pg.app.test_client() as client:
            for index in range(limit + 2):
                statuses.append(client.post('/api/resend-verification', json={
                    'email': f'massresend{index}@signup.test'}).status_code)
        self.assertIn(429, statuses)

    def test_verify_endpoint_is_rate_limited(self):
        clear_rate_limits()
        limit, _window = ratelimit.POLICIES['email_verify']
        statuses = []
        with server_pg.app.test_client() as client:
            for _ in range(limit + 2):
                statuses.append(client.post('/api/verify-email',
                                            json={'token': 'bogus'}).status_code)
        self.assertIn(429, statuses, 'token guessing must be rate limited')


# --- login behaviour --------------------------------------------------------

class LoginBehaviourTests(MailCaptureTestCase):

    def test_an_unverified_user_can_still_log_in_but_is_flagged(self):
        delete_user('unverified@login.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('unverified@login.test'))
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/login', json={
                'username': 'unverified@login.test', 'password': GOOD_PASSWORD})
        self.assertEqual(response.status_code, 200, 'login must still work unverified')
        self.assertFalse(response.get_json()['email_verified'])

    def test_a_verified_user_logs_in_as_verified(self):
        delete_user('verified@login.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('verified@login.test'))
            client.post('/api/verify-email', json={'token': self.mail.last_token()})
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/login', json={
                'username': 'verified@login.test', 'password': GOOD_PASSWORD})
        self.assertTrue(response.get_json()['email_verified'])

    def test_verification_status_endpoint_directs_the_user(self):
        delete_user('status@login.test')
        with server_pg.app.test_client() as client:
            client.post('/api/signup', json=signup_payload('status@login.test'))
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            client.post('/api/login', json={
                'username': 'status@login.test', 'password': GOOD_PASSWORD})
            unverified = client.get('/api/verification-status').get_json()
        self.assertFalse(unverified['email_verified'])
        self.assertEqual(unverified['next'], '/verify-email')

    def test_verification_status_requires_a_session(self):
        with server_pg.app.test_client() as client:
            self.assertEqual(client.get('/api/verification-status').status_code, 401)


# --- onboarding gate --------------------------------------------------------

class OnboardingGateTests(MailCaptureTestCase):

    def signup_and_login(self, client, email):
        delete_user(email)
        client.post('/api/signup', json=signup_payload(email))
        clear_rate_limits()
        client.post('/api/login', json={'username': email, 'password': GOOD_PASSWORD})

    def test_unverified_user_is_redirected_away_from_the_onboarding_page(self):
        with server_pg.app.test_client() as client:
            self.signup_and_login(client, 'gate@onb.test')
            response = client.get('/onboarding')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/verify-email'))

    def test_unverified_user_is_refused_by_the_onboarding_api(self):
        """The redirect is convenience; this is the enforcement."""
        with server_pg.app.test_client() as client:
            self.signup_and_login(client, 'gateapi@onb.test')
            preview = client.post('/api/onboarding/preview', json={'domain': 'example.com'})
            approve = client.post('/api/onboarding/approve', json={'profile': {}})
        for response in (preview, approve):
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.get_json()['code'], 'email_unverified')
            self.assertEqual(response.get_json()['next'], '/verify-email')

    def test_anonymous_onboarding_api_is_401_not_403(self):
        with server_pg.app.test_client() as client:
            self.assertEqual(
                client.post('/api/onboarding/preview', json={}).status_code, 401)

    def test_a_verified_user_passes_the_gate(self):
        with server_pg.app.test_client() as client:
            delete_user('passed@onb.test')
            client.post('/api/signup', json=signup_payload('passed@onb.test'))
            client.post('/api/verify-email', json={'token': self.mail.last_token()})
            page = client.get('/onboarding')
            # The gate is passed; the preview then fails on the network guard or
            # provider config, which is a different layer and not a 403.
            preview = client.post('/api/onboarding/preview', json={'domain': 'example.com'})
        self.assertEqual(page.status_code, 200)
        self.assertNotEqual(preview.status_code, 403)
        if preview.status_code >= 400:
            self.assertNotEqual(preview.get_json().get('code'), 'email_unverified')


# --- existing-user compatibility -------------------------------------------

class ExistingUserCompatibilityTests(MailCaptureTestCase):
    """Accounts predating verification must keep working."""

    def make_legacy_user(self, email):
        """A pre-Phase-C account: no verification, no terms, as the old code wrote."""
        delete_user(email)
        with engine.begin() as conn:
            return conn.execute(insert(users).values(
                username=email.split('@')[0] + '_legacy', email=email,
                password_hash=generate_password_hash(GOOD_PASSWORD),
                created_at=datetime.utcnow() - timedelta(days=90),
                is_platform_admin=False, is_active=True,
                email_verified_at=None, terms_accepted_at=None, terms_version=None,
            )).inserted_primary_key[0]

    def test_a_legacy_user_can_still_log_in(self):
        self.make_legacy_user('legacy@old.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/login', json={
                'username': 'legacy@old.test', 'password': GOOD_PASSWORD})
        self.assertEqual(response.status_code, 200)

    def test_the_migration_backfill_lets_a_legacy_user_into_onboarding(self):
        """Mirrors migration 9c4e2a7b5d61, which runs at deploy time."""
        user_id = self.make_legacy_user('legacy2@old.test')
        with engine.begin() as conn:
            conn.execute(update(users)
                         .where((users.c.id == user_id)
                                & (users.c.email_verified_at.is_(None)))
                         .values(email_verified_at=users.c.created_at))
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            client.post('/api/login', json={
                'username': 'legacy2@old.test', 'password': GOOD_PASSWORD})
            page = client.get('/onboarding')
        self.assertEqual(page.status_code, 200,
                         'a backfilled legacy account must not be gated out')

    def test_the_backfill_does_not_invent_a_terms_acceptance(self):
        user_id = self.make_legacy_user('legacy3@old.test')
        with engine.begin() as conn:
            conn.execute(update(users)
                         .where((users.c.id == user_id)
                                & (users.c.email_verified_at.is_(None)))
                         .values(email_verified_at=users.c.created_at))
        with engine.connect() as conn:
            row = conn.execute(select(users.c.terms_accepted_at, users.c.terms_version)
                               .where(users.c.id == user_id)).mappings().first()
        self.assertIsNone(row['terms_accepted_at'],
                          'a legacy account never accepted the current terms')
        self.assertIsNone(row['terms_version'])

    def test_the_legacy_register_endpoint_still_works(self):
        delete_user('legacyreg@old.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/register', json={
                'username': 'legacyreg_user', 'email': 'legacyreg@old.test',
                'password': GOOD_PASSWORD})
        self.assertEqual(response.status_code, 201,
                         '/api/register must keep its contract')


# --- CSRF / session ---------------------------------------------------------

class SecuritySurfaceTests(MailCaptureTestCase):

    def test_signup_requires_a_csrf_token(self):
        client = raw_client(server_pg.app)
        response = client.post('/api/signup', json=signup_payload('csrf@signup.test'))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'csrf_invalid')

    def test_verify_and_resend_require_a_csrf_token(self):
        client = raw_client(server_pg.app)
        for path in ('/api/verify-email', '/api/resend-verification'):
            with self.subTest(path=path):
                response = client.post(path, json={'token': 'x', 'email': 'a@b.test'})
                self.assertEqual(response.status_code, 403)

    def test_verification_rotates_the_session_csrf_token(self):
        delete_user('rotate@signup.test')
        client = raw_client(server_pg.app)
        token = client.get('/api/csrf-token').get_json()['csrf_token']
        client.post('/api/signup', json=signup_payload('rotate@signup.test'),
                    headers={'X-CSRF-Token': token})
        raw = self.mail.last_token()
        client.post('/api/verify-email', json={'token': raw},
                    headers={'X-CSRF-Token': token})
        after = client.get('/api/csrf-token').get_json()['csrf_token']
        self.assertNotEqual(token, after,
                            'signing in during verification must rotate the token')

    def test_the_signup_page_renders_with_a_token(self):
        with server_pg.app.test_client() as client:
            html = client.get('/signup').get_data(as_text=True)
        self.assertIn("id='csrf'", html)
        self.assertIn('/api/signup', html)

    def test_the_verify_page_renders(self):
        with server_pg.app.test_client() as client:
            html = client.get('/verify-email').get_data(as_text=True)
        self.assertIn('/api/verify-email', html)
        self.assertIn('/api/resend-verification', html)


# --- mailer configuration ---------------------------------------------------

class MailerConfigurationTests(unittest.TestCase):

    def setUp(self):
        """Email configuration is global state, so assert on a known baseline.

        Another test module can legitimately save an enabled provider; depending
        on the order it ran in would make this test flap rather than fail
        honestly.
        """
        from app import email_settings
        from app.models import system_settings

        with engine.begin() as conn:
            conn.execute(delete(system_settings).where(
                system_settings.c.key.like(email_settings.PREFIX + '%')))
        self._smtp_host = os.environ.pop('SMTP_HOST', None)

    def tearDown(self):
        if self._smtp_host is not None:
            os.environ['SMTP_HOST'] = self._smtp_host

    def test_development_without_smtp_uses_console_mode(self):
        self.assertEqual(mailer.delivery_mode(), mailer.MODE_CONSOLE)

    def test_mail_status_never_exposes_the_password(self):
        status = mailer.mail_status()
        self.assertIn('password_set', status)
        self.assertIsInstance(status['password_set'], bool)
        self.assertNotIn('password', [k for k in status if k != 'password_set'])
        for value in status.values():
            self.assertNotEqual(value, os.environ.get('SMTP_PASSWORD') or '\x00')

    def test_verification_url_is_absolute_and_carries_the_token(self):
        url = accounts.verification_url('abc123')
        self.assertTrue(url.startswith('http'))
        self.assertIn('token=abc123', url)

    def test_ttl_is_bounded(self):
        original = os.environ.get('EMAIL_VERIFICATION_TTL_HOURS')
        try:
            os.environ['EMAIL_VERIFICATION_TTL_HOURS'] = '99999'
            self.assertLessEqual(accounts.verification_ttl_hours(), 168)
            os.environ['EMAIL_VERIFICATION_TTL_HOURS'] = 'nonsense'
            self.assertEqual(accounts.verification_ttl_hours(), 24)
        finally:
            if original is None:
                os.environ.pop('EMAIL_VERIFICATION_TTL_HOURS', None)
            else:
                os.environ['EMAIL_VERIFICATION_TTL_HOURS'] = original


if __name__ == '__main__':
    unittest.main()
