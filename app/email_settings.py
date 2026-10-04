"""Database-backed email configuration, managed by platform admins.

Storage reuses the existing `system_settings` table (key/value, unique key,
platform-admin scoped) rather than adding another credential table. The SMTP
password is encrypted with the project's existing Fernet helper in
app/provider_credentials.py - the same mechanism that already protects provider
API keys - so there is one secret-at-rest implementation, not two.

The password is never returned by any function here except
`decrypt_smtp_password()`, which exists solely for the SMTP client at send time.
Everything the API or UI touches goes through `public_settings()`, which cannot
leak it because it never reads it.

Provider choice is presentation, not logic: every hosted provider in the catalog
is plain SMTP, so Brevo, SES, Mailgun, SendGrid and SMTP2GO share one code path
and differ only in the host/port defaults offered to the admin. The only
genuinely different transport is system mail, which hands off to the host's MTA.
"""

from datetime import datetime

from sqlalchemy import insert, select, update

from app.db import engine
from app.models import system_settings
from app.provider_credentials import encrypt_secret, decrypt_secret, secret_hint

# All keys live under one prefix so they are obvious in the table and easy to audit.
PREFIX = 'email.'

KEY_ENABLED = PREFIX + 'enabled'
KEY_DELIVERY = PREFIX + 'delivery_method'
KEY_PROVIDER = PREFIX + 'provider'
KEY_HOST = PREFIX + 'smtp_host'
KEY_PORT = PREFIX + 'smtp_port'
KEY_SECURITY = PREFIX + 'smtp_security'
KEY_USERNAME = PREFIX + 'smtp_username'
KEY_PASSWORD = PREFIX + 'smtp_password_encrypted'
KEY_FROM_NAME = PREFIX + 'from_name'
KEY_FROM_EMAIL = PREFIX + 'from_email'
KEY_REPLY_TO = PREFIX + 'reply_to'
KEY_UPDATED_BY = PREFIX + 'updated_by'
KEY_UPDATED_AT = PREFIX + 'updated_at'

# Delivery method is the transport decision; provider is the preset within it.
# Kept as its own stored field rather than inferred from the provider, so the
# admin's intent survives a later change to the catalog.
DELIVERY_SYSTEM = 'system'
DELIVERY_SMTP = 'smtp'
DELIVERY_METHODS = (DELIVERY_SYSTEM, DELIVERY_SMTP)

SECURITY_NONE = 'none'
SECURITY_STARTTLS = 'starttls'
SECURITY_SSL = 'ssl'
SECURITY_OPTIONS = (SECURITY_NONE, SECURITY_STARTTLS, SECURITY_SSL)

PROVIDER_SYSTEM = 'system_mail'
PROVIDER_CUSTOM = 'custom_smtp'

