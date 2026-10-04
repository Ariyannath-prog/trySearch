"""Outbound email over generic SMTP, configured entirely from the environment.

No vendor is hardcoded: any SMTP relay works, so moving between providers is a
configuration change rather than a code change. Credentials come from the
environment and are never logged, never returned in an API response and never
written to a file.

Three delivery states, and the distinction matters operationally:

* **configured** - SMTP_HOST is set. Mail is actually sent.
* **console** - no SMTP_HOST, and this is not production. The message is written to
  the application log so development and tests have a working flow without a relay.
  Returns a *successful* send, because nothing is broken; the message simply went to
  the log.
* **unconfigured** - no SMTP_HOST in production. `send()` returns a failure rather
  than pretending. The caller decides what to tell the user; signup must not report
  "check your email" when nothing was sent.

That last case is deliberate. A silent fake-send is the failure mode where a user
waits forever for a mail that was never going to arrive.
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


def _env(name, default=''):
    value = os.environ.get(name)
    return value.strip() if value else default


def smtp_settings():
    """Read SMTP configuration. Never returns the password to callers."""
    host = _env('SMTP_HOST')
    try:
        port = int(_env('SMTP_PORT', '587') or 587)
    except ValueError:
        port = 587
    return {
        'host': host,
        'port': port,
        'username': _env('SMTP_USERNAME'),
        'use_tls': _env('SMTP_USE_TLS', 'true').lower() not in ('0', 'false', 'no'),
        'use_ssl': _env('SMTP_USE_SSL', 'false').lower() in ('1', 'true', 'yes'),
        'from_email': _env('MAIL_FROM'),
        'from_name': _env('MAIL_FROM_NAME', 'trySearch'),
        'timeout': 20,
    }


def delivery_mode():
    """Which of the three states we are in. Safe to expose; contains no secrets."""
    if _env('SMTP_HOST'):
        return MODE_CONFIGURED
    return MODE_UNCONFIGURED if IS_PRODUCTION else MODE_CONSOLE


def is_configured():
    return delivery_mode() == MODE_CONFIGURED


def mail_status():
    """Operator-facing status for an admin/health view. No credentials."""
    settings = smtp_settings()
    return {
        'mode': delivery_mode(),
        'host': settings['host'] or None,
        'port': settings['port'] if settings['host'] else None,
        'username_set': bool(settings['username']),
        'password_set': bool(os.environ.get('SMTP_PASSWORD')),
        'from_email': settings['from_email'] or None,
        'use_tls': settings['use_tls'],
        'use_ssl': settings['use_ssl'],
    }


def app_base_url():
    """Absolute base for links in email.

    Required, because a verification link has to be absolute and the sending code
    has no request context to infer a host from. Falls back to the production
    domain rather than emitting a relative link that cannot be clicked.
    """
    return _env('APP_BASE_URL', 'https://trysearch.aevix.xyz').rstrip('/')


def _sender(settings):
    from_email = settings['from_email'] or settings['username']
    if not from_email:
        # Last resort so a console-mode message still has a plausible From.
        from_email = 'no-reply@localhost'
    return formataddr((settings['from_name'], from_email))


def send(*, to, subject, text_body, html_body=None):
    """Send one message. Returns (ok, mode, error).

    Never raises: a failed send must not take down the request that triggered it.
    The caller decides whether a failure is fatal to the user's flow.
    """
    mode = delivery_mode()
    settings = smtp_settings()

    if mode == MODE_UNCONFIGURED:
        logger.error('Email not sent: SMTP_HOST is not configured in production. '
                     'Recipient and subject withheld from this log line.')
        return False, mode, 'Email delivery is not configured on this server.'

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = _sender(settings)
    message['To'] = to
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype='html')

    if mode == MODE_CONSOLE:
        # Development and tests. The body is logged so the verification link is
        # reachable without a relay; this branch can never run in production
        # because delivery_mode() returns 'unconfigured' there.
        logger.warning('[email:console] to=%s subject=%s\n%s', to, subject, text_body)
        return True, mode, None

    try:
        if settings['use_ssl']:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings['host'], settings['port'],
                                  timeout=settings['timeout'], context=context) as client:
                _authenticate(client, settings)
                client.send_message(message)
        else:
            with smtplib.SMTP(settings['host'], settings['port'],
                              timeout=settings['timeout']) as client:
                client.ehlo()
                if settings['use_tls']:
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                _authenticate(client, settings)
                client.send_message(message)
    except Exception as error:  # noqa: BLE001 - delivery must not break the request
        # str(error) from smtplib does not contain the password, but the exception
        # type and a short message are all an operator needs here anyway.
        logger.error('Email send failed: %s: %s', type(error).__name__, str(error)[:200])
        return False, mode, 'Could not send the email. Please try again shortly.'

    return True, mode, None


def _authenticate(client, settings):
    password = os.environ.get('SMTP_PASSWORD')
    if settings['username'] and password:
        client.login(settings['username'], password)


# --- message templates -------------------------------------------------------
# Plain text first, HTML as an alternative. Kept deliberately plain: a verification
# mail that looks like marketing is a verification mail that lands in spam.

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
