# trySearch — replica architecture spec & build order

Version 1.0 · 26 Aug 2026. Supersedes PRD §7 and §14. Written against the audited prototype
(`docs/prototype-audit.md`) and the competitor teardown
(`docs/searchable-teardown.md`).

**Decisions locked in this document**

| Decision | Choice | Why |
|---|---|---|
| Datastore | **Postgres only**, no ClickHouse, no Mongo, no vector DB | Workload is append-heavy runs + daily rollups + tenant isolation + billing counts. Relational. Already shipped. |
| Queue | **Postgres-backed job table + CLI worker.** No Redis, no Celery | Already half-built (`analytics_audit_jobs`). One less service to run at 13 clients. |
| Retrieval | **BM25 sparse over crawled chunks.** No embeddings | Already built and correct. Vector DBs appear in zero of the three open-source builds of this product. |
| Engine collection | **Hybrid.** Adapter interface now, official APIs behind it, scraper vendor added at the first paying client | Founder decision. Keeps COGS at ~$0.03/prompt/run until revenue justifies ~$0.15–0.45. |
| Runtime | Flask monolith, split later | One engineer. Splitting now buys nothing. |

---

## 1. System shape

```
                    ┌─────────────────────────────────────────────┐
   browser ────────▶│  Flask app (server_pg.py)                   │
                    │  routes · auth · reads from metrics_daily   │
                    └───────────────┬─────────────────────────────┘
                                    │ INSERT job (queued)
                                    ▼
                    ┌─────────────────────────────────────────────┐
   cron (1/min) ───▶│  worker CLI: run_scheduled_analytics()      │
                    │  lease → dispatch → recover stale           │
                    └───────┬───────────────┬───────────┬─────────┘
                            │               │           │
              ┌─────────────▼───┐  ┌────────▼──────┐  ┌─▼──────────────┐
              │ prompt_scan     │  │ site_audit    │  │ rollup / digest│
              │ engine adapters │  │ crawler + RAG │  │ + opportunities│
              └────────┬────────┘  └───────┬───────┘  └───────┬────────┘
                       │                   │                  │
                       ▼                   ▼                  ▼
              answers (raw JSON)    audit_pages/chunks   metrics_daily
                       │                                       ▲
                       └──── extractions ──── mentions ────────┘
                                            citations
```

Three invariants hold the whole thing together:

1. **`answers` are immutable.** The provider's full JSON is written before anything is derived
   from it. Every metric can then be recomputed, audited, and backfilled after a formula change.
   The prototype already does this (`analytics_provider_answers.raw_response`) — protect it.
2. **Dashboards read `metrics_daily`, never `answers`.** Rollups are the read path.
3. **Every provider call writes a `usage_ledger` row, success or failure.** Spend ceilings count
   usage rows, not run rows — a retry storm writes no runs but burns money.

---

## 2. Data model

Renames from the prototype are marked. All tables below carry `workspace_id` unless they are
org-level. Row-level isolation is enforced by a single guard, not by per-route predicates.

### 2.1 Tenancy — new, blocks everything else

```sql
CREATE TABLE organizations (
  id            bigserial PRIMARY KEY,
  name          text NOT NULL,
  plan_id       bigint REFERENCES plans(id),
  stripe_customer_id text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
  org_id  bigint NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role    text   NOT NULL CHECK (role IN ('owner','admin','member','client_viewer')),
  PRIMARY KEY (org_id, user_id)
);

-- analytics_projects becomes this
CREATE TABLE workspaces (
  id          bigserial PRIMARY KEY,
  org_id      bigint NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  brand_name  text NOT NULL,
  domains     text[] NOT NULL DEFAULT '{}',
  geo         text NOT NULL DEFAULT 'US',
  language    text NOT NULL DEFAULT 'en',
  kind        text NOT NULL DEFAULT 'project' CHECK (kind IN ('project','pitch')),
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','soft_deleted')),
  deleted_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON workspaces (org_id) WHERE status = 'active';

CREATE TABLE brand_aliases (
  workspace_id bigint NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  alias        text   NOT NULL,
  PRIMARY KEY (workspace_id, alias)
);

CREATE TABLE competitors (
  id           bigserial PRIMARY KEY,
  workspace_id bigint NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name         text NOT NULL,
  domains      text[] NOT NULL DEFAULT '{}',
  aliases      text[] NOT NULL DEFAULT '{}'
);
```

`kind = 'pitch'` is Searchable's Pitch Workspace, copied deliberately: an agency spins up a
prospect's brand with its own small prompt budget, runs it, shows it in the pitch, and converts it
to a real workspace in one click. Cheapest high-value agency feature in their product.

