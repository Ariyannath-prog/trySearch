"""Report assembly over stored evidence."""

from sqlalchemy import (
    case,
    select,
    update,
    desc,
    func,
)
import hashlib
import json
import re

from app.crawler.fetch import normalise_site_host
from app.db import engine
from app.extraction.mentions import domain_matches, project_brand_aliases, text_mentions_alias
from app.jobs import latest_site_audit
from app.llm import open_model_settings
from app.models import extractions, analytics_answer_sources, analytics_content_opportunities, competitors, mentions as mentions_table, workspaces, analytics_prompt_scan_runs, analytics_provider_answers, analytics_topics, analytics_tracked_prompts, engines
from app.rollup import SCHEDULED_RUN_TYPE, latest_metrics, latest_metrics_all_engines, score_from_counts, sentiment_index_from_labels
from app.stats import MIN_ANSWERS_FOR_SCORE, describe_delta, metric, score_envelope
from app.tenancy import workspace_for_member
from app.utils import row_to_dict, to_iso


def analytics_report(workspace_id, user_id):
    """Dashboard payload. Reads metrics_daily and nothing else for its numbers.

    The previous version synthesised an "engines" list out of site-crawl sub-scores
    - Metadata, Content, Crawlability, Structured data - and rendered them where AI
    engines belong. That is a crawl score wearing a visibility score's clothes, and
    it is gone. Site health is still reported, in its own section, as itself.
    """
    project = workspace_for_member(workspace_id, user_id)
    if not project:
        return None

    series = latest_metrics(workspace_id)
    latest = series[0] if series else None
    per_engine = [row for row in latest_metrics_all_engines(workspace_id)
                  if row['engine_id'] is not None]
    # metrics_daily only stores engine_id; a dashboard has nothing to label a
    # row with unless the name comes along for the ride.
    if per_engine:
        engine_ids = {row['engine_id'] for row in per_engine}
        with engine.connect() as conn:
            names = {
                erow['id']: {'key': erow['key'], 'display_name': erow['display_name']}
                for erow in conn.execute(
                    select(engines.c.id, engines.c.key, engines.c.display_name)
                    .where(engines.c.id.in_(engine_ids))
                ).mappings()
            }
        for row in per_engine:
            row.update(names.get(row['engine_id'], {'key': None, 'display_name': None}))

    # Every metric leaves this function as {value, low, high, n} with an explicit
    # state, never as a bare number. T11: the product's stated differentiator.
    visibility = score_envelope(latest, has_completed_run=bool(series))
    previous = series[1] if len(series) > 1 else None
    visibility['delta'] = describe_delta(
        visibility.get('visibility_score'),
        {'value': previous['visibility_score']} if previous else None,
    )

    return {
        'project': row_to_dict(project),
        'visibility': visibility,
        'history': [row_to_dict(row) for row in reversed(series)],
        'engines': [row_to_dict(row) for row in per_engine],
        # Site health is a property of the website, not an engine result.
        'site_health': latest_site_audit(workspace_id),
        'topic_breakdown': topic_breakdown(workspace_id),
    }


