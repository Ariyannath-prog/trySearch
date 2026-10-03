"""Application factory and blueprint registration."""

from datetime import timedelta

from flask import Flask

from app.config import BASE_DIR, IS_PRODUCTION


def create_app():
    # The monolith was Flask(__name__, static_folder='.') from the repo root, so
    # static_folder resolved to the repo root. From inside this package __name__ is
    # 'app', which would resolve to repo/app/ and break every static file and page
    # route. BASE_DIR pins both back to the repo root.
    app = Flask(
        __name__,
        static_folder=BASE_DIR,
        static_url_path='',
        root_path=BASE_DIR,
    )

    import os
    secret_key = os.environ.get('SECRET_KEY')
    if IS_PRODUCTION and not secret_key:
        raise RuntimeError('SECRET_KEY must be set when APP_ENV=production.')
    app.secret_key = secret_key or 'dev-secret-key-change-me'
    app.permanent_session_lifetime = timedelta(days=30)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=IS_PRODUCTION,
    )

    # The monolith created the schema and pinned the database identity as an import
    # side effect. Same work, same point in startup, now explicit.
    from app.db import bootstrap_database
    bootstrap_database()

    # Imported here rather than at module scope so that importing app.config or
    # app.db does not pull in every blueprint.
    from app.auth import auth_bp
    from app.integrations.gsc import gsc_bp
    from app.routes.analytics import analytics_bp
    from app.routes.admin_api import admin_api_bp
    from app.routes.admin_plans import admin_plans_bp, plans_bp
    from app.routes.audit import audit_bp
    from app.routes.admin import admin_bp
    from app.routes.content import content_bp
    from app.routes.evidence import evidence_bp
    from app.routes.onboarding import onboarding_bp
    from app.routes.pages import pages_bp
    from app.routes.prompts import prompts_bp
    from app.routes.reports import reports_bp
    from app.routes.sentiment import sentiment_bp

    for blueprint in (
        auth_bp,
        gsc_bp,
        analytics_bp,
        admin_api_bp,
        # Plan administration shares the /api/admin prefix but lives in its own
        # module, so admin_api.py does not keep growing.
        admin_plans_bp,
        plans_bp,
        audit_bp,
        admin_bp,
        content_bp,
        evidence_bp,
        onboarding_bp,
        pages_bp,
        prompts_bp,
        reports_bp,
        sentiment_bp,
    ):
        app.register_blueprint(blueprint)

    # Registered after the blueprints so the hook sees every route. CSRF refuses
    # state-changing /api/ requests by default rather than relying on each new
    # route remembering a decorator; app/security.py lists the two deliberate
    # exemptions and why they exist.
    from app.security import register_csrf
    register_csrf(app)

    @app.template_filter('humandate')
    def humandate(value):
        """ISO timestamp -> '31 Aug 2026'. A report is read by a buyer."""
        if not value:
            return ''
        from datetime import datetime as _dt
        text = str(value).replace('Z', '').split('.')[0]
        try:
            return _dt.fromisoformat(text).strftime('%d %b %Y')
        except ValueError:
            return str(value)

    from app.worker import register_cli
    register_cli(app)

    return app