**Alias rule, from the reference implementation:** never store an alias that is a substring of the
canonical name. Mention detection is word-boundary matching, so `"Aspire Asia"` for `"Aspire"` is
redundant and inflates nothing. Generate aliases with that constraint in the prompt.

### 2.2 Tracking core

```sql
CREATE TABLE engines (
  id             bigserial PRIMARY KEY,
  key            text UNIQUE NOT NULL,     -- 'chatgpt','gemini','perplexity','claude',...
  display_name   text NOT NULL,
  source_type    text NOT NULL CHECK (source_type IN ('api','scraper','serp_vendor')),
  adapter_version text NOT NULL,
  enabled        boolean NOT NULL DEFAULT true
);

CREATE TABLE prompts (
  id           bigserial PRIMARY KEY,
  workspace_id bigint NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  text         text NOT NULL,
  category     text NOT NULL CHECK (category IN ('discovery','comparison','purchase','brand')),
  branded      boolean NOT NULL,           -- computed from text, never asked of the model
  language     text NOT NULL DEFAULT 'en',
  topic_id     bigint REFERENCES topics(id),
  active       boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON prompts (workspace_id) WHERE active;

CREATE TABLE runs (
  id           bigserial PRIMARY KEY,
  workspace_id bigint NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  type         text NOT NULL CHECK (type IN ('scheduled','on_demand')),
  status       text NOT NULL CHECK (status IN ('queued','running','partial','complete','failed')),
  region       text NOT NULL DEFAULT 'US',
  started_at   timestamptz,
  finished_at  timestamptz,
  idempotency_key text NOT NULL,
  UNIQUE (workspace_id, idempotency_key)
);

CREATE TABLE answers (
  id            bigserial PRIMARY KEY,
  run_id        bigint NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  workspace_id  bigint NOT NULL,
  prompt_id     bigint NOT NULL REFERENCES prompts(id),
  engine_id     bigint NOT NULL REFERENCES engines(id),
  status        text NOT NULL CHECK (status IN ('ok','failed','empty')),
  answer_text   text,
  raw_response  jsonb NOT NULL,            -- IMMUTABLE. never update this row.
  model_version text,
  latency_ms    int,
  cost_usd      numeric(10,6),
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON answers (workspace_id, created_at);
CREATE INDEX ON answers (run_id);
```

`answers` replaces `analytics_provider_answers`. The prototype's flat extraction columns
(`brand_mentioned`, `brand_cited`, `source_present`, `best_source_rank`) move out into their own
versioned table so extraction can be re-run without touching the raw record:

```sql
CREATE TABLE extractions (
  id                 bigserial PRIMARY KEY,
  answer_id          bigint NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
  extractor_version  text NOT NULL,
  is_current         boolean NOT NULL DEFAULT true,
  brand_mentioned    boolean NOT NULL,
  brand_rank         int,                  -- ordinal among brands named. NULL = not mentioned
  brand_cited        boolean NOT NULL,
  sentiment          text CHECK (sentiment IN ('pos','neu','neg')),
  sentiment_conf     real,
  summary            text,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON extractions (answer_id) WHERE is_current;

CREATE TABLE mentions (
  id            bigserial PRIMARY KEY,
  extraction_id bigint NOT NULL REFERENCES extractions(id) ON DELETE CASCADE,
  entity_type   text NOT NULL CHECK (entity_type IN ('brand','competitor')),
  competitor_id bigint REFERENCES competitors(id),
  rank          int NOT NULL,
  char_offset   int NOT NULL
);

CREATE TABLE citations (
  id            bigserial PRIMARY KEY,
  answer_id     bigint NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
  position      int NOT NULL,
  url           text NOT NULL,
  final_domain  text NOT NULL,
  title         text,
  category      text NOT NULL CHECK (category IN ('own','competitor','editorial','social','forum','developer','other'))
);
CREATE INDEX ON citations (answer_id);
CREATE INDEX ON citations (final_domain);
```

The partial unique index `ON extractions (answer_id) WHERE is_current` enforces the "exactly one
current extraction per answer" invariant in the database rather than in application code.

### 2.3 Rollups — the read path

```sql
CREATE TABLE metrics_daily (
  workspace_id     bigint NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  date             date   NOT NULL,
  engine_id        bigint,                 -- NULL = blended across enabled engines
  visibility_score numeric(5,2),
  mention_rate     numeric(5,4),
  position_score   numeric(5,4),
  citation_rate    numeric(5,4),
  sov              numeric(5,4),
  sentiment_index  numeric(5,2),
  answer_count     int NOT NULL,
  PRIMARY KEY (workspace_id, date, engine_id)
);
```