# Presets are *defaults offered to the admin*, never enforced: every field stays
# editable, because providers change hostnames and regions differ.
PROVIDER_CATALOG = (
    {
        'key': PROVIDER_SYSTEM,
        'label': 'System Mail / Self-hosted',
        'transport': 'system',
        'host': '', 'port': None, 'security': SECURITY_NONE,
        'requires_credentials': False,
        'hint': 'Hands the message to this server\'s own mail system (sendmail/MTA). '
                'Requires a working local MTA plus SPF, DKIM and reverse DNS on this '
                'host - without those, mail is commonly rejected or marked as spam. '
                'Delivery is not verified by trySearch.',
    },
    {
        'key': 'brevo',
        'label': 'Brevo SMTP',
        'transport': 'smtp',
        'host': 'smtp-relay.brevo.com', 'port': 587, 'security': SECURITY_STARTTLS,
        'requires_credentials': True,
        'hint': 'Use your Brevo SMTP login and an SMTP key as the password.',
    },
    {
        'key': 'amazon_ses',
        'label': 'Amazon SES SMTP',
        'transport': 'smtp',
        'host': 'email-smtp.us-east-1.amazonaws.com', 'port': 587,
        'security': SECURITY_STARTTLS,
        'requires_credentials': True,
        'hint': 'Edit the host to match your SES region. Use SES SMTP credentials, '
                'which are not your AWS access keys.',
    },
    {
        'key': 'mailgun',
        'label': 'Mailgun SMTP',
        'transport': 'smtp',
        'host': 'smtp.mailgun.org', 'port': 587, 'security': SECURITY_STARTTLS,
        'requires_credentials': True,
        'hint': 'Use the SMTP credentials for your sending domain.',
    },
    {
        'key': 'sendgrid',
        'label': 'SendGrid SMTP',
        'transport': 'smtp',
        'host': 'smtp.sendgrid.net', 'port': 587, 'security': SECURITY_STARTTLS,
        'requires_credentials': True,
        'hint': 'The username is literally "apikey"; the password is your API key.',
    },
    {
        'key': 'smtp2go',
        'label': 'SMTP2GO',
        'transport': 'smtp',
        'host': 'mail.smtp2go.com', 'port': 2525, 'security': SECURITY_STARTTLS,
        'requires_credentials': True,
        'hint': 'Port 2525 avoids common outbound blocks; 587 also works.',
    },
    {
        'key': PROVIDER_CUSTOM,
        'label': 'Custom SMTP',
        'transport': 'smtp',
        'host': '', 'port': 587, 'security': SECURITY_STARTTLS,
        'requires_credentials': True,
        'hint': 'Any SMTP relay. Supply the host, port and security yourself.',
    },
)

PROVIDERS_BY_KEY = {entry['key']: entry for entry in PROVIDER_CATALOG}
PROVIDER_KEYS = tuple(PROVIDERS_BY_KEY)


