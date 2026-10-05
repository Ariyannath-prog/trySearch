"""Platform-admin email provider configuration.

Every endpoint is behind `require_platform_admin_api()`, the same tier that guards
plans and provider credentials. Organization owners, workspace members and client
viewers have no route to any of this.

The SMTP password enters through PUT and leaves through nothing. No response here
contains it, no log line records it, and the connection test reports failures by
class rather than echoing SMTP server text, which can quote the username back.
"""

from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app import email_settings, mailer
from app.admin_audit import write_admin_audit
from app.admin_auth import get_platform_admin, require_platform_admin_api
from app.db import engine
from app.models import users

admin_email_bp = Blueprint('admin_email', __name__, url_prefix='/api/admin')


def _actor_label(actor):
    return actor.get('username') or f"user #{actor.get('id')}"


@admin_email_bp.route('/email-settings', methods=['GET'])
def get_email_settings():
    """Current configuration, the provider catalog, and effective status."""
    error = require_platform_admin_api()
    if error:
        return error

    settings = email_settings.public_settings()

    updated_by = settings.get('updated_by')
    if updated_by:
        try:
            with engine.connect() as conn:
                settings['updated_by_username'] = conn.execute(
                    select(users.c.username).where(users.c.id == int(updated_by))
                ).scalar_one_or_none()
        except (TypeError, ValueError):
            settings['updated_by_username'] = None

    return jsonify({
        'settings': settings,
        'providers': email_settings.provider_catalog(),
        'security_options': list(email_settings.SECURITY_OPTIONS),
        'delivery_methods': [
            {'key': email_settings.DELIVERY_SYSTEM,
             'label': 'System mail (local MTA)'},
            {'key': email_settings.DELIVERY_SMTP,
             'label': 'External SMTP'},
        ],
        # Which configuration would actually be used for the next send, so an
        # admin can see when stale environment variables are still winning.
        'status': mailer.mail_status(),
    })


@admin_email_bp.route('/email-settings', methods=['PUT'])
def put_email_settings():
    """Save configuration.

    Saving never sends anything. A test email is a separate, explicit action, so
    nobody is surprised by mail going out because they pressed Save.
    """
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    payload = request.get_json(silent=True)

    before = email_settings.public_settings()
    try:
        values, password = email_settings.validate(payload, existing=before)
    except email_settings.EmailSettingsError as settings_error:
        return jsonify({'error': settings_error.message,
                        'field': settings_error.field}), 400

    with engine.begin() as conn:
        email_settings.save(values, password, actor_user_id=actor['id'], conn=conn)
        after = email_settings.public_settings(conn=conn)

        # The audit records that the secret changed, never the secret. `before`
        # and `after` come from public_settings(), which cannot contain it.
        write_admin_audit(
            actor['id'], 'email_settings.updated',
            target_type='email_settings', target_id='global',
            details={
                'before': {k: v for k, v in before.items()
                           if k not in ('password_mask',)},
                'after': {k: v for k, v in after.items()
                          if k not in ('password_mask',)},
                'password_changed': password is not None,
            },
            request=request, conn=conn,
        )

    return jsonify({'status': 'saved', 'settings': after,
                    'status_effective': mailer.mail_status()})


@admin_email_bp.route('/email-settings/password', methods=['DELETE'])
def delete_email_password():
    """Remove the stored SMTP password without clearing the rest."""
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    with engine.begin() as conn:
        email_settings.clear_password(conn=conn)
        write_admin_audit(
            actor['id'], 'email_settings.password_cleared',
            target_type='email_settings', target_id='global',
            details={'password_changed': True}, request=request, conn=conn,
        )
    return jsonify({'status': 'cleared',
                    'settings': email_settings.public_settings()})


@admin_email_bp.route('/email-settings/test-connection', methods=['POST'])
def test_email_connection():
    """Connect, negotiate security and authenticate. Sends no message.

    Tests the *saved* configuration, so what is verified is what will actually be
    used. Draft values are not accepted here: verifying an unsaved host would
    report success for a configuration the application would not use.
    """
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    ok, detail = mailer.verify_connection()

    write_admin_audit(
        actor['id'], 'email_settings.connection_tested',
        target_type='email_settings', target_id='global',
        details={'ok': ok, 'detail': detail}, request=request,
    )

    return jsonify({'ok': ok, 'detail': detail,
                    'status': mailer.mail_status()}), (200 if ok else 502)


@admin_email_bp.route('/email-settings/test-email', methods=['POST'])
def send_test_email():
    """Send a diagnostic message to an address the admin typed.

    The recipient is always explicit. Defaulting it to the admin's own address
    would make it too easy to fire mail without meaning to, and defaulting it to
    anything else would be worse.
    """
    error = require_platform_admin_api()
    if error:
        return error

    actor = get_platform_admin()
    data = request.get_json(silent=True) or {}
    recipient = (data.get('to') or '').strip()

    if not recipient:
        return jsonify({
            'error': 'Enter the address to send the test to.', 'field': 'to'}), 400
    if '@' not in recipient or ' ' in recipient:
        return jsonify({'error': 'Enter a valid email address.', 'field': 'to'}), 400

    mode = mailer.delivery_mode()
    if mode == mailer.MODE_UNCONFIGURED:
        return jsonify({
            'error': 'Configure and enable an email provider first.',
            'code': 'email_unconfigured',
        }), 409

    ok, send_mode, send_error = mailer.send_test_email(
        to=recipient, requested_by=_actor_label(actor))

    write_admin_audit(
        actor['id'], 'email_settings.test_email_sent',
        target_type='email_settings', target_id='global',
        details={'ok': ok, 'mode': send_mode, 'recipient': recipient},
        request=request,
    )

    if not ok:
        return jsonify({'ok': False, 'error': send_error, 'mode': send_mode}), 502

    message = ('Test email sent.' if send_mode == mailer.MODE_CONFIGURED
               else 'Email is in console mode on this environment; the message was '
                    'written to the application log instead of being sent.')
    return jsonify({'ok': True, 'mode': send_mode, 'message': message,
                    'sent_at': datetime.utcnow().isoformat()})
