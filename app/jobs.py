"""Job records, site audit execution and the on-demand thread."""

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

from app.crawler.crawl import crawl_website
from app.db import engine
from app.models import analytics_audit_findings, analytics_audit_jobs, analytics_audit_pages, analytics_site_audits, analytics_sitemaps, memberships, workspaces
from app.rag.answers import generate_standard_rag_insights
from app.rag.index import index_rag_page, rag_index_summary
from app.utils import row_to_dict

def create_analytics_job(workspace, job_type, provider=None, run_type='scheduled'):
    """Queue a job for a workspace.

    T5 dropped analytics_audit_jobs.user_id: a job belongs to a workspace, and who
    may see it follows from org membership rather than from a column on the row.
    """
    now = datetime.utcnow()
    with engine.begin() as conn:
        result = conn.execute(insert(analytics_audit_jobs).values(
            workspace_id=workspace['id'], job_type=job_type, run_type=run_type,
            provider=provider, status='queued', progress=0, total_items=0,
            completed_items=0, error=None, created_at=now,
            started_at=None, completed_at=None,
        ))
    return result.inserted_primary_key[0]

def update_analytics_job(job_id, **values):
    with engine.begin() as conn:
        conn.execute(update(analytics_audit_jobs).where(analytics_audit_jobs.c.id == job_id).values(**values))

def analytics_job_for_user(job_id, user_id):
    """A job is visible when the user belongs to the org owning its workspace."""
    with engine.connect() as conn:
        row = conn.execute(
            select(analytics_audit_jobs)
            .join(workspaces, workspaces.c.id == analytics_audit_jobs.c.workspace_id)
            .join(memberships, memberships.c.org_id == workspaces.c.org_id)
            .where(
                (analytics_audit_jobs.c.id == job_id)
                & (memberships.c.user_id == user_id)
            )
        ).mappings().first()
    return row_to_dict(row) if row else None

def persist_site_audit(workspace_id, job_id, crawl):
    """Persist the aggregate, every selected page, sitemap, and finding atomically."""
    now = datetime.utcnow()
    with engine.begin() as conn:
        result = conn.execute(insert(analytics_site_audits).values(
            workspace_id=workspace_id, job_id=job_id, status=crawl['status'],
            source_type='website_crawl', start_url=crawl['start_url'][:2048],
            final_url=(crawl.get('final_url') or '')[:2048] or None,
            pages_discovered=crawl['pages_discovered'], pages_audited=crawl['pages_audited'],
            pages_failed=crawl['pages_failed'], readiness_score=crawl['readiness_score'],
            metadata_score=crawl['metadata_score'], content_score=crawl['content_score'],
            crawlability_score=crawl['crawlability_score'],
            structured_data_score=crawl['structured_data_score'], summary=crawl['summary'],
            created_at=now, completed_at=now,
        ))
        audit_id = result.inserted_primary_key[0]

        for sitemap in crawl.get('sitemaps', []):
            conn.execute(insert(analytics_sitemaps).values(
                audit_id=audit_id, url=sitemap['url'][:2048], status=sitemap['status'],
                urls_discovered=sitemap.get('urls_discovered', 0), error=sitemap.get('error'),
            ))

        for page in crawl.get('pages', []):
            score = page.get('readiness_score')
            page_result = conn.execute(insert(analytics_audit_pages).values(
                audit_id=audit_id, url=page.get('requested_url', page.get('url', ''))[:2048],
                final_url=(page.get('url') or '')[:2048] or None,
                fetched=bool(page.get('fetched')), http_status=page.get('http_status'),
                title=page.get('title'), description=page.get('description'),
                headings_count=len(page.get('headings') or []), word_count=page.get('word_count', 0),
                schema_blocks=page.get('schema_blocks', 0), canonical=(page.get('canonical') or '')[:2048] or None,
                noindex=bool(page.get('noindex')), language=(page.get('language') or '')[:40] or None,
                internal_links=page.get('internal_links', 0), external_links=page.get('external_links', 0),
                readiness_score=score, metadata_score=page.get('metadata_score'),
                content_score=page.get('content_score'), crawlability_score=page.get('crawlability_score'),
                structured_data_score=page.get('structured_data_score'),
                issues_count=len(page.get('findings') or []), error=page.get('error'),
                fetched_at=page.get('fetched_at') or now,
            ))
            page_id = page_result.inserted_primary_key[0]
            for finding in page.get('findings', []):
                conn.execute(insert(analytics_audit_findings).values(
                    audit_id=audit_id, page_id=page_id, code=finding['code'], area=finding['area'],
                    severity=finding['severity'], evidence=finding['evidence'],
                    recommendation=finding['recommendation'],
                ))
            if page.get('fetched'):
                index_rag_page(
                    conn, workspace_id=workspace_id, audit_id=audit_id,
                    page_id=page_id, page=page, created_at=now,
                )

        conn.execute(update(workspaces).where(workspaces.c.id == workspace_id).values(updated_at=now))
    return audit_id