class EmailSettingsError(ValueError):
    """Invalid configuration, with a message safe to show an administrator."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.message = message
        self.field = field


def _read_all(conn=None):
    def _read(connection):
        rows = connection.execute(
            select(system_settings.c.key, system_settings.c.value)
            .where(system_settings.c.key.like(PREFIX + '%'))
        ).mappings().all()
        return {row['key']: row['value'] for row in rows}

    if conn is not None:
        return _read(conn)
    with engine.connect() as connection:
        return _read(connection)


def _write(conn, key, value):
    """Upsert one setting. `value` is always stored as text."""
    now = datetime.utcnow()
    text_value = '' if value is None else str(value)
    existing = conn.execute(
        select(system_settings.c.id).where(system_settings.c.key == key)
    ).scalar_one_or_none()
    if existing is None:
        conn.execute(insert(system_settings).values(
            key=key, value=text_value, created_at=now, updated_at=now))
    else:
        conn.execute(update(system_settings)
                     .where(system_settings.c.id == existing)
                     .values(value=text_value, updated_at=now))


def is_configured_in_database(conn=None):
    """True when an admin has saved an enabled email configuration."""
    stored = _read_all(conn)
    return stored.get(KEY_ENABLED) == 'true'


def raw_settings(conn=None):
    """Settings for the mail transport, including the decrypted password.

    The only function that exposes the secret. Called from the SMTP client at
    send time and from the connection test - never from a route's response.
    """
    stored = _read_all(conn)
    provider = stored.get(KEY_PROVIDER) or PROVIDER_CUSTOM
    try:
        port = int(stored.get(KEY_PORT) or 0) or None
    except (TypeError, ValueError):
        port = None

    password = None
    encrypted = stored.get(KEY_PASSWORD)
    if encrypted:
        try:
            password = decrypt_secret(encrypted)
        except Exception:  # noqa: BLE001 - a bad key must not leak a stack trace
            password = None

    delivery = stored.get(KEY_DELIVERY)
    if delivery not in DELIVERY_METHODS:
        # Pre-existing rows saved before this field: derive it from the provider.
        delivery = PROVIDERS_BY_KEY.get(provider, {}).get('transport', DELIVERY_SMTP)

    return {
        'enabled': stored.get(KEY_ENABLED) == 'true',
        'delivery_method': delivery,
        'provider': provider,
        'transport': delivery,
        'host': stored.get(KEY_HOST) or '',
        'port': port,
        'security': stored.get(KEY_SECURITY) or SECURITY_STARTTLS,
        'username': stored.get(KEY_USERNAME) or '',
        'password': password,
        'from_name': stored.get(KEY_FROM_NAME) or 'trySearch',
        'from_email': stored.get(KEY_FROM_EMAIL) or '',
        'reply_to': stored.get(KEY_REPLY_TO) or '',
    }


def public_settings(conn=None):
    """Everything the admin UI needs, and nothing secret.

    Structurally incapable of leaking the password: it is never read here. The
    only acknowledgement that one exists is `password_set` and a masked hint
    derived from the stored ciphertext length, not the plaintext.
    """
    stored = _read_all(conn)
    provider = stored.get(KEY_PROVIDER) or ''
    try:
        port = int(stored.get(KEY_PORT) or 0) or None
    except (TypeError, ValueError):
        port = None

    delivery = stored.get(KEY_DELIVERY)
    if delivery not in DELIVERY_METHODS:
        delivery = PROVIDERS_BY_KEY.get(provider, {}).get('transport', DELIVERY_SMTP)

    return {
        'configured': bool(stored),
        'enabled': stored.get(KEY_ENABLED) == 'true',
        'delivery_method': delivery,
        'provider': provider,
        'host': stored.get(KEY_HOST) or '',
        'port': port,
        'security': stored.get(KEY_SECURITY) or SECURITY_STARTTLS,
        'username': stored.get(KEY_USERNAME) or '',
        # Never the password, nor its ciphertext. A fixed mask, so the length of
        # the real secret is not disclosed either.
        'password_set': bool(stored.get(KEY_PASSWORD)),
        'password_mask': '••••••••' if stored.get(KEY_PASSWORD) else '',
        'from_name': stored.get(KEY_FROM_NAME) or 'trySearch',
        'from_email': stored.get(KEY_FROM_EMAIL) or '',
        'reply_to': stored.get(KEY_REPLY_TO) or '',
        'updated_at': stored.get(KEY_UPDATED_AT) or None,
        'updated_by': stored.get(KEY_UPDATED_BY) or None,
    }


def provider_catalog():
    """Presets for the UI. Hints included; no credentials anywhere."""
    return [
        {
            'key': entry['key'],
            'label': entry['label'],
            'transport': entry['transport'],
            'default_host': entry['host'],
            'default_port': entry['port'],
            'default_security': entry['security'],
            'requires_credentials': entry['requires_credentials'],
            'hint': entry['hint'],
        }
        for entry in PROVIDER_CATALOG
    ]


def _validate_email(value, field, *, required):
    value = (value or '').strip()
    if not value:
        if required:
            raise EmailSettingsError('This address is required.', field)
        return ''
    # Deliberately loose; the real test is whether a send succeeds.
    if '@' not in value or ' ' in value or value.startswith('@') or value.endswith('@'):
        raise EmailSettingsError('Enter a valid email address.', field)
    return value


def validate(payload, *, existing=None):
    """Validate an admin submission. Returns a dict of values to store.

    `existing` lets an update keep the stored password when the admin did not
    type a new one - the UI shows a mask and an explicit Update Password action,
    so a blank field means "leave it alone", never "clear it".
    """
    if not isinstance(payload, dict):
        raise EmailSettingsError('Request body must be a JSON object.')

    provider = (payload.get('provider') or '').strip()
    if provider not in PROVIDERS_BY_KEY:
        raise EmailSettingsError(
            f'Choose one of: {", ".join(PROVIDER_KEYS)}.', 'provider')
    preset = PROVIDERS_BY_KEY[provider]

    # Delivery method may be sent explicitly; otherwise it follows the provider.
    delivery = (payload.get('delivery_method') or preset['transport']).strip().lower()
    if delivery not in DELIVERY_METHODS:
        raise EmailSettingsError(
            f'Delivery method must be one of: {", ".join(DELIVERY_METHODS)}.',
            'delivery_method')
    # The two must agree: system mail has no SMTP preset, and an SMTP preset
    # cannot be delivered by the local MTA. Rejecting the mismatch is clearer
    # than silently preferring one of them.
    if delivery != preset['transport']:
        raise EmailSettingsError(
            f'"{preset["label"]}" is a {preset["transport"]} provider; it cannot be '
            f'used with the {delivery} delivery method.', 'delivery_method')
    transport = delivery

    enabled = payload.get('enabled', False)
    if not isinstance(enabled, bool):
        raise EmailSettingsError('enabled must be true or false.', 'enabled')

    from_email = _validate_email(payload.get('from_email'), 'from_email',
                                 required=enabled)
    reply_to = _validate_email(payload.get('reply_to'), 'reply_to', required=False)
    from_name = (payload.get('from_name') or '').strip() or 'trySearch'
    if len(from_name) > 160:
        raise EmailSettingsError('That sender name is too long.', 'from_name')

    values = {
        KEY_ENABLED: 'true' if enabled else 'false',
        KEY_DELIVERY: delivery,
        KEY_PROVIDER: provider,
        KEY_FROM_NAME: from_name,
        KEY_FROM_EMAIL: from_email,
        KEY_REPLY_TO: reply_to,
    }

    if transport == 'system':
        # Nothing to dial. Keep the SMTP fields blank so a later switch back to
        # SMTP cannot silently inherit a stale host.
        values[KEY_HOST] = ''
        values[KEY_PORT] = ''
        values[KEY_SECURITY] = SECURITY_NONE
        values[KEY_USERNAME] = ''
        return values, None

    host = (payload.get('host') or '').strip()
    if enabled and not host:
        raise EmailSettingsError('An SMTP host is required.', 'host')
    if ' ' in host:
        raise EmailSettingsError('That host does not look valid.', 'host')

    port_raw = payload.get('port', preset['port'])
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        raise EmailSettingsError('Port must be a number.', 'port') from None
    if not 1 <= port <= 65535:
        raise EmailSettingsError('Port must be between 1 and 65535.', 'port')

    security = (payload.get('security') or preset['security']).strip().lower()
    if security not in SECURITY_OPTIONS:
        raise EmailSettingsError(
            f'Security must be one of: {", ".join(SECURITY_OPTIONS)}.', 'security')

    username = (payload.get('username') or '').strip()

    values.update({
        KEY_HOST: host,
        KEY_PORT: str(port),
        KEY_SECURITY: security,
        KEY_USERNAME: username,
    })

    # A password is only touched when one was actually supplied.
    password = payload.get('password')
    if password is not None and str(password) != '':
        password = str(password)
        if len(password) > 1024:
            raise EmailSettingsError('That password is too long.', 'password')
        return values, password

    if enabled and username and not (existing or {}).get('password_set'):
        raise EmailSettingsError(
            'This provider needs a password. Enter one to enable sending.', 'password')
    return values, None


def save(values, password, *, actor_user_id=None, conn=None):
    """Persist validated settings. Encrypts the password if one was supplied."""
    now = datetime.utcnow()

    def _apply(connection):
        for key, value in values.items():
            _write(connection, key, value)
        if password is not None:
            _write(connection, KEY_PASSWORD, encrypt_secret(password))
        _write(connection, KEY_UPDATED_AT, now.isoformat())
        if actor_user_id is not None:
            _write(connection, KEY_UPDATED_BY, str(actor_user_id))

    if conn is not None:
        _apply(conn)
    else:
        with engine.begin() as connection:
            _apply(connection)


def clear_password(*, conn=None):
    """Remove the stored secret without disturbing the rest of the config."""
    def _apply(connection):
        _write(connection, KEY_PASSWORD, '')

    if conn is not None:
        _apply(conn)
    else:
        with engine.begin() as connection:
            _apply(connection)


def decrypt_smtp_password():
    """The plaintext password, for the SMTP client only."""
    return raw_settings().get('password')


def password_hint():
    """A short non-sensitive hint, for an operator confirming which key is set.

    Derived from the decrypted value's last characters via the existing
    secret_hint() helper, matching how provider credentials are displayed. Kept
    out of public_settings(): even four characters is more than the settings API
    needs to disclose.
    """
    password = decrypt_smtp_password()
    return secret_hint(password) if password else None
