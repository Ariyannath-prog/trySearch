# PRD — **trySearch**: AEO / AI Search Visibility Platform
**A functional replica of Searchable (searchable.com), powered by an LLM intelligence layer**

| Field | Value |
|---|---|
| Version | **2.0 (26 Aug 2026)** — revised after the competitor technical teardown and the prototype code audit |
| Author | [TO CONFIRM: your name] |
| Status | Ready for team review. §12 blockers 1 and 4 now closed. |
| Product name | **trySearch** (working; final branding TBD) |
| Current state | Prototype live on Render. **Materially further along than v1.0 of this PRD claimed** — 4,756 LOC, 36 tables, 56 routes, Postgres, working site crawler, BM25 RAG, GSC OAuth, scheduler. Full audit: `docs/prototype-audit.md`. Repo: github.com/Ariyannath-prog/trySearch |
| Go-to-market | Phase A: concierge B2B, 10–13 hand-served clients at **$2k+/mo**, core features only. Phase B (after 3–5 months): self-serve tiers — Starter $149 / Growth $449 / Enterprise (see `docs/PRD.md (plan matrix section)`) |
| Team | Not fixed. Sized so hiring can be planned from the build order in `docs/architecture-spec.md` §7. |
| Core AI backend | Currently Hugging Face Inference Providers (`openai/gpt-oss-120b`) with an Ollama fallback, used **only to summarise stored evidence, never to invent metrics**. Model choice is swappable and not architectural. |

### What changed from v1.0

1. **Engine strategy is now hybrid, not APIs-only.** An adapter interface goes in first; official
   APIs sit behind it; a licensed scraper vendor is added at the first paying client (§4.2, §6a).
2. **The datastore question is settled: Postgres.** MongoDB is a half-finished side branch in the
   repo and is being deleted (§7).
3. **§1.5's status table was wrong** and is replaced with the audited version.
4. **§7 and §14 are superseded** by `docs/architecture-spec.md`, which carries the DDL,
   adapter contract, job rules and extraction pipeline. They are summarised here, not duplicated.
5. **§6a gains real scraper-vendor prices** and a per-answer cost for each collection path.

---

## 1. Background

### 1.1 Problem Statement
Consumers and buyers increasingly ask AI assistants (ChatGPT, Gemini, Perplexity, Claude, Copilot,
Google AI Overviews) which products, services, and brands to use — and the AI answers directly,
often with no click to the brand's website. Traditional SEO tooling measures Google rankings, not
AI answers. As a result:

- Brands cannot see whether AI engines mention them, cite them, misdescribe them, or recommend
  competitors instead.
- Marketing teams have no equivalent of rank tracking for AI answers, no way to attribute traffic
  from AI engines, and no playbook of actions to improve AI visibility.
- Agencies cannot offer AEO as a service without a platform to measure and report it per client.

`[TO CONFIRM: quantified problem data for YOUR target market.]`

### 1.2 Reference product — verified surface (26 Aug 2026)

Searchable sells **one platform SKU, not several products**. Two public prices: Professional
$125/mo and Scale $400/mo; tiers differ by usage caps (100/500 prompts, 20/80 articles,
200/1,000 page audits), not by module. Their surface is **10 modules / 46 named features**:

| # | Module | Features | Core technique |
|---|---|---|---|
| 1 | AEO Insights | 7 | Scraped surfaces + APIs → substring mention match, ordinal rank, LLM sentiment, URL/domain classification |
| 2 | Prompts | 4 | One structured LLM call from a homepage fetch |
| 3 | Searchable Agent | 5 | Tool-use agent over internal report APIs (same 7 ops exposed as MCP) |
| 4 | Content Studio | 4 | LLM generation + a **deterministic** readiness rubric |
| 5 | On Page | 4 | Static-HTML crawler, RFC 9309 robots parser, JSON-LD validation |
| 6 | Actions | 2 | One structured LLM call over a deterministic SQL digest, cached |
| 7 | AI Search Traffic | 4 | GA4 + GSC OAuth, referrer regex. Zero AI |
| 8 | LLM Analytics (beta) | 6 | Server-log ingestion via CDN; bot verification rDNS → IP → ASN → heuristics |
| 9 | AI Shopping | 6 | Same extraction pipeline, entity swapped to product/merchant |
| 10 | Agency | 4 | Pure app logic: Pitch Workspaces, white-label, report templates, API keys |

