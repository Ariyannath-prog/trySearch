"""Prompt scan execution and provider answer persistence."""

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
import json
import os
import random
import time

from app.config import PERPLEXITY_MAX_PROMPTS_PER_SCAN
from app.costs import ceiling_status, record_usage
from app.rollup import rollup_workspace_day
from app.db import engine
from app.engines.perplexity import call_perplexity_answer, call_perplexity_search, normalise_perplexity_sources, perplexity_answer_citations, perplexity_answer_text
from app.extraction.pipeline import (
    categorise_sources,
    extract_answer,
    stored_brand_aliases,
    workspace_competitors,
)
from app.extraction.mentions import domain_matches, project_brand_aliases, text_mentions_alias
from app.http_client import ProviderAPIError
from app.jobs import update_analytics_job
from app.llm import open_model_settings
from app.metrics import provider_evidence_rows
from app.models import analytics_answer_sources, analytics_audit_jobs, competitors, analytics_content_opportunities, workspaces, analytics_prompt_scan_runs, analytics_provider_answers, analytics_scan_schedules, analytics_topics, analytics_tracked_prompts
from app.recommendations import open_model_evidence_opportunities, rule_based_opportunities
from app.routes.pages import index

def next_schedule_time(frequency, from_time=None):
    from_time = from_time or datetime.utcnow()
    return from_time + {'daily': timedelta(days=1), 'weekly': timedelta(days=7), 'monthly': timedelta(days=30)}[frequency]

# Individual answers retry; the fan-out parent never does. A provider blip should
# cost one answer, not the whole run, and a run that genuinely failed should not be
# re-dispatched in a loop.
ANSWER_RETRY_ATTEMPTS = max(1, min(int(os.environ.get('ANSWER_RETRY_ATTEMPTS', '3')), 6))
ANSWER_RETRY_BASE_SECONDS = 0.5


def batched(items, size):
    """Yield successive lists of at most `size` items."""
    size = max(1, size)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def retry_delay(attempt):
    """Exponential backoff with full jitter, so retries do not synchronise."""
    ceiling = ANSWER_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
    return random.uniform(0, min(ceiling, 8.0))


def call_with_retries(call, *args, attempts=None):
    """Return (payload, error_message). Never raises past its own boundary.

    Retries only the provider call for a single answer. The caller records the
    error as evidence and carries on to the next prompt.
    """
    attempts = attempts or ANSWER_RETRY_ATTEMPTS
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return call(*args), None
        except ProviderAPIError as error:
            last_error = error
            if attempt < attempts:
                time.sleep(retry_delay(attempt))
    return None, f'{last_error} (after {attempts} attempts)'


def persist_provider_answer(scan_id, prompt, project, search_payload, answer_payload, errors, latency_ms):
    answer_text = perplexity_answer_text(answer_payload or {})
    sources = normalise_perplexity_sources(search_payload or {}, answer_payload or {})
    answer_available = bool(answer_payload is not None and answer_text)
    # brand_mentioned / brand_cited / rank are no longer frozen onto the answer row.
    # They are derived below by the versioned extractor, so a formula change can be
    # replayed over this same immutable answer at zero provider cost.
    status = 'succeeded' if search_payload is not None and answer_available else ('partial' if search_payload is not None or answer_available else 'failed')
    raw_response = json.dumps({'search': search_payload, 'answer': answer_payload}, ensure_ascii=False)
    now = datetime.utcnow()
    answer_model = (answer_payload or {}).get('model') or f"preset:{os.environ.get('PERPLEXITY_AGENT_PRESET', 'low')}"
    with engine.begin() as conn:
        result = conn.execute(insert(analytics_provider_answers).values(
            scan_run_id=scan_id, prompt_id=prompt['id'],
            prompt_text=prompt['prompt'], prompt_intent=prompt.get('intent'), topic_name=prompt.get('topic_name'),
            provider='Perplexity',
            model=str(answer_model)[:160], status=status,
            search_request_id=(search_payload or {}).get('id'), answer_request_id=(answer_payload or {}).get('id'),
            answer_text=answer_text or None, raw_response=raw_response,
            latency_ms=latency_ms,
            error='; '.join(errors)[:2000] if errors else None,
            created_at=now, completed_at=now,
        ))
        answer_id = result.inserted_primary_key[0]
        if sources:
            conn.execute(insert(analytics_answer_sources), [dict(source, answer_id=answer_id) for source in sources])
        # Same transaction as the answer insert, so an answer is never visible
        # without its citations categorised.
        workspace_competitor_rows = workspace_competitors(project['id'], conn)
        if answer_available:
            extract_answer(
                answer={'id': answer_id, 'answer_text': answer_text},
                workspace=project,
                competitors=workspace_competitor_rows,
                aliases=stored_brand_aliases(project['id'], conn),
                conn=conn,
            )
        else:
            # No answer text means nothing to rank, but the search results are still
            # evidence and still get categorised.
            categorise_sources(answer_id, workspace=project,
                               competitors=workspace_competitor_rows, conn=conn)
    return answer_id

