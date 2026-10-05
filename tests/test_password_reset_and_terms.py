"""Password reset, terms of service, and the email delivery-method field.

Reset reuses the Phase C token table via purpose='password_reset', so the tests
also check that the two purposes cannot be substituted for one another - a
verification link must not be spendable as a password reset.
"""

import os
import re
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'reset-terms-test-secret'
os.environ.setdefault('APP_BASE_URL', 'https://example.test')
os.environ.setdefault('PROVIDER_CREDENTIAL_ENCRYPTION_KEY',
                      'cp0ZQ3QrPMLQX8rC3Z8Qn1h0nQ3H8kFQ0hQ1pQ8vQ2E=')

import server_pg  # noqa: E402
from conftest import raw_client  # noqa: E402

from sqlalchemy import delete, func, insert, select, update  # noqa: E402
from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

from app import accounts, email_settings, mailer, ratelimit, terms  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import email_verification_tokens, rate_limit_counters, system_settings, users  # noqa: E402

OLD_PASSWORD = 'original-password-value'
NEW_PASSWORD = 'brand-new-password-value'


def wipe_email_settings():
    with engine.begin() as conn:
        conn.execute(delete(system_settings).where(
            system_settings.c.key.like(email_settings.PREFIX + '%')))


def clear_rate_limits():
    with engine.begin() as conn:
        conn.execute(delete(rate_limit_counters))


def delete_user(email):
    with engine.begin() as conn:
        ids = [r[0] for r in conn.execute(
            select(users.c.id).where(func.lower(users.c.email) == email.lower())).all()]
        if ids:
            conn.execute(delete(email_verification_tokens)
                         .where(email_verification_tokens.c.user_id.in_(ids)))
            conn.execute(delete(users).where(users.c.id.in_(ids)))


def make_account(email, *, verified=True, active=True, password=OLD_PASSWORD):
    delete_user(email)
    now = datetime.utcnow()
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=email.split('@')[0] + '_rt', email=email,
            password_hash=generate_password_hash(password), created_at=now,
            is_platform_admin=False, is_active=active,
            email_verified_at=now if verified else None,
        )).inserted_primary_key[0]


class CapturedMail:
    def __init__(self):
        self.sent = []

    def __enter__(self):
        self._original = mailer.send

        def fake_send(*, to, subject, text_body, html_body=None, config=None):
            self.sent.append({'to': to, 'subject': subject, 'text': text_body})
            return True, mailer.MODE_CONSOLE, None

        mailer.send = fake_send
        return self

    def __exit__(self, *exc):
        mailer.send = self._original

    def last_token(self):
        assert self.sent, 'no email was sent'
        match = re.search(r'token=([A-Za-z0-9_\-]+)', self.sent[-1]['text'])
        assert match, f'no token in body: {self.sent[-1]["text"][:200]}'
        return match.group(1)


# --- terms of service -------------------------------------------------------

