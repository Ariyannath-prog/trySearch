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

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

STRING_ARRAY = ARRAY(Text)

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
    Column('is_platform_admin', Boolean, nullable=False, default=False),
    Column('is_active', Boolean, nullable=False, default=True),
    Column('last_login_at', DateTime, nullable=True),
    # NULL means unverified. Onboarding is hard-blocked until this is set; login
    # still works, so the user can be directed to verification rather than stranded.
    Column('email_verified_at', DateTime, nullable=True),
    Column('terms_accepted_at', DateTime, nullable=True),
    Column('terms_version', Text, nullable=True),
    # email already carries a plain UNIQUE, which still lets 'A@x.com' and
    # 'a@x.com' both exist. Normalising in Python alone does not hold under
    # concurrency, so uniqueness is enforced on lower(email) by the database -
    # the same reasoning behind uq_extractions_current_answer. A stored
    # email_normalized column would merely be a second source of truth.
    Index('uq_users_email_lower', text('lower(email)'), unique=True),
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
    # plans exists as of the Phase A commercial-model migration, so the spec's
    # `plan_id REFERENCES plans(id)` is finally a real foreign key. ON DELETE
    # RESTRICT is what makes "a plan referenced by an organization cannot be
    # deleted" a database guarantee rather than an application convention -
    # archive/deactivate is the only retirement path.
    Column('plan_id', Integer, ForeignKey('plans.id', ondelete='RESTRICT'),
           nullable=True),
    Column('stripe_customer_id', Text, nullable=True),
    # Commercial account type, deliberately NOT the subscription plan: a brand and
    # an agency can sit on the same plan, and one plan may be sold to either.
    Column('account_type', Text, nullable=False, server_default='brand'),
    # What the entitlement layer reads to decide whether plan_id's limits apply
    # right now. Stripe later writes these from webhook state; nothing fabricates
    # billing in the meantime.
    Column('plan_status', Text, nullable=False, server_default='none'),
    Column('trial_ends_at', DateTime, nullable=True),
    Column('plan_started_at', DateTime, nullable=True),
    # The plan's monthly spend ceiling in USD. Belongs on plans, which does not
    # exist until Stripe in week 3+, so it sits here and falls back to
    # DEFAULT_MONTHLY_COST_CEILING_USD when null.
    Column('monthly_cost_ceiling_usd', Numeric(10, 2), nullable=True),
    Column('created_at', DateTime, nullable=False),
    CheckConstraint("account_type IN ('brand', 'agency')",
                    name='ck_organizations_account_type'),
    CheckConstraint(
        "plan_status IN ('none', 'trialing', 'active', 'past_due', 'canceled')",
        name='ck_organizations_plan_status',
    ),
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
    # geo above is the primary country (ISO 3166-1 alpha-2) and language is BCP-47.
    # Both already existed; what changes in Phase A is that onboarding persists the
    # user's selection instead of hardcoding 'US'/'en'. Geographic *reach* is a
    # different axis and cannot be folded into a single text column, hence the two
    # fields below. analytics_prompt_scan_runs.region is a per-run engine locale and
    # is deliberately left alone.
    Column('target_scope', Text, nullable=False, server_default='country'),
    Column('target_locations', STRING_ARRAY, nullable=False, default=list),
    CheckConstraint("kind IN ('project', 'pitch')", name='ck_workspace_kind'),
    CheckConstraint("target_scope IN ('city', 'region', 'country', 'worldwide')",
                    name='ck_workspaces_target_scope'),
    CheckConstraint("status IN ('active', 'soft_deleted')", name='ck_workspace_status'),
    Index('ix_workspaces_org_active', 'org_id', postgresql_where=text("status = 'active'")),
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
    # Where this *active* competitor came from. Enumerated, not free text. This is
    # provenance on the kept record and explicitly not a suggestion-history system:
    # rejected suggestions are never persisted at all, because onboarding's preview
    # step writes nothing and approve inserts only what was submitted.
    Column('source', Text, nullable=False, server_default='manual'),
    Column('created_at', DateTime, nullable=True),
    UniqueConstraint('workspace_id', 'name', name='uq_competitor_name'),
    CheckConstraint("source IN ('ai_suggested', 'manual', 'imported')",
                    name='ck_competitors_source'),
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
    # engine_id is the real identity; provider stays as a denormalised label so
    # existing queries and stored evidence keep reading straightforwardly.
    Column('engine_id', Integer, ForeignKey('engines.id'), nullable=True, index=True),
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
                    postgresql_where=text('is_current')),
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
    # PostgreSQL's functional index preserves uniqueness for NULL engine IDs.
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