def run_prompt_scan_job(job_id):
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
        prompts = conn.execute(select(
            analytics_tracked_prompts,
            analytics_topics.c.name.label('topic_name'),
        ).outerjoin(
            analytics_topics, analytics_tracked_prompts.c.topic_id == analytics_topics.c.id
        ).where(
            (analytics_tracked_prompts.c.workspace_id == job['workspace_id']) &
            (analytics_tracked_prompts.c.active.is_(True))
        ).order_by(analytics_tracked_prompts.c.id)).mappings().all()
        competitor_rows = conn.execute(select(competitors).where(
            competitors.c.workspace_id == job['workspace_id']
        )).mappings().all()
    if not project or not prompts:
        update_analytics_job(job_id, status='failed_terminal', progress=100, error='Add at least one active tracked prompt first.', completed_at=datetime.utcnow())
        return
    if not os.environ.get('PERPLEXITY_API_KEY'):
        update_analytics_job(job_id, status='failed_terminal', progress=100, error='PERPLEXITY_API_KEY is not configured.', completed_at=datetime.utcnow())
        return

    with engine.connect() as conn:
        completed_run = conn.execute(select(
            analytics_prompt_scan_runs.c.status,
            analytics_prompt_scan_runs.c.prompt_count,
            analytics_prompt_scan_runs.c.completed_count,
            analytics_prompt_scan_runs.c.completed_at,
        ).where(analytics_prompt_scan_runs.c.job_id == job_id)
            .order_by(desc(analytics_prompt_scan_runs.c.created_at)).limit(1)).mappings().first()
    if completed_run and completed_run['completed_at'] and completed_run['status'] in {'succeeded', 'partial', 'failed'}:
        job_status = 'succeeded' if completed_run['status'] in {'succeeded', 'partial'} else 'failed_terminal'
        update_analytics_job(
            job_id, status=job_status, progress=100,
            completed_items=completed_run['completed_count'], total_items=completed_run['prompt_count'],
            error=None if job_status == 'succeeded' else 'The saved provider scan did not complete successfully.',
            completed_at=datetime.utcnow(),
        )
        return

    project = dict(project)
    workspace_id = project['id']
    org_id = project['org_id']

    # Ceiling checked before dispatch, so an org over budget spends nothing more.
    # Counted from usage_ledger rows, not run rows: a retry storm writes no runs.
    state, spend, ceiling = ceiling_status(org_id)
    if state == 'exceeded':
        update_analytics_job(
            job_id, status='failed_terminal', progress=100,
            error=(f'Monthly spend ceiling reached: ${spend} of ${ceiling}. '
                   f'No provider calls were made.'),
            completed_at=datetime.utcnow(),
        )
        return
    if state == 'alert':
        print(f'[costs] org {org_id} at ${spend} of ${ceiling} monthly ceiling (>=60%).')

    prompts = [dict(row) for row in prompts]
    competitor_rows = [dict(row) for row in competitor_rows]
    competitor_snapshot = json.dumps([
        # competitors.domains is text[] as of T5; the snapshot keeps its single-domain
        # shape, taking the first, so stored evidence stays readable by older rows.
        {'name': c['name'], 'domain': (c.get('domains') or [None])[0]}
        for c in competitor_rows
    ], ensure_ascii=False)
    region = None
    with engine.connect() as conn:
        schedule = conn.execute(select(analytics_scan_schedules).where(
            analytics_scan_schedules.c.workspace_id == project['id']
        )).mappings().first()
    if schedule:
        region = schedule['region']
    now = datetime.utcnow()
    with engine.begin() as conn:
        existing_run = conn.execute(select(analytics_prompt_scan_runs.c.id).where(
            analytics_prompt_scan_runs.c.job_id == job_id
        )).scalar_one_or_none()
        if existing_run:
            # Resuming an interrupted run. Answers already collected are kept: they
            # are immutable evidence and they have already been paid for. Deleting
            # them and starting over would re-submit every successful call, which is
            # exactly the double-charge architecture-spec 4 rule 2 forbids.
            scan_id = existing_run
            conn.execute(analytics_content_opportunities.delete().where(
                analytics_content_opportunities.c.scan_run_id == scan_id
            ))
            conn.execute(update(analytics_prompt_scan_runs).where(
                analytics_prompt_scan_runs.c.id == scan_id
            ).values(
                status='running', prompt_count=len(prompts),
                competitor_snapshot=competitor_snapshot,
                mention_rate=None, citation_rate=None, source_presence_rate=None,
                share_of_voice=None, recommendation_summary=None, error=None,
                completed_at=None,
            ))
        else:
            result = conn.execute(insert(analytics_prompt_scan_runs).values(
                workspace_id=project['id'], job_id=job_id, provider='Perplexity',
                run_type=job.get('run_type') or 'scheduled',
                model=f"preset:{os.environ.get('PERPLEXITY_AGENT_PRESET', 'low')}"[:160], region=region,
                competitor_snapshot=competitor_snapshot,
                status='running', prompt_count=len(prompts), completed_count=0,
                mention_rate=None, citation_rate=None, source_presence_rate=None,
                share_of_voice=None, recommendation_summary=None, error=None,
                created_at=now, completed_at=None,
            ))
            scan_id = result.inserted_primary_key[0]
        conn.execute(update(analytics_audit_jobs).where(analytics_audit_jobs.c.id == job_id).values(
            total_items=len(prompts), completed_items=0, progress=0,
        ))

    with engine.connect() as conn:
        answered_prompt_ids = {
            row[0] for row in conn.execute(select(analytics_provider_answers.c.prompt_id).where(
                analytics_provider_answers.c.scan_run_id == scan_id
            )).all()
        }
    pending = [prompt for prompt in prompts if prompt['id'] not in answered_prompt_ids]

    failures = []
    index = len(answered_prompt_ids)
    # A 300-prompt workspace becomes 12 sequential batches of 25 rather than a 409.
    # The batch boundary is where progress is committed, so killing the worker
    # mid-run loses at most one batch of work, never the run.
    for batch in batched(pending, PERPLEXITY_MAX_PROMPTS_PER_SCAN):
        for prompt in batch:
            index += 1
            started = time.monotonic()
            errors = []
            search_payload, search_error = call_with_retries(
                call_perplexity_search, prompt['prompt'], region)
            # Every provider call is metered, success or failure: a retry storm
            # writes no runs but still burns money.
            record_usage(workspace_id=workspace_id, org_id=org_id,
                         category='engine_query', provider='Perplexity')
            if search_error:
                errors.append(f'Search API: {search_error}')
            answer_payload, answer_error = call_with_retries(
                call_perplexity_answer, prompt['prompt'])
            record_usage(workspace_id=workspace_id, org_id=org_id,
                         category='agent', provider='Perplexity')
            if answer_error:
                errors.append(f'Agent API: {answer_error}')
            elif not perplexity_answer_text(answer_payload):
                errors.append('Agent API: completed without answer text')
            if errors:
                failures.append(f"Prompt {prompt['id']}: {'; '.join(errors)}")
            # The row is written whatever happened, so a failed answer is recorded
            # evidence rather than a silent gap.
            persist_provider_answer(
                scan_id, prompt, project, search_payload, answer_payload, errors,
                round((time.monotonic() - started) * 1000),
            )
            # Extraction is a regex and costs nothing, but the row keeps the ledger a
            # complete record of what was derived from which answer - and makes
            # "re-running extraction is free" auditable rather than merely claimed.
            record_usage(workspace_id=workspace_id, org_id=org_id,
                         category='extraction', provider='regex')
            if index < len(prompts):
                time.sleep(max(0, min(float(os.environ.get('PERPLEXITY_REQUEST_DELAY_SECONDS', '0.2')), 3)))
        update_analytics_job(
            job_id, completed_items=index, total_items=len(prompts),
            progress=round(index / len(prompts) * 100),
        )

    evidence_rows = provider_evidence_rows(scan_id)
    returned_models = list(dict.fromkeys(row['model'] for row in evidence_rows if row.get('model')))
    answer_measured = [row for row in evidence_rows if row.get('answer_text')]
    source_measured = [row for row in evidence_rows if row.get('source_present') is not None]
    mention_rate = round(sum(bool(row['brand_mentioned']) for row in answer_measured) / len(answer_measured) * 100, 2) if answer_measured else None
    citation_rate = round(sum(bool(row['brand_cited']) for row in answer_measured) / len(answer_measured) * 100, 2) if answer_measured else None
    source_presence_rate = round(sum(bool(row['source_present']) for row in source_measured) / len(source_measured) * 100, 2) if source_measured else None

    brand_occurrences = sum(text_mentions_alias(row.get('answer_text'), project_brand_aliases(project)) for row in answer_measured)
    competitor_occurrences = 0
    for competitor in competitor_rows:
        aliases = [competitor['name'], competitor.get('domain')]
        competitor_occurrences += sum(text_mentions_alias(row.get('answer_text'), aliases) for row in answer_measured)
    voice_denominator = brand_occurrences + competitor_occurrences
    share_of_voice = round(brand_occurrences / voice_denominator * 100, 2) if voice_denominator else None

    rule_opportunities = rule_based_opportunities(project, evidence_rows)
    opportunity_source = 'stored-evidence rules'
    opportunities = rule_opportunities
    open_model = open_model_settings()
    if open_model['configured']:
        try:
            model_opportunities = open_model_evidence_opportunities(project, evidence_rows)
            if model_opportunities:
                opportunities = model_opportunities
                opportunity_source = f"{open_model['provider']} · {open_model['model']}"
        except (ProviderAPIError, json.JSONDecodeError) as error:
            failures.append(f'Open-model recommendation layer: {error}')
    with engine.begin() as conn:
        conn.execute(analytics_content_opportunities.delete().where(
            analytics_content_opportunities.c.scan_run_id == scan_id
        ))
        if opportunities:
            conn.execute(insert(analytics_content_opportunities), [
                {
                    'workspace_id': project['id'], 'scan_run_id': scan_id, 'source': opportunity_source,
                    'title': item['title'][:255], 'rationale': item['rationale'],
                    'evidence_refs': item['evidence_refs'], 'priority': item['priority'],
                    'created_at': datetime.utcnow(),
                }
                for item in opportunities
            ])
        completed_count = len([row for row in evidence_rows if row['status'] in {'succeeded', 'partial'}])
        scan_status = 'succeeded' if completed_count == len(prompts) and not failures else ('partial' if completed_count else 'failed')
        summary = '; '.join(item['title'] for item in opportunities[:3]) if opportunities else None
        conn.execute(update(analytics_prompt_scan_runs).where(analytics_prompt_scan_runs.c.id == scan_id).values(
            status=scan_status, completed_count=completed_count,
            model=(' / '.join(returned_models)[:160] if returned_models else f"preset:{os.environ.get('PERPLEXITY_AGENT_PRESET', 'low')}"),
            mention_rate=mention_rate, citation_rate=citation_rate,
            source_presence_rate=source_presence_rate, share_of_voice=share_of_voice,
            recommendation_summary=summary, error='\n'.join(failures)[:5000] if failures else None,
            completed_at=datetime.utcnow(),
        ))
    # Roll up into metrics_daily, the only table dashboards read. Idempotent, so a
    # retried job recomputes the day rather than double-counting it.
    rollup_workspace_day(workspace_id, datetime.utcnow().date())

    final_job_status = 'succeeded' if evidence_rows and any(row['status'] in {'succeeded', 'partial'} for row in evidence_rows) else 'failed_terminal'
    update_analytics_job(
        job_id, status=final_job_status, progress=100, completed_items=len(prompts), total_items=len(prompts),
        error='\n'.join(failures)[:2000] if final_job_status != 'succeeded' else None,
        completed_at=datetime.utcnow(),
    )