Recompute from `extractions` after every completed run. Never from `answers`. Scheduled runs only
— on-demand `runs.type = 'on_demand'` are excluded, because they bias the sample toward the moments
someone was optimising.

**Visibility Score, per PRD §13, now computable because `brand_rank` exists:**

```
VS = 100 × (0.5 × MentionRate + 0.3 × PositionScore + 0.2 × CitationRate)

PositionScore = mean(1 / brand_rank) over answers where brand_mentioned
                (0 if never mentioned in the period)
```

Two rules the prototype already gets right and must keep: store `NULL`, not `0`, when a denominator
is empty; and blend across engines by simple average of per-engine scores, not by answer count, so
a low-volume engine can't be drowned out.

Two rules to add, which are the product's stated position: **never render a bare number** — every
headline metric ships with its 95 % Wilson interval and sample size, and a delta smaller than the
interval renders as *no measurable change*; and **do not display VS below 20 answers** in the
period — show "collecting data" instead.

### 2.4 Cost control — build before the first client

```sql
CREATE TABLE usage_ledger (
  id           bigserial PRIMARY KEY,
  workspace_id bigint NOT NULL,
  org_id       bigint NOT NULL,            -- denormalised on purpose
  date         date NOT NULL,
  category     text NOT NULL CHECK (category IN ('engine_query','extraction','agent','content','crawl')),
  provider     text NOT NULL,
  units        int NOT NULL DEFAULT 1,
  cost_usd     numeric(10,6) NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON usage_ledger (org_id, created_at);
```

`org_id` is denormalised so the ceiling check is one cheap index scan. Write a row for **every**
provider call including failures. Alert at 60 % of the plan's monthly cost budget, throttle at
100 %. Without this the PRD §6a margin guardrails are decorative.

### 2.5 Keep as-is from the prototype

`analytics_site_audits`, `analytics_audit_pages`, `analytics_audit_findings`,
`analytics_sitemaps`, `analytics_rag_documents`, `analytics_rag_chunks`, `analytics_rag_insights`,
`gsc_connections`, `gsc_properties`, `gsc_sync_runs`, `gsc_query_rows`, `content_documents`.
Add `workspace_id` to each and drop `user_id`.

### 2.6 Drop

`analytics_runs`, `analytics_engine_metrics`, `analytics_prompts` (all legacy mock-era, which is why
`is_legacy_mock_analytics_run` exists), `prompt_collections` / `prompt_queries` /
`prompt_query_results` and `visibility_watchlists` / `visibility_scans` /
`visibility_engine_results` / `visibility_mentions` (three parallel half-implementations of the
same tracking concept), `master_workspaces` (superseded by real workspaces), plus everything Mongo.

That is 36 tables down to roughly 26, with the duplication removed.

---

## 3. The engine adapter interface

The single most important refactor. Today Perplexity's response shape *is* the system; it must
become one implementation of a contract.

```python
# engines/base.py
@dataclass(frozen=True)
class EngineResult:
    answer_text: str | None
    citations: list[Citation]        # position, url, title
    raw_response: dict               # stored verbatim, never parsed downstream
    model_version: str | None
    cost_usd: Decimal
    latency_ms: int
    status: Literal['ok', 'failed', 'empty']
    error: str | None = None

class EngineAdapter(Protocol):
    key: str                         # 'perplexity'
    source_type: Literal['api', 'scraper', 'serp_vendor']
    adapter_version: str
    supports_citations: bool
    supports_regions: bool

    def run(self, prompt: str, *, region: str, timeout_s: int) -> EngineResult: ...
    def estimate_cost(self, prompt: str) -> Decimal: ...
```

Rules that make it worth having:

- **Adapters never touch the database.** They take a string and return a value. The worker persists.
- **Adapters never raise past their own boundary.** A failure is `status='failed'` with an error
  string, so one engine going down never fails a run for the others.
- **`raw_response` is stored untouched.** Parsing lives in the adapter; downstream code sees only
  `EngineResult`.
- **A contract test per adapter runs daily in CI** against a fixed prompt, asserting the shape and
  alerting on parse failure. Engines change their response format constantly; you want to hear it
  from CI, not from a client.
- **Adding an engine must never require a schema change.** `engines` is a table, not an enum.

Adapters in order:

