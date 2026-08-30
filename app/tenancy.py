"""The single isolation guard.

Every workspace-scoped read and write goes through require_workspace(). Nothing
hand-writes an ownership predicate any more: T5 removed user_id from the rows, and
this module is the only place that turns a session into "may this person touch this
workspace, and in what way".

Two deliberate choices:

* A non-member gets **404**, not 403. Telling someone "this exists but is not yours"
  leaks the existence of another tenant's workspace. A member whose role is too weak
  for the operation gets **403**, because they can already see the thing.
* Write access is inferred from the HTTP method by default. A route cannot forget to
  declare that it mutates, which is the failure mode a `write=True` argument invites.
"""

from collections import namedtuple
from datetime import datetime

from flask import jsonify, request, session
from sqlalchemy import insert, select

from app.db import engine
from app.models import (
    analytics_audit_jobs,
    content_documents,
    memberships,
    organizations,
    workspaces,
)

ROLES = ('owner', 'admin', 'member', 'client_viewer')

# client_viewer is the agency's end client: they see the report and change nothing.
WRITE_ROLES = frozenset({'owner', 'admin', 'member'})

# Roles allowed to administer an organization itself (billing, members, deletion).
ADMIN_ROLES = frozenset({'owner', 'admin'})

WRITE_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

Access = namedtuple('Access', 'user_id org_id role workspace')


def _unauthenticated():
    return jsonify({'error': 'Authentication required.'}), 401


def _not_found(noun='Workspace'):
    return jsonify({'error': f'{noun} not found.'}), 404


def _forbidden(role):
    return jsonify({
        'error': 'Your role does not allow this action.',
        'role': role,
    }), 403


def current_user_id():
    """The signed-in user's id, or (None, 401 response)."""
    user_id = session.get('user_id')
    if not user_id:
        return None, _unauthenticated()
    return user_id, None


def _is_write(write):
    if write is None:
        return request.method in WRITE_METHODS
    return bool(write)


def require_workspace(workspace_id, *, write=None):
    """Resolve the session user's access to a workspace.

    Returns (Access, None) when allowed, or (None, response) to return directly.
    `write` defaults to inferring from the request method.
    """
    user_id, error = current_user_id()
    if error:
        return None, error

    with engine.connect() as conn:
        row = conn.execute(
            select(
                workspaces,
                memberships.c.role.label('membership_role'),
                memberships.c.org_id.label('membership_org_id'),
            )
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (workspaces.c.id == workspace_id)
                & (memberships.c.user_id == user_id)
                & (workspaces.c.status == 'active')
            )
            .limit(1)
        ).mappings().first()

    # Not a member, or no such workspace: indistinguishable on purpose.
    if not row:
        return None, _not_found()

    row = dict(row)
    role = row.pop('membership_role')
    org_id = row.pop('membership_org_id')

    if _is_write(write) and role not in WRITE_ROLES:
        return None, _forbidden(role)

    return Access(user_id=user_id, org_id=org_id, role=role, workspace=row), None


def require_org(org_id, *, write=None, admin=False):
    """Resolve the session user's membership of an organization."""
    user_id, error = current_user_id()
    if error:
        return None, error

    with engine.connect() as conn:
        row = conn.execute(
            select(memberships.c.role, organizations.c.id)
            .join(organizations, organizations.c.id == memberships.c.org_id)
            .where(
                (memberships.c.org_id == org_id)
                & (memberships.c.user_id == user_id)
            )
            .limit(1)
        ).mappings().first()

    if not row:
        return None, _not_found('Organization')

    role = row['role']
    if admin and role not in ADMIN_ROLES:
        return None, _forbidden(role)
    if _is_write(write) and role not in WRITE_ROLES:
        return None, _forbidden(role)

    return Access(user_id=user_id, org_id=org_id, role=role, workspace=None), None