def topic_breakdown(workspace_id, conn=None):
    """Mention/citation rate per topic, scheduled runs only.

    Same run_type restriction collect_counts() uses for metrics_daily itself
    (PRD §13: on-demand runs are excluded so someone actively testing a
    change doesn't bias the numbers) - kept consistent rather than inventing
    a second methodology. Reuses app.stats.metric() for the same
    {value,low,high,n} envelope every other rate in the product uses.
    """
    query = (
        select(
            analytics_provider_answers.c.topic_name,
            extractions.c.brand_mentioned,
            extractions.c.brand_cited,
        )
        .select_from(analytics_provider_answers)
        .join(analytics_prompt_scan_runs,
              analytics_prompt_scan_runs.c.id == analytics_provider_answers.c.scan_run_id)
        .join(extractions,
              (extractions.c.answer_id == analytics_provider_answers.c.id) & extractions.c.is_current)
        .where(
            (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
            & (analytics_prompt_scan_runs.c.run_type == SCHEDULED_RUN_TYPE)
        )
    )
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        rows = conn.execute(query).mappings().all()
    finally:
        if own_conn:
            conn.close()

    buckets = {}
    for row in rows:
        key = row['topic_name'] or 'Untagged'
        bucket = buckets.setdefault(key, {'total': 0, 'mentioned': 0, 'cited': 0})
        bucket['total'] += 1
        if row['brand_mentioned']:
            bucket['mentioned'] += 1
        if row['brand_cited']:
            bucket['cited'] += 1

    return [
        {
            'topic': name,
            'mention_rate': metric(bucket['mentioned'], bucket['total']),
            'citation_rate': metric(bucket['cited'], bucket['total']),
        }
        for name, bucket in sorted(buckets.items())
    ]


def answer_derivations(answer_ids, conn):
    """Per-answer values that used to be flat columns on analytics_provider_answers.

    T9 moved brand_mentioned / brand_cited / brand_rank into the versioned
    extractions table, and derives source_present / best_source_rank from
    analytics_answer_sources. Reading them in one place keeps every caller working
    off the *current* extraction rather than a stale copy frozen at scan time.
    """
    if not answer_ids:
        return {}
    derived = {
        answer_id: {'brand_mentioned': None, 'brand_rank': None, 'brand_cited': None,
                    'source_present': None, 'best_source_rank': None}
        for answer_id in answer_ids
    }

    for row in conn.execute(
        select(extractions).where(
            extractions.c.answer_id.in_(answer_ids) & extractions.c.is_current)
    ).mappings():
        derived[row['answer_id']].update(
            brand_mentioned=row['brand_mentioned'],
            brand_rank=row['brand_rank'],
            brand_cited=row['brand_cited'],
        )

    for row in conn.execute(
        select(
            analytics_answer_sources.c.answer_id,
            func.count().label('total'),
            func.min(
                case((analytics_answer_sources.c.category == 'own',
                      analytics_answer_sources.c.rank), else_=None)
            ).label('best_own_rank'),
        )
        .where(analytics_answer_sources.c.answer_id.in_(answer_ids))
        .group_by(analytics_answer_sources.c.answer_id)
    ).mappings():
        # source_present means "our own domain appeared in the search results",
        # not "the search returned anything". None stays reserved for "search did
        # not run", which is a different fact from "ran and we were absent".
        derived[row['answer_id']].update(
            source_present=row['best_own_rank'] is not None,
            best_source_rank=row['best_own_rank'],
        )

    return derived


def provider_evidence_rows(scan_id):
    with engine.connect() as conn:
        rows = conn.execute(select(
            analytics_provider_answers,
            func.coalesce(analytics_provider_answers.c.prompt_text, analytics_tracked_prompts.c.prompt).label('resolved_prompt'),
            func.coalesce(analytics_provider_answers.c.prompt_intent, analytics_tracked_prompts.c.intent).label('resolved_intent'),
            func.coalesce(analytics_provider_answers.c.topic_name, analytics_topics.c.name).label('resolved_topic_name'),
        ).outerjoin(
            analytics_tracked_prompts, analytics_provider_answers.c.prompt_id == analytics_tracked_prompts.c.id
        ).outerjoin(
            analytics_topics, analytics_tracked_prompts.c.topic_id == analytics_topics.c.id
        ).where(analytics_provider_answers.c.scan_run_id == scan_id)
            .order_by(analytics_provider_answers.c.id)).mappings().all()
        derived = answer_derivations([row['id'] for row in rows], conn)
    evidence = []
    for row in rows:
        item = row_to_dict(row)
        item['prompt'] = item.pop('resolved_prompt')
        item['intent'] = item.pop('resolved_intent')
        item['topic_name'] = item.pop('resolved_topic_name')
        item.update(derived.get(item['id'], {}))
        evidence.append(item)
    return evidence

def latest_prompt_evidence(workspace_id, run_id=None):
    with engine.connect() as conn:
        project = conn.execute(select(workspaces).where(
            workspaces.c.id == workspace_id
        )).mappings().first()
        statement = select(analytics_prompt_scan_runs).where(
            analytics_prompt_scan_runs.c.workspace_id == workspace_id
        )
        if run_id:
            statement = statement.where(analytics_prompt_scan_runs.c.id == run_id)
        scan = conn.execute(statement.order_by(desc(analytics_prompt_scan_runs.c.created_at)).limit(1)).mappings().first()
        if not scan:
            return {'run': None, 'answers': [], 'opportunities': [], 'history': []}
        scan = dict(scan)
        try:
            scan['competitor_set'] = json.loads(scan.get('competitor_snapshot') or '[]')
        except json.JSONDecodeError:
            scan['competitor_set'] = []
        scan.pop('competitor_snapshot', None)
        answer_rows = provider_evidence_rows(scan['id'])
        answer_ids = [row['id'] for row in answer_rows]
        sources_by_answer = {answer_id: [] for answer_id in answer_ids}
        if answer_ids:
            source_rows = conn.execute(select(analytics_answer_sources).where(
                analytics_answer_sources.c.answer_id.in_(answer_ids)
            ).order_by(analytics_answer_sources.c.answer_id, analytics_answer_sources.c.rank)).mappings().all()
            for source in source_rows:
                sources_by_answer[source['answer_id']].append(row_to_dict(source))
        opportunities = [row_to_dict(row) for row in conn.execute(select(analytics_content_opportunities).where(
            analytics_content_opportunities.c.scan_run_id == scan['id']
        ).order_by(analytics_content_opportunities.c.priority, analytics_content_opportunities.c.id)).mappings().all()]
        history = [row_to_dict(row) for row in conn.execute(select(
            analytics_prompt_scan_runs.c.id, analytics_prompt_scan_runs.c.status,
            analytics_prompt_scan_runs.c.provider, analytics_prompt_scan_runs.c.model,
            analytics_prompt_scan_runs.c.region, analytics_prompt_scan_runs.c.competitor_snapshot,
            analytics_prompt_scan_runs.c.prompt_count, analytics_prompt_scan_runs.c.completed_count,
            analytics_prompt_scan_runs.c.mention_rate, analytics_prompt_scan_runs.c.citation_rate,
            analytics_prompt_scan_runs.c.source_presence_rate, analytics_prompt_scan_runs.c.share_of_voice,
            analytics_prompt_scan_runs.c.created_at, analytics_prompt_scan_runs.c.completed_at,
        ).where(analytics_prompt_scan_runs.c.workspace_id == workspace_id)
            .order_by(desc(analytics_prompt_scan_runs.c.created_at)).limit(12)).mappings().all()]
        history_ids = [item['id'] for item in history]
        historical_answers = []
        if history_ids:
            historical_answers = [row_to_dict(row) for row in conn.execute(select(
                analytics_provider_answers.c.scan_run_id,
                analytics_provider_answers.c.prompt_id,
                analytics_provider_answers.c.prompt_text,
                analytics_provider_answers.c.id,
                analytics_provider_answers.c.answer_text,
            ).where(
                analytics_provider_answers.c.scan_run_id.in_(history_ids)
            )).mappings().all()]
            historical_derived = answer_derivations(
                [a['id'] for a in historical_answers], conn)
            for answer in historical_answers:
                answer.update(historical_derived.get(answer['id'], {}))
    for answer in answer_rows:
        answer['sources'] = sources_by_answer.get(answer['id'], [])
        answer.pop('raw_response', None)

    # Enrich every run with metrics that can be reproduced from its saved
    # provider-answer cohort. These values drive the overview charts; they are
    # never inferred by the RAG recommendation layer.
    historical_answers_by_run = {history_id: [] for history_id in history_ids}
    for answer in historical_answers:
        historical_answers_by_run.setdefault(answer['scan_run_id'], []).append(answer)
    for item in history:
        run_answers = historical_answers_by_run.get(item['id'], [])
        rank_values = [
            int(answer['best_source_rank']) for answer in run_answers
            if answer.get('best_source_rank') is not None and int(answer['best_source_rank']) > 0
        ]
        # Preserve multiplicity: two identical tracked prompts are two measured
        # observations and therefore are not the same cohort as one prompt.
        prompt_snapshot = sorted([
            (answer.get('prompt_text') or f"prompt:{answer.get('prompt_id')}").strip()
            for answer in run_answers
        ])
        try:
            competitor_snapshot = json.loads(item.get('competitor_snapshot') or '[]')
        except json.JSONDecodeError:
            competitor_snapshot = []
        cohort_payload = {
            'prompts': prompt_snapshot,
            'competitors': sorted(
                [
                    {
                        'name': (competitor.get('name') or '').strip(),
                        'domain': normalise_site_host(competitor.get('domain') or ''),
                    }
                    for competitor in competitor_snapshot if isinstance(competitor, dict)
                ],
                key=lambda competitor: (competitor['name'].casefold(), competitor['domain']),
            ),
            'region': item.get('region') or None,
        }
        item['cohort_id'] = hashlib.sha256(
            json.dumps(cohort_payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
        ).hexdigest()[:12]
        item['answer_measured_count'] = sum(bool(answer.get('answer_text')) for answer in run_answers)
        item['ranked_appearance_count'] = len(rank_values)
        item['average_source_position'] = round(sum(rank_values) / len(rank_values), 2) if rank_values else None
        item.pop('competitor_snapshot', None)

    latest_history = next((item for item in history if item['id'] == scan['id']), None)
    if latest_history:
        for field in ('cohort_id', 'answer_measured_count', 'ranked_appearance_count', 'average_source_position'):
            scan[field] = latest_history[field]

    # Rank the tracked brand and the scan-time competitor snapshot from the
    # latest saved answers. A mention is counted at most once per answer, and
    # average source rank uses only stored Perplexity Search result positions.
    brand_rankings = []
    if project:
        project = dict(project)
        brand_definitions = [{
            'name': project['brand_name'],
            'domain': project['domain'],
            'aliases': project_brand_aliases(project),
            'tracked': True,
        }]
        for competitor in scan.get('competitor_set') or []:
            if not isinstance(competitor, dict) or not (competitor.get('name') or competitor.get('domain')):
                continue
            brand_definitions.append({
                'name': competitor.get('name') or competitor.get('domain'),
                'domain': competitor.get('domain'),
                'aliases': [competitor.get('name'), competitor.get('domain')],
                'tracked': False,
            })
        measured_answers = [answer for answer in answer_rows if answer.get('answer_text')]
        for brand in brand_definitions:
            mention_count = sum(
                text_mentions_alias(answer.get('answer_text'), brand['aliases'])
                for answer in measured_answers
            )
            source_positions = []
            if brand.get('domain'):
                for answer in answer_rows:
                    ranks = [
                        int(source['rank']) for source in sources_by_answer.get(answer['id'], [])
                        if source.get('source_kind') == 'search_result' and
                        source.get('url') and domain_matches(source['url'], brand['domain'])
                    ]
                    if ranks:
                        source_positions.append(min(ranks))
                    elif brand['tracked'] and answer.get('best_source_rank'):
                        # Backward-compatible fallback for evidence saved before
                        # normalized search-result rows were persisted.
                        source_positions.append(int(answer['best_source_rank']))
            brand_rankings.append({
                'name': brand['name'], 'domain': brand.get('domain'), 'tracked': brand['tracked'],
                'mention_count': mention_count, 'answer_count': len(measured_answers),
                'visibility': round(mention_count / len(measured_answers) * 100, 2) if measured_answers else None,
                'source_appearance_count': len(source_positions),
                'average_source_position': round(sum(source_positions) / len(source_positions), 2) if source_positions else None,
            })
        total_mentions = sum(item['mention_count'] for item in brand_rankings)
        for item in brand_rankings:
            item['share_of_voice'] = round(item['mention_count'] / total_mentions * 100, 2) if total_mentions else None
        brand_rankings.sort(key=lambda item: (
            item['visibility'] is None,
            -(item['visibility'] or 0),
            not item['tracked'],
            item['name'].casefold(),
        ))
        for rank, item in enumerate(brand_rankings, 1):
            item['rank'] = rank
    history.reverse()
    return {
        'run': row_to_dict(scan), 'answers': answer_rows, 'opportunities': opportunities,
        'history': history, 'brand_rankings': brand_rankings,
        'measurement': {
            'source': 'stored_provider_evidence',
            'provider': scan.get('provider'), 'model': scan.get('model'),
            'cohort_id': scan.get('cohort_id'), 'region': scan.get('region'),
            'prompt_count': scan.get('prompt_count'), 'completed_count': scan.get('completed_count'),
            'measured_at': scan.get('completed_at') or scan.get('created_at'),
        },
    }


def citation_domain_rollup(workspace_id, conn=None):
    """Most-cited domains for a workspace, with counts, share and category.

    One GROUP BY over analytics_answer_sources. No N+1: the category is already
    stored on the row by extraction, so nothing is classified at read time.
    """
    query = (
        select(
            analytics_answer_sources.c.domain,
            analytics_answer_sources.c.category,
            func.count().label('citations'),
        )
        .select_from(analytics_answer_sources)
        .join(analytics_provider_answers,
              analytics_provider_answers.c.id == analytics_answer_sources.c.answer_id)
        .join(analytics_prompt_scan_runs,
              analytics_prompt_scan_runs.c.id
              == analytics_provider_answers.c.scan_run_id)
        .where(analytics_prompt_scan_runs.c.workspace_id == workspace_id)
        .group_by(analytics_answer_sources.c.domain,
                  analytics_answer_sources.c.category)
        .order_by(desc(func.count()))
    )
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        rows = [dict(row) for row in conn.execute(query).mappings().all()]
    finally:
        if own_conn:
            conn.close()

    total = sum(row['citations'] for row in rows) or 0
    for row in rows:
        row['share'] = (row['citations'] / total) if total else None
        # own / competitor / third-party is the split a client actually reads.
        row['bucket'] = (
            row['category'] if row['category'] in ('own', 'competitor')
            else 'third_party'
        )
    return {'total_citations': total, 'domains': rows}


def competitor_citation_gaps(workspace_id, conn=None, limit=25):
    """Third-party domains that cite a competitor and never cite you.

    The actionable output of the module: not "you lack citations" but "here is
    where your rivals are cited and you are not".

    One query. Each domain is counted against answers where the brand was
    mentioned and answers where a competitor was mentioned, using the extraction
    rows rather than re-deriving anything at read time.
    """
    brand_hits = func.sum(
        case((extractions.c.brand_mentioned.is_(True), 1), else_=0)
    ).label('brand_answers')
    competitor_hits = func.count().label('total_answers')

    query = (
        select(
            analytics_answer_sources.c.domain,
            analytics_answer_sources.c.category,
            brand_hits,
            competitor_hits,
        )
        .select_from(analytics_answer_sources)
        .join(analytics_provider_answers,
              analytics_provider_answers.c.id == analytics_answer_sources.c.answer_id)
        .join(analytics_prompt_scan_runs,
              analytics_prompt_scan_runs.c.id
              == analytics_provider_answers.c.scan_run_id)
        .join(extractions,
              (extractions.c.answer_id == analytics_provider_answers.c.id)
              & extractions.c.is_current)
        .where(
            (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
            # Own and competitor domains are not gaps: one is already yours, the
            # other you are never going to be cited on.
            & (analytics_answer_sources.c.category.notin_(('own', 'competitor')))
        )
        .group_by(analytics_answer_sources.c.domain,
                  analytics_answer_sources.c.category)
        .having(brand_hits == 0)
        .order_by(desc(func.count()))
        .limit(limit)
    )
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        rows = [dict(row) for row in conn.execute(query).mappings().all()]
    finally:
        if own_conn:
            conn.close()
    return rows


def citation_listing(workspace_id, conn=None):
    """Every cited URL for a workspace, one row per URL.

    citation_domain_rollup() answers "which domains" at the domain grain; this
    answers "which URLs, cited by what, when" - the grain the Citations page's
    per-citation table and evidence drawer need. One flat SELECT (no N+1),
    grouped by URL in Python, the same shape topic_breakdown() already uses for
    its own per-topic grouping. No run_type filter: citations are evidence of
    what a scan actually returned, not the gated Visibility Score, so an
    on-demand "Run scan" click is included here exactly like
    citation_domain_rollup() and the existing /citations endpoint already do.
    """
    query = (
        select(
            analytics_answer_sources.c.answer_id,
            analytics_answer_sources.c.rank,
            analytics_answer_sources.c.source_kind,
            analytics_answer_sources.c.url,
            analytics_answer_sources.c.domain,
            analytics_answer_sources.c.category,
            analytics_provider_answers.c.prompt_id,
            analytics_provider_answers.c.provider,
            analytics_provider_answers.c.created_at,
            func.coalesce(analytics_provider_answers.c.prompt_text,
                          analytics_tracked_prompts.c.prompt).label('prompt'),
        )
        .select_from(analytics_answer_sources)
        .join(analytics_provider_answers,
              analytics_provider_answers.c.id == analytics_answer_sources.c.answer_id)
        .join(analytics_prompt_scan_runs,
              analytics_prompt_scan_runs.c.id == analytics_provider_answers.c.scan_run_id)
        .outerjoin(analytics_tracked_prompts,
                   analytics_provider_answers.c.prompt_id == analytics_tracked_prompts.c.id)
        .where(analytics_prompt_scan_runs.c.workspace_id == workspace_id)
    )
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        rows = conn.execute(query).mappings().all()
    finally:
        if own_conn:
            conn.close()

    by_url = {}
    for row in rows:
        entry = by_url.setdefault(row['url'], {
            'url': row['url'], 'domain': row['domain'], 'category': row['category'],
            'source_kinds': set(), 'engines': set(), 'prompts': {},
            'first_seen': row['created_at'], 'last_seen': row['created_at'],
            'occurrences': [],
        })
        entry['source_kinds'].add(row['source_kind'])
        entry['engines'].add(row['provider'])
        if row['prompt']:
            entry['prompts'][row['prompt']] = True
        if row['created_at'] < entry['first_seen']:
            entry['first_seen'] = row['created_at']
        if row['created_at'] > entry['last_seen']:
            entry['last_seen'] = row['created_at']
        entry['occurrences'].append({
            'answer_id': row['answer_id'], 'prompt_id': row['prompt_id'],
            'prompt': row['prompt'], 'engine': row['provider'],
            'rank': row['rank'], 'created_at': to_iso(row['created_at']),
        })

    listing = []
    for entry in by_url.values():
        entry['citation_count'] = len(entry['occurrences'])
        entry['source_kinds'] = sorted(entry['source_kinds'])
        entry['engines'] = sorted(entry['engines'])
        entry['prompts'] = sorted(entry['prompts'].keys())
        entry['occurrences'].sort(key=lambda o: o['created_at'], reverse=True)
        entry['bucket'] = (
            entry['category'] if entry['category'] in ('own', 'competitor') else 'third_party'
        )
        entry['first_seen'] = to_iso(entry['first_seen'])
        entry['last_seen'] = to_iso(entry['last_seen'])
        listing.append(entry)
    listing.sort(key=lambda entry: entry['citation_count'], reverse=True)
    return listing


def _entity_metrics(*, total, mention_rows, cited_answer_ids):
    """mention_rows: [(answer_id, rank_or_None), ...] for one entity.

    Wraps rollup.score_from_counts() - the exact PRD §13 formula the brand's
    own official Visibility Score is computed with - so a competitor's score
    is arithmetically comparable to the brand's, not a lookalike computed a
    different way.
    """
    mentioned = len(mention_rows)
    reciprocal_rank_sum = sum(1.0 / r for _, r in mention_rows if r)
    scored = score_from_counts(
        total_answers=total, mentioned=mentioned,
        reciprocal_rank_sum=reciprocal_rank_sum, cited=len(cited_answer_ids),
    )
    ranks = [r for _, r in mention_rows if r]
    return {
        'mention_rate': metric(mentioned, total),
        'citation_rate': metric(len(cited_answer_ids), total),
        'average_rank': round(sum(ranks) / len(ranks), 2) if ranks else None,
        'visibility_score': scored['visibility_score'] if total >= MIN_ANSWERS_FOR_SCORE else None,
        'mention_count': mentioned,
        '_day_metrics': scored,  # only used internally for the trend series
    }


def competitor_intelligence(workspace_id, conn=None):
    """Competitor comparison: the live `competitors` table plus the brand,
    scored on the exact cohort and formula metrics_daily uses for the brand's
    own Visibility Score (workspace-scoped, run_type='scheduled' only - the
    same restriction rollup.collect_counts() applies, so a competitor's score
    is on equal footing with the brand's, not measured more generously).

    Three flat queries, no N+1: measured answers (+ the brand's own stored
    extraction flags, same shape as collect_counts()), competitor mention
    rows (already stored by the extraction pipeline - no text is re-scanned),
    and already-classified 'competitor' source rows (domain-matched to a
    specific tracked competitor in Python, the same way brand_rankings()
    matches a domain against a single brand).
    """
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        project = conn.execute(select(workspaces).where(
            workspaces.c.id == workspace_id)).mappings().first()
        competitor_rows = [row_to_dict(row) for row in conn.execute(
            select(competitors).where(competitors.c.workspace_id == workspace_id)
            .order_by(competitors.c.name)).mappings().all()]

        answers = conn.execute(
            select(
                analytics_provider_answers.c.id,
                analytics_provider_answers.c.provider,
                analytics_provider_answers.c.created_at,
                func.coalesce(analytics_provider_answers.c.prompt_text,
                              analytics_tracked_prompts.c.prompt).label('prompt'),
                extractions.c.brand_mentioned, extractions.c.brand_rank,
                extractions.c.brand_cited,
            )
            .select_from(analytics_provider_answers)
            .join(analytics_prompt_scan_runs,
                  analytics_prompt_scan_runs.c.id == analytics_provider_answers.c.scan_run_id)
            .join(extractions,
                  (extractions.c.answer_id == analytics_provider_answers.c.id) & extractions.c.is_current)
            .outerjoin(analytics_tracked_prompts,
                       analytics_provider_answers.c.prompt_id == analytics_tracked_prompts.c.id)
            .where(
                (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
                & (analytics_prompt_scan_runs.c.run_type == SCHEDULED_RUN_TYPE)
            )
        ).mappings().all()
        answers_by_id = {row['id']: row for row in answers}

        competitor_mention_rows, competitor_source_rows = [], []
        if answers:
            competitor_mention_rows = conn.execute(
                select(mentions_table.c.competitor_id, mentions_table.c.rank,
                       extractions.c.answer_id)
                .select_from(mentions_table)
                .join(extractions,
                      (extractions.c.id == mentions_table.c.extraction_id) & extractions.c.is_current)
                .join(analytics_provider_answers,
                      analytics_provider_answers.c.id == extractions.c.answer_id)
                .join(analytics_prompt_scan_runs,
                      analytics_prompt_scan_runs.c.id == analytics_provider_answers.c.scan_run_id)
                .where(
                    (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
                    & (analytics_prompt_scan_runs.c.run_type == SCHEDULED_RUN_TYPE)
                    & (mentions_table.c.entity_type == 'competitor')
                )
            ).mappings().all()

            competitor_source_rows = conn.execute(
                select(analytics_answer_sources.c.answer_id, analytics_answer_sources.c.url)
                .select_from(analytics_answer_sources)
                .join(analytics_provider_answers,
                      analytics_provider_answers.c.id == analytics_answer_sources.c.answer_id)
                .join(analytics_prompt_scan_runs,
                      analytics_prompt_scan_runs.c.id == analytics_provider_answers.c.scan_run_id)
                .where(
                    (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
                    & (analytics_prompt_scan_runs.c.run_type == SCHEDULED_RUN_TYPE)
                    & (analytics_answer_sources.c.category == 'competitor')
                )
            ).mappings().all()
    finally:
        if own_conn:
            conn.close()

    total = len(answers)

    mentions_by_competitor = {}
    mentions_by_answer = {}
    for row in competitor_mention_rows:
        mentions_by_competitor.setdefault(row['competitor_id'], []).append(
            (row['answer_id'], row['rank']))
        mentions_by_answer.setdefault(row['answer_id'], []).append(
            (row['competitor_id'], row['rank']))

    urls_by_answer = {}
    for row in competitor_source_rows:
        urls_by_answer.setdefault(row['answer_id'], []).append(row['url'])

    def cited_answer_ids_for(domains):
        if not domains:
            return set()
        return {
            answer_id for answer_id, urls in urls_by_answer.items()
            if any(domain_matches(url, domain) for url in urls for domain in domains)
        }

    # -- entities -----------------------------------------------------------
    brand_mention_rows = [
        (row['id'], row['brand_rank']) for row in answers if row['brand_mentioned']
    ]
    brand_cited_ids = {row['id'] for row in answers if row['brand_cited']}
    brand_metrics = _entity_metrics(
        total=total, mention_rows=brand_mention_rows, cited_answer_ids=brand_cited_ids)
    entities = [{
        'id': 'brand', 'name': project['brand_name'] if project else None,
        'domain': project['domain'] if project else None, 'tracked': True,
        **{k: v for k, v in brand_metrics.items() if not k.startswith('_')},
    }]

    for competitor in competitor_rows:
        mention_rows = mentions_by_competitor.get(competitor['id'], [])
        cited_ids = cited_answer_ids_for(competitor.get('domains'))
        entity_metrics = _entity_metrics(
            total=total, mention_rows=mention_rows, cited_answer_ids=cited_ids)
        entities.append({
            'id': competitor['id'], 'name': competitor['name'],
            'domain': (competitor.get('domains') or [None])[0],
            'domains': competitor.get('domains') or [], 'tracked': False,
            **{k: v for k, v in entity_metrics.items() if not k.startswith('_')},
        })

    total_mentions = sum(e['mention_count'] for e in entities)
    for entity in entities:
        entity['share_of_voice'] = (
            round(entity['mention_count'] / total_mentions, 4) if total_mentions else None)
    entities.sort(key=lambda e: (
        e['mention_rate']['value'] is None, -(e['mention_rate']['value'] or 0),
        not e['tracked'], (e['name'] or '').casefold(),
    ))
    for rank, entity in enumerate(entities, 1):
        entity['rank'] = rank

    # -- trend, bucketed by UTC day -----------------------------------------
    def day_of(answer_id):
        return answers_by_id[answer_id]['created_at'].date()

    days_totals = {}
    for row in answers:
        d = row['created_at'].date()
        days_totals[d] = days_totals.get(d, 0) + 1

    def day_series(mention_rows_by_day, cited_ids_by_day):
        series = []
        for d in sorted(days_totals):
            day_total = days_totals[d]
            day_mentions = mention_rows_by_day.get(d, [])
            day_cited = cited_ids_by_day.get(d, set())
            scored = score_from_counts(
                total_answers=day_total, mentioned=len(day_mentions),
                reciprocal_rank_sum=sum(1.0 / r for r in day_mentions if r),
                cited=len(day_cited),
            )
            series.append({
                'date': d.isoformat(),
                'visibility_score': scored['visibility_score'],
                'mention_rate': scored['mention_rate'],
            })
        return series

    brand_ranks_by_day = {}
    brand_cited_by_day = {}
    for row in answers:
        d = row['created_at'].date()
        if row['brand_mentioned']:
            brand_ranks_by_day.setdefault(d, []).append(row['brand_rank'])
        if row['brand_cited']:
            brand_cited_by_day.setdefault(d, set()).add(row['id'])
    trend = {'brand': day_series(brand_ranks_by_day, brand_cited_by_day)}

    for competitor in competitor_rows:
        ranks_by_day = {}
        for answer_id, rank in mentions_by_competitor.get(competitor['id'], []):
            ranks_by_day.setdefault(day_of(answer_id), []).append(rank)
        cited_ids = cited_answer_ids_for(competitor.get('domains'))
        cited_by_day = {}
        for answer_id in cited_ids:
            cited_by_day.setdefault(day_of(answer_id), set()).add(answer_id)
        trend[str(competitor['id'])] = day_series(ranks_by_day, cited_by_day)

    # -- prompts where a competitor outperforms the brand --------------------
    outperforms = []
    competitor_names = {c['id']: c['name'] for c in competitor_rows}
    for row in answers:
        brand_rank = row['brand_rank'] if row['brand_mentioned'] else None
        for competitor_id, competitor_rank in mentions_by_answer.get(row['id'], []):
            if competitor_rank is None:
                continue
            if brand_rank is not None and competitor_rank >= brand_rank:
                continue
            outperforms.append({
                'answer_id': row['id'], 'prompt': row['prompt'], 'engine': row['provider'],
                'created_at': to_iso(row['created_at']),
                'competitor_id': competitor_id,
                'competitor_name': competitor_names.get(competitor_id),
                'competitor_rank': competitor_rank, 'brand_rank': brand_rank,
            })
    outperforms.sort(key=lambda o: o['created_at'], reverse=True)

    return {
        'measured_answer_count': total, 'threshold': MIN_ANSWERS_FOR_SCORE,
        'entities': entities, 'trend': trend, 'outperforms': outperforms,
    }


def _context_snippet(text_value, offset, radius=80):
    """A window of the stored answer text around a real character offset -
    never a fabricated excerpt, and never the whole answer (that's what the
    evidence drawer is for)."""
    if not text_value or offset is None:
        return None
    start = max(0, offset - radius)
    end = min(len(text_value), offset + radius)
    snippet = text_value[start:end].strip()
    if start > 0:
        snippet = '…' + snippet
    if end < len(text_value):
        snippet = snippet + '…'
    return snippet


def mention_listing(workspace_id, conn=None, limit=500):
    """Every measured answer for a workspace, across every run - not just the
    latest one (latest_prompt_evidence) or one scan (provider_evidence_rows).
    Reuses answer_derivations() for the brand fields it already computes
    correctly rather than re-deriving them, and reads the mentions table
    (already written by the extraction pipeline) for context and competitor
    attribution - nothing here re-scans answer text.
    """
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        answers = conn.execute(
            select(
                analytics_provider_answers.c.id,
                analytics_provider_answers.c.provider,
                analytics_provider_answers.c.answer_text,
                analytics_provider_answers.c.created_at,
                analytics_provider_answers.c.scan_run_id,
                analytics_prompt_scan_runs.c.run_type,
                analytics_prompt_scan_runs.c.region,
                func.coalesce(analytics_provider_answers.c.prompt_text,
                              analytics_tracked_prompts.c.prompt).label('prompt'),
                func.coalesce(analytics_provider_answers.c.topic_name,
                              analytics_topics.c.name).label('topic_name'),
            )
            .select_from(analytics_provider_answers)
            .join(analytics_prompt_scan_runs,
                  analytics_prompt_scan_runs.c.id == analytics_provider_answers.c.scan_run_id)
            .outerjoin(analytics_tracked_prompts,
                       analytics_provider_answers.c.prompt_id == analytics_tracked_prompts.c.id)
            .outerjoin(analytics_topics,
                       analytics_tracked_prompts.c.topic_id == analytics_topics.c.id)
            .where(analytics_prompt_scan_runs.c.workspace_id == workspace_id)
            .order_by(desc(analytics_provider_answers.c.created_at))
            .limit(limit)
        ).mappings().all()
        answer_ids = [row['id'] for row in answers]
        derived = answer_derivations(answer_ids, conn)

        brand_offsets = {}
        competitor_names = {}
        mentions_by_answer = {}
        if answer_ids:
            for row in conn.execute(
                select(extractions.c.answer_id, mentions_table.c.char_offset)
                .select_from(mentions_table)
                .join(extractions,
                      (extractions.c.id == mentions_table.c.extraction_id) & extractions.c.is_current)
                .where(
                    (extractions.c.answer_id.in_(answer_ids))
                    & (mentions_table.c.entity_type == 'brand')
                )
            ).mappings():
                brand_offsets[row['answer_id']] = row['char_offset']

            competitor_names = dict(conn.execute(
                select(competitors.c.id, competitors.c.name)
                .where(competitors.c.workspace_id == workspace_id)
            ).all())

            for row in conn.execute(
                select(extractions.c.answer_id, mentions_table.c.competitor_id,
                       mentions_table.c.rank)
                .select_from(mentions_table)
                .join(extractions,
                      (extractions.c.id == mentions_table.c.extraction_id) & extractions.c.is_current)
                .where(
                    (extractions.c.answer_id.in_(answer_ids))
                    & (mentions_table.c.entity_type == 'competitor')
                )
            ).mappings():
                mentions_by_answer.setdefault(row['answer_id'], []).append({
                    'competitor_id': row['competitor_id'],
                    'name': competitor_names.get(row['competitor_id']),
                    'rank': row['rank'],
                })
    finally:
        if own_conn:
            conn.close()

    listing = []
    for row in answers:
        item = dict(row)
        item['id'] = row['id']
        item['created_at'] = to_iso(row['created_at'])
        item.update(derived.get(row['id'], {}))
        item['context'] = _context_snippet(row['answer_text'], brand_offsets.get(row['id']))
        item['competitors'] = mentions_by_answer.get(row['id'], [])
        answer_text = item.pop('answer_text', None)
        item['answer_preview'] = (
            (answer_text[:220].rstrip() + '…') if answer_text and len(answer_text) > 220
            else answer_text
        )
        listing.append(item)
    return listing


def sentiment_intelligence(workspace_id, conn=None, limit=500):
    """Brand sentiment for a workspace - scoped to scheduled runs and
    brand-mentioned answers only, the same cohort app.sentiment's classifier
    draws from and metrics_daily.sentiment_index is computed over, so the
    overview, by-topic and by-engine numbers can never disagree with each
    other about which answers count.

    Brand-only, by design (see app/sentiment.py's own docstring): the
    mentions table has no sentiment column, so a specific competitor's
    sentiment is not representable without a schema change this pass
    deliberately does not make.
    """
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        rows = conn.execute(
            select(
                analytics_provider_answers.c.id,
                analytics_provider_answers.c.provider,
                analytics_provider_answers.c.created_at,
                func.coalesce(analytics_provider_answers.c.prompt_text,
                              analytics_tracked_prompts.c.prompt).label('prompt'),
                func.coalesce(analytics_provider_answers.c.topic_name,
                              analytics_topics.c.name).label('topic_name'),
                extractions.c.sentiment,
                extractions.c.sentiment_conf,
            )
            .select_from(analytics_provider_answers)
            .join(analytics_prompt_scan_runs,
                  analytics_prompt_scan_runs.c.id == analytics_provider_answers.c.scan_run_id)
            .join(extractions,
                  (extractions.c.answer_id == analytics_provider_answers.c.id) & extractions.c.is_current)
            .outerjoin(analytics_tracked_prompts,
                       analytics_provider_answers.c.prompt_id == analytics_tracked_prompts.c.id)
            .outerjoin(analytics_topics,
                       analytics_tracked_prompts.c.topic_id == analytics_topics.c.id)
            .where(
                (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
                & (analytics_prompt_scan_runs.c.run_type == SCHEDULED_RUN_TYPE)
                & (extractions.c.brand_mentioned.is_(True))
            )
            .order_by(desc(analytics_provider_answers.c.created_at))
            .limit(limit)
        ).mappings().all()

        engine_names = dict(conn.execute(select(engines.c.id, engines.c.display_name)).all())
    finally:
        if own_conn:
            conn.close()

    labels = [row['sentiment'] for row in rows]
    distribution = {'positive': 0, 'neutral': 0, 'negative': 0}
    for label in labels:
        if label in distribution:
            distribution[label] += 1
    classified_count = sum(distribution.values())

    by_topic = {}
    for row in rows:
        key = row['topic_name'] or 'Untagged'
        bucket = by_topic.setdefault(key, {'mentioned': 0, 'labels': []})
        bucket['mentioned'] += 1
        bucket['labels'].append(row['sentiment'])
    topic_breakdown_rows = [
        {
            'topic': name, 'mentioned': bucket['mentioned'],
            'classified': sum(1 for label in bucket['labels'] if label in distribution),
            'sentiment_index': sentiment_index_from_labels(bucket['labels']),
        }
        for name, bucket in sorted(by_topic.items())
    ]

    # By engine: the most recent metrics_daily row per engine - already
    # computed by the rollup, not re-derived here.
    latest_by_engine = {}
    for row in latest_metrics_all_engines(workspace_id):
        if row['engine_id'] is None:
            continue
        existing = latest_by_engine.get(row['engine_id'])
        if existing is None or row['date'] > existing['date']:
            latest_by_engine[row['engine_id']] = row
    engine_breakdown = [
        {
            'engine_id': engine_id, 'engine': engine_names.get(engine_id, 'Unknown'),
            'sentiment_index': row['sentiment_index'], 'answer_count': row['answer_count'],
            'date': row['date'].isoformat() if row['date'] else None,
        }
        for engine_id, row in sorted(latest_by_engine.items(), key=lambda kv: engine_names.get(kv[0], ''))
    ]

    # Trend: the blended metrics_daily row per day, chronological.
    trend = [
        {'date': row['date'].isoformat(), 'sentiment_index': row['sentiment_index']}
        for row in reversed(latest_metrics(workspace_id, engine_id=None))
    ]

    evidence = [
        {
            'id': row['id'], 'provider': row['provider'], 'prompt': row['prompt'],
            'topic_name': row['topic_name'], 'sentiment': row['sentiment'],
            'sentiment_conf': row['sentiment_conf'], 'created_at': to_iso(row['created_at']),
        }
        for row in rows
    ]

    return {
        'configured': open_model_settings()['configured'],
        'mentioned_count': len(rows), 'classified_count': classified_count,
        'overall_sentiment_index': sentiment_index_from_labels(labels),
        'distribution': distribution,
        'topics': topic_breakdown_rows, 'engines': engine_breakdown,
        'trend': trend, 'evidence': evidence,
    }


_FINDING_PRIORITY = {'critical': 'high', 'high': 'high', 'medium': 'medium', 'low': 'low'}


def recommendation_intelligence(workspace_id, conn=None):
    """Merge the two things in this backend that actually generate a
    title/rationale/priority recommendation - nothing else does, and this
    function invents no third source:

    - analytics_content_opportunities: rule-based or open-model-summarized
      opportunities already written at the end of every prompt scan
      (app/scanning.py), never re-derived here.
    - analytics_audit_findings, via the existing latest_site_audit() (no new
      query): every finding already carries its own `recommendation` text.

    Neither table has a status/done column, so every item here is read-only
    evidence, not a workflow state.
    """
    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        opportunity_rows = [row_to_dict(row) for row in conn.execute(
            select(analytics_content_opportunities)
            .where(analytics_content_opportunities.c.workspace_id == workspace_id)
            .order_by(analytics_content_opportunities.c.priority, desc(analytics_content_opportunities.c.created_at))
        ).mappings().all()]

        answer_ids = set()
        for row in opportunity_rows:
            answer_ids.update(int(match.split(':')[1]) for match in
                              re.findall(r'answer:\d+', row['evidence_refs'] or ''))
        answer_context = {}
        if answer_ids:
            for row in conn.execute(
                select(
                    analytics_provider_answers.c.id, analytics_provider_answers.c.provider,
                    analytics_provider_answers.c.created_at,
                    func.coalesce(analytics_provider_answers.c.prompt_text,
                                  analytics_tracked_prompts.c.prompt).label('prompt'),
                    func.coalesce(analytics_provider_answers.c.topic_name,
                                  analytics_topics.c.name).label('topic_name'),
                )
                .select_from(analytics_provider_answers)
                .outerjoin(analytics_tracked_prompts,
                           analytics_provider_answers.c.prompt_id == analytics_tracked_prompts.c.id)
                .outerjoin(analytics_topics,
                           analytics_tracked_prompts.c.topic_id == analytics_topics.c.id)
                .where(analytics_provider_answers.c.id.in_(answer_ids))
            ).mappings():
                answer_context[row['id']] = dict(row)
    finally:
        if own_conn:
            conn.close()

    recommendations = []
    for row in opportunity_rows:
        answer_refs = [int(match.split(':')[1]) for match in
                       re.findall(r'answer:\d+', row['evidence_refs'] or '')]
        evidence = [
            {
                'answer_id': answer_id, 'prompt': ctx.get('prompt'), 'topic_name': ctx.get('topic_name'),
                'provider': ctx.get('provider'), 'created_at': to_iso(ctx.get('created_at')),
            }
            for answer_id in answer_refs
            for ctx in [answer_context.get(answer_id)] if ctx
        ]
        recommendations.append({
            'id': f"opportunity:{row['id']}", 'kind': 'content_opportunity',
            'title': row['title'], 'rationale': row['rationale'], 'priority': row['priority'],
            'area': 'AI Visibility', 'source': row['source'], 'created_at': to_iso(row['created_at']),
            'evidence': evidence, 'link': '/mentions',
        })

    audit = latest_site_audit(workspace_id)
    if audit:
        pages_by_id = {page['id']: page for page in audit['pages']}
        for finding in audit['findings']:
            page = pages_by_id.get(finding['page_id'])
            recommendations.append({
                'id': f"finding:{finding['id']}", 'kind': 'site_finding',
                'title': finding['code'].replace('_', ' ').capitalize(),
                'rationale': finding['recommendation'], 'priority': _FINDING_PRIORITY.get(finding['severity'], 'medium'),
                'area': finding['area'], 'source': 'Site audit',
                'created_at': to_iso(audit['run'].get('completed_at') or audit['run'].get('created_at')),
                'evidence': [{'evidence_text': finding['evidence'],
                             'url': page['final_url'] or page['url'] if page else None}],
                'link': '/site-audit',
            })

    # Newest first within a priority tier, then high priority ahead of low -
    # two stable passes rather than one composite key, since "newest" needs
    # descending order and "priority" needs ascending in the same sort.
    priority_rank = {'high': 0, 'medium': 1, 'low': 2}
    recommendations.sort(key=lambda item: item['created_at'] or '', reverse=True)
    recommendations.sort(key=lambda item: priority_rank.get(item['priority'], 1))
    return recommendations


def scan_history(workspace_id, limit=200, *, filters=None, conn=None):
    """Every prompt-scan run for a workspace, newest first - plain columns
    already stored on analytics_prompt_scan_runs, no aggregation. This is
    the list latest_prompt_evidence(workspace_id, run_id) drills into for
    one specific run's full evidence (unchanged, reused as-is).

    `filters` is a parsed dict from app.analytics_filters.parse_filters()
    (date range / region / engine_ids) - optional, so every existing caller
    that doesn't pass one keeps working unfiltered exactly as before.
    """
    from app.analytics_filters import engine_providers_for_ids, scan_run_filter_clause

    own_conn = conn is None
    conn = conn or engine.connect()
    try:
        providers = engine_providers_for_ids((filters or {}).get('engine_ids'), conn)
        clause = scan_run_filter_clause(filters or {}, providers=providers)
        rows = [row_to_dict(row) for row in conn.execute(
            select(analytics_prompt_scan_runs)
            .where((analytics_prompt_scan_runs.c.workspace_id == workspace_id) & clause)
            .order_by(desc(analytics_prompt_scan_runs.c.created_at))
            .limit(limit)
        ).mappings().all()]
    finally:
        if own_conn:
            conn.close()
    for row in rows:
        try:
            row['competitor_snapshot'] = json.loads(row.get('competitor_snapshot') or '[]')
        except json.JSONDecodeError:
            row['competitor_snapshot'] = []
    return rows