| Order | Adapter | Type | Notes |
|---|---|---|---|
| 1 | `perplexity` | api | Refactor of what exists. Sonar; `/search` for ranked sources + `/v1/agent` for the answer. |
| 2 | `openai` | api | Responses API with the `web_search` tool. Force browsing or some runs silently measure the non-browsing model. |
| 3 | `gemini` | api | Flash-Lite + Search grounding. 5,000 grounded prompts/month free **per Google Cloud project, not per client** — a lifesaver at 1–3 clients, irrelevant at 20. |
| 4 | `anthropic` | api | Claude with web search. |
| — | *hold the line here until a client pays* | | |
| 5 | `chatgpt_scraped` | scraper | Vendor. Unlocks real citations. |
| 6 | `google_ai_overview` | serp_vendor | **Comes from the ordinary organic SERP endpoint** as an `ai_overview` item, not from an AI-specific one. |
| 7 | `google_ai_mode` | serp_vendor | A *different* endpoint from AI Overview. Confusing the two is the classic bug in this category. |

Honest-labelling requirement, enforced in the UI: every engine column shows its `source_type`.
Gemini-with-grounding is a proxy for Google AI Overviews, not the thing itself, and the methodology
page must say so.

---

## 4. Job orchestration

Replace `threading.Thread` entirely. Everything runs in the CLI worker; the web process only
enqueues.

```
runs (parent, one per workspace per scheduled date)
  └── answer_jobs (one per prompt × engine)
```

Rules, each one earned the hard way in the reference implementation:

1. **Idempotency key** `{workspace_id}:{date}:{type}` on `runs`, unique-constrained. A double cron
   tick cannot double-charge.
2. **Retry limit zero at the job level for fan-outs.** By the time a fan-out job fails it has
   already paid for the calls that succeeded, and a naive retry re-submits all of them. Retry the
   individual `answer_job`, never the parent.
3. **Per-answer retry: 3 attempts, exponential backoff with jitter.** A failed answer marks the run
   `partial`; it never fails the run.
4. **Lease with expiry.** The prototype's 45-minute stale-lease recovery is right — keep it, and
   make the expiry longer than the longest possible fan-out, because you cannot cancel an in-flight
   paid request and an early expiry double-pays.
5. **Cost ceiling checked before dispatch**, counting `usage_ledger` rows for the org this month.
6. **Rate limit** on-demand runs to 20/day/workspace.
7. **Batch, don't refuse.** A 300-prompt workspace becomes 12 batches of 25, not a `409`.
8. **Every failure reported with engine + adapter_version tags** so per-engine failure rates stay
   visible.

Concurrency: keep it single-worker and sequential until it hurts. At 13 clients × 300 prompts × 3
engines daily that is ~11,700 calls/day; at ~1 s each with a 0.2 s delay it is roughly 4 hours of
wall clock spread over the day. One worker is enough. Add a second only when a client needs a
narrower run window.

---

## 5. Extraction pipeline

Versioned by `extractor_version`, re-runnable over immutable `answers`.

1. **Mentions** — word-boundary regex over `[brand_name] + brand_aliases + domains`, exactly what
   the prototype's `text_mentions_alias` already does. Do not replace this with NER or embeddings;
   the entire category runs on substring matching. Record the **character offset** of each first
   match.
2. **Rank** — sort brand and competitor first-match offsets ascending; `brand_rank` is the brand's
   index. Mentioned but the only brand named → rank 1. Not mentioned → `NULL`.
3. **Citations** — resolve redirects, strip `www.`, lowercase, then classify by set membership:
   own domains → competitor domains → curated editorial / social / forum / developer lists →
   `other`. Start the editorial list at a few hundred domains for your clients' verticals; the
   reference implementation carries ~25,000 and keeps it server-only so it never reaches the
   browser bundle. **This list is a real moat — start it now and grow it every week.**
4. **Sentiment** — one cheap structured LLM call per answer, batched. Keep it out of the Visibility
   Score in v1 (lowest extraction confidence); display it as a companion index.
5. **Eval set** — 300 hand-labelled answers, versioned in the repo. Every extractor change must not
   regress precision or recall. This is PRD §9 and it does not exist yet; start it at 50 labelled
   answers from real client scans and grow it.

Cost note: extraction is the one place to use a small model via a batch API. At ~1.5k in / 300 out
per answer it is ~$0.002/answer, and because raw answers are cached, re-running extraction after a
pipeline improvement costs **zero engine fees**.

---

## 6. Recommendations layer

Keep the prototype's pattern — it is already what Searchable does — and extend it:

1. Build a **deterministic digest** in SQL: overall and per-engine visibility, per-prompt standing
   vs the leading competitor over 7d and 30d, the citation landscape, open actions from prior weeks.
