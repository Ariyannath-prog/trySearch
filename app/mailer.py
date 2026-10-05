"""The application's one way to send email.

    EmailService
      -> SystemMailProvider   (hands off to the host's MTA)
      -> SmtpProvider         (any SMTP relay)

Every hosted vendor - Brevo, Amazon SES, Mailgun, SendGrid, SMTP2GO - is plain
SMTP, so they all run through SmtpProvider and differ only in the host/port an
admin saved. There is deliberately no vendor-specific code anywhere in the
application, and `send()` is the only entry point callers use.

Configuration is resolved in this order:

1. The database, when a platform admin has saved an **enabled** configuration
   (app/email_settings.py). This is the normal path.
2. `SMTP_*` environment variables, kept as a fallback so a deploy that sets them
   keeps working and so email can be configured before an admin can log in.
3. Nothing - in which case production refuses to send rather than pretending.

Credentials never appear in a return value, a log line or an error message. The
failure strings handed back to callers are written for an end user and contain no
host, username or password.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.config import IS_PRODUCTION

logger = logging.getLogger(__name__)

MODE_CONFIGURED = 'configured'
MODE_CONSOLE = 'console'
MODE_UNCONFIGURED = 'unconfigured'

SOURCE_DATABASE = 'database'
SOURCE_ENVIRONMENT = 'environment'
SOURCE_NONE = 'none'

# One generic message for any delivery failure. Anything more specific risks
# echoing a host or credential back to whoever triggered the send.
GENERIC_SEND_ERROR = 'Could not send the email. Please try again shortly.'


def _env(name, default=''):
    value = os.environ.get(name)
    return value.strip() if value else default


# --- configuration resolution ------------------------------------------------

def _environment_config():
    host = _env('SMTP_HOST')
    if not host:
        return None
    try:
        port = int(_env('SMTP_PORT', '587') or 587)
    except ValueError:
        port = 587
    if _env('SMTP_USE_SSL', 'false').lower() in ('1', 'true', 'yes'):
        security = 'ssl'
    elif _env('SMTP_USE_TLS', 'true').lower() not in ('0', 'false', 'no'):
        security = 'starttls'
    else:
        security = 'none'
    return {
        'enabled': True,
        'provider': 'custom_smtp',
        'transport': 'smtp',
        'host': host,
        'port': port,
        'security': security,
        'username': _env('SMTP_USERNAME'),
        'password': os.environ.get('SMTP_PASSWORD') or None,
        'from_name': _env('MAIL_FROM_NAME', 'trySearch'),
        'from_email': _env('MAIL_FROM'),
        'reply_to': '',
        'source': SOURCE_ENVIRONMENT,
    }


def resolve_config():
    """The configuration that would be used for the next send, or None."""
    try:
        from app import email_settings

        if email_settings.is_configured_in_database():
            config = email_settings.raw_settings()
            config['source'] = SOURCE_DATABASE
            return config
    except Exception as error:  # noqa: BLE001
        # A missing table or unreadable key must not take down sending outright;
        # fall through to the environment instead.
        logger.warning('Could not read email settings from the database: %s',
                       type(error).__name__)
    return _environment_config()


def delivery_mode():
    """configured | console | unconfigured. Contains no secrets."""
    config = resolve_config()
    if config and config.get('enabled'):
        if config.get('transport') == 'system':
            return MODE_CONFIGURED
        if config.get('host'):
            return MODE_CONFIGURED
    return MODE_UNCONFIGURED if IS_PRODUCTION else MODE_CONSOLE


def is_configured():
    return delivery_mode() == MODE_CONFIGURED


def mail_status():
    """Operator-facing status. Reports whether a password exists, never its value."""
    config = resolve_config() or {}
    return {
        'mode': delivery_mode(),
        'source': config.get('source', SOURCE_NONE),
        'provider': config.get('provider') or None,
        'transport': config.get('transport') or None,
        'host': config.get('host') or None,
        'port': config.get('port') if config.get('host') else None,
        'security': config.get('security') or None,
        'username_set': bool(config.get('username')),
        'password_set': bool(config.get('password')),
        'from_email': config.get('from_email') or None,
        'from_name': config.get('from_name') or None,
        'reply_to': config.get('reply_to') or None,
    }


def app_base_url():
    """Absolute base for links in email.

    Needed because sending has no request context to infer a host from, and a
    relative verification link cannot be clicked from an inbox.
    """
    return _env('APP_BASE_URL', 'https://trysearch.aevix.xyz').rstrip('/')


# --- message construction ----------------------------------------------------

def _build_message(config, *, to, subject, text_body, html_body=None):
    from_email = (config.get('from_email') or config.get('username')
                  or 'no-reply@localhost')
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = formataddr((config.get('from_name') or 'trySearch', from_email))
    message['To'] = to
    if config.get('reply_to'):
        message['Reply-To'] = config['reply_to']
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype='html')
    return message


# --- providers ---------------------------------------------------------------

class SystemMailProvider:
    """Hands the message to the host's own mail system.

    Delivery depends entirely on that MTA plus the host's SPF, DKIM and reverse
    DNS. A successful handoff here is not evidence the mail was accepted
    downstream, and the admin UI says so rather than implying otherwise.
    """

    key = 'system'

    def __init__(self, config):
        self.config = config

    def send(self, message):
        host = _env('SYSTEM_MAIL_HOST', '127.0.0.1')
        try:
            port = int(_env('SYSTEM_MAIL_PORT', '25') or 25)
        except ValueError:
            port = 25
        with smtplib.SMTP(host, port, timeout=20) as client:
            client.send_message(message)

    def verify(self):
        host = _env('SYSTEM_MAIL_HOST', '127.0.0.1')
        try:
            port = int(_env('SYSTEM_MAIL_PORT', '25') or 25)
        except ValueError:
            port = 25
        with smtplib.SMTP(host, port, timeout=15) as client:
            client.ehlo()
        return 'Connected to the local mail system.'


class SmtpProvider:
    """Any SMTP relay. One implementation for every hosted vendor."""

    key = 'smtp'

    def __init__(self, config):
        self.config = config

    def _connect(self, timeout):
        config = self.config
        host, port = config['host'], config['port'] or 587
        if config.get('security') == 'ssl':
            return smtplib.SMTP_SSL(host, port, timeout=timeout,
                                    context=ssl.create_default_context())
        return smtplib.SMTP(host, port, timeout=timeout)

    def _prepare(self, client):
        client.ehlo()
        if self.config.get('security') == 'starttls':
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        username = self.config.get('username')
        password = self.config.get('password')
        if username and password:
            client.login(username, password)

    def send(self, message):
        with self._connect(20) as client:
            self._prepare(client)
            client.send_message(message)

    def verify(self):
        """Connect, negotiate security and authenticate. Sends no message."""
        with self._connect(15) as client:
            self._prepare(client)
        return 'Connected and authenticated successfully.'


def provider_for(config):
    if config.get('transport') == 'system':
        return SystemMailProvider(config)
    return SmtpProvider(config)


# --- the service -------------------------------------------------------------

def send(*, to, subject, text_body, html_body=None, config=None):
    """Send one message. Returns (ok, mode, error).

    Never raises: a failed send must not take down the request that triggered it.
    The caller decides whether the failure is fatal to the user's flow.
    """
    mode = delivery_mode()

    if mode == MODE_UNCONFIGURED:
        # Recipient and subject withheld: this log line is about configuration,
        # not about who was emailed.
        logger.error('Email not sent: no email provider is configured.')
        return False, mode, 'Email delivery is not configured on this server.'

    resolved = config or resolve_config() or {}
    message = _build_message(resolved, to=to, subject=subject,
                             text_body=text_body, html_body=html_body)

    if mode == MODE_CONSOLE:
        # Development and tests only; production resolves to 'unconfigured'
        # instead, so this branch can never silently swallow real mail.
        logger.warning('[email:console] to=%s subject=%s\n%s', to, subject, text_body)
        return True, mode, None

    try:
        provider_for(resolved).send(message)
    except Exception as error:  # noqa: BLE001 - delivery must not break the request
        logger.error('Email send failed via %s: %s',
                     resolved.get('provider') or 'unknown', type(error).__name__)
        return False, mode, GENERIC_SEND_ERROR

    return True, mode, None


def verify_connection(config=None):
    """Test connectivity and credentials without sending. Returns (ok, detail).

    `detail` is written for an administrator and still never contains the
    password. SMTP error text can echo the username, so authentication failures
    are reported by class rather than verbatim.
    """
    resolved = config or resolve_config()
    if not resolved or not (resolved.get('host') or resolved.get('transport') == 'system'):
        return False, 'No email provider is configured yet.'

    try:
        return True, provider_for(resolved).verify()
    except smtplib.SMTPAuthenticationError:
        return False, 'The server rejected those credentials.'
    except smtplib.SMTPConnectError:
        return False, 'Could not open a connection to that host and port.'
    except smtplib.SMTPServerDisconnected:
        return False, 'The server closed the connection. Check the port and security setting.'
    except ssl.SSLError:
        return False, 'TLS negotiation failed. Check the security setting for this port.'
    except OSError as error:
        # Hostname and port are the admin's own input, so naming the failure
        # class is useful and discloses nothing they did not type.
        return False, f'Network error reaching the mail server ({type(error).__name__}).'
    except Exception as error:  # noqa: BLE001
        return False, f'Connection test failed ({type(error).__name__}).'


# --- message templates -------------------------------------------------------
# Deliberately plain: a verification mail that looks like marketing is a
# verification mail that lands in spam.

def verification_message(*, verify_url, expires_hours):
    subject = 'Confirm your trySearch email address'
    text_body = (
        'Welcome to trySearch.\n\n'
        'Confirm this email address to finish setting up your account:\n\n'
        f'{verify_url}\n\n'
        f'This link can be used once and expires in {expires_hours} hours.\n\n'
        'If you did not create a trySearch account, you can ignore this message.\n'
    )
    html_body = (
        '<!doctype html><html><body style="font-family:system-ui,sans-serif;'
        'line-height:1.6;color:#15121b">'
        '<h2 style="margin:0 0 12px">Welcome to trySearch</h2>'
        '<p>Confirm this email address to finish setting up your account.</p>'
        f'<p><a href="{verify_url}" style="display:inline-block;padding:11px 18px;'
        'background:#ed3b78;color:#fff;border-radius:8px;text-decoration:none;'
        'font-weight:700">Confirm email address</a></p>'
        f'<p style="color:#6b6475;font-size:13px">This link can be used once and '
        f'expires in {expires_hours} hours.</p>'
        f'<p style="color:#6b6475;font-size:13px">If the button does not work, paste '
        f'this into your browser:<br><span>{verify_url}</span></p>'
        '<p style="color:#6b6475;font-size:13px">If you did not create a trySearch '
        'account, you can ignore this message.</p>'
        '</body></html>'
    )
    return subject, text_body, html_body


def send_verification_email(*, to, verify_url, expires_hours):
    subject, text_body, html_body = verification_message(
        verify_url=verify_url, expires_hours=expires_hours)
    return send(to=to, subject=subject, text_body=text_body, html_body=html_body)


def test_message(*, requested_by=None):
    subject = 'trySearch test email'
    text_body = (
        'This is a test message from trySearch.\n\n'
        'If you received it, the email provider configured in the admin panel is '
        'able to send mail.\n'
    )
    if requested_by:
        text_body += f'\nRequested from the admin panel by: {requested_by}\n'
    return subject, text_body, None


def send_test_email(*, to, requested_by=None, config=None):
    """Send the diagnostic message to an address the admin typed explicitly."""
    subject, text_body, html_body = test_message(requested_by=requested_by)
    return send(to=to, subject=subject, text_body=text_body,
                html_body=html_body, config=config)


def password_reset_message(*, reset_url, expires_hours):
    subject = 'Reset your trySearch password'
    text_body = (
        'Someone asked to reset the password for this trySearch account.\n\n'
        'If it was you, use this link to choose a new password:\n\n'
        f'{reset_url}\n\n'
        f'The link can be used once and expires in {expires_hours} hours.\n\n'
        'If it was not you, you can ignore this message - your password has not '
        'been changed.\n'
    )
    html_body = (
        '<!doctype html><html><body style="font-family:system-ui,sans-serif;'
        'line-height:1.6;color:#15121b">'
        '<h2 style="margin:0 0 12px">Reset your password</h2>'
        '<p>Someone asked to reset the password for this trySearch account.</p>'
        f'<p><a href="{reset_url}" style="display:inline-block;padding:11px 18px;'
        'background:#ed3b78;color:#fff;border-radius:8px;text-decoration:none;'
        'font-weight:700">Choose a new password</a></p>'
        f'<p style="color:#6b6475;font-size:13px">The link can be used once and '
        f'expires in {expires_hours} hours.</p>'
        f'<p style="color:#6b6475;font-size:13px">If the button does not work, paste '
        f'this into your browser:<br><span>{reset_url}</span></p>'
        '<p style="color:#6b6475;font-size:13px">If it was not you, ignore this '
        'message - your password has not been changed.</p>'
        '</body></html>'
    )
    return subject, text_body, html_body


def send_password_reset_email(*, to, reset_url, expires_hours):
    subject, text_body, html_body = password_reset_message(
        reset_url=reset_url, expires_hours=expires_hours)
    return send(to=to, subject=subject, text_body=text_body, html_body=html_body)