class TermsOfServiceTests(unittest.TestCase):

    def test_the_terms_page_is_public(self):
        with server_pg.app.test_client() as client:
            response = client.get('/terms')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['Content-Type'])

    def test_every_section_is_rendered(self):
        with server_pg.app.test_client() as client:
            html = client.get('/terms').get_data(as_text=True)
        # Sections carry anchor ids now, so the contents list can link to them.
        self.assertEqual(html.count('<section id="s'), len(terms.SECTIONS))
        for heading, _paragraphs in terms.SECTIONS:
            self.assertIn(heading, html)
        # And the body text itself, not just the headings. Compared against the
        # escaped form, because Jinja escapes apostrophes and ampersands - which
        # is the behaviour we want, so the test matches the rendered output.
        from markupsafe import escape

        for _heading, paragraphs in terms.SECTIONS:
            self.assertIn(str(escape(paragraphs[0][:60])), html)

    def test_the_page_shows_the_version_and_effective_date(self):
        with server_pg.app.test_client() as client:
            html = client.get('/terms').get_data(as_text=True)
        self.assertIn(terms.VERSION, html)
        self.assertIn(terms.EFFECTIVE_DATE, html)

    def test_the_recorded_version_matches_the_published_terms(self):
        """One constant, so accounts cannot be recorded against absent text."""
        self.assertEqual(accounts.CURRENT_TERMS_VERSION, terms.VERSION)

    def test_a_signup_records_the_published_version(self):
        delete_user('termsrec@reset.test')
        clear_rate_limits()
        with CapturedMail():
            with server_pg.app.test_client() as client:
                response = client.post('/api/signup', json={
                    'email': 'termsrec@reset.test', 'password': NEW_PASSWORD,
                    'password_confirmation': NEW_PASSWORD, 'terms_accepted': True})
        self.assertEqual(response.status_code, 202)
        with engine.connect() as conn:
            recorded = conn.execute(select(users.c.terms_version).where(
                users.c.email == 'termsrec@reset.test')).scalar_one()
        self.assertEqual(recorded, terms.VERSION)

        with server_pg.app.test_client() as client:
            html = client.get('/terms').get_data(as_text=True)
        self.assertIn(recorded, html,
                      'the recorded version must be the one actually published')

    def test_the_signup_checkbox_links_to_the_terms(self):
        with server_pg.app.test_client() as client:
            html = client.get('/signup').get_data(as_text=True)
        self.assertIn("href='/terms'", html)

    def test_the_signup_terms_link_opens_safely_in_a_new_tab(self):
        """A half-filled signup form must survive reading the terms.

        rel=noopener because target=_blank without it hands the opened page a
        reference back to the signup window.
        """
        with server_pg.app.test_client() as client:
            html = client.get('/signup').get_data(as_text=True)
        self.assertIn("target='_blank'", html)
        self.assertIn("rel='noopener'", html)

    def test_the_terms_are_reachable_with_no_session_at_all(self):
        """Readable before an account exists, so no auth and no redirect."""
        client = raw_client(server_pg.app)
        response = client.get('/terms')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Location', response.headers)

    def test_the_terms_link_from_signup_actually_resolves(self):
        """Follows the href the page publishes rather than a hardcoded path."""
        import re

        with server_pg.app.test_client() as client:
            signup = client.get('/signup').get_data(as_text=True)
            match = re.search(r"<a href='(/[^']*)' target='_blank'", signup)
            self.assertIsNotNone(match, 'no terms link found on the signup page')
            followed = client.get(match.group(1))
        self.assertEqual(followed.status_code, 200)
        self.assertIn(terms.VERSION, followed.get_data(as_text=True))

    def test_the_terms_page_uses_the_platform_design_system(self):
        """Not a bespoke stylesheet: the same tokens the rest of the site uses."""
        with server_pg.app.test_client() as client:
            html = client.get('/terms').get_data(as_text=True)
            tokens = client.get('/static/css/tokens.css')
        self.assertIn('/static/css/tokens.css', html)
        self.assertEqual(tokens.status_code, 200,
                         'the design system stylesheet must actually be served')
        self.assertIn('Instrument+Sans', html)
        self.assertIn('Martian+Mono', html)
        self.assertGreater(html.count('var(--'), 20,
                           'the page should style itself from design tokens')

    def test_the_terms_page_carries_no_hardcoded_palette(self):
        """Guards against the earlier ad-hoc dark-navy styling creeping back."""
        with server_pg.app.test_client() as client:
            html = client.get('/terms').get_data(as_text=True)
        for literal in ('#0b1220', '#eef3ff', '#ffba08'):
            self.assertNotIn(literal, html,
                             f'{literal} is not a design-system colour')

    def test_the_terms_page_has_navigable_structure(self):
        with server_pg.app.test_client() as client:
            html = client.get('/terms').get_data(as_text=True)
        self.assertEqual(html.count('<section id="s'), len(terms.SECTIONS))
        self.assertEqual(html.count('href="#s'), len(terms.SECTIONS),
                         'every section needs a contents entry')
        self.assertIn('try<span>Search</span>', html)

    def test_the_terms_page_escapes_its_content(self):
        """Rendered through Jinja, so a future clause with an ampersand is safe."""
        original = terms.SECTIONS
        terms.SECTIONS = (('Injection & <check>',
                           ['A clause with <b>markup</b> & an ampersand.']),)
        try:
            with server_pg.app.test_client() as client:
                html = client.get('/terms').get_data(as_text=True)
        finally:
            terms.SECTIONS = original
        self.assertIn('Injection &amp; &lt;check&gt;', html)
        self.assertNotIn('<b>markup</b>', html)

    def test_the_terms_contain_substantive_content(self):
        """Guards against the page degrading back into a placeholder."""
        total = sum(len(' '.join(paragraphs)) for _h, paragraphs in terms.SECTIONS)
        self.assertGreater(total, 2000, 'the terms must be real text')
        self.assertGreaterEqual(len(terms.SECTIONS), 8)