**No RAG anywhere in their product**, and no evidence of a vector store. The only genuine RAG in
this category is Profound's Knowledge Bases. Full evidence:
`docs/searchable-teardown.md`.

Company context: incorporated 14 Jul 2025, ~$18M raised, ~1,000 customers and $2M revenue in 4.5
months, one engineering role publicly listed, 996 schedule. This is 12–18 months of build produced
very fast by a small team.

### 1.3 Goals
- **G1:** Ship a working v1 covering tracking, analytics, recommendations, agent, and integrations.
- **G2:** Support **multi-client workspaces from day one** (agency use case) with white-label
  reporting. *This is now the top P0 item — the prototype is single-user and cannot do it.*
- **G3:** Use an LLM as the intelligence layer for prompt generation, sentiment, recommendations,
  agent chat, schema generation and content — never for computing a measured metric.
- **G4:** Reach 4 engines in P1 via official APIs, with scraper-vendor engines behind the same
  adapter interface, added when a client pays for them.

### 1.4 Non-Goals (v1)
Paid AI ads · full AI-commerce module · public MCP server · mobile apps (responsive web only) ·
SOC 2 certification (design for it, certify later).

### 1.5 Current build status — **audited 26 Aug 2026** (replaces the v1.0 table)

Verdicts: **Done** meets the AC · **Reuse** solid foundation, needs extension · **Rewrite** wrong
shape for where this goes · **Missing** not started.

| # | Main feature | PRD modules | Audited status |
|---|---|---|---|
| 1 | AI Visibility (tracking + score) | B + C1–C2 | **Reuse.** Scheduler, run records, evidence persistence and raw-response storage all work. But: single engine (Perplexity only), no `brand_rank`, no sentiment, no per-competitor rows, no `metrics_daily` rollup, §13 Visibility Score not implemented. |
| 2 | Insights & recommendations | C3–C4 + E | **Partial.** E1's pattern is already correct — deterministic rules over stored evidence, optionally rewritten by an open-weight model that is explicitly forbidden from inventing metrics. Missing: impact × effort, status, C3 citation view, C4 alerts, E2 agent chat, E3 schema generator. |
| 3 | Content & technical optimisation | F + G | **G near-done, F partial.** The crawler (`WebsiteAuditParser`, sitemap discovery, per-page readiness/metadata/content/crawlability/structured-data scores, SSRF-guarded fetch) is the strongest code in the repo. Missing: robots.txt AI-bot rules, `llms.txt`, page cap is 12 not 500. |
| 4 | Integrations & reporting | D + H | **D2 (GSC) done and well built** — full OAuth, Fernet-encrypted tokens, sync runs, degraded-state fields. D1 GA4, D3–D5 and **all of H** missing. |
| — | Accounts, workspaces, billing | A + I | **Rewrite.** No orgs, workspaces, memberships or roles; isolation is a hand-repeated `WHERE user_id = ?` across four separate helpers. No Stripe, no plans, no quotas, **no usage ledger**. |

**Resolved:** the v1.0 blocker "prototype code audit" is closed. See
`docs/prototype-audit.md` for the per-story verdicts and the ranked fix list.

### 1.6 Key Risks

