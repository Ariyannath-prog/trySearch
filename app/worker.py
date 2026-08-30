"""CLI worker: schedule enqueue, stale-lease recovery, job dispatch."""

from datetime import date, datetime, timedelta
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Boolean,
    Float,
    Integer,
    String,
    Text,
    DateTime,
    UniqueConstraint,
    select,
    insert,
    update,
    desc,
    func,
    text,
)
import os

from app.db import engine
from app.jobs import create_analytics_job, run_site_audit_job, update_analytics_job
from app.models import analytics_audit_jobs, workspaces, analytics_scan_schedules
from app.scanning import next_schedule_time, run_prompt_scan_job

def run_scheduled_analytics_command():
    """Recover queued jobs and enqueue due prompt schedules for a cron worker."""
    now = datetime.utcnow()
    stale_before = now - timedelta(minutes=45)
    batch_size = max(1, min(int(os.environ.get('ANALYTICS_JOB_BATCH', '10')), 50))
    with engine.begin() as conn:
        conn.execute(update(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.status == 'running') &
            (analytics_audit_jobs.c.started_at < stale_before)
        ).values(status='failed_retryable', error='Recovered after a stale worker lease.'))
        # The worker runs on behalf of the system, not a user. T5 dropped
        # workspaces.user_id, and the job row no longer records one either, so the
        # schedule is joined to its workspace purely to confirm it still exists.
        due_schedules = conn.execute(select(
            analytics_scan_schedules,
            workspaces.c.id.label('workspace_row_id'),
            workspaces.c.status.label('workspace_status'),
        ).join(
            workspaces, analytics_scan_schedules.c.workspace_id == workspaces.c.id
        ).where(
            (analytics_scan_schedules.c.enabled.is_(True)) &
            (analytics_scan_schedules.c.next_run_at <= now)
        ).order_by(analytics_scan_schedules.c.next_run_at).limit(batch_size)).mappings().all()
    scheduled_count = 0
    for schedule in due_schedules:
        if schedule['workspace_status'] != 'active':
            continue
        project = {'id': schedule['workspace_id']}
        with engine.connect() as conn:
            active = conn.execute(select(analytics_audit_jobs.c.id).where(
                (analytics_audit_jobs.c.workspace_id == schedule['workspace_id']) &
                (analytics_audit_jobs.c.job_type == 'prompt_scan') &
                (analytics_audit_jobs.c.status.in_(['queued', 'running', 'failed_retryable']))
            ).limit(1)).scalar_one_or_none()
        if not active:
            create_analytics_job(project, 'prompt_scan', provider='Perplexity')
            scheduled_count += 1
        with engine.begin() as conn:
            conn.execute(update(analytics_scan_schedules).where(
                analytics_scan_schedules.c.id == schedule['id']
            ).values(
                last_run_at=now, next_run_at=next_schedule_time(schedule['frequency'], now),
                updated_at=now,
            ))

    with engine.connect() as conn:
        jobs = conn.execute(select(analytics_audit_jobs.c.id, analytics_audit_jobs.c.job_type).where(
            analytics_audit_jobs.c.status.in_(['queued', 'failed_retryable'])
        ).order_by(analytics_audit_jobs.c.created_at).limit(batch_size)).all()
    processed = 0
    for job_id, job_type in jobs:
        try:
            if job_type == 'site_audit':
                run_site_audit_job(job_id)
                processed += 1
            elif job_type == 'prompt_scan':
                run_prompt_scan_job(job_id)
                processed += 1
        except Exception as error:
            # architecture-spec 4 rule 2: retry limit zero at the job level for
            # fan-outs. A prompt_scan that raised has already paid for the answers
            # it completed, and re-dispatching would buy them all again. A site
            # audit fans out to nothing paid, so it stays retryable.
            #
            # This is not the recovery path for a killed worker: that leaves the row
            # in 'running' with no exception, and the 45-minute stale lease above
            # requeues it. The resumed run skips prompts already answered, so it
            # costs only the remainder.
            status = 'failed_terminal' if job_type == 'prompt_scan' else 'failed_retryable'
            update_analytics_job(
                job_id, status=status, error=str(error)[:2000],
                completed_at=datetime.utcnow(),
            )
    print(f'Analytics worker queued {scheduled_count} scheduled scan(s) and processed {processed} job(s).')


def register_cli(app):
    """Re-register the `flask run-scheduled-analytics` command on the built app.

    In the monolith this was an @app.cli.command decorator at import time. The
    function stays directly callable so existing invocations keep working.
    """
    app.cli.command('run-scheduled-analytics')(run_scheduled_analytics_command)


def reextract_command(workspace_id):
    """Re-run extraction over stored answers. Zero provider calls."""
    from app.extraction.pipeline import EXTRACTOR_VERSION, reextract_workspace

    count = reextract_workspace(workspace_id)
    print(f'Re-extracted {count} answer(s) for workspace {workspace_id} '
          f'at extractor version {EXTRACTOR_VERSION}. No provider calls were made.')
    return count


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog='python -m app.worker')
    sub = parser.add_subparsers(dest='command', required=True)

    run = sub.add_parser('run', help='process due schedules and queued jobs')
    reextract = sub.add_parser(
        'reextract', help='re-derive extractions from stored answers, at no cost')
    reextract.add_argument('--workspace', type=int, required=True)

    args = parser.parse_args(argv)
    if args.command == 'reextract':
        return reextract_command(args.workspace)
    return run_scheduled_analytics_command()


if __name__ == '__main__':
    main()