providers = Table(
    'providers',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('key', String(80), nullable=False, unique=True),
    Column('display_name', String(160), nullable=False),
    Column('category', String(80), nullable=False, server_default='llm'),
    Column('auth_type', String(40), nullable=False),
    Column('base_url', String(2048), nullable=True),
    Column('docs_url', String(2048), nullable=True),
    Column('config', JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column('enabled', Boolean, nullable=False, default=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)


engines = Table(
    'engines',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('key', Text, nullable=False, unique=True),
    Column('display_name', Text, nullable=False),
    # api | scraper | serp_vendor. The UI labels this so a grounded-search proxy
    # is never presented as the engine it approximates.
    Column('source_type', Text, nullable=False),
    Column('adapter_version', Text, nullable=False),
    Column('enabled', Boolean, nullable=False, default=True),
    Column('provider_id', Integer, ForeignKey('providers.id', ondelete='SET NULL'), nullable=True),
    CheckConstraint("source_type IN ('api', 'scraper', 'serp_vendor')",
                    name='ck_engines_source_type'),
)


# A workspace with no rows here uses every platform-enabled engine, exactly
# as before this table existed. Onboarding's "choose engines" step writes
# one row per currently platform-enabled engine, so a workspace that has
# made a choice always holds a complete, explicit snapshot rather than a
# partial one an absent row could be misread as.
workspace_engines = Table(
    'workspace_engines',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('workspace_id', Integer, ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
    Column('engine_id', Integer, ForeignKey('engines.id', ondelete='CASCADE'), nullable=False),
    Column('enabled', Boolean, nullable=False, default=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
    UniqueConstraint('workspace_id', 'engine_id', name='uq_workspace_engines_workspace_engine'),
)


workspace_branding = Table(
    'workspace_branding',
    metadata,
    Column('workspace_id', Integer, primary_key=True),
    Column('display_name', Text, nullable=True),
    Column('logo_url', Text, nullable=True),
    Column('accent_colour', Text, nullable=True),
    # Below Enterprise the trysearch mark stays in the header and footer. The plan
    # decides this, not the client, so it is a server-side flag.
    Column('hide_trysearch_mark', Boolean, nullable=False, default=False),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)

report_shares = Table(
    'report_shares',
    metadata,
    Column('id', Integer, primary_key=True),
    # secrets.token_urlsafe(32). Never sequential, never derived from the id.
    Column('token', Text, nullable=False, unique=True),
    Column('workspace_id', Integer, nullable=False, index=True),
    Column('sections', Text, nullable=True),
    Column('expires_at', DateTime, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('revoked_at', DateTime, nullable=True),
)


# --- Platform administration -------------------------------------------------
# Platform admins are separate from organization membership roles.
# Secrets live in provider_credentials and are encrypted before storage.

admin_audit_logs = Table(
    'admin_audit_logs',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('actor_user_id', Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True),
    Column('action', Text, nullable=False),
    Column('target_type', String(80), nullable=True),
    Column('target_id', String(120), nullable=True),
    Column('details', JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column('ip_address', String(64), nullable=True),
    Column('user_agent', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Index('ix_admin_audit_logs_created_at', 'created_at'),
    Index('ix_admin_audit_logs_target', 'target_type', 'target_id'),
)


provider_credentials = Table(
    'provider_credentials',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('provider', String(80), nullable=False),
    Column('provider_id', Integer, ForeignKey('providers.id', ondelete='SET NULL'), nullable=True, index=True),
    Column('engine_id', Integer, ForeignKey('engines.id', ondelete='SET NULL'), nullable=True, index=True),
    Column('label', String(160), nullable=False),
    Column('encrypted_secret', Text, nullable=False),
    Column('secret_hint', String(32), nullable=True),
    Column('enabled', Boolean, nullable=False, default=True),
    Column('last_tested_at', DateTime, nullable=True),
    Column('last_error', Text, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
    UniqueConstraint('provider', 'label', name='uq_provider_credentials_provider_label'),
)


feature_flags = Table(
    'feature_flags',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('key', String(160), nullable=False, unique=True),
    Column('enabled', Boolean, nullable=False, default=False),
    Column('config', JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)


system_settings = Table(
    'system_settings',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('key', String(160), nullable=False, unique=True),
    Column('value', Text, nullable=False, server_default=''),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
)


# --- Commercial model: plans and entitlements --------------------------------
# Plans are created and edited by platform admins at runtime, never seeded with
# commercial pricing from a migration and never hardcoded in frontend code. A plan
# is a draft (active=false) until an admin activates it, so nothing reaches a
# customer by accident.

plans = Table(
    'plans',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('slug', Text, nullable=False, unique=True),
    Column('name', Text, nullable=False),
    Column('description', Text, nullable=False, server_default=''),
    # Draft by default. active = orderable and visible to customers.
    Column('active', Boolean, nullable=False, server_default=text('false')),
    # Retired but still honoured for organizations already on it. There is no
    # delete path for a plan anywhere in the application.
    Column('archived_at', DateTime, nullable=True),
    Column('display_order', Integer, nullable=False, server_default='0'),
    Column('currency', String(3), nullable=False, server_default='USD'),
    Column('billing_interval', Text, nullable=False, server_default='monthly'),
    # Money is Numeric, never float - the same rule costs.py follows.
    Column('price_monthly', Numeric(10, 2), nullable=True),
    # Schema-ready for annual billing. Deliberately not exposed in the customer
    # payload yet; monthly is the only interval sold in this phase.
    Column('price_annual', Numeric(10, 2), nullable=True),
    # 0 means no trial. One field rather than a boolean plus a duration that can
    # contradict it.
    Column('trial_days', Integer, nullable=False, server_default='0'),
    # Which account types may buy this plan. text[] reuses the STRING_ARRAY
    # convention domains/aliases already use.
    Column('account_types', STRING_ARRAY, nullable=False, default=list),
    # Infrastructure cost control, which models.py has always said belongs on the
    # plan. Kept a column rather than an entitlement so money has one code path:
    # costs.ceiling_for_org() resolves org override -> plan -> env default.
    Column('monthly_cost_ceiling_usd', Numeric(10, 2), nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
    CheckConstraint("billing_interval IN ('monthly', 'annual')",
                    name='ck_plans_billing_interval'),
    CheckConstraint('trial_days >= 0', name='ck_plans_trial_days'),
    CheckConstraint('display_order >= 0', name='ck_plans_display_order'),
    CheckConstraint("account_types <@ ARRAY['brand', 'agency']::text[]",
                    name='ck_plans_account_types'),
    Index('ix_plans_active_order', 'display_order', 'id',
          postgresql_where=text('active')),
)


# One row per (plan, entitlement key). value_type + JSONB rather than four mostly
# NULL typed columns: it matches the JSONB already used by feature_flags.config and
# admin_audit_logs.details, and the CHECK below makes type correctness a database
# guarantee instead of an application convention.
#
# Which keys are meaningful is declared in app/entitlements.py. An admin can only
# write a declared key, because a typo'd key would be a limit nobody enforces -
# a silent commercial bug rather than a loud one.
plan_entitlements = Table(
    'plan_entitlements',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('plan_id', Integer, ForeignKey('plans.id', ondelete='CASCADE'),
           nullable=False, index=True),
    Column('key', Text, nullable=False),
    Column('value_type', Text, nullable=False),
    Column('value', JSONB, nullable=False),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
    UniqueConstraint('plan_id', 'key', name='uq_plan_entitlement'),
    CheckConstraint(
        "(value_type = 'int' AND jsonb_typeof(value) = 'number') OR "
        "(value_type = 'bool' AND jsonb_typeof(value) = 'boolean') OR "
        "(value_type = 'string' AND jsonb_typeof(value) = 'string') OR "
        "(value_type = 'list' AND jsonb_typeof(value) = 'array')",
        name='ck_plan_entitlement_value',
    ),
)


# --- Workspace-level access for client viewers -------------------------------
# memberships stays the single org-level source of truth for roles. This table only
# ever *narrows* access, and only for the client_viewer role:
#
#   owner / admin / member -> org membership alone grants the workspace (unchanged)
#   client_viewer          -> org membership AND a row here for that workspace
#
# Without this, require_workspace()'s join on memberships.org_id hands every
# client_viewer every workspace in the agency's org - i.e. one agency client could
# see another agency client's data. A workspace_members table was rejected because
# it would store roles in a second place.
workspace_access = Table(
    'workspace_access',
    metadata,
    Column('workspace_id', Integer, ForeignKey('workspaces.id', ondelete='CASCADE'),
           primary_key=True),
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'),
           primary_key=True),
    Column('granted_by', Integer, ForeignKey('users.id', ondelete='SET NULL'),
           nullable=True),
    Column('created_at', DateTime, nullable=False),
    Index('ix_workspace_access_user', 'user_id'),
)


# --- Email verification and password reset -----------------------------------
# Only the SHA-256 hash is stored. The raw secrets.token_urlsafe(32) exists solely
# in the email that was sent, so a database read cannot be replayed as a credential.
# `purpose` is why password reset reuses this table instead of a near-identical
# second one. report_shares was considered for reuse and rejected: it is
# workspace-scoped and stores its token in plaintext, which is fine for a share
# link and not for an authentication credential.
email_verification_tokens = Table(
    'email_verification_tokens',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'),
           nullable=False),
    Column('purpose', Text, nullable=False),
    Column('token_hash', Text, nullable=False, unique=True),
    Column('expires_at', DateTime, nullable=False),
    Column('used_at', DateTime, nullable=True),
    Column('created_at', DateTime, nullable=False),
    Column('requested_ip', Text, nullable=True),
    CheckConstraint("purpose IN ('email_verify', 'password_reset')",
                    name='ck_evt_purpose'),
    Index('ix_evt_user_purpose', 'user_id', 'purpose'),
)


# --- Rate limiting -----------------------------------------------------------
# Fixed-window counters in PostgreSQL, because TrySearch runs under gunicorn: a
# process-local counter would reset on every restart and would be wrong the moment
# a second worker exists. One upsert per guarded request
# (INSERT ... ON CONFLICT DO UPDATE ... RETURNING hits), so rows stay bounded at one
# per key per window rather than one per event. Pruned by the existing CLI worker.
rate_limit_counters = Table(
    'rate_limit_counters',
    metadata,
    Column('bucket_key', Text, primary_key=True),
    Column('window_start', DateTime, primary_key=True),
    Column('hits', Integer, nullable=False, server_default='0'),
    Column('updated_at', DateTime, nullable=False),
    Index('ix_rate_limit_window', 'window_start'),
)