| Risk | L | S | Mitigation |
|---|---|---|---|
| **ToS/legal:** consumer AI UIs prohibit automated scraping | High | High | Never scrape in-house. Official APIs for P0–P1; a **licensed vendor** (Cloro, Olostep, Bright Data, Oxylabs, DataForSEO) for UI-only surfaces from P3. Legal review before the first vendor contract. |
| **Cost blowout** | High | High | `usage_ledger` from P0, org-level monthly ceiling checked before dispatch, alert at 60 %, throttle at 100 %. Frequency, not prompt count, is the biggest lever. |
| **API answers ≠ consumer app answers** | High | Medium | Label every engine with its `source_type` in the UI; Gemini-with-grounding is a *proxy* for AI Overviews and the methodology page must say so. |
| **No citations on the API-only path** | High | High | Accepted for P0–P1. It blocks Sources, citation gaps and Shopping. The hybrid adapter means one vendor contract unblocks all of them without a rewrite. |
| **Engines change response formats constantly** | High | Medium | Adapter per engine, contract test per adapter daily in CI, alert on parse failure. |
| **Cross-tenant data leak** | Medium | Critical | Single `require_workspace()` guard; `workspace_id` on every table; the four hand-rolled `_for_user` helpers deleted. P0 item #2. |
| **Trademark/IP** | Medium | High | Independent branding, own copy and design, no copying of their UI. Legal review. |
| **Extraction accuracy** | Medium | Medium | Word-boundary alias matching (what the category actually uses) + a versioned eval set, re-runnable over immutable raw answers at zero engine cost. |

---

## 2. Users & Personas

1. **Agency account manager (primary v1):** 5–50 client brands; per-client workspaces, comparison,
   white-label reports.
2. **In-house marketer:** one brand; a weekly "what changed and what do I do" view.
3. **Content/SEO specialist:** consumes recommendations, generates schema and content.
4. **Admin/owner:** billing, seats, API keys, integrations.

Roles for v1: **Owner, Admin, Member, Client-Viewer** (read-only, white-label).

---

## 3. Solution Overview (User Journey)

1. Sign up → **Organization** → one or more **Brand Workspaces** (agency = one per client).
2. Onboarding wizard: domain → homepage fetch → **one structured LLM call** returns brand name,
   aliases, competitors and ~25 categorised prompts → user reviews and approves.
3. **Tracking engine** runs the approved set across enabled engines on a schedule; stores raw
   answers immutably, then extracts mentions, rank, citations and sentiment.
4. **Dashboards** show Visibility Score with its confidence interval and sample size, share of
   voice, citations by source, trends, per-engine breakdown.
5. **Integrations** (GSC done, GA4 next) add AI-referral traffic and correlate it with visibility.
6. **Recommendations** — a deterministic digest → one structured LLM call → a prioritised action
   plan with **evidence rendered above advice**, and `done_at` annotated on the trend charts.
7. **Content Studio** drafts from a recommendation; **On Page** audits the site and generates
   schema and `llms.txt`.
8. **Reports:** scheduled white-label PDF / share link per workspace.

---

## 4. Functional Requirements

### 4.1 Module A — Accounts, Workspaces, Onboarding

**A1. Multi-workspace isolation.** Given an org, when an admin creates a workspace with brand name
and domain, it appears in the switcher within 2 s and no data crosses workspaces — enforced by
`workspace_id` on every table and a single `require_workspace()` guard, not per-route predicates.
Edge cases: duplicate domain (allow, warn); deletion (soft delete, 30-day restore); plan limit
(block with upgrade CTA).
*Status: **Rewrite**. Blocks G2 and every agency feature.*

**A2. Guided setup with generated prompts.** Given brand, competitors (≤10), topics (≤10) and
geo/language, when the user clicks Generate, the model returns 25 categorised prompts
(discovery / comparison / purchase-intent / brand) in <30 s, each editable and toggleable before
the first run. Prompt-writing rules, from the reference implementation: the **majority must be
unbranded**; each is a short search-style fragment under ~12 words, not a sentence; aliases that
are substrings of the canonical name are never generated. Branded/unbranded is **computed from the
text**, not asked of the model.
Edge cases: timeout → one retry then manual-entry fallback; non-English geo → prompts in the target
language; blank/offensive input → validation.
*Status: **Missing**. Highest-leverage single item in P1 — it is what makes the product demoable.*