# --- forgot password --------------------------------------------------------

class ForgotPasswordTests(unittest.TestCase):

    def setUp(self):
        clear_rate_limits()

    def test_a_known_address_receives_a_reset_link(self):
        make_account('known@reset.test')
        with CapturedMail() as mail:
            with server_pg.app.test_client() as client:
                response = client.post('/api/forgot-password',
                                       json={'email': 'known@reset.test'})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(len(mail.sent), 1)
            self.assertIn('/reset-password?token=', mail.sent[0]['text'])

    def test_an_unknown_address_looks_identical(self):
        make_account('known2@reset.test')
        with CapturedMail():
            with server_pg.app.test_client() as client:
                known = client.post('/api/forgot-password',
                                    json={'email': 'known2@reset.test'})
            clear_rate_limits()
            with server_pg.app.test_client() as client:
                unknown = client.post('/api/forgot-password',
                                      json={'email': 'nobody@reset.test'})
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.get_json(), unknown.get_json())

    def test_a_malformed_address_also_looks_identical(self):
        """Probing must not distinguish invalid from unknown."""
        with CapturedMail():
            with server_pg.app.test_client() as client:
                malformed = client.post('/api/forgot-password',
                                        json={'email': 'not-an-email'})
            clear_rate_limits()
            with server_pg.app.test_client() as client:
                unknown = client.post('/api/forgot-password',
                                      json={'email': 'ghost@reset.test'})
        self.assertEqual(malformed.status_code, unknown.status_code)
        self.assertEqual(malformed.get_json(), unknown.get_json())

    def test_nothing_is_sent_for_an_unknown_address(self):
        with CapturedMail() as mail:
            with server_pg.app.test_client() as client:
                client.post('/api/forgot-password', json={'email': 'ghost2@reset.test'})
            self.assertEqual(mail.sent, [])

    def test_nothing_is_sent_for_a_deactivated_account(self):
        make_account('inactive@reset.test', active=False)
        with CapturedMail() as mail:
            with server_pg.app.test_client() as client:
                response = client.post('/api/forgot-password',
                                       json={'email': 'inactive@reset.test'})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(mail.sent, [])

    def test_an_unverified_account_can_still_reset(self):
        """Otherwise a user who mistyped their password is permanently stuck."""
        make_account('unverified@reset.test', verified=False)
        with CapturedMail() as mail:
            with server_pg.app.test_client() as client:
                client.post('/api/forgot-password',
                            json={'email': 'unverified@reset.test'})
            self.assertEqual(len(mail.sent), 1)

    def test_forgot_password_is_rate_limited_per_address(self):
        make_account('rl@reset.test')
        limit, _window = ratelimit.POLICIES['password_reset']
        statuses = []
        with CapturedMail():
            with server_pg.app.test_client() as client:
                for _ in range(limit + 2):
                    statuses.append(client.post('/api/forgot-password',
                                                json={'email': 'rl@reset.test'}).status_code)
        self.assertIn(429, statuses)

    def test_forgot_password_is_rate_limited_per_source(self):
        limit, _window = ratelimit.POLICIES['password_reset_ip']
        statuses = []
        with CapturedMail():
            with server_pg.app.test_client() as client:
                for index in range(limit + 2):
                    statuses.append(client.post('/api/forgot-password', json={
                        'email': f'mass{index}@reset.test'}).status_code)
        self.assertIn(429, statuses,
                      'varying the address must not defeat the limit')

    def test_forgot_password_requires_a_csrf_token(self):
        client = raw_client(server_pg.app)
        response = client.post('/api/forgot-password', json={'email': 'a@b.test'})
        self.assertEqual(response.status_code, 403)


# --- reset password ---------------------------------------------------------

