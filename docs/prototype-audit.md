# trySearch prototype — code audit vs the PRD acceptance criteria

Audited 26 Aug 2026 against `github.com/Ariyannath-prog/trySearch` @ `f7cb45d`, using the
Searchable teardown in `docs/searchable-teardown.md` as the parity benchmark.
This closes PRD §12 blocker #1.

## Headline: the PRD understates what exists

PRD §1.5 says "basic dashboard, core UI, and the first of 4 planned main features." That is wrong.
`server_pg.py` is **4,756 lines, 36 tables, 56 routes** and already contains working versions of
four separate PRD modules plus a scheduler and a test suite. What is missing is not features —
it is **multi-tenancy, multi-engine, and cost control**.

## What the stack actually is

| Layer | What's there | Verdict |
|---|---|---|
| Web | Flask 3.1 + gunicorn, single-file monolith `server_pg.py` | Keep for now. Rewrite only when a second engineer joins. |
| ORM / DB | SQLAlchemy 2.0 Core (not ORM) + `psycopg` 3. **Postgres is already the production database.** SQLite for local | **Keep.** This is the right choice and matches the reference implementation of this product. |
| Deploy | Render, `render.yaml`, health check on `/api/health`, Dockerfile, Procfile | Keep. |
| Auth | Flask session cookie, `werkzeug` password hash, `users` table | Reuse; needs orgs/roles on top. |
| Jobs | `analytics_audit_jobs` table + `threading.Thread` for on-demand + a CLI cron worker with **stale-lease recovery at 45 min** | Reuse the table and the worker; **replace the thread** (see below). |
| Engine | **Perplexity only** — `POST /search` (ranked sources) + `POST /v1/agent` (answer). Cap 25 prompts/scan | Rewrite into an adapter interface. |
| Reasoning | Hugging Face Inference Providers router (`openai/gpt-oss-120b`) or local Ollama, used **only** to summarise stored evidence | Keep the pattern, swap the model. |
| Crawler | Own `WebsiteAuditParser`, SSRF-guarded fetch, sitemap discovery, per-page scoring | **Keep — this is the best code in the repo.** |
| RAG | BM25 sparse retrieval over crawled page chunks. No embeddings, no vector DB | **Keep. Correct call.** |
| GSC | Full OAuth, Fernet-encrypted refresh tokens, property selection, sync runs, query rows | Keep. |
| Mongo | `server_mongo.py` (320 lines — auth + contacts only), `mongo_config.py`, `migrate_postgres_to_mongo.py` | **Dead branch. Delete it.** See below. |

### The MongoDB question, resolved

The Mongo belief came from this repo, not from Searchable. And in this repo Mongo is a
**half-finished side branch**: `server_mongo.py` has 12 routes covering login, register, profile and
the contact form. It contains none of analytics, prompts, evidence, audits, RAG, GSC or content.
The migration script copies exactly two collections — `users` and `contacts`.

Meanwhile `server_pg.py` — the file `render.yaml` and `Procfile` actually boot — is the whole
product, on Postgres, with 36 relational tables. Searchable's own database is unidentified and
there is no public evidence they use Mongo either.

**Decision: Postgres, already made and already shipped.** Delete `server_mongo.py`,
`mongo_config.py`, `migrate_postgres_to_mongo.py` and the `pymongo`/`dnspython` requirements. They
are two divergent auth implementations of the same product, which is how you get a security bug.

## Module-by-module verdict

Statuses: **Done** = meets the PRD acceptance criteria · **Reuse** = solid foundation, needs
extension · **Rewrite** = the shape is wrong for where this is going · **Missing** = not started.

### Module A — Accounts, workspaces, onboarding → **Rewrite**

- A1 (multi-workspace isolation): **Rewrite.** There is no org, no workspace, no membership, no
  role. Isolation is `WHERE user_id = ?` repeated by hand in `project_for_user`,
  `watchlist_for_user`, `prompt_collection_for_user`, `content_document_for_user`. One forgotten
  predicate is a cross-tenant leak, and there are four separate ownership helpers to forget it in.
  Agency use — the PRD's primary persona — is impossible today.
- A2 (Claude-generated starter prompts, ≤30s, categorised): **Missing.** Prompts are typed in by
  hand via `POST /tracked-prompts`. There is no generation call and no onboarding wizard. This is
  the single highest-leverage missing piece: it is one structured LLM call and it is what makes the
  product demoable in under ten minutes.

### Module B — Prompt tracking engine → **Reuse, then extend**

The bones are right and better than the PRD gave credit for.