### 4.2 Module B — Prompt Tracking Engine (core)

**Engine strategy — REVISED: hybrid, adapter-first.**

The adapter interface goes in before the second engine, so adding one never requires a schema
change. `engines` is a table, not an enum. Full contract in
`docs/architecture-spec.md` §3.

| Phase | Engine | Source type | Data source | Notes |
|---|---|---|---|---|
| P0 | Perplexity | api | Sonar: `/search` for ranked sources + `/v1/agent` for the answer | Already built; refactor behind the adapter |
| P1 | ChatGPT | api | OpenAI Responses API + `web_search` tool | **Force browsing** or some runs silently measure the non-browsing model |
| P1 | Google | api | Gemini Flash-Lite + Search grounding | 5,000 grounded prompts/mo free **per GCP project, not per client** — decisive at 1–3 clients, irrelevant at 20 |
| P1 | Claude | api | Anthropic + web search | |
| P3 | ChatGPT (real UI) | scraper | Licensed vendor | Unlocks real citations |
| P3 | Google AI Overviews | serp_vendor | **Ordinary organic SERP endpoint**, `ai_overview` item | Not an AI-specific endpoint |
| P3 | Google AI Mode | serp_vendor | A **different** endpoint from AI Overview | Confusing the two is the classic bug in this category |

**Honest-labelling requirement:** every engine column displays its `source_type`. Gemini-with-
grounding is labelled a proxy for AI Overviews until a vendor adapter exists.

**B1. Scheduled runs.** Given N active prompts and E enabled engines, the scheduled run stores N×E
answer records with raw response, engine, model version, timestamp, geo and run_id. Idempotency key
`{workspace_id}:{date}:{type}`. Individual answers retry 3× with jittered backoff; a failed answer
marks the run `partial` and never fails it. **Fan-out jobs are never retried at the parent level** —
by the time one fails it has already paid for the calls that succeeded.
*Status: **Partial.** Scheduler, stale-lease recovery and run records exist. Missing: adapters,
E > 1, batching (today a >25-prompt workspace gets a `409`), and execution off the web process —
on-demand scans currently run in a `threading.Thread` inside gunicorn and die on deploy.*

**B2. Extraction.** From a stored raw answer, record: brand mentioned (bool), `brand_rank` (ordinal
among brands named, by character offset of first match), competitors mentioned as rows, citations
(URL, final domain, category ∈ own/competitor/editorial/social/forum/developer/other), sentiment
(pos/neu/neg + confidence), summary. Versioned by `extractor_version`, exactly one current
extraction per answer (enforced by a partial unique index), re-runnable over immutable answers at
**zero engine cost**. Target ≥90 % precision on the eval set.
*Status: **Partial.** Word-boundary alias matching works and is the right technique — do not
replace it with NER. Missing: rank, sentiment, per-competitor rows, and the eval set itself.*

**B3. On-demand "test now".** Rate-limited to 20/day/workspace; result in <60 s per engine or
marked pending. **Excluded from all metrics** (PRD §13 rule 4) — today they are not, which biases
every score toward the moments someone was optimising.
*Status: **Partial.***

### 4.3 Module C — Analytics & Dashboards

**C1. Visibility Score & share of voice.** Per §13, computed per engine per day into
`metrics_daily`, blended across engines by simple average so a low-volume engine can't be drowned
out. Store `NULL`, not `0`, when a denominator is empty. Dashboards read the rollup, never raw
answers; page load <3 s at 90 days × 300 prompts × 4 engines.
**Two rules that are the product's position, not decoration:** never render a bare number — every
headline ships with its 95 % Wilson interval and sample size visible without a hover, and a delta
smaller than the interval renders as *no measurable change*; and don't display VS below 20 answers
in the period.
*Status: **Rewrite the metric.** Rates are computed correctly; `PositionScore` is impossible
without `brand_rank`, and there is no rollup table.*