class ResetPasswordTests(unittest.TestCase):

    def setUp(self):
        clear_rate_limits()

    def request_reset(self, email):
        make_account(email)
        with CapturedMail() as mail:
            with server_pg.app.test_client() as client:
                client.post('/api/forgot-password', json={'email': email})
            return mail.last_token()

    def test_a_valid_token_changes_the_password(self):
        token = self.request_reset('ok@reset.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/reset-password', json={
                'token': token, 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['next'], '/login')
        with engine.connect() as conn:
            stored = conn.execute(select(users.c.password_hash).where(
                users.c.email == 'ok@reset.test')).scalar_one()
        self.assertTrue(check_password_hash(stored, NEW_PASSWORD))
        self.assertFalse(check_password_hash(stored, OLD_PASSWORD))

    def test_the_new_password_is_hashed_not_stored_plaintext(self):
        token = self.request_reset('hash@reset.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            client.post('/api/reset-password', json={
                'token': token, 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
        with engine.connect() as conn:
            stored = conn.execute(select(users.c.password_hash).where(
                users.c.email == 'hash@reset.test')).scalar_one()
        self.assertNotIn(NEW_PASSWORD, stored)

    def test_the_user_can_log_in_with_the_new_password(self):
        token = self.request_reset('login@reset.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            client.post('/api/reset-password', json={
                'token': token, 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            good = client.post('/api/login', json={
                'username': 'login@reset.test', 'password': NEW_PASSWORD})
        self.assertEqual(good.status_code, 200)
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            old = client.post('/api/login', json={
                'username': 'login@reset.test', 'password': OLD_PASSWORD})
        self.assertEqual(old.status_code, 401, 'the old password must stop working')

    def test_reset_does_not_sign_the_user_in(self):
        token = self.request_reset('nosession@reset.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            client.post('/api/reset-password', json={
                'token': token, 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
            me = client.get('/api/me').get_json()
        self.assertFalse(me['logged_in'])

    def test_a_reused_token_is_refused(self):
        token = self.request_reset('reuse@reset.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            first = client.post('/api/reset-password', json={
                'token': token, 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
        self.assertEqual(first.status_code, 200)
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            second = client.post('/api/reset-password', json={
                'token': token, 'password': 'another-password-entirely',
                'password_confirmation': 'another-password-entirely'})
        self.assertEqual(second.status_code, 410)
        self.assertEqual(second.get_json()['code'], accounts.RESET_USED)
        with engine.connect() as conn:
            stored = conn.execute(select(users.c.password_hash).where(
                users.c.email == 'reuse@reset.test')).scalar_one()
        self.assertTrue(check_password_hash(stored, NEW_PASSWORD),
                        'the second attempt must not have changed anything')

    def test_an_expired_token_is_refused(self):
        token = self.request_reset('expired@reset.test')
        with engine.begin() as conn:
            conn.execute(update(email_verification_tokens)
                         .where(email_verification_tokens.c.token_hash
                                == accounts.hash_token(token))
                         .values(expires_at=datetime.utcnow() - timedelta(minutes=1)))
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/reset-password', json={
                'token': token, 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.get_json()['code'], accounts.RESET_EXPIRED)

    def test_an_invalid_token_is_refused(self):
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/reset-password', json={
                'token': 'nonsense', 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], accounts.RESET_INVALID)

    def test_a_verification_token_cannot_be_spent_as_a_reset(self):
        """The two purposes must not be interchangeable."""
        delete_user('purpose@reset.test')
        clear_rate_limits()
        with CapturedMail() as mail:
            with server_pg.app.test_client() as client:
                client.post('/api/signup', json={
                    'email': 'purpose@reset.test', 'password': OLD_PASSWORD,
                    'password_confirmation': OLD_PASSWORD, 'terms_accepted': True})
            verify_token = mail.last_token()
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/reset-password', json={
                'token': verify_token, 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], accounts.RESET_INVALID)

    def test_a_reset_token_cannot_be_spent_as_a_verification(self):
        token = self.request_reset('purpose2@reset.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/verify-email', json={'token': token})
        self.assertEqual(response.status_code, 400)

    def test_password_rules_match_signup(self):
        token = self.request_reset('weak@reset.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            short = client.post('/api/reset-password', json={
                'token': token, 'password': 'abc', 'password_confirmation': 'abc'})
        self.assertEqual(short.status_code, 400)
        self.assertEqual(short.get_json()['field'], 'password')

    def test_mismatched_confirmation_is_refused(self):
        token = self.request_reset('mismatch@reset.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            response = client.post('/api/reset-password', json={
                'token': token, 'password': NEW_PASSWORD,
                'password_confirmation': 'something-else-entirely'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'password_confirmation')

    def test_a_failed_validation_does_not_spend_the_token(self):
        token = self.request_reset('unspent@reset.test')
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            client.post('/api/reset-password', json={
                'token': token, 'password': 'abc', 'password_confirmation': 'abc'})
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            good = client.post('/api/reset-password', json={
                'token': token, 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
        self.assertEqual(good.status_code, 200,
                         'a rejected password must leave the link usable')

    def test_completing_a_reset_verifies_an_unverified_address(self):
        """Receiving the link proves inbox control, the same bar verification sets."""
        make_account('verifyviareset@reset.test', verified=False)
        clear_rate_limits()
        with CapturedMail() as mail:
            with server_pg.app.test_client() as client:
                client.post('/api/forgot-password',
                            json={'email': 'verifyviareset@reset.test'})
            token = mail.last_token()
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            client.post('/api/reset-password', json={
                'token': token, 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
        with engine.connect() as conn:
            verified = conn.execute(select(users.c.email_verified_at).where(
                users.c.email == 'verifyviareset@reset.test')).scalar_one()
        self.assertIsNotNone(verified)

    def test_a_second_outstanding_link_is_void_after_a_reset(self):
        email = 'twolinks@reset.test'
        make_account(email)
        with CapturedMail() as mail:
            with server_pg.app.test_client() as client:
                client.post('/api/forgot-password', json={'email': email})
            first = mail.last_token()
            clear_rate_limits()
            with server_pg.app.test_client() as client:
                client.post('/api/forgot-password', json={'email': email})
            second = mail.last_token()
        self.assertNotEqual(first, second)
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            used = client.post('/api/reset-password', json={
                'token': second, 'password': NEW_PASSWORD,
                'password_confirmation': NEW_PASSWORD})
        self.assertEqual(used.status_code, 200)
        clear_rate_limits()
        with server_pg.app.test_client() as client:
            stale = client.post('/api/reset-password', json={
                'token': first, 'password': 'yet-another-password',
                'password_confirmation': 'yet-another-password'})
        self.assertEqual(stale.status_code, 410)

    def test_reset_requires_a_csrf_token(self):
        client = raw_client(server_pg.app)
        response = client.post('/api/reset-password', json={'token': 'x'})
        self.assertEqual(response.status_code, 403)

    def test_the_reset_pages_render(self):
        with server_pg.app.test_client() as client:
            forgot = client.get('/forgot-password').get_data(as_text=True)
            reset = client.get('/reset-password').get_data(as_text=True)
        self.assertIn('/api/forgot-password', forgot)
        self.assertIn('/api/reset-password', reset)
        self.assertIn("id='csrf'", reset)

    def test_the_login_page_offers_the_reset_link(self):
        with server_pg.app.test_client() as client:
            html = client.get('/login').get_data(as_text=True)
        self.assertIn("href='/forgot-password'", html)


# --- signup remains unavailable without a provider --------------------------

class SignupUnavailableWithoutProviderTests(unittest.TestCase):

    def setUp(self):
        clear_rate_limits()

    def test_signup_returns_503_when_email_is_unconfigured(self):
        original = mailer.delivery_mode
        mailer.delivery_mode = lambda: mailer.MODE_UNCONFIGURED
        try:
            delete_user('unavail@reset.test')
            with server_pg.app.test_client() as client:
                response = client.post('/api/signup', json={
                    'email': 'unavail@reset.test', 'password': NEW_PASSWORD,
                    'password_confirmation': NEW_PASSWORD, 'terms_accepted': True})
        finally:
            mailer.delivery_mode = original
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'email_unconfigured')

    def test_no_account_is_created_when_email_is_unconfigured(self):
        original = mailer.delivery_mode
        mailer.delivery_mode = lambda: mailer.MODE_UNCONFIGURED
        try:
            delete_user('unavail2@reset.test')
            with server_pg.app.test_client() as client:
                client.post('/api/signup', json={
                    'email': 'unavail2@reset.test', 'password': NEW_PASSWORD,
                    'password_confirmation': NEW_PASSWORD, 'terms_accepted': True})
        finally:
            mailer.delivery_mode = original
        with engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(users).where(
                users.c.email == 'unavail2@reset.test')).scalar_one()
        self.assertEqual(count, 0, 'an unconfirmable account must not be created')

    def test_forgot_password_also_refuses_when_unconfigured(self):
        original = mailer.delivery_mode
        mailer.delivery_mode = lambda: mailer.MODE_UNCONFIGURED
        try:
            with server_pg.app.test_client() as client:
                response = client.post('/api/forgot-password',
                                       json={'email': 'anyone@reset.test'})
        finally:
            mailer.delivery_mode = original
        self.assertEqual(response.status_code, 503)


# --- delivery method --------------------------------------------------------

class DeliveryMethodTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        now = datetime.utcnow()
        with engine.begin() as conn:
            existing = conn.execute(select(users.c.id).where(
                users.c.username == 'dm_admin')).scalar_one_or_none()
            if existing is None:
                conn.execute(insert(users).values(
                    username='dm_admin', email='dm_admin@reset.test',
                    password_hash=generate_password_hash(OLD_PASSWORD),
                    created_at=now, is_platform_admin=True, is_active=True,
                    email_verified_at=now))
            else:
                conn.execute(update(users).where(users.c.id == existing)
                             .values(is_platform_admin=True, is_active=True))

    def setUp(self):
        clear_rate_limits()
        with engine.begin() as conn:
            conn.execute(delete(system_settings).where(
                system_settings.c.key.like(email_settings.PREFIX + '%')))

    def tearDown(self):
        """Email settings are global; do not leak them to other modules."""
        wipe_email_settings()

    def login(self, client):
        return client.post('/api/login',
                           json={'username': 'dm_admin', 'password': OLD_PASSWORD})

    def test_delivery_methods_are_offered(self):
        with server_pg.app.test_client() as client:
            self.login(client)
            body = client.get('/api/admin/email-settings').get_json()
        keys = {m['key'] for m in body['delivery_methods']}
        self.assertEqual(keys, {'system', 'smtp'})

    def test_smtp_delivery_is_saved_and_reported(self):
        with server_pg.app.test_client() as client:
            self.login(client)
            response = client.put('/api/admin/email-settings', json={
                'delivery_method': 'smtp', 'provider': 'brevo', 'enabled': True,
                'host': 'smtp-relay.brevo.com', 'port': 587, 'security': 'starttls',
                'username': 'u', 'password': 'p', 'from_name': 'trySearch',
                'from_email': 'no-reply@trysearch.example'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['settings']['delivery_method'], 'smtp')

    def test_system_delivery_is_saved_and_reported(self):
        with server_pg.app.test_client() as client:
            self.login(client)
            response = client.put('/api/admin/email-settings', json={
                'delivery_method': 'system', 'provider': 'system_mail',
                'enabled': True, 'from_name': 'trySearch',
                'from_email': 'no-reply@trysearch.example'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['settings']['delivery_method'], 'system')

    def test_a_mismatched_method_and_provider_is_refused(self):
        """system_mail cannot be delivered over SMTP, or the reverse."""
        with server_pg.app.test_client() as client:
            self.login(client)
            wrong_a = client.put('/api/admin/email-settings', json={
                'delivery_method': 'smtp', 'provider': 'system_mail', 'enabled': True,
                'host': 'x.example', 'port': 587, 'security': 'starttls',
                'from_name': 'trySearch', 'from_email': 'a@b.test'})
            wrong_b = client.put('/api/admin/email-settings', json={
                'delivery_method': 'system', 'provider': 'brevo', 'enabled': True,
                'from_name': 'trySearch', 'from_email': 'a@b.test'})
        for response in (wrong_a, wrong_b):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()['field'], 'delivery_method')

    def test_an_unknown_delivery_method_is_refused(self):
        with server_pg.app.test_client() as client:
            self.login(client)
            response = client.put('/api/admin/email-settings', json={
                'delivery_method': 'carrier_pigeon', 'provider': 'brevo',
                'enabled': False, 'from_name': 'trySearch', 'from_email': ''})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'delivery_method')

    def test_an_omitted_method_follows_the_provider(self):
        """Backwards compatible with a payload written before this field existed."""
        with server_pg.app.test_client() as client:
            self.login(client)
            response = client.put('/api/admin/email-settings', json={
                'provider': 'system_mail', 'enabled': True,
                'from_name': 'trySearch', 'from_email': 'a@b.test'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['settings']['delivery_method'], 'system')


if __name__ == '__main__':
    unittest.main()