- B1 (N prompts × E engines on a schedule): **Partial.** `analytics_scan_schedules` with
  `next_run_at`, `run_scheduled_analytics_command` with batch limits and stale-lease recovery, and
  `analytics_prompt_scan_runs` as the run record — all correct. But **E = 1**. Everything is
  hardcoded to Perplexity: `call_perplexity_search`, `call_perplexity_answer`,
  `normalise_perplexity_sources`, `perplexity_answer_citations`, and a `provider` column that is
  always `'Perplexity'`. There is no adapter interface.
  - Also: on-demand runs execute in a `threading.Thread` inside the gunicorn worker. With
    `--workers 2 --threads 4` on Render Starter, a 25-prompt scan at ~1s/prompt blocks a request
    thread for half a minute and dies silently on deploy. Move all execution to the CLI worker.
  - Also: the 25-prompt-per-scan cap is enforced by **refusing the scan** (`409`), not by batching.
    That breaks the moment a client has 100 prompts.
- B2 (structured extraction, ≥90% precision): **Partial, and honest about it.**
  `text_mentions_alias` is a word-boundary regex over `[brand_name, domain, domain_label]` — which
  is exactly what the category does, so don't over-engineer it. What's missing against the AC:
  **no `brand_rank`/position**, **no sentiment**, and **no per-competitor mention rows**. Competitor
  counting happens inline in the scan loop and is thrown away; only the aggregate
  `share_of_voice` survives. The evidence table for §9's eval set does not exist.
  - `analytics_provider_answers.raw_response` **does** store the full provider payload. That is the
    most important schema decision in this category and you already made it. Protect it.
- B3 (on-demand "test now", rate-limited): **Partial.** `POST /prompt-scans` exists; there is no
  per-workspace daily rate limit and on-demand runs are **not excluded from the metrics**, which
  biases every score toward moments you were optimising (PRD §13 rule 4).

### Module C — Analytics & dashboards → **Reuse**

- C1 (Visibility Score, SOV, trends, per-engine): **Rewrite the metric.** The scan computes
  `mention_rate`, `citation_rate`, `source_presence_rate` and `share_of_voice` correctly and stores
  `None` rather than `0` when the denominator is empty — that is the right instinct and matches the
  reference implementation. But **the PRD §13 Visibility Score is not implemented**: there is no
  `PositionScore`, so `VS = 0.5·MentionRate + 0.3·PositionScore + 0.2·CitationRate` cannot be
  computed. `analytics_runs.visibility_score` is a legacy column from the mock era —
  `is_legacy_mock_analytics_run` exists to hide those rows, which tells its own story.
  - There is **no `metrics_daily` rollup**. Every dashboard read recomputes from evidence rows.
    Fine at 25 prompts; not fine at 300 × 3 engines × 90 days.
- C2 (prompt drill-down, history diff): **Partial.** `/evidence` and `/evidence/<answer_id>` give
  the answer, sources and per-prompt state. No answer-changed-on-date diff.
- C3 (citation intelligence — most-cited domains): **Missing as a view.**
  `analytics_answer_sources` already stores `rank, source_kind, title, url, domain, snippet` per
  answer, so the data is there; the aggregation and the own-vs-competitor-vs-third-party
  classification are not.
- C4 (alerts): **Missing entirely.** No alerts table, no channels, no thresholds.

### Module D — Integrations → **Reuse (D2 is done)**

- D2 (Google Search Console): **Done, and well built.** Full OAuth start/callback, refresh-token
  rotation, `cryptography.Fernet` encryption at rest keyed by `OAUTH_TOKEN_ENCRYPTION_KEY`,
  property selection, `gsc_sync_runs` + `gsc_query_rows`, degraded-state `status`/`last_error`
  fields. This meets the PRD common AC. Reuse this module's shape for every other OAuth provider.
- D1 (GA4): **Missing.** This is the one the PRD calls P1 and it's the bigger commercial unlock —
  AI referral traffic is what a marketing lead actually reports upward.
- D3 (Bing WMT), D4 (WordPress/Webflow), D5 (HubSpot): **Missing.** Correctly deferred.

### Module E — Agent & recommendations → **Reuse the design, extend the surface**

- E1 (prioritised action plan): **Partial, and architecturally correct.**
  `rule_based_opportunities` derives up to 5 opportunities deterministically from stored evidence,
  then `open_model_evidence_opportunities` optionally rewrites them with an open-weight model —
  with the docstring *"Use an open-weight model only to summarize stored evidence, never to invent
  metrics."* That is precisely the pattern Searchable's Actions module uses (one structured call
  over a deterministic digest, no agent loop) and it is the right one. What's missing: impact ×
  effort scoring, `open|done|dismissed` status, done-date annotations on charts, and any reference
  to prior open actions.