**C2. Prompt-level drill-down.** Latest answer per engine, history diff ("answer changed on X"),
sources cited. *Partial — evidence endpoints exist, no diff.*

**C3. Citation intelligence.** Most-cited domains for the workspace, classified own /
competitor / third-party, with competitor citation gaps. *Missing as a view; the data is already
stored per answer.*

**C4. Alerts.** Email when: brand drops from a top-10 prompt, a competitor overtakes SOV, sentiment
turns negative, or a tracked citation disappears. Thresholds configurable, interval-tested
server-side. Slack in P2. *Missing.*

### 4.4 Module D — Integrations

- **D1 GA4:** OAuth; sessions/conversions segmented by AI referral source; correlation with
  Visibility Score. **Mirror the GSC module's shape exactly.** *Missing — highest-value integration
  remaining.*
- **D2 Google Search Console:** *Done.* Full OAuth, refresh rotation, Fernet-encrypted tokens,
  property selection, sync runs, degraded-state `status`/`last_error`.
- **D3 Bing WMT · D4 WordPress/Webflow · D5 HubSpot:** P2–P3.
- **Common AC:** on token expiry or scope change the workspace shows a degraded-state banner within
  24 h and dependent widgets show "reconnect" — never stale data without a stale label.

### 4.5 Module E — Agent & Recommendations

**E1. Prioritised action plan.** Deterministic SQL digest → **one structured LLM call** → ≤10
actions, each with rationale tied to specific data, impact (H/M/L), effort (H/M/L), a deep link,
and `evidence_refs` pointing at real `answer_id`s re-attached server-side after the model returns.
Status open/done/dismissed; `done_at` annotated on trend charts. Served from cache — a page load
never triggers a model call. **Evidence renders above advice** on every card.
*Status: **Partial and architecturally correct.** The prototype's rule-based-then-summarised
pattern is exactly what Searchable's Actions module does. Extend, don't replace.*

**E2. Agent chat** grounded in the workspace's data via tool use over the internal report APIs;
refuses out-of-scope questions; cites the data it used. **Dependency: build the internal report API
surface first — the agent is a thin layer over it.** *Missing.*

**E3. Schema markup generator.** Given a URL, propose JSON-LD, validate against schema.org, offer
copy or CMS push. The crawler already parses `schema_blocks` per page. *Missing.*

### 4.6 Module F — Content Studio
Brief → outline → draft, optimised for citation-worthiness, in a brand voice learned from crawled
pages (the BM25 RAG index already holds them). Human-in-the-loop editing; export to CMS draft or
download. Add a deterministic AI-readiness score and a similarity guard.
*Status: **Partial** — documents, generation, outline and recommendations exist.*

### 4.7 Module G — Technical Optimisation
**G1. AI-readiness audit:** crawl up to 500 pages (currently capped at 12); checks for schema
presence and validity, **robots.txt AI-bot access parsed per RFC 9309 against the ~21 AI
user-agent tokens**, `llms.txt` presence, heading/answer structure, canonical/meta, sitemap
declared-vs-reachable diff. Output: scored checklist feeding Module E.
**G2. `llms.txt` generator** and robots guidance with copy-paste output.
*Status: **Near-done.** Missing G2, robots AI-bot rules, and the page cap. **Not** rendering JS is a
deliberate methodology choice — document it, don't "fix" it.*

### 4.8 Module H — Reporting
Scheduled white-label reports per workspace: logo/colours, PDF + share link, selectable sections.
Uses the last complete run, labelled, if generation happens mid-run.
*Status: **Missing.** Per §6b the report **is** the sales artifact — this is a revenue blocker.*

### 4.9 Module I — Admin, Billing, Security
Roles per §2; Google OAuth SSO; Stripe subscriptions; plan gating per
`docs/PRD.md (plan matrix section)`; audit log; per-workspace export; account deletion with 30-day
purge (GDPR). **`usage_ledger` is P0, not P2** — the §6a guardrails are unenforceable without it.
*Status: **Missing.***