def latest_site_audit(workspace_id):
    with engine.connect() as conn:
        audit = conn.execute(select(analytics_site_audits).where(
            analytics_site_audits.c.workspace_id == workspace_id
        ).order_by(desc(analytics_site_audits.c.created_at)).limit(1)).mappings().first()
        if not audit:
            return None
        audit = dict(audit)
        pages = [row_to_dict(row) for row in conn.execute(select(analytics_audit_pages).where(
            analytics_audit_pages.c.audit_id == audit['id']
        ).order_by(desc(analytics_audit_pages.c.readiness_score), analytics_audit_pages.c.url)).mappings().all()]
        findings = [row_to_dict(row) for row in conn.execute(select(analytics_audit_findings).where(
            analytics_audit_findings.c.audit_id == audit['id']
        ).order_by(analytics_audit_findings.c.severity, analytics_audit_findings.c.id)).mappings().all()]
        sitemaps = [row_to_dict(row) for row in conn.execute(select(analytics_sitemaps).where(
            analytics_sitemaps.c.audit_id == audit['id']
        ).order_by(analytics_sitemaps.c.id)).mappings().all()]
        history = [row_to_dict(row) for row in conn.execute(select(
            analytics_site_audits.c.id, analytics_site_audits.c.status,
            analytics_site_audits.c.readiness_score, analytics_site_audits.c.pages_audited,
            analytics_site_audits.c.created_at,
        ).where(analytics_site_audits.c.workspace_id == workspace_id)
            .order_by(desc(analytics_site_audits.c.created_at)).limit(12)).mappings().all()]
    history.reverse()
    return {
        'run': row_to_dict(audit), 'pages': pages, 'findings': findings,
        'sitemaps': sitemaps, 'history': history, 'rag': rag_index_summary(audit['id']),
    }

def run_site_audit_job(job_id):
    """Claim and execute one durable crawl job. A cron worker can retry queued jobs."""
    with engine.begin() as conn:
        job = conn.execute(select(analytics_audit_jobs).where(
            analytics_audit_jobs.c.id == job_id
        )).mappings().first()
        if not job or job['status'] not in {'queued', 'failed_retryable'}:
            return
        claimed = conn.execute(update(analytics_audit_jobs).where(
            (analytics_audit_jobs.c.id == job_id) &
            (analytics_audit_jobs.c.status.in_(['queued', 'failed_retryable']))
        ).values(status='running', started_at=datetime.utcnow(), completed_at=None, error=None))
        if claimed.rowcount != 1:
            return
        project = conn.execute(select(workspaces).where(
            workspaces.c.id == job['workspace_id']
        )).mappings().first()
        existing_audit = conn.execute(select(
            analytics_site_audits.c.id, analytics_site_audits.c.status,
            analytics_site_audits.c.pages_audited,
        ).where(analytics_site_audits.c.job_id == job_id)
            .order_by(desc(analytics_site_audits.c.created_at)).limit(1)).mappings().first()
    if not project:
        update_analytics_job(job_id, status='failed_terminal', error='Project no longer exists.', completed_at=datetime.utcnow())
        return
    if existing_audit:
        succeeded = existing_audit['status'] != 'failed'
        if succeeded:
            rag_summary = rag_index_summary(existing_audit['id'])
            if rag_summary['chunks_indexed'] and not rag_summary['insights']:
                generate_standard_rag_insights(dict(project), existing_audit['id'])
        update_analytics_job(
            job_id, status='succeeded' if succeeded else 'failed_terminal', progress=100,
            completed_items=existing_audit['pages_audited'], total_items=existing_audit['pages_audited'],
            error=None if succeeded else 'The saved website audit did not complete successfully.',
            completed_at=datetime.utcnow(),
        )
        return

    def progress(completed, total):
        percent = min(99, round(completed / max(total, 1) * 100))
        update_analytics_job(job_id, completed_items=completed, total_items=total, progress=percent)

    try:
        project = dict(project)
        crawl = crawl_website(project.get('website_url') or project['domain'], progress_callback=progress)
        audit_id = persist_site_audit(project['id'], job_id, crawl)
        if crawl['status'] != 'failed':
            generate_standard_rag_insights(project, audit_id)
        update_analytics_job(
            job_id, status='succeeded' if crawl['status'] != 'failed' else 'failed_terminal',
            progress=100, completed_items=len(crawl['pages']), total_items=len(crawl['pages']),
            error=None if crawl['status'] != 'failed' else crawl['summary'], completed_at=datetime.utcnow(),
        )
    except Exception as error:  # A durable status is more useful than a dropped worker traceback.
        update_analytics_job(job_id, status='failed_retryable', error=str(error)[:2000], completed_at=datetime.utcnow())
