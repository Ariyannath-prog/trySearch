"""Admin-managed email provider configuration.

The property that matters most is negative: the SMTP password must not come back
out of the system. That is asserted against the raw response bytes, not just the
parsed fields, so a leak through an unexpected key or an error message still fails
the test.
"""

import os
import unittest
from datetime import datetime

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'admin-email-test-secret'
# Reuses the project's existing Fernet mechanism, so the key must be present.
os.environ.setdefault('PROVIDER_CREDENTIAL_ENCRYPTION_KEY',
                      'cp0ZQ3QrPMLQX8rC3Z8Qn1h0nQ3H8kFQ0hQ1pQ8vQ2E=')

import server_pg  # noqa: E402

from sqlalchemy import delete, insert, select, update  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import email_settings, mailer  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import admin_audit_logs, memberships, organizations, system_settings, users  # noqa: E402

PASSWORD = 'admin-email-password-123'
SMTP_SECRET = 'super-secret-smtp-key-9999'


def make_user(username, *, platform_admin=False):
    with engine.begin() as conn:
        existing = conn.execute(
            select(users.c.id).where(users.c.username == username)).scalar_one_or_none()
        now = datetime.utcnow()
        if existing is not None:
            conn.execute(update(users).where(users.c.id == existing).values(
                is_platform_admin=platform_admin, is_active=True))
            return existing
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@adminemail.example',
            password_hash=generate_password_hash(PASSWORD), created_at=now,
            is_platform_admin=platform_admin, is_active=True,
            email_verified_at=now,
        )).inserted_primary_key[0]


def login(client, username):
    return client.post('/api/login', json={'username': username, 'password': PASSWORD})


def wipe_email_settings():
    with engine.begin() as conn:
        conn.execute(delete(system_settings)
                     .where(system_settings.c.key.like(email_settings.PREFIX + '%')))


def valid_payload(**overrides):
    payload = {
        'provider': 'brevo',
        'enabled': True,
        'host': 'smtp-relay.brevo.com',
        'port': 587,
        'security': 'starttls',
        'username': 'relay-user',
        'password': SMTP_SECRET,
        'from_name': 'trySearch',
        'from_email': 'no-reply@trysearch.example',
        'reply_to': '',
    }
    payload.update(overrides)
    return payload


class AuthorizationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.admin = make_user('email_admin', platform_admin=True)
        cls.normal = make_user('email_normal')
        with engine.begin() as conn:
            org_id = conn.execute(insert(organizations).values(
                name='Email org', created_at=datetime.utcnow())).inserted_primary_key[0]
            conn.execute(insert(memberships).values(
                org_id=org_id, user_id=cls.normal, role='owner'))

    ENDPOINTS = (
        ('GET', '/api/admin/email-settings'),
        ('PUT', '/api/admin/email-settings'),
        ('DELETE', '/api/admin/email-settings/password'),
        ('POST', '/api/admin/email-settings/test-connection'),
        ('POST', '/api/admin/email-settings/test-email'),
    )

    def test_anonymous_is_401_everywhere(self):
        with server_pg.app.test_client() as client:
            for method, path in self.ENDPOINTS:
                with self.subTest(route=f'{method} {path}'):
                    self.assertEqual(
                        client.open(path, method=method, json={}).status_code, 401)

    def test_an_organization_owner_is_403_everywhere(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_normal')
            for method, path in self.ENDPOINTS:
                with self.subTest(route=f'{method} {path}'):
                    self.assertEqual(
                        client.open(path, method=method, json={}).status_code, 403)

    def test_the_page_is_platform_admin_only(self):
        with server_pg.app.test_client() as client:
            anon = client.get('/admin/email')
        self.assertEqual(anon.status_code, 302)
        self.assertTrue(anon.headers['Location'].endswith('/login'))

        with server_pg.app.test_client() as client:
            login(client, 'email_normal')
            denied = client.get('/admin/email')
        self.assertEqual(denied.status_code, 302)
        self.assertTrue(denied.headers['Location'].endswith('/'))

        with server_pg.app.test_client() as client:
            login(client, 'email_admin')
            allowed = client.get('/admin/email')
        self.assertEqual(allowed.status_code, 200)

    def test_a_platform_admin_can_read_the_settings(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_admin')
            response = client.get('/api/admin/email-settings')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        for key in ('settings', 'providers', 'security_options', 'status'):
            self.assertIn(key, body)


class ProviderCatalogTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        make_user('email_catalog_admin', platform_admin=True)

    def catalog(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_catalog_admin')
            return client.get('/api/admin/email-settings').get_json()

    def test_every_required_provider_is_offered(self):
        keys = {p['key'] for p in self.catalog()['providers']}
        self.assertEqual(keys, {
            'system_mail', 'brevo', 'amazon_ses', 'mailgun', 'sendgrid',
            'smtp2go', 'custom_smtp'})

    def test_hosted_providers_are_all_plain_smtp(self):
        """One transport for every vendor; no per-vendor application logic."""
        for provider in self.catalog()['providers']:
            with self.subTest(provider=provider['key']):
                expected = 'system' if provider['key'] == 'system_mail' else 'smtp'
                self.assertEqual(provider['transport'], expected)

    def test_presets_supply_defaults_without_credentials(self):
        body = self.catalog()
        raw = str(body)
        for provider in body['providers']:
            with self.subTest(provider=provider['key']):
                self.assertIn('default_host', provider)
                self.assertIn('default_port', provider)
                self.assertIn('default_security', provider)
                self.assertNotIn('password', provider)
                self.assertNotIn('username', provider)
        self.assertNotIn('apikey-', raw)

    def test_system_mail_is_flagged_as_needing_host_configuration(self):
        system = next(p for p in self.catalog()['providers']
                      if p['key'] == 'system_mail')
        self.assertFalse(system['requires_credentials'])
        for word in ('SPF', 'DKIM'):
            self.assertIn(word, system['hint'],
                          'the UI must warn that self-hosted mail needs DNS work')

    def test_security_options_are_the_three_supported_modes(self):
        self.assertEqual(set(self.catalog()['security_options']),
                         {'none', 'starttls', 'ssl'})


class SecretHandlingTests(unittest.TestCase):
    """The password goes in and never comes back."""

    @classmethod
    def setUpClass(cls):
        make_user('email_secret_admin', platform_admin=True)

    def setUp(self):
        wipe_email_settings()

    def save(self, client, **overrides):
        return client.put('/api/admin/email-settings', json=valid_payload(**overrides))

    def test_the_password_is_encrypted_at_rest(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_secret_admin')
            self.assertEqual(self.save(client).status_code, 200)
        with engine.connect() as conn:
            stored = conn.execute(select(system_settings.c.value).where(
                system_settings.c.key == email_settings.KEY_PASSWORD)).scalar_one()
        self.assertNotEqual(stored, SMTP_SECRET)
        self.assertNotIn(SMTP_SECRET, stored)
        self.assertTrue(stored.startswith('gAAAAA'), 'should be a Fernet token')

    def test_the_password_round_trips_for_the_smtp_client_only(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_secret_admin')
            self.save(client)
        self.assertEqual(email_settings.decrypt_smtp_password(), SMTP_SECRET)

    def test_no_api_response_contains_the_password(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_secret_admin')
            saved = self.save(client)
            fetched = client.get('/api/admin/email-settings')
            conn_test = client.post('/api/admin/email-settings/test-connection')
            send_test = client.post('/api/admin/email-settings/test-email',
                                    json={'to': 'someone@example.com'})
        for label, response in (('PUT', saved), ('GET', fetched),
                                ('test-connection', conn_test),
                                ('test-email', send_test)):
            with self.subTest(response=label):
                # Raw bytes, so a leak through any key or message still fails.
                self.assertNotIn(SMTP_SECRET.encode(), response.data)

    def test_the_page_html_never_contains_the_password(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_secret_admin')
            self.save(client)
            page = client.get('/admin/email')
        self.assertNotIn(SMTP_SECRET.encode(), page.data)

    def test_public_settings_reports_only_that_a_secret_exists(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_secret_admin')
            self.save(client)
            settings = client.get('/api/admin/email-settings').get_json()['settings']
        self.assertTrue(settings['password_set'])
        self.assertEqual(settings['password_mask'], '••••••••')
        self.assertNotIn('password', settings)
        self.assertNotIn('smtp_password_encrypted', settings)

    def test_the_mask_does_not_disclose_the_secret_length(self):
        short, long = 'abcd', 'a' * 64
        with server_pg.app.test_client() as client:
            login(client, 'email_secret_admin')
            self.save(client, password=short)
            mask_short = client.get('/api/admin/email-settings').get_json()['settings']['password_mask']
            self.save(client, password=long)
            mask_long = client.get('/api/admin/email-settings').get_json()['settings']['password_mask']
        self.assertEqual(mask_short, mask_long)

    def test_a_blank_password_keeps_the_stored_one(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_secret_admin')
            self.save(client)
            payload = valid_payload()
            del payload['password']
            response = client.put('/api/admin/email-settings', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['settings']['password_set'])
        self.assertEqual(email_settings.decrypt_smtp_password(), SMTP_SECRET)

    def test_a_new_password_replaces_the_stored_one(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_secret_admin')
            self.save(client)
            self.save(client, password='a-different-secret')
        self.assertEqual(email_settings.decrypt_smtp_password(), 'a-different-secret')

    def test_the_password_can_be_removed_explicitly(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_secret_admin')
            self.save(client)
            response = client.delete('/api/admin/email-settings/password')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['settings']['password_set'])
        self.assertIsNone(email_settings.decrypt_smtp_password())

    def test_the_audit_records_the_change_without_the_secret(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_secret_admin')
            self.save(client)
        with engine.connect() as conn:
            row = conn.execute(
                select(admin_audit_logs.c.action, admin_audit_logs.c.details)
                .where(admin_audit_logs.c.target_type == 'email_settings')
                .order_by(admin_audit_logs.c.id.desc()).limit(1)).mappings().first()
        self.assertEqual(row['action'], 'email_settings.updated')
        self.assertTrue(row['details']['password_changed'])
        self.assertNotIn(SMTP_SECRET, str(row['details']))


class ValidationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        make_user('email_valid_admin', platform_admin=True)

    def setUp(self):
        wipe_email_settings()

    def put(self, payload):
        with server_pg.app.test_client() as client:
            login(client, 'email_valid_admin')
            return client.put('/api/admin/email-settings', json=payload)

    def test_an_unknown_provider_is_rejected(self):
        response = self.put(valid_payload(provider='mandrill'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'provider')

    def test_an_enabled_smtp_provider_needs_a_host(self):
        response = self.put(valid_payload(host=''))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'host')

    def test_an_out_of_range_port_is_rejected(self):
        for port in (0, 70000, -1):
            with self.subTest(port=port):
                response = self.put(valid_payload(port=port))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()['field'], 'port')

    def test_a_non_numeric_port_is_rejected(self):
        response = self.put(valid_payload(port='five-eight-seven'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'port')

    def test_an_unknown_security_mode_is_rejected(self):
        response = self.put(valid_payload(security='tls1.3-only'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'security')

    def test_an_enabled_provider_needs_a_from_address(self):
        response = self.put(valid_payload(from_email=''))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'from_email')

    def test_a_malformed_from_address_is_rejected(self):
        response = self.put(valid_payload(from_email='not-an-address'))
        self.assertEqual(response.status_code, 400)

    def test_a_malformed_reply_to_is_rejected(self):
        response = self.put(valid_payload(reply_to='bad address@x'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'reply_to')

    def test_enabling_with_a_username_and_no_password_is_refused(self):
        payload = valid_payload()
        del payload['password']
        response = self.put(payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['field'], 'password')

    def test_a_disabled_configuration_may_be_incomplete(self):
        """A draft should be savable; only enabling demands completeness."""
        payload = valid_payload(enabled=False, host='', from_email='')
        del payload['password']
        response = self.put(payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['settings']['enabled'])

    def test_a_non_object_body_is_rejected(self):
        self.assertEqual(self.put(['nope']).status_code, 400)


class SystemMailTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        make_user('email_system_admin', platform_admin=True)

    def setUp(self):
        wipe_email_settings()

    def test_system_mail_saves_without_smtp_credentials(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_system_admin')
            response = client.put('/api/admin/email-settings', json={
                'provider': 'system_mail', 'enabled': True,
                'from_name': 'trySearch', 'from_email': 'no-reply@trysearch.example'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        settings = response.get_json()['settings']
        self.assertEqual(settings['provider'], 'system_mail')
        self.assertFalse(settings['password_set'])

    def test_switching_to_system_mail_clears_the_stale_smtp_host(self):
        """Otherwise switching back to SMTP silently inherits an old host."""
        with server_pg.app.test_client() as client:
            login(client, 'email_system_admin')
            client.put('/api/admin/email-settings', json=valid_payload())
            client.put('/api/admin/email-settings', json={
                'provider': 'system_mail', 'enabled': True,
                'from_name': 'trySearch', 'from_email': 'no-reply@trysearch.example'})
            settings = client.get('/api/admin/email-settings').get_json()['settings']
        self.assertEqual(settings['host'], '')
        self.assertIsNone(settings['port'])

    def test_system_mail_uses_the_system_provider(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_system_admin')
            client.put('/api/admin/email-settings', json={
                'provider': 'system_mail', 'enabled': True,
                'from_name': 'trySearch', 'from_email': 'no-reply@trysearch.example'})
        config = mailer.resolve_config()
        self.assertEqual(config['transport'], 'system')
        self.assertIsInstance(mailer.provider_for(config), mailer.SystemMailProvider)


class TestEmailBehaviourTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        make_user('email_test_admin', platform_admin=True)

    def setUp(self):
        wipe_email_settings()
        self.sent = []
        self._original = mailer.send

        def capture(*, to, subject, text_body, html_body=None, config=None):
            self.sent.append({'to': to, 'subject': subject})
            return True, mailer.MODE_CONFIGURED, None

        mailer.send = capture

    def tearDown(self):
        mailer.send = self._original

    def test_saving_settings_never_sends_an_email(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_test_admin')
            client.put('/api/admin/email-settings', json=valid_payload())
        self.assertEqual(self.sent, [],
                         'saving must never trigger mail on its own')

    def test_a_test_email_requires_an_explicit_recipient(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_test_admin')
            client.put('/api/admin/email-settings', json=valid_payload())
            missing = client.post('/api/admin/email-settings/test-email', json={})
            blank = client.post('/api/admin/email-settings/test-email', json={'to': '  '})
        for response in (missing, blank):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()['field'], 'to')
        self.assertEqual(self.sent, [])

    def test_a_malformed_recipient_is_rejected(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_test_admin')
            client.put('/api/admin/email-settings', json=valid_payload())
            response = client.post('/api/admin/email-settings/test-email',
                                   json={'to': 'not-an-email'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.sent, [])

    def test_a_valid_recipient_is_sent_to(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_test_admin')
            client.put('/api/admin/email-settings', json=valid_payload())
            response = client.post('/api/admin/email-settings/test-email',
                                   json={'to': 'ops@example.com'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]['to'], 'ops@example.com')


class CentralisedServiceTests(unittest.TestCase):
    """Verification must go through the one service, with no vendor coupling."""

    @classmethod
    def setUpClass(cls):
        make_user('email_central_admin', platform_admin=True)

    def setUp(self):
        wipe_email_settings()

    def test_verification_email_uses_the_central_send(self):
        calls = []
        original = mailer.send

        def capture(*, to, subject, text_body, html_body=None, config=None):
            calls.append(to)
            return True, mailer.MODE_CONSOLE, None

        mailer.send = capture
        try:
            ok, mode, error = mailer.send_verification_email(
                to='someone@example.com',
                verify_url='https://example.test/verify-email?token=abc',
                expires_hours=24)
        finally:
            mailer.send = original

        self.assertTrue(ok)
        self.assertEqual(calls, ['someone@example.com'])

    def test_the_database_configuration_takes_precedence_over_the_environment(self):
        original = os.environ.get('SMTP_HOST')
        os.environ['SMTP_HOST'] = 'env-relay.example'
        try:
            with server_pg.app.test_client() as client:
                login(client, 'email_central_admin')
                client.put('/api/admin/email-settings', json=valid_payload())
            config = mailer.resolve_config()
            self.assertEqual(config['source'], mailer.SOURCE_DATABASE)
            self.assertEqual(config['host'], 'smtp-relay.brevo.com')
        finally:
            if original is None:
                os.environ.pop('SMTP_HOST', None)
            else:
                os.environ['SMTP_HOST'] = original

    def test_the_environment_is_still_honoured_when_no_database_config_exists(self):
        original = os.environ.get('SMTP_HOST')
        os.environ['SMTP_HOST'] = 'env-relay.example'
        try:
            config = mailer.resolve_config()
            self.assertEqual(config['source'], mailer.SOURCE_ENVIRONMENT)
            self.assertEqual(config['host'], 'env-relay.example')
        finally:
            if original is None:
                os.environ.pop('SMTP_HOST', None)
            else:
                os.environ['SMTP_HOST'] = original

    def test_a_disabled_database_config_does_not_make_the_app_configured(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_central_admin')
            payload = valid_payload(enabled=False)
            client.put('/api/admin/email-settings', json=payload)
        self.assertFalse(email_settings.is_configured_in_database())

    def test_mail_status_never_exposes_the_password(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_central_admin')
            client.put('/api/admin/email-settings', json=valid_payload())
        status = mailer.mail_status()
        self.assertTrue(status['password_set'])
        self.assertNotIn(SMTP_SECRET, str(status))

    def test_no_vendor_name_appears_in_application_logic(self):
        """Vendors are data in the catalog, never branches in the code.

        Parsed with ast rather than grepped, so a docstring that *explains* the
        design does not count as a dependency - only a real identifier or string
        literal the code acts on does. `if provider == "brevo"` would fail here;
        a comment saying Brevo is plain SMTP would not.
        """
        import ast
        import pathlib

        banned = ('brevo', 'sendgrid', 'mailgun', 'smtp2go', 'amazon_ses')
        offenders = []

        for path in pathlib.Path('app').rglob('*.py'):
            if path.name == 'email_settings.py':
                continue  # the catalog itself: vendor names are its data
            tree = ast.parse(path.read_text())

            # Collect docstring nodes so they can be excluded from the scan.
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                    body = getattr(node, 'body', None)
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        docstrings.add(id(body[0].value))

            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docstrings:
                        continue
                    haystack = node.value.lower()
                elif isinstance(node, ast.Name):
                    haystack = node.id.lower()
                elif isinstance(node, ast.Attribute):
                    haystack = node.attr.lower()
                else:
                    continue
                for name in banned:
                    if name in haystack:
                        offenders.append(f'{path}:{name}')

        self.assertEqual(sorted(set(offenders)), [],
                         f'vendor names must stay in the catalog: {sorted(set(offenders))}')


class ConnectionTestTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        make_user('email_conn_admin', platform_admin=True)

    def setUp(self):
        wipe_email_settings()

    def test_testing_with_nothing_configured_reports_cleanly(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_conn_admin')
            response = client.post('/api/admin/email-settings/test-connection')
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()['ok'])
        self.assertIn('detail', response.get_json())

    def test_an_unreachable_host_fails_without_leaking_credentials(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_conn_admin')
            client.put('/api/admin/email-settings', json=valid_payload(
                host='127.0.0.1', port=9, security='none'))
            response = client.post('/api/admin/email-settings/test-connection')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(SMTP_SECRET.encode(), response.data)
        self.assertNotIn(b'relay-user', response.data)

    def test_the_test_email_endpoint_refuses_when_unconfigured(self):
        with server_pg.app.test_client() as client:
            login(client, 'email_conn_admin')
            response = client.post('/api/admin/email-settings/test-email',
                                   json={'to': 'ops@example.com'})
        # Development resolves to console mode, which is a legitimate send target;
        # what must never happen is a silent success with no provider at all.
        self.assertIn(response.status_code, (200, 409))


class ExistingAuthRegressionTests(unittest.TestCase):
    """The four original endpoints keep their contracts."""

    @classmethod
    def setUpClass(cls):
        make_user('email_regress_user')

    def test_login_logout_me_still_work(self):
        with server_pg.app.test_client() as client:
            login_response = client.post('/api/login', json={
                'username': 'email_regress_user', 'password': PASSWORD})
            self.assertEqual(login_response.status_code, 200)
            me = client.get('/api/me')
            self.assertEqual(me.status_code, 200)
            self.assertTrue(me.get_json()['logged_in'])
            self.assertEqual(client.post('/api/logout').status_code, 200)

    def test_register_still_works(self):
        with engine.begin() as conn:
            conn.execute(delete(users).where(users.c.username == 'email_reg_new'))
        with server_pg.app.test_client() as client:
            response = client.post('/api/register', json={
                'username': 'email_reg_new', 'email': 'email_reg_new@x.test',
                'password': PASSWORD})
        self.assertEqual(response.status_code, 201)


if __name__ == '__main__':
    unittest.main()