---

## 5. Phasing

The detailed, estimated build order lives in `docs/architecture-spec.md` §7. Summary:

| Phase | Contents | Business mode | Exit criteria |
|---|---|---|---|
| **P0 — Foundation** (~2 wks) | Delete Mongo branch · orgs/workspaces/memberships + isolation guard · all jobs to the CLI worker · `usage_ledger` + ceilings · split extractions/mentions/citations, add `brand_rank` · `metrics_daily` + §13 Visibility Score | Internal | Two isolated workspaces under one org, real VS, a cost number per run |
| **P1 — Concierge** (~5 wks) | Adapter interface + OpenAI/Gemini/Claude adapters + contract tests · onboarding wizard with generated prompts · citation intelligence · sentiment · confidence intervals · recommendations v2 · GA4 · **reports** · alerts | **10–13 clients at $2k+/mo**, high-touch | 10 paying clients; weekly report loop running unaided for each |
| **P2 — Self-serve** (~4 wks) | Stripe + plans + gating · self-signup + roles · `llms.txt` and robots checks · brand voice from RAG · CMS export · agent chat | Starter $149 / Growth $449 / Enterprise | Signup → first run with no human help; ≥70 % activation |
| **P3 — On demand** | Scraper-vendor adapters (real citations, AI Overviews, AI Mode) · crawler log analytics · AI Shopping · MCP server · HubSpot | Expansion | Triggered by a client paying for it |

`[TO CONFIRM: calendar dates. Minimum team for P0–P1: 1 full-stack + 1 backend/data engineer +
founder on product and sales.]`

---

## 6a. Cost model — revised 26 Aug 2026

### Per-answer cost by collection path

| Path | Unit price | Per answer |
|---|---|---|
| Perplexity Sonar | $1/M in + $1/M out + $5/1k req search fee (low context) | **≈$0.006** |
| OpenAI mini + web_search | $10/1k web-search calls + tokens | **≈$0.012** |
| Gemini Flash + grounding | 5,000 grounded prompts/mo free per GCP project, then $14/1k queries | **$0 → $0.015–0.03** |
| Anthropic + web search | tokens + a per-use search surcharge | **≈$0.012** |
| Extraction (small model, batch) | ~1.5k in / 300 out | **≈$0.002** |
| **Blended, 4 API engines + extraction** | | **≈$0.04–0.06** |

### Scraper-vendor prices (P3), per prompt per month at ~300 evaluations

| Vendor | Cost | Known buyers |
|---|---|---|
| Oxylabs | ~$0.15 | — |
| Bright Data | ~$0.45 | — |
| Cloro | ~$0.65 | Ahrefs, Scrunch, Evertune |
| DataForSEO | ~$1.20 | — |
| Olostep | ~$2.25 | Profound, AthenaHQ, AirOps |

Cloro is the only one returning **parsed structured citations**; with the others you write and
maintain a parser per provider. Latency, not price, is the real constraint — a scraped ChatGPT run
takes about a minute, so everything is async submit-and-poll.

There is also a **pre-collected corpus** option: DataForSEO's LLM Mentions API sells ~333M existing
prompts/responses at ~$0.001/row + $0.10/request, with no collection infrastructure at all. Worth
using for a free "AI visibility snapshot" lead-magnet page even while the real product runs live.

**DIY scraping is not an option.** Cloudflare JA4 fingerprinting blocks datacenter IPs within a few
requests, and vendors estimate 8–15 engineer-hours a month chasing weekly UI changes.

### Monthly cost per client (API-only path, P1)

