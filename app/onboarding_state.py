"""Where a user is in onboarding, derived from existing state.

Nothing new is stored. The stage is read from the tables onboarding already
writes - workspaces, analytics_tracked_prompts, workspace_engines,
analytics_audit_jobs and analytics_prompt_scan_runs - so there is no second
source of truth to keep in step with reality, and no migration.

The ordering of the checks is the important part, and it is deliberate:

**Completeness is tested before any step-level check.** A workspace that has been
scanned is finished, full stop. Pre-existing production workspaces were created
before the engine-selection step existed, so they have zero `workspace_engines`
rows and zero competitors; testing for those first would class a real customer's
established workspace as half-built and drag them back through onboarding. By
asking "has this ever produced a scan?" first, an established workspace can never
be mistaken for an abandoned one.

Absence of `workspace_engines` rows is also legitimate on its own terms:
scanning.enabled_engines() reads it as "use every platform-enabled engine". So it
only tells us anything about onboarding for a workspace that has never scanned.

Authorization is not re-implemented here. The set of workspaces a user may see
comes from tenancy.workspaces_for_user(), which already applies org membership and
the client_viewer workspace-grant rule.
"""

from sqlalchemy import func, select

from app.db import engine
from app.models import (
    analytics_audit_jobs,
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_tracked_prompts,
    workspace_engines,
)
from app.tenancy import workspaces_for_user

# Stages, in the order a new account passes through them.
STAGE_NOT_STARTED = 'not_started'      # no workspace yet
STAGE_NEEDS_ENGINES = 'needs_engines'  # workspace + prompts, no engine choice saved
STAGE_NEEDS_SCAN = 'needs_scan'        # engines chosen, first scan never requested
STAGE_SCANNING = 'scanning'            # first scan requested, no results yet
STAGE_COMPLETE = 'complete'            # at least one workspace has been scanned

# Which onboarding step the wizard should open on, per stage.
RESUME_FOR_STAGE = {
    STAGE_NOT_STARTED: 'domain',
    STAGE_NEEDS_ENGINES: 'engines',
    STAGE_NEEDS_SCAN: 'analysis',
    STAGE_SCANNING: 'analysis',
    STAGE_COMPLETE: None,
}

ONBOARDING_PATH = '/onboarding'
DASHBOARD_PATH = '/analytics'

# A job in one of these states means the first scan is under way.
ACTIVE_JOB_STATUSES = ('queued', 'running', 'failed_retryable')


def _workspace_signals(conn, workspace_ids):
    """Per-workspace counts of the artefacts each onboarding step leaves behind."""
    if not workspace_ids:
        return {}

    def _counts(table, column, **extra):
        statement = select(column, func.count()).where(column.in_(workspace_ids))
        for attribute, value in extra.items():
            statement = statement.where(getattr(table.c, attribute) == value)
        return dict(conn.execute(statement.group_by(column)).all())

    prompts = _counts(analytics_tracked_prompts,
                      analytics_tracked_prompts.c.workspace_id, active=True)
    engines = _counts(workspace_engines, workspace_engines.c.workspace_id)
    runs = _counts(analytics_prompt_scan_runs,
                   analytics_prompt_scan_runs.c.workspace_id)

    # Answers prove a scan actually produced evidence, not merely that a run row
    # exists. Counted through the run so the join stays workspace-scoped.
    answers = dict(conn.execute(
        select(analytics_prompt_scan_runs.c.workspace_id, func.count())
        .select_from(
            analytics_provider_answers.join(
                analytics_prompt_scan_runs,
                analytics_prompt_scan_runs.c.id
                == analytics_provider_answers.c.scan_run_id)
        )
        .where(analytics_prompt_scan_runs.c.workspace_id.in_(workspace_ids))
        .group_by(analytics_prompt_scan_runs.c.workspace_id)
    ).all())

    active_jobs = dict(conn.execute(
        select(analytics_audit_jobs.c.workspace_id, func.count())
        .where(
            analytics_audit_jobs.c.workspace_id.in_(workspace_ids),
            analytics_audit_jobs.c.job_type == 'prompt_scan',
            analytics_audit_jobs.c.status.in_(ACTIVE_JOB_STATUSES),
        )
        .group_by(analytics_audit_jobs.c.workspace_id)
    ).all())

    any_jobs = dict(conn.execute(
        select(analytics_audit_jobs.c.workspace_id, func.count())
        .where(
            analytics_audit_jobs.c.workspace_id.in_(workspace_ids),
            analytics_audit_jobs.c.job_type == 'prompt_scan',
        )
        .group_by(analytics_audit_jobs.c.workspace_id)
    ).all())

    return {
        workspace_id: {
            'active_prompts': prompts.get(workspace_id, 0),
            'engine_rows': engines.get(workspace_id, 0),
            'scan_runs': runs.get(workspace_id, 0),
            'answers': answers.get(workspace_id, 0),
            'active_scan_jobs': active_jobs.get(workspace_id, 0),
            'scan_jobs': any_jobs.get(workspace_id, 0),
        }
        for workspace_id in workspace_ids
    }


