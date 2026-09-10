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
    from app.routes.audit import audit_bp
    from app.routes.content import content_bp
    from app.routes.evidence import evidence_bp
    from app.routes.onboarding import onboarding_bp
    from app.routes.pages import pages_bp
    from app.routes.prompts import prompts_bp

    for blueprint in (
        auth_bp,
        gsc_bp,
        analytics_bp,
        audit_bp,
        content_bp,
        evidence_bp,
        onboarding_bp,
        pages_bp,
        prompts_bp,
    ):
        app.register_blueprint(blueprint)

    from app.worker import register_cli
    register_cli(app)

    return app
