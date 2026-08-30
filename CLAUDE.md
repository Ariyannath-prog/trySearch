# trySearch — working agreement

You are working on **trySearch**, an AEO / AI-search-visibility platform. Brands want to know
whether ChatGPT, Gemini, Perplexity and Claude mention them when someone asks a buying question.
We run their tracked prompts against those engines on a schedule, extract mentions and citations,
and turn the result into metrics and recommendations.

Read `SPRINT.md` for the current task list. Do one task per branch. Do not start the next task
until the current one's acceptance criteria pass.

---

## The three invariants

Everything else in this file is negotiable. These are not.

1. **`answers` are immutable.** The provider's full JSON response is written to `answers.raw_response`
   before anything is derived from it, and that row is never updated. This is what lets us recompute
   every metric after a formula change, and re-run extraction at zero engine cost.
2. **Dashboards read `metrics_daily`, never `answers`.** If a read path touches raw answers, it is
   wrong. Rollups are the read path.
3. **Every provider call writes a `usage_ledger` row — success or failure.** Spend ceilings count
   usage rows, not run rows: a retry storm writes no runs but burns money.

If a task appears to require breaking one of these, stop and say so instead of breaking it.

---

## Never do these

- **No vector database, no embeddings, no pgvector.** Retrieval is BM25 over crawled page chunks
  and it already works. The entire competitive category runs without embeddings.
- **No NER or ML for mention detection.** It is a word-boundary regex over
  `brand_name + aliases + domains`. This is what the market leader does. Do not "improve" it.
- **No Redis, no Celery, no RabbitMQ.** The job queue is a Postgres table plus a CLI worker.
- **No MongoDB.** `server_mongo.py`, `mongo_config.py` and `migrate_postgres_to_mongo.py` are being
  deleted. Never add pymongo back.
- **No microservices, no Next.js rewrite, no framework migration.** Flask stays.
- **No JS rendering in the crawler.** Fetching static HTML is a deliberate methodology choice — it
  mirrors how real AI crawlers read a page. Document it; do not fix it.
- **No scraping of consumer AI UIs.** Official APIs only. Scraped surfaces come from a licensed
  vendor later, behind the same adapter interface.
- **Never invent a metric with an LLM.** Models generate prompts, sentiment labels, summaries and
  recommendation prose. Every number is computed in SQL or Python from stored evidence.
- **Never call a paid API from a test.** Tests use recorded fixtures in `tests/fixtures/`.
- **Never run a migration against production.** Staging only. Ask before anything touching prod.

---

## Repo map

```
server_pg.py          # thin entrypoint after T1: app factory + blueprint registration only
app/
  models.py           # SQLAlchemy Core table definitions, one place
  auth.py             # session auth, register/login/me
  tenancy.py          # require_workspace(), require_org(), role checks
  routes/             # one blueprint per surface: analytics, prompts, evidence, audit, content, reports
  engines/            # base.py (EngineAdapter protocol) + one module per engine
  extraction/         # mentions, rank, citations, sentiment. versioned.
  crawler/            # WebsiteAuditParser, fetch guards, sitemap discovery, scoring
  rag/                # BM25 chunking + ranking
  integrations/       # gsc.py, ga4.py — OAuth, token vault, sync
  metrics.py          # rollups, Visibility Score, Wilson intervals
  worker.py           # job dispatch, leases, recovery
  costs.py            # usage_ledger writes, per-provider estimation, ceilings
migrations/           # Alembic
tests/
  fixtures/           # recorded provider JSON. never hand-written.
```

Legacy files being deleted, do not extend them: `server.py`, `server_mongo.py`, `mongo_config.py`,
`migrate_postgres_to_mongo.py`, and the mock-era tables listed in `SPRINT.md` T5.

---

## Conventions

- **Python 3.13, Flask 3.1, SQLAlchemy 2.0 Core** (not the ORM — match the existing style),
  `psycopg` 3. No new runtime dependencies without saying why in the PR description.
- **Every table carries `workspace_id`** unless it is org-level. Every query that reads
  workspace-scoped data goes through `require_workspace()`. Never hand-write
  `WHERE user_id = ?` again — that pattern is the bug we are removing.
- **All schema changes are Alembic migrations.** No `ensure_database_column`, no `create_all` in
  application code.
- **Money is `numeric`, never float.** Timestamps are `timestamptz`, always UTC.
- **A rate is `NULL` when its denominator is zero, never `0`.** A brand that was never measured is
  not a brand that scored zero, and the UI must be able to tell the difference.
- **Adapters never touch the database and never raise past their own boundary.** They take a string
  and return an `EngineResult`. A provider failure is `status='failed'` with an error string, so one
  engine going down never fails a run for the others.
- Keep functions small enough to read. If a module passes ~600 lines, split it.

## Product rules that are UI, not backend

These are the product's differentiator. Do not quietly drop them to ship faster.

- **Never render a bare number.** Every headline metric displays its 95% Wilson interval and its
  sample size without needing a hover. A delta smaller than the interval renders as
  *"no measurable change"*, not as an arrow.
- **Three distinct empty states**, never one: *not yet run* · *ran and the brand was absent* ·
  *too few runs to say*. These are different facts and users act on them differently.
- **Evidence above advice.** Every recommendation card shows the observation that produced it, with
  a link to the raw answers, rendered above the recommendation text.
- **Label every engine with its source type.** Gemini-with-search-grounding is a *proxy* for Google
  AI Overviews, not the thing itself, and the UI must say so.

---

## Testing

The acceptance criterion for every task is a passing test, because that is what gets reviewed.

- `pytest`. Fast, no network, no paid calls.
- **Provider fixtures are recorded, not written.** `scripts/record_fixture.py` makes one real call
  with a real key and saves the raw JSON to `tests/fixtures/<engine>/<case>.json`. Adapter tests
  replay those. When a provider changes its format, you re-record; you never edit the JSON by hand.
- **Contract test per adapter**, run daily in CI, asserting the shape of a live response and
  alerting on parse failure. Engines change formats constantly and we want to hear it from CI.
- **Isolation tests are mandatory** for anything touching tenancy: create two workspaces in two
  orgs, assert every read path returns nothing for the wrong one.
- Metrics get golden tests: a fixed set of extraction rows in, an exact Visibility Score out,
  including the worked example from the PRD (VS = 44.5).

## Definition of done

A task is done when: the acceptance criteria in `SPRINT.md` pass · `pytest` is green · a migration
exists if the schema changed · no new dependency appeared without justification · none of the three
invariants was broken · the PR description says in three sentences what changed and what to check.

## When you are unsure

Stop and ask. Specifically: before deleting data, before touching production, before adding a
dependency, before changing a metric formula, and before doing anything this file forbids. A
question costs a minute; a silent wrong assumption costs a week.

---

## Reference docs in this repo

- `SPRINT.md` — the current task list. Work one task per branch, in order.
- `docs/architecture-spec.md` — **the DDL, the engine adapter contract, job orchestration rules,
  the extraction pipeline, and the build order.** Read this before any schema or engine work.
- `docs/prototype-audit.md` — what already exists in this codebase and why each sprint task is
  here. Read this before deleting or rewriting anything.
- `docs/searchable-teardown.md` — how the competitor we're replicating actually works, feature by
  feature. Read this when you need to know what a module is supposed to do.
- `docs/PRD.md` — product requirements, acceptance criteria per module, the Visibility Score
  specification (§13), and the cost model (§6a).
- `docs/handover-plan.md` — **not for you.** Account and credential notes for the owner only.
  Ignore it.