def _is_scanned(signals):
    """Has this workspace ever been scanned?

    A run row or any stored answer both count. A run that failed still means
    onboarding reached the end - the dashboard can show the failure, and trapping
    someone in the wizard because their first scan errored would be worse than
    letting them in to see why.
    """
    return signals['scan_runs'] > 0 or signals['answers'] > 0


def onboarding_state(user_id):
    """Resolve the user's onboarding stage and where they should be sent.

    Returns a dict safe to hand to a client: stage, next path, the workspace to
    resume, the step to resume on, and the signals the decision was made from
    (useful when a support question is "why did it send me there?").
    """
    workspaces = workspaces_for_user(user_id)

    if not workspaces:
        return {
            'stage': STAGE_NOT_STARTED,
            'next': ONBOARDING_PATH,
            'resume': RESUME_FOR_STAGE[STAGE_NOT_STARTED],
            'workspace_id': None,
            'workspace_count': 0,
            'onboarding_complete': False,
            'signals': {},
        }

    # workspaces_for_user() orders newest-updated first, which is the one a
    # half-finished onboarding would have been working on.
    ids = [row['id'] for row in workspaces]
    with engine.connect() as conn:
        signals = _workspace_signals(conn, ids)

    # Completeness first - see the module docstring for why the order matters.
    for row in workspaces:
        if _is_scanned(signals[row['id']]):
            return {
                'stage': STAGE_COMPLETE,
                'next': DASHBOARD_PATH,
                'resume': None,
                'workspace_id': row['id'],
                'workspace_count': len(workspaces),
                'onboarding_complete': True,
                'signals': signals[row['id']],
            }

    # Nothing has been scanned. Resume the most recently touched workspace.
    target = workspaces[0]
    target_signals = signals[target['id']]

    if target_signals['active_scan_jobs'] > 0 or target_signals['scan_jobs'] > 0:
        # The first scan was requested; results have not landed yet.
        stage = STAGE_SCANNING
    elif target_signals['engine_rows'] > 0:
        stage = STAGE_NEEDS_SCAN
    else:
        # No saved engine selection on a workspace that has never scanned: the
        # engine step was not reached. (On an established workspace this would
        # mean "all engines", which is why completeness is checked first.)
        stage = STAGE_NEEDS_ENGINES

    return {
        'stage': stage,
        'next': ONBOARDING_PATH,
        'resume': RESUME_FOR_STAGE[stage],
        'workspace_id': target['id'],
        'workspace_count': len(workspaces),
        'onboarding_complete': False,
        'signals': target_signals,
    }


def post_login_destination(user_id, *, email_verified):
    """Where to send someone immediately after they sign in.

    Verification comes first: an unconfirmed address cannot reach onboarding at
    all (the onboarding API refuses it), so pointing them anywhere else would be
    a dead end.
    """
    if not email_verified:
        return '/verify-email'
    return onboarding_state(user_id)['next']