| Tier | Quota (proposed) | Answers/mo | Engine + extraction | + LLM layer | Total | Price | Margin |
|---|---|---|---|---|---|---|---|
| Starter $149 | 50 prompts × 3 engines × weekly | ~650 | ~$8 | ~$4 | **~$12** | $149 | ~92 % |
| Growth $449 | 150 × 3 × every 3 days | ~4,500 | ~$60 | ~$12 | **~$72** | $449 | ~84 % |
| Concierge $2k+ | 300 × 4 × daily | ~36,000 | ~$450 | ~$100 | **~$550** | $2,000+ | ~72 % |

Adding one scraped engine to the concierge tier at Bright Data pricing costs roughly
300 × $0.45 ≈ **$135/mo** — comfortably absorbed at $2k, and the reason the hybrid decision works.

**Guardrails (hard rules):** daily runs are concierge-only — frequency, not prompt count, is the
biggest lever · extraction always batched, and re-runs over cached raw answers cost **$0** in
engine fees · `usage_ledger` records actual dollars per call, alert at 60 %, throttle at 100 % ·
recheck this sheet whenever any provider reprices.

## 6b. Go-To-Market
Founder-led and organic pre-funding: X and LinkedIn founder content, B2B content marketing, heavy
short-form clipping, direct outreach for the first 10–13 concierge clients. Product implication:
**share-link reports must look excellent unbranded** — the report is the sales artifact. A public
"AI Visibility snapshot" lead-magnet page (cheap to build on the pre-collected corpus above) is a
strong P2 growth candidate.

## 6. Non-Functional Requirements
- **Cost:** per-org monthly ceiling checked before dispatch; alert at 60 %, throttle at 100 %.
- **Performance:** dashboards <3 s p95 (guaranteed by reading `metrics_daily`); agent first token
  <5 s p95.
- **Reliability:** run completion ≥99 %, partials always surfaced; one engine failing never blocks
  another (adapters never raise past their boundary).
- **Data:** raw answers retained 13 months and immutable; extraction re-runnable.
- **Security:** OAuth tokens encrypted at rest (already done, Fernet); row-level workspace
  isolation via one guard; SSRF protection on every outbound fetch (already done); no client data
  used for training.
- **Compliance:** GDPR; DPA template for agency clients.

---

## 7. Architecture

Full specification: **`docs/architecture-spec.md`**. Locked decisions:

- **Postgres only.** No ClickHouse until `metrics_daily` is measurably slow. No MongoDB — the
  repo's Mongo branch is 320 lines of auth and contacts and is being deleted. No vector database.
- **Postgres-backed job table + CLI worker.** No Redis, no Celery.
- **BM25 sparse retrieval** over crawled page chunks. Already built; correct.
- **Flask monolith**, split when a second engineer joins, not before.
- **Adapter per engine**, contract-tested daily in CI.
- Three invariants: answers immutable · dashboards read rollups · every provider call writes a
  usage row.

---

## 8. Success Metrics (v1)
Activation ≥70 % of new workspaces complete a first tracked run within 24 h · ≥60 % weekly active
workspaces viewing dashboards · ≥30 % of recommendations marked done within 30 days · extraction
precision ≥90 % / recall ≥85 % on the eval set · <2 % run-failure rate · 3 paying agency accounts
within 60 days of P1.

## 9. Testing Requirements
- **Extraction eval set:** start at 50 hand-labelled real answers from client scans, grow to 300.
  Versioned in the repo. No extractor change may regress precision or recall.
- **Adapter contract tests** daily in CI with alerting on parse failure.
- Per-module happy path plus every edge case above as acceptance tests.
- Load test: 300 prompts × 4 engines × 13 workspaces nightly.

## 10. Release & Rollout
Feature-flag every module; P0 internal → P1 invite-only → open signup. Adapters and the extraction
pipeline are versioned; raw answers are immutable so analytics can be recomputed after any
rollback.

## 11. Out-of-Scope Clarifications
We do not and cannot guarantee placement inside AI engines. The product measures and advises —
important for marketing copy and for contracts.

---

## 12. Open questions — status 26 Aug 2026

