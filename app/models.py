"""SQLAlchemy Core table definitions, one place."""

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

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, JSON, Numeric
from sqlalchemy.dialects.postgresql import ARRAY

# The spec calls for text[]. Postgres gets exactly that; SQLite, which every test
# runs on, has no array type and gets JSON instead. Python sees a list either way.
STRING_ARRAY = ARRAY(Text).with_variant(JSON, 'sqlite')

from app.db import metadata

contacts = Table(
    'contacts',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('name', String(255), nullable=False),
    Column('email', String(255), nullable=False),
    Column('message', Text, nullable=False),
    Column('created_at', DateTime, nullable=False),
)

users = Table(
    'users',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('username', String(150), nullable=False, unique=True),
    Column('email', String(255), nullable=False, unique=True),
    Column('password_hash', String(255), nullable=False),
    Column('created_at', DateTime, nullable=False),
)

app_metadata = Table(
    'app_metadata',
    metadata,
    Column('key', String(100), primary_key=True),
    Column('value', String(255), nullable=False),
)

# --- Tenancy (architecture spec 2.1) -----------------------------------------
# Every workspace-scoped table below carries workspace_id. user_id survives only
# on users and memberships: ownership is resolved through membership in an org,
# never by a hand-written predicate on the row.

organizations = Table(
    'organizations',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('name', Text, nullable=False),
    # The spec has plan_id REFERENCES plans(id), but plans does not exist yet -
    # Stripe and plan gating are week 3+. Column kept, foreign key deferred until
    # the table it points at is real.
    Column('plan_id', Integer, nullable=True),
    Column('stripe_customer_id', Text, nullable=True),
    # The plan's monthly spend ceiling in USD. Belongs on plans, which does not
    # exist until Stripe in week 3+, so it sits here and falls back to
    # DEFAULT_MONTHLY_COST_CEILING_USD when null.
    Column('monthly_cost_ceiling_usd', Numeric(10, 2), nullable=True),
    Column('created_at', DateTime, nullable=False),
)

memberships = Table(
    'memberships',
    metadata,
    Column('org_id', Integer, ForeignKey('organizations.id', ondelete='CASCADE'),
           primary_key=True),
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'),
           primary_key=True),
    Column('role', Text, nullable=False),
    CheckConstraint(
        "role IN ('owner', 'admin', 'member', 'client_viewer')",
        name='ck_membership_role',
    ),
)

workspaces = Table(
    'workspaces',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('org_id', Integer, ForeignKey('organizations.id', ondelete='CASCADE'),
           nullable=False),
    Column('brand_name', Text, nullable=False),
    Column('domains', STRING_ARRAY, nullable=False, default=list),
    Column('geo', Text, nullable=False, server_default='US'),
    Column('language', Text, nullable=False, server_default='en'),
    Column('kind', Text, nullable=False, server_default='project'),
    Column('status', Text, nullable=False, server_default='active'),
    Column('deleted_at', DateTime, nullable=True),
    Column('created_at', DateTime, nullable=False),
    # Not in spec 2.1, kept from workspaces: the crawler, site audit and
    # RAG modules read both today, and T5 has no mandate to rewrite them.
    Column('domain', String(255), nullable=True),
    Column('website_url', String(2048), nullable=True),
    Column('industry', String(150), nullable=True),
    Column('updated_at', DateTime, nullable=True),
    CheckConstraint("kind IN ('project', 'pitch')", name='ck_workspace_kind'),
    CheckConstraint("status IN ('active', 'soft_deleted')", name='ck_workspace_status'),
    Index('ix_workspaces_org_active', 'org_id', sqlite_where=text("status = 'active'"),
          postgresql_where=text("status = 'active'")),
)

brand_aliases = Table(
    'brand_aliases',
    metadata,
    Column('workspace_id', Integer, ForeignKey('workspaces.id', ondelete='CASCADE'),
           primary_key=True),
    Column('alias', Text, primary_key=True),
)

competitors = Table(
    'competitors',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, ForeignKey('workspaces.id', ondelete='CASCADE'),
           nullable=False, index=True),
    Column('name', Text, nullable=False),
    Column('domains', STRING_ARRAY, nullable=False, default=list),
    Column('aliases', STRING_ARRAY, nullable=False, default=list),
    Column('created_at', DateTime, nullable=True),
    UniqueConstraint('workspace_id', 'name', name='uq_competitor_name'),
)