def workspace_for_member(workspace_id, user_id):
    """Scoped lookup for non-route code, returning the row or None.

    require_workspace() returns Flask responses, which is right for a route and
    useless inside metrics or an integration. This is the same predicate without
    the HTTP half - still membership-based, never a predicate on the row.
    """
    with engine.connect() as conn:
        row = conn.execute(
            select(workspaces)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (workspaces.c.id == workspace_id)
                & (memberships.c.user_id == user_id)
                & (workspaces.c.status == 'active')
            )
            .limit(1)
        ).mappings().first()
    return dict(row) if row else None


def workspaces_for_user(user_id):
    """Every active workspace the user can reach, newest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(workspaces, memberships.c.role.label('role'))
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (memberships.c.user_id == user_id)
                & (workspaces.c.status == 'active')
            )
            .order_by(workspaces.c.updated_at.desc())
        ).mappings().all()
    return [dict(row) for row in rows]


def require_document(document_id, *, write=None):
    """Resolve access to a content document through its workspace.

    The route carries no workspace id, so the document's own workspace_id is the
    join key. Same 404-for-non-members rule as require_workspace.
    """
    user_id, error = current_user_id()
    if error:
        return None, None, error

    with engine.connect() as conn:
        row = conn.execute(
            select(content_documents, memberships.c.role.label('membership_role'),
                   memberships.c.org_id.label('membership_org_id'))
            .join(workspaces, workspaces.c.id == content_documents.c.workspace_id)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (content_documents.c.id == document_id)
                & (memberships.c.user_id == user_id)
                & (workspaces.c.status == 'active')
            )
            .limit(1)
        ).mappings().first()

    if not row:
        return None, None, _not_found('Content document')

    row = dict(row)
    role = row.pop('membership_role')
    org_id = row.pop('membership_org_id')
    if _is_write(write) and role not in WRITE_ROLES:
        return None, None, _forbidden(role)

    access = Access(user_id=user_id, org_id=org_id, role=role, workspace=None)
    return access, row, None


def documents_for_user(user_id):
    """Every content document in every workspace the user can reach."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(content_documents)
            .join(workspaces, workspaces.c.id == content_documents.c.workspace_id)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (memberships.c.user_id == user_id)
                & (workspaces.c.status == 'active')
            )
            .order_by(content_documents.c.updated_at.desc())
        ).mappings().all()
    return [dict(row) for row in rows]


def require_job(job_id, *, write=None):
    """Resolve access to a background job through the workspace that owns it."""
    user_id, error = current_user_id()
    if error:
        return None, None, error

    with engine.connect() as conn:
        row = conn.execute(
            select(analytics_audit_jobs, memberships.c.role.label('membership_role'),
                   memberships.c.org_id.label('membership_org_id'))
            .join(workspaces, workspaces.c.id == analytics_audit_jobs.c.workspace_id)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (analytics_audit_jobs.c.id == job_id)
                & (memberships.c.user_id == user_id)
            )
            .limit(1)
        ).mappings().first()

    if not row:
        return None, None, _not_found('Job')

    row = dict(row)
    role = row.pop('membership_role')
    org_id = row.pop('membership_org_id')
    if _is_write(write) and role not in WRITE_ROLES:
        return None, None, _forbidden(role)

    return Access(user_id=user_id, org_id=org_id, role=role, workspace=None), row, None


def default_org_for_user(user_id):
    """The org a new workspace belongs to, created with the user as owner if absent.

    Registration predates tenancy and creates only a users row, so the personal org
    is created lazily here rather than failing on first workspace creation.
    """
    with engine.connect() as conn:
        org_id = conn.execute(
            select(memberships.c.org_id)
            .where(memberships.c.user_id == user_id)
            .order_by(memberships.c.org_id)
            .limit(1)
        ).scalar_one_or_none()
    if org_id:
        return org_id
    with engine.begin() as conn:
        org_id = conn.execute(insert(organizations).values(
            name='Personal workspace', created_at=datetime.utcnow(),
        )).inserted_primary_key[0]
        conn.execute(insert(memberships).values(
            org_id=org_id, user_id=user_id, role='owner',
        ))
    return org_id