**Resolved since v1.0**
- ~~Prototype code audit~~ → done, `docs/prototype-audit.md`.
- ~~Engine list~~ → hybrid, adapter-first: Perplexity now, +OpenAI/Gemini/Claude in P1, scraper
  vendors in P3 on client demand.
- ~~Database~~ → Postgres. Mongo branch deleted.
- ~~Architecture and data model~~ → `docs/architecture-spec.md`.
- ~~Business model, pricing, CRM, name, GTM, VS formula, compliance~~ → as v1.0.

**Still open — blockers marked ⚠️**
1. ⚠️ **Lock tier quotas.** Prices cannot go on a website until they're confirmed. Also: 30 minutes
   cataloguing exactly which free API credits exist and when they expire, so runway math is real.
2. ⚠️ **Team and dates.** No calendar until at least two engineers are committed.
3. **Which scraper vendor**, and the trigger for signing. Proposal: sign when the third concierge
   client asks for citations or AI Overviews. Legal review before signing.
4. **First target geo/segment** for the 10–13 concierge clients — drives prompt languages and
   pricing currency.
5. **Tier placements** in `docs/PRD.md (plan matrix section)` — a proposal, two objects and one line
   each to move.
6. **Trial length and quota** (currently 14 days, Starter features, 25 prompts); does the trial get
   reports?
7. Final branding and domain; check trademark distance from "Searchable".

---

## 13. Visibility Score — formal specification

```
VS = 100 × ( 0.5 × MentionRate + 0.3 × PositionScore + 0.2 × CitationRate )
```

Over all scheduled-run answers in the period for the workspace's active prompts:

- **MentionRate** = answers where the brand or any alias is mentioned ÷ total answers. 0–1.
- **PositionScore** = mean reciprocal rank over *mentioned* answers only, where rank is the brand's
  index among all brands named in that answer, ordered by character offset of first match
  (1st → 1.0, 2nd → 0.5, 3rd → 0.33…). Mentioned but the only brand named → rank 1. Never
  mentioned in the period → 0.
- **CitationRate** = answers containing ≥1 citation to a workspace-owned domain ÷ total answers.

**Worked example.** 100 answers. Mentioned in 40 → MentionRate 0.40. Among those: 1st in 20, 2nd in
12, 3rd in 8 → PositionScore = (20 + 6 + 2.67)/40 = **0.717**. Own domain cited in 15 →
CitationRate 0.15. `VS = 100 × (0.200 + 0.215 + 0.030)` = **44.5**.

**Companion metrics, displayed alongside and deliberately not inside VS:** Share of Voice = brand
mentions ÷ (brand + tracked-competitor mentions); Sentiment index = (positive − negative) ÷
mentioned answers, shown −100…+100, excluded from VS in v1 because extraction confidence is lowest
there.

**Rules.** Weights are config constants, shown on the in-app methodology page; changing them
requires recomputing history, which is possible because answers are immutable · VS is computed per
engine per day then blended by simple average · don't display below 20 answers in the period ·
on-demand runs are excluded · **every displayed VS carries its 95 % Wilson interval and sample
size**, and a delta smaller than the interval renders as *no measurable change*.

---

## 14. Data model

Superseded by **`docs/architecture-spec.md` §2**, which carries the DDL. In brief:
`organizations` → `memberships` → `workspaces` (+ `brand_aliases`, `competitors`) · `engines`,
`prompts`, `runs`, `answers` (raw JSON, immutable) · `extractions` (versioned, one current per
answer) → `mentions`, `citations` · `metrics_daily` (the only read path for dashboards) ·
`usage_ledger` (org-denormalised, one row per provider call) · the prototype's existing audit, RAG,
GSC and content tables re-keyed to `workspace_id` · Stripe `plans` / `subscriptions` · `alerts`,
`recommendations`, `audit_log`.

Tables being dropped: the legacy mock-era analytics tables, the two parallel half-implementations
of tracking (`prompt_collections*`, `visibility_*`), `master_workspaces`, and everything Mongo.
Net: 36 tables → ~26, with the duplication removed.