- E2 (agent chat grounded in workspace data): **Missing.** Note the dependency — build the internal
  report APIs first; the agent is a thin tool-use layer over them.
- E3 (schema markup generator): **Missing.** The crawler already parses `schema_blocks` per page,
  so the input exists.

### Module F — Content Studio → **Reuse**

`content_documents` + `POST /documents/<id>/generate` + `make_content_draft` produce title, meta
description, outline, body and recommendations. Missing: brand-voice learning from crawled pages
(the RAG index could supply this directly), the AI-readiness score, CMS export, and any similarity
guard.

### Module G — Technical optimisation → **Done, near enough**

The strongest module in the repo, and closer to Searchable's On Page than the PRD assumes.
`crawl_website` + `WebsiteAuditParser` + `score_website_snapshot` + `discover_sitemap_pages`
deliver per-page `readiness / metadata / content / crawlability / structured_data` scores plus
`analytics_audit_findings` with `code, area, severity, evidence, recommendation`, and
`analytics_sitemaps` for declared-vs-reachable diffing. `validate_public_web_url` +
`SafeRedirectHandler` + `verified_http_opener` are a real SSRF guard — better than most production
crawlers.

Gaps against G1/G2 and against Searchable: **no robots.txt AI-bot rule parsing** (RFC 9309 against
the ~21 AI user-agent tokens), **no `llms.txt` check or generator**, page cap is 12 (`AUDIT_MAX_PAGES`)
vs the PRD's 500, and no JS rendering — though *not* rendering JS is defensible and deliberate in
this category, so document it rather than fix it.

### Module H — Reporting → **Missing**

No white-label reports, no PDF, no share links, no scheduling. Per PRD §6b this is the sales
artifact itself, so it is a revenue blocker, not a nice-to-have.

### Module I — Admin, billing, security → **Missing**

No Stripe, no plans, no quotas, no roles, no audit log, no data export, no account deletion.
And critically: **no `usage_ledger`.** Nothing counts what a scan cost. PRD §6a's margin guardrails
(alert at 60%, throttle at 100%) cannot be enforced because the numbers don't exist.

## Ranked list of what to fix

**Correctness and safety — do these first, they are cheap**

1. **Delete the Mongo branch.** Two auth implementations of one product.
2. **Move on-demand scans off `threading.Thread`** onto the CLI worker. Today a deploy mid-scan
   loses the run and a long scan blocks a gunicorn thread.
3. **Exclude on-demand runs from metrics** (PRD §13 rule 4).
4. **Add `usage_ledger`** and write one row per provider call, success or failure, with an
   estimated cost — before you have a client to bill, not after. Count *usage rows*, not run rows:
   a retry storm writes no runs but burns money.
5. **Batch the 25-prompt cap** instead of returning `409`.

**Architecture — do these before the second engineer arrives**

6. **`workspace_id` on every table**, with org / membership / role above it, and a single
   `require_workspace(...)` guard replacing the four hand-rolled `_for_user` helpers.
7. **Engine adapter interface.** One module per engine returning a normalised
   `{answer_text, citations[], raw_response, model, cost_usd}`. Perplexity becomes the first
   implementation, not the shape of the system.
8. **`metrics_daily` rollup**, and make every dashboard read it.
9. **Extraction v2**: persist `brand_rank`, per-competitor mention rows, and sentiment, versioned
   by `extractor_version` so it can be re-run over the immutable `raw_response` you already keep.

**Product — what makes it sellable**

10. Onboarding wizard + generated starter prompt set (A2). One LLM call; biggest demo unlock.
11. GA4 (D1) — mirror the GSC module exactly.
12. Reports (H) — the sales artifact.
13. Alerts (C4), citation intelligence view (C3), robots/llms.txt checks (G1/G2).

## Corrected status table for PRD §1.5

| # | Main feature | PRD modules | Real status |
|---|---|---|---|
| 1 | AI Visibility (tracking + score) | B + C1–C2 | **Reuse** — engine works, single-provider, no position/sentiment, no rollup, VS formula not implemented |
| 2 | Insights & recommendations | C3–C4 + E | **Partial** — E1 pattern correct and built; C3, C4, E2, E3 missing |
| 3 | Content & technical optimisation | F + G | **G near-done, F partial** — crawler is the strongest code in the repo |
| 4 | Integrations & reporting | D + H | **D2 done, D1/D3–D5 and all of H missing** |
| — | Accounts, workspaces, billing | A + I | **Rewrite** — single-user only; no orgs, roles, plans, quotas or usage ledger |
