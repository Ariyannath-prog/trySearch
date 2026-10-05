"""Platform Admin Control Center pages."""

from flask import Blueprint, render_template

from app.admin_auth import require_platform_admin_page

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.route('/')
def admin_overview():
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/overview.html')


@admin_bp.route('/users')
def admin_users():
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/users.html')


@admin_bp.route('/providers')
def admin_providers():
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/providers.html')


@admin_bp.route('/organizations')
def admin_organizations():
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/organizations.html')


@admin_bp.route('/plans')
def admin_plans():
    """Plan and entitlement management.

    Page shell only: every field, limit and entitlement key is fetched from the
    Phase A admin API at runtime, so the commercial model stays admin-controlled
    and nothing about pricing is baked into this template.
    """
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/plans.html')


@admin_bp.route('/workspaces')
def admin_workspaces():
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/workspaces.html')


@admin_bp.route('/engines')
def admin_engines():
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/engines.html')


@admin_bp.route('/api-keys')
def admin_api_keys():
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/api_keys.html')


@admin_bp.route('/audit')
def admin_audit():
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/audit.html')


@admin_bp.route('/email')
def admin_email():
    """Email provider configuration.

    Page shell only: the provider catalog, current settings and effective status
    all come from /api/admin/email-settings at runtime. No credential is ever
    rendered into this template.
    """
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/email.html')


@admin_bp.route('/settings')
def admin_settings():
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/settings.html')


@admin_bp.route('/system')
def admin_system():
    error = require_platform_admin_page()
    if error:
        return error
    return render_template('admin/system.html')
