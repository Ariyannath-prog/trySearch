"""The white-label report.

PRD §6b: the report *is* the sales artifact, and it travels further than the
dashboard does. That drives two rules:

* **Every number carries its sample size**, same as T11. A report that hides `n`
  is worse than no report, because the reader has no way to discount it and no
  way to ask.
* **No section is ever filled with a fabricated number.** An empty section states
  which of the three empty states it is in, in words.
"""

import json
import secrets
from datetime import datetime, timedelta

from sqlalchemy import insert, select, update

from app.db import engine
from app.metrics import citation_domain_rollup, competitor_citation_gaps
from app.models import (
    analytics_prompt_scan_runs,
    analytics_tracked_prompts,
    report_shares,
    workspace_branding,
    workspaces,
)
from app.rollup import latest_metrics
from app.stats import (
    STATE_ABSENT,
    STATE_INSUFFICIENT,
    STATE_NOT_YET_RUN,
    score_envelope,
)
from app.utils import row_to_dict

SECTIONS = ('visibility', 'share_of_voice', 'citations', 'prompts', 'methodology')

DEFAULT_ACCENT = '#2f6df6'
SHARE_TOKEN_BYTES = 32
DEFAULT_SHARE_DAYS = 30

EMPTY_STATE_COPY = {
    STATE_NOT_YET_RUN: 'No scan has completed yet for this workspace.',
    STATE_ABSENT: 'Scans completed and the brand was not mentioned in any answer.',
    STATE_INSUFFICIENT: 'Too few answers in this period to report a score.',
}


def branding_for(workspace_id, conn):
    row = conn.execute(
        select(workspace_branding).where(
            workspace_branding.c.workspace_id == workspace_id)
    ).mappings().first()
    branding = dict(row) if row else {}
    return {
        'display_name': branding.get('display_name'),
        'logo_url': branding.get('logo_url'),
        'accent_colour': branding.get('accent_colour') or DEFAULT_ACCENT,
        # Below Enterprise the mark stays. Default is "show", so a missing row
        # never accidentally produces an unbranded report.
        'show_trysearch_mark': not branding.get('hide_trysearch_mark', False),
    }


