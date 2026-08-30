"""Versioned extraction over stored answers.

Extraction derives rank, per-entity mentions and citation categories from an answer
that is already saved. It never calls a provider, so re-running it after a formula
change costs nothing but CPU - that is the whole reason
analytics_provider_answers.raw_response is immutable.

Bump EXTRACTOR_VERSION whenever the derivation changes. A re-run inserts a new row
and flips the previous one's is_current in the same transaction; nothing is updated
in place, so every historical extraction stays inspectable.
"""

from datetime import datetime

from sqlalchemy import insert, select, update

from app.db import engine
from app.extraction.citations import (
    classify_citation,
    competitor_domains,
    workspace_domains,
)
from app.extraction.mentions import project_brand_aliases
from app.extraction.rank import rank_entities
from app.models import (
    analytics_answer_sources,
    brand_aliases,
    competitors as competitors_table,
    extractions,
    mentions as mentions_table,
    workspaces,
)

EXTRACTOR_VERSION = '2026.08.2'


def stored_brand_aliases(workspace_id, conn):
    rows = conn.execute(
        select(brand_aliases.c.alias).where(brand_aliases.c.workspace_id == workspace_id)
    ).scalars().all()
    return list(rows)


def workspace_competitors(workspace_id, conn):
    rows = conn.execute(
        select(competitors_table).where(competitors_table.c.workspace_id == workspace_id)
    ).mappings().all()
    return [dict(row) for row in rows]


def categorise_sources(answer_id, *, workspace, competitors, conn):
    """Classify an answer's citations and report whether any is our own.

    Deliberately separate from extract_answer: citations belong to the search
    results, which exist even for an answer that came back with no text. Tying
    categories to extraction would leave search-only answers uncategorised and make
    best_source_rank silently null.
    """
    own = workspace_domains(workspace)
    rivals = competitor_domains(competitors)

    sources = conn.execute(
        select(analytics_answer_sources).where(
            analytics_answer_sources.c.answer_id == answer_id)
    ).mappings().all()

    brand_cited = False
    for source in sources:
        category = classify_citation(
            source['url'], own_domains=own, competitor_domains=rivals)
        if category == 'own':
            brand_cited = True
        conn.execute(
            analytics_answer_sources.update()
            .where(analytics_answer_sources.c.id == source['id'])
            .values(category=category)
        )
    return brand_cited


def extract_answer(*, answer, workspace, competitors, aliases, conn):
    """Derive one extraction from a stored answer. Returns the new extraction id.

    Runs inside the caller's transaction so the insert and the is_current flip
    cannot be observed apart.
    """
    answer_text = answer.get('answer_text') or ''
    brand_alias_list = project_brand_aliases(workspace, aliases)

    brand_rank, mention_rows = rank_entities(
        answer_text, brand_aliases=brand_alias_list, competitors=competitors)

    brand_cited = categorise_sources(
        answer['id'], workspace=workspace, competitors=competitors, conn=conn)

    # Supersede the previous version before inserting the new one; the partial
    # unique index would reject two current rows for the same answer.
    conn.execute(
        update(extractions)
        .where((extractions.c.answer_id == answer['id']) & (extractions.c.is_current))
        .values(is_current=False)
    )

    extraction_id = conn.execute(insert(extractions).values(
        answer_id=answer['id'],
        extractor_version=EXTRACTOR_VERSION,
        is_current=True,
        brand_mentioned=brand_rank is not None,
        brand_rank=brand_rank,
        brand_cited=brand_cited,
        sentiment=None,
        sentiment_conf=None,
        summary=None,
        created_at=datetime.utcnow(),
    )).inserted_primary_key[0]

    if mention_rows:
        conn.execute(insert(mentions_table), [
            {**row, 'extraction_id': extraction_id} for row in mention_rows
        ])

    return extraction_id


def extract_for_answer(answer, workspace, competitors=None, aliases=None):
    """Convenience wrapper that opens its own transaction."""
    with engine.begin() as conn:
        if competitors is None:
            competitors = workspace_competitors(workspace['id'], conn)
        if aliases is None:
            aliases = stored_brand_aliases(workspace['id'], conn)
        return extract_answer(answer=answer, workspace=workspace,
                              competitors=competitors, aliases=aliases, conn=conn)


def reextract_workspace(workspace_id):
    """Re-run extraction over every stored answer for a workspace.

    Zero provider calls: the answers are already on disk. Returns the number of
    answers re-extracted.
    """
    from app.costs import record_usage
    from app.models import analytics_prompt_scan_runs, analytics_provider_answers

    with engine.connect() as conn:
        workspace = conn.execute(
            select(workspaces).where(workspaces.c.id == workspace_id)
        ).mappings().first()
        if not workspace:
            return 0
        workspace = dict(workspace)
        answers = [dict(row) for row in conn.execute(
            select(analytics_provider_answers)
            .join(analytics_prompt_scan_runs,
                  analytics_prompt_scan_runs.c.id
                  == analytics_provider_answers.c.scan_run_id)
            .where(
                (analytics_prompt_scan_runs.c.workspace_id == workspace_id)
                & (analytics_provider_answers.c.status == 'ok')
            )
        ).mappings().all()]

    if not answers:
        return 0

    with engine.begin() as conn:
        competitors = workspace_competitors(workspace_id, conn)
        aliases = stored_brand_aliases(workspace_id, conn)
        for answer in answers:
            extract_answer(answer=answer, workspace=workspace,
                           competitors=competitors, aliases=aliases, conn=conn)

    # Metered at zero cost, so a re-run is visible in the ledger as work that
    # happened and money that was not spent.
    for _ in answers:
        record_usage(workspace_id=workspace_id, org_id=workspace['org_id'],
                     category='extraction', provider='regex')

    return len(answers)