2. **One structured LLM call** over that digest. No web search, no agent loop.
3. Persist to `recommendations` with `impact` × `effort`, `evidence_refs` pointing at real
   `answer_id`s, and `status ∈ open|done|dismissed`. Re-attach real IDs server-side after the model
   returns; never let the model invent them.
4. Serve from cache. A page load must never trigger a model call.
5. **Evidence above advice** on every card — the observation that produced the recommendation, with
   a link to the raw answers, rendered above the recommendation itself. This is the project's
   stated differentiator and it is a UI rule, not a model rule.

`recommendations.done_at` annotates the trend charts, which is the one thing nobody in the category
does: showing whether the fix worked on the same screen as the metric.

---

## 7. Build order

Sequenced so each phase ends somewhere sellable. Estimates assume one full-stack engineer plus the
founder; halve the calendar with a second engineer from P1.

### P0 — Make the foundation safe (≈2 weeks)

Nothing here is visible to a client. All of it becomes impossible to retrofit later.

| # | Work | Est |
|---|---|---|
| 1 | Delete the Mongo branch; one auth path | 0.5 d |
| 2 | `organizations` / `memberships` / `workspaces`; `workspace_id` on every table; one `require_workspace()` guard replacing the four `_for_user` helpers; data migration | 4 d |
| 3 | Move all job execution to the CLI worker; drop `threading.Thread`; batch instead of `409` | 2 d |
| 4 | `usage_ledger` + cost estimation per call + org monthly ceiling | 2 d |
| 5 | Split `extractions` / `mentions` / `citations` out of the flat answer row; add `brand_rank`; `extractor_version` | 3 d |
| 6 | `metrics_daily` rollup job; implement PRD §13 Visibility Score; exclude on-demand runs | 2 d |

**Exit:** two workspaces under one org, isolated, with a real Visibility Score and a cost number
per run.

### P1 — Concierge-sellable (≈5 weeks)

| # | Work | Est |
|---|---|---|
| 7 | Engine adapter interface; Perplexity refactored behind it | 3 d |
| 8 | OpenAI adapter (Responses API + web_search, forced browsing) | 2 d |
| 9 | Gemini adapter (Flash-Lite + Search grounding) | 2 d |
| 10 | Contract tests per adapter, daily in CI, with alerting | 1 d |
| 11 | Onboarding wizard: domain → homepage fetch → one structured LLM call → brand, aliases, competitors, ~25 categorised prompts → review & approve | 4 d |
| 12 | Citation classification + most-cited-domains view (C3); start the editorial domain list | 3 d |
| 13 | Sentiment extraction, batched, displayed as a companion index | 2 d |
| 14 | Confidence intervals + sample sizes on every headline; three distinct empty states | 3 d |
| 15 | Recommendations v2: impact × effort, status, evidence-above-advice, chart annotations | 4 d |
| 16 | GA4 integration — mirror the GSC module exactly | 3 d |
| 17 | Reports (H): white-label PDF + share link, scheduled | 5 d |
| 18 | Alerts (C4): email, threshold config, interval-tested server-side | 3 d |

**Exit:** the ten-to-thirteen $2k/mo concierge clients can be served without manual work. Reports
are the sales artifact, so 17 is not optional.

### P2 — Self-serve (≈4 weeks)

Stripe + plans + quota gating (the `GATE` / `GATEWHY` objects already designed in
`docs/PRD.md (plan matrix section)`), self-signup, roles, `llms.txt` + robots AI-bot checks in the
crawler, Content Studio brand-voice from the RAG index, CMS export, agent chat over the internal
report APIs.

### P3 — Only when a client pays for it

Scraper vendor adapters (ChatGPT scraped, AI Overviews, AI Mode) — the moment one arrives, real
citations and six more modules unlock. Then crawler log analytics (rDNS → IP range → ASN →
heuristics verification), AI Shopping, MCP server, HubSpot.

---

## 8. What not to build

- **No vector database.** BM25 over crawled chunks is already there and is what the category uses.
- **No ClickHouse** until `metrics_daily` queries are actually slow. Benchmark before assuming.
- **No Redis or Celery.** The Postgres job table is enough at this scale.
- **No microservices.** One engineer, one process.
- **No NER or ML mention extraction.** Word-boundary regex over aliases. The market leader's
  approach is not better than yours here.
- **No JS rendering in the crawler.** Not rendering JS mirrors how AI crawlers actually read pages.
  Document it as a methodology choice rather than fixing it.
- **No DIY scraping of consumer UIs.** Cloudflare JA4 fingerprinting kills datacenter IPs within a
  few requests, and vendors estimate 8–15 engineer-hours a month chasing UI changes. Buy it or skip
  it.