def last_complete_run(workspace_id, conn):
    """The most recent finished run, and whether one is currently in progress.

    A report generated mid-scan uses the last *complete* run and says so, rather
    than reporting a half-finished cohort as if it were the period.
    """
    in_progress = conn.execute(
        select(analytics_prompt_scan_runs.c.id)
        .where(
            (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
            & (analytics_prompt_scan_runs.c.status == 'running')
        ).limit(1)
    ).scalar_one_or_none()

    complete = conn.execute(
        select(analytics_prompt_scan_runs)
        .where(
            (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
            & (analytics_prompt_scan_runs.c.status.in_(('succeeded', 'partial')))
        )
        .order_by(analytics_prompt_scan_runs.c.created_at.desc())
        .limit(1)
    ).mappings().first()

    return {
        'run': dict(complete) if complete else None,
        'scan_in_progress': bool(in_progress),
    }


def build_report(workspace_id, *, sections=None, conn=None):
    """Assemble the report payload. Reads rollups, never raw answers."""
    selected = tuple(s for s in (sections or SECTIONS) if s in SECTIONS) or SECTIONS
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        workspace = conn.execute(
            select(workspaces).where(workspaces.c.id == workspace_id)
        ).mappings().first()
        if not workspace:
            return None
        workspace = dict(workspace)

        branding = branding_for(workspace_id, conn)
        run_state = last_complete_run(workspace_id, conn)
        series = latest_metrics(workspace_id)
        latest = series[0] if series else None

        report = {
            'workspace': row_to_dict(workspace),
            'branding': branding,
            'generated_at': datetime.utcnow().isoformat() + 'Z',
            'sections': list(selected),
            'scan_in_progress': run_state['scan_in_progress'],
            # Labelled with its date so a mid-scan report is honest about vintage.
            'as_of': (row_to_dict(run_state['run']) or {}).get('completed_at')
            if run_state['run'] else None,
        }

        if 'visibility' in selected:
            envelope = score_envelope(latest, has_completed_run=bool(series))
            report['visibility'] = envelope
            if envelope['state'] != 'ok':
                report['visibility']['empty_reason'] = EMPTY_STATE_COPY[envelope['state']]

        if 'share_of_voice' in selected:
            sov = (latest or {}).get('sov')
            report['share_of_voice'] = {
                'value': sov,
                'n': (latest or {}).get('answer_count') or 0,
                # Never a fabricated zero.
                'empty_reason': None if sov is not None else
                'Share of voice needs at least one tracked competitor and one '
                'measured answer.',
            }

        if 'citations' in selected:
            rollup = citation_domain_rollup(workspace_id, conn)
            report['citations'] = {
                'total': rollup['total_citations'],
                'domains': rollup['domains'][:15],
                'gaps': competitor_citation_gaps(workspace_id, conn, limit=10),
                'empty_reason': None if rollup['total_citations'] else
                'No citations have been recorded yet.',
            }

        if 'prompts' in selected:
            rows = conn.execute(
                select(analytics_tracked_prompts)
                .where(
                    (analytics_tracked_prompts.c.workspace_id == workspace_id)
                    & (analytics_tracked_prompts.c.active.is_(True))
                )
                .order_by(analytics_tracked_prompts.c.created_at)
            ).mappings().all()
            report['prompts'] = {
                'rows': [row_to_dict(r) for r in rows],
                'n': len(rows),
                'empty_reason': None if rows else 'No prompts are being tracked yet.',
            }

        if 'methodology' in selected:
            report['methodology'] = methodology()

        return report
    finally:
        if own_conn:
            conn.close()


def methodology():
    """Stated on the report, not buried. The numbers mean nothing without it."""
    return {
        'formula': 'VS = 100 × (0.5·MentionRate + 0.3·PositionScore + 0.2·CitationRate)',
        'interval': '95% Wilson score interval, shown with every rate.',
        'excluded': 'On-demand scans are excluded; only scheduled runs count.',
        'threshold': 'The Visibility Score is withheld below 20 answers in the period.',
        'rank': 'Rank is the brand position by character offset of first mention, '
                'among all brands named in the same answer.',
        'engines': 'Each engine is labelled with its source type. A grounded-search '
                   'model is a proxy for that engine\'s public answers, not the '
                   'consumer product itself.',
    }


# --- share links -------------------------------------------------------------

def create_share(workspace_id, *, sections=None, days=DEFAULT_SHARE_DAYS):
    """Mint an unguessable, read-only share token."""
    token = secrets.token_urlsafe(SHARE_TOKEN_BYTES)
    now = datetime.utcnow()
    expires = now + timedelta(days=days) if days else None
    with engine.begin() as conn:
        conn.execute(insert(report_shares).values(
            token=token, workspace_id=workspace_id,
            sections=json.dumps(list(sections)) if sections else None,
            expires_at=expires, created_at=now, revoked_at=None,
        ))
    return {'token': token, 'expires_at': expires.isoformat() + 'Z' if expires else None}


def resolve_share(token):
    """Return the share row if the token is live, else None.

    Expiry and revocation are checked here so every caller gets the same answer.
    """
    if not token:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            select(report_shares).where(report_shares.c.token == token)
        ).mappings().first()
    if not row:
        return None
    row = dict(row)
    if row.get('revoked_at'):
        return None
    if row.get('expires_at') and row['expires_at'] < datetime.utcnow():
        return None
    return row


def revoke_share(token):
    with engine.begin() as conn:
        conn.execute(
            update(report_shares)
            .where(report_shares.c.token == token)
            .values(revoked_at=datetime.utcnow())
        )
