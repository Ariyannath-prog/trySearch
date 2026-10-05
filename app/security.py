"""CSRF protection for browser state-changing requests.

This reuses the convention the codebase already established in
app/integrations/gsc.py: a `secrets.token_urlsafe(32)` held in the Flask session and
compared with `secrets.compare_digest`. That is a synchronizer token, so no new
dependency, no signing library and no second mechanism is introduced.

Two decisions worth stating:

* **Enforcement is a before_request hook, not a decorator.** A decorator has to be
  remembered on every new mutating route, and the one that gets forgotten is the one
  that matters. Refusing by default and listing the exemptions means a new endpoint
  is protected before anybody thinks about it.
* **The token lives in the session cookie, which is already HttpOnly, SameSite=Lax
  and Secure in production.** The browser reads it from an endpoint rather than from
  the cookie, which is why this is a synchronizer token and not double-submit.

SameSite=Lax already blocks the cookie on cross-site form POSTs. That is real but
partial - it says nothing about same-site script and does not cover older browsers -
so it is treated as defence in depth, not as the control.
"""

import secrets

from flask import jsonify, request, session

TOKEN_SESSION_KEY = 'csrf_token'
TOKEN_HEADER = 'X-CSRF-Token'
TOKEN_FORM_FIELD = 'csrf_token'
TOKEN_BYTES = 32

STATE_CHANGING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

# Endpoints that cannot carry a token and must stay reachable.
#
# Every entry is a deliberate decision, not an oversight:
#
# * /api/contacts is the public, unauthenticated contact form. There is no session
#   to ride, so CSRF has nothing to protect; the control that matters there is rate
#   limiting. index.html is a fully static page with no fetch and no form, and must
#   not be modified, so this exemption also guarantees the public site keeps working.
# * The Google OAuth callback is a cross-site GET redirect by construction. It is
#   already protected by its own `state` nonce (gsc.py), which is the correct
#   mechanism for that flow.
EXEMPT_PATHS = frozenset({
    '/api/contacts',
    '/api/analytics/integrations/google/callback',
})


def issue_token():
    """Return this session's CSRF token, creating one on first use."""
    token = session.get(TOKEN_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        session[TOKEN_SESSION_KEY] = token
    return token


def rotate_token():
    """Issue a fresh token. Called after a privilege change such as login."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    session[TOKEN_SESSION_KEY] = token
    return token


def submitted_token():
    """The token the client sent, from the header or a form field."""
    header = request.headers.get(TOKEN_HEADER)
    if header:
        return header
    if request.form:
        return request.form.get(TOKEN_FORM_FIELD)
    # JSON bodies may carry it too, so a caller that cannot set headers is not
    # locked out. Read silently: a malformed body is a 400 from the route, not here.
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        value = payload.get(TOKEN_FORM_FIELD)
        if isinstance(value, str):
            return value
    return None


def token_is_valid():
    expected = session.get(TOKEN_SESSION_KEY)
    provided = submitted_token()
    if not expected or not provided:
        return False
    return secrets.compare_digest(str(provided), str(expected))


def _is_guarded(path, method):
    if method not in STATE_CHANGING_METHODS:
        return False
    if path in EXEMPT_PATHS:
        return False
    # Only the JSON API mutates state. Page routes are GET-only, and the static
    # file route serves files.
    return path.startswith('/api/')


def register_csrf(app):
    """Refuse state-changing API requests that do not carry a valid token."""

    @app.before_request
    def _enforce_csrf():
        if not _is_guarded(request.path, request.method):
            return None
        if token_is_valid():
            return None
        return jsonify({
            'error': 'This request could not be verified. Reload the page and try again.',
            'code': 'csrf_invalid',
        }), 403

    # Make the token available to server-rendered templates (the admin panel and
    # the inline login/register pages) without each one having to ask for it.
    @app.context_processor
    def _csrf_context():
        return {'csrf_token': issue_token()}