analytics_audit_jobs = Table(
    'analytics_audit_jobs',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    # Carried through to the run, so metrics can exclude on-demand scans.
    Column('run_type', Text, nullable=False, server_default='scheduled'),
    Column('job_type', String(40), nullable=False),
    Column('provider', String(40), nullable=True),
    Column('status', String(32), nullable=False),
    Column('progress', Integer, nullable=False, default=0),
    Column('total_items', Integer, nullable=False, default=0),
    Column('completed_items', Integer, nullable=False, default=0),
    Column('error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('started_at', DateTime, nullable=True),
    Column('completed_at', DateTime, nullable=True),
)

analytics_site_audits = Table(
    'analytics_site_audits',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('job_id', Integer, nullable=True, index=True),
    Column('status', String(32), nullable=False),
    Column('source_type', String(40), nullable=False, default='website_crawl'),
    Column('start_url', String(2048), nullable=False),
    Column('final_url', String(2048), nullable=True),
    Column('pages_discovered', Integer, nullable=False, default=0),
    Column('pages_audited', Integer, nullable=False, default=0),
    Column('pages_failed', Integer, nullable=False, default=0),
    Column('readiness_score', Integer, nullable=True),
    Column('metadata_score', Integer, nullable=True),
    Column('content_score', Integer, nullable=True),
    Column('crawlability_score', Integer, nullable=True),
    Column('structured_data_score', Integer, nullable=True),
    Column('summary', Text, nullable=False),
    Column('created_at', DateTime, nullable=False),
    Column('completed_at', DateTime, nullable=True),
)

analytics_audit_pages = Table(
    'analytics_audit_pages',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('url', String(2048), nullable=False),
    Column('final_url', String(2048), nullable=True),
    Column('fetched', Boolean, nullable=False, default=False),
    Column('http_status', Integer, nullable=True),
    Column('title', Text, nullable=True),
    Column('description', Text, nullable=True),
    Column('headings_count', Integer, nullable=False, default=0),
    Column('word_count', Integer, nullable=False, default=0),
    Column('schema_blocks', Integer, nullable=False, default=0),
    Column('canonical', String(2048), nullable=True),
    Column('noindex', Boolean, nullable=False, default=False),
    Column('language', String(40), nullable=True),
    Column('internal_links', Integer, nullable=False, default=0),
    Column('external_links', Integer, nullable=False, default=0),
    Column('readiness_score', Integer, nullable=True),
    Column('metadata_score', Integer, nullable=True),
    Column('content_score', Integer, nullable=True),
    Column('crawlability_score', Integer, nullable=True),
    Column('structured_data_score', Integer, nullable=True),
    Column('issues_count', Integer, nullable=False, default=0),
    Column('error', Text, nullable=True),
    Column('fetched_at', DateTime, nullable=False),
    UniqueConstraint('audit_id', 'url', name='uq_analytics_audit_page'),
)

analytics_audit_findings = Table(
    'analytics_audit_findings',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('page_id', Integer, nullable=True, index=True),
    Column('code', String(80), nullable=False),
    Column('area', String(40), nullable=False),
    Column('severity', String(20), nullable=False),
    Column('evidence', Text, nullable=False),
    Column('recommendation', Text, nullable=False),
)

analytics_sitemaps = Table(
    'analytics_sitemaps',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('url', String(2048), nullable=False),
    Column('status', String(32), nullable=False),
    Column('urls_discovered', Integer, nullable=False, default=0),
    Column('error', Text, nullable=True),
)

analytics_rag_documents = Table(
    'analytics_rag_documents',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('page_id', Integer, nullable=False, index=True),
    Column('url', String(2048), nullable=False),
    Column('title', Text, nullable=True),
    Column('content_hash', String(64), nullable=False),
    Column('content_text', Text, nullable=False),
    Column('word_count', Integer, nullable=False),
    Column('created_at', DateTime, nullable=False),
    UniqueConstraint('audit_id', 'page_id', name='uq_analytics_rag_document_page'),
)

analytics_rag_chunks = Table(
    'analytics_rag_chunks',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('document_id', Integer, nullable=False, index=True),
    Column('chunk_index', Integer, nullable=False),
    Column('content_hash', String(64), nullable=False),
    Column('content_text', Text, nullable=False),
    Column('token_count', Integer, nullable=False),
    Column('created_at', DateTime, nullable=False),
    UniqueConstraint('document_id', 'chunk_index', name='uq_analytics_rag_document_chunk'),
)

analytics_rag_insights = Table(
    'analytics_rag_insights',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('audit_id', Integer, nullable=False, index=True),
    Column('question', Text, nullable=False),
    Column('provider', String(80), nullable=False),
    Column('model', String(160), nullable=False),
    Column('status', String(32), nullable=False),
    Column('answer_text', Text, nullable=True),
    Column('evidence_refs', Text, nullable=False),
    Column('retrieved_chunk_count', Integer, nullable=False, default=0),
    Column('error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
)

gsc_connections = Table(
    'gsc_connections',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False),
    Column('encrypted_refresh_token', Text, nullable=True),
    Column('encrypted_access_token', Text, nullable=True),
    Column('token_expires_at', DateTime, nullable=True),
    Column('granted_scopes', Text, nullable=True),
    Column('selected_property', String(2048), nullable=True),
    Column('status', String(32), nullable=False),
    Column('last_error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
    UniqueConstraint('workspace_id', name='uq_gsc_connections_workspace_id'),
)

gsc_properties = Table(
    'gsc_properties',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('connection_id', Integer, nullable=False, index=True),
    Column('site_url', String(2048), nullable=False),
    Column('permission_level', String(80), nullable=False),
    Column('selected', Boolean, nullable=False, default=False),
    UniqueConstraint('connection_id', 'site_url', name='uq_gsc_connection_property'),
)

gsc_sync_runs = Table(
    'gsc_sync_runs',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('connection_id', Integer, nullable=False, index=True),
    Column('property_url', String(2048), nullable=False),
    Column('status', String(32), nullable=False),
    Column('start_date', String(10), nullable=False),
    Column('end_date', String(10), nullable=False),
    Column('rows_saved', Integer, nullable=False, default=0),
    Column('data_state', String(20), nullable=False, default='final'),
    Column('error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('completed_at', DateTime, nullable=True),
)

gsc_query_rows = Table(
    'gsc_query_rows',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('sync_run_id', Integer, nullable=False, index=True),
    Column('query', Text, nullable=False),
    Column('page', String(2048), nullable=True),
    Column('clicks', Float, nullable=False),
    Column('impressions', Float, nullable=False),
    Column('ctr', Float, nullable=False),
    Column('position', Float, nullable=False),
)

analytics_topics = Table(
    'analytics_topics',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('name', String(180), nullable=False),
    Column('created_at', DateTime, nullable=False),
    UniqueConstraint('workspace_id', 'name', name='uq_analytics_topic'),
)


analytics_tracked_prompts = Table(
    'analytics_tracked_prompts',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('topic_id', Integer, nullable=True, index=True),
    Column('prompt', Text, nullable=False),
    Column('intent', String(80), nullable=False, default='Discovery'),
    Column('active', Boolean, nullable=False, default=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

analytics_prompt_scan_runs = Table(
    'analytics_prompt_scan_runs',
    metadata,
    Column('id', Integer, primary_key=True),
    # PRD 13: on-demand runs are excluded from metrics, because they happen at the
    # moment someone is optimising and would bias every score upward.
    Column('run_type', Text, nullable=False, server_default='scheduled'),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('job_id', Integer, nullable=True, index=True),
    Column('provider', String(40), nullable=False),
    Column('model', String(160), nullable=False),
    Column('region', String(8), nullable=True),
    Column('competitor_snapshot', Text, nullable=True),
    Column('status', String(32), nullable=False),
    Column('prompt_count', Integer, nullable=False, default=0),
    Column('completed_count', Integer, nullable=False, default=0),
    Column('mention_rate', Float, nullable=True),
    Column('citation_rate', Float, nullable=True),
    Column('source_presence_rate', Float, nullable=True),
    Column('share_of_voice', Float, nullable=True),
    Column('recommendation_summary', Text, nullable=True),
    Column('error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('completed_at', DateTime, nullable=True),
)

analytics_provider_answers = Table(
    'analytics_provider_answers',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('scan_run_id', Integer, nullable=False, index=True),
    Column('prompt_id', Integer, nullable=False, index=True),
    Column('prompt_text', Text, nullable=True),
    Column('prompt_intent', String(80), nullable=True),
    Column('topic_name', String(180), nullable=True),
    Column('provider', String(40), nullable=False),
    Column('model', String(160), nullable=False),
    Column('status', String(32), nullable=False),
    Column('search_request_id', String(255), nullable=True),
    Column('answer_request_id', String(255), nullable=True),
    Column('answer_text', Text, nullable=True),
    Column('raw_response', Text, nullable=True),
    Column('latency_ms', Integer, nullable=True),
    Column('error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('completed_at', DateTime, nullable=True),
)

analytics_answer_sources = Table(
    'analytics_answer_sources',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('answer_id', Integer, nullable=False, index=True),
    Column('rank', Integer, nullable=False),
    Column('source_kind', String(32), nullable=False),
    Column('title', Text, nullable=True),
    Column('url', String(2048), nullable=False),
    Column('domain', String(255), nullable=True),
    Column('snippet', Text, nullable=True),
    # T9: own | competitor | editorial | social | forum | developer | other.
    # Deliberately NOT a separate citations table - a parallel table would leave
    # two sources of truth for the same evidence (SPRINT Week 2 amendment).
    Column('category', Text, nullable=True),
    Column('published_at', String(80), nullable=True),
)

analytics_scan_schedules = Table(
    'analytics_scan_schedules',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False),
    Column('enabled', Boolean, nullable=False, default=False),
    Column('frequency', String(20), nullable=False, default='weekly'),
    Column('region', String(8), nullable=True),
    Column('next_run_at', DateTime, nullable=True),
    Column('last_run_at', DateTime, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
    UniqueConstraint('workspace_id', name='uq_analytics_scan_schedules_workspace_id'),
)

analytics_content_opportunities = Table(
    'analytics_content_opportunities',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('scan_run_id', Integer, nullable=True, index=True),
    Column('source', String(80), nullable=False),
    Column('title', String(255), nullable=False),
    Column('rationale', Text, nullable=False),
    Column('evidence_refs', Text, nullable=False),
    Column('priority', String(20), nullable=False),
    Column('created_at', DateTime, nullable=False),
)








content_documents = Table(
    'content_documents',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('title', String(200), nullable=False),
    Column('brand_name', String(150), nullable=False),
    Column('keyword', String(200), nullable=False),
    Column('content_type', String(80), nullable=False),
    Column('tone', String(80), nullable=False),
    Column('content', Text, nullable=False, default=''),
    Column('seo_title', String(200), nullable=False, default=''),
    Column('meta_description', String(320), nullable=False, default=''),
    Column('outline', Text, nullable=False, default=''),
    Column('recommendations', Text, nullable=False, default=''),
    Column('status', String(40), nullable=False, default='Draft'),
    Column('version', Integer, nullable=False, default=0),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)


usage_ledger = Table(
    'usage_ledger',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, nullable=False),
    # Denormalised on purpose: the ceiling check is then one index scan.
    Column('org_id', Integer, nullable=False, index=True),
    Column('date', Date, nullable=False),
    Column('category', Text, nullable=False),
    Column('provider', Text, nullable=False),
    Column('units', Integer, nullable=False, default=1),
    # Money is numeric, never float.
    Column('cost_usd', Numeric(10, 6), nullable=False),
    Column('created_at', DateTime, nullable=False),
    CheckConstraint(
        "category IN ('engine_query', 'extraction', 'agent', 'content', 'crawl')",
        name='ck_usage_ledger_category',
    ),
    Index('ix_usage_ledger_org_created', 'org_id', 'created_at'),
)


extractions = Table(
    'extractions',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('answer_id', Integer, nullable=False, index=True),
    Column('extractor_version', Text, nullable=False),
    Column('is_current', Boolean, nullable=False, default=True),
    Column('brand_mentioned', Boolean, nullable=False),
    Column('brand_rank', Integer, nullable=True),
    Column('brand_cited', Boolean, nullable=False),
    Column('sentiment', Text, nullable=True),
    Column('sentiment_conf', Float, nullable=True),
    Column('summary', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    # "Exactly one current extraction per answer" is enforced here, by the database,
    # not by application code. A partial unique index is the only thing that holds
    # under concurrent re-extraction.
    Index('uq_extractions_current_answer', 'answer_id', unique=True,
          sqlite_where=text('is_current'), postgresql_where=text('is_current')),
)

mentions = Table(
    'mentions',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('extraction_id', Integer, nullable=False, index=True),
    Column('entity_type', Text, nullable=False),
    Column('competitor_id', Integer, nullable=True),
    Column('rank', Integer, nullable=False),
    Column('char_offset', Integer, nullable=False),
    CheckConstraint("entity_type IN ('brand', 'competitor')",
                    name='ck_mentions_entity_type'),
)


metrics_daily = Table(
    'metrics_daily',
    metadata,
    Column('workspace_id', Integer, nullable=False),
    Column('date', Date, nullable=False),
    # NULL means the blended row across engines. A real PRIMARY KEY cannot contain
    # NULL, so uniqueness is a functional index over COALESCE below - which behaves
    # identically on Postgres and SQLite.
    Column('engine_id', Integer, nullable=True),
    Column('visibility_score', Float, nullable=True),
    Column('mention_rate', Float, nullable=True),
    Column('position_score', Float, nullable=True),
    Column('citation_rate', Float, nullable=True),
    Column('sov', Float, nullable=True),
    Column('sentiment_index', Float, nullable=True),
    Column('answer_count', Integer, nullable=False, default=0),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
    Index('uq_metrics_daily_key', 'workspace_id', 'date',
          text('coalesce(engine_id, -1)'), unique=True),
)
