# Searchable — technical teardown (what it runs on, feature by feature)

Researched 26 Aug 2026. Companion to `claude/geo-competitor-teardown.md` (that one is UI/UX; this
one is implementation). Full formatted version:
https://claude.ai/code/artifact/2485a473-40ff-4423-830e-ae404ac3aedc

Findings are labelled **verified** (stated by them or observable) vs **inferred** (from how the
category provably works). Nothing here is a guess presented as fact.

## The short answer

- **1 product, not several.** Single platform SKU. Two public prices: Professional $125/mo,
  Scale $400/mo. Tiers differ by *usage caps* (100/500 prompts, 20/80 articles, 200/1,000 page
  audits), not by module. Every module is in every plan.
- **10 modules, 46 named features.** Inventory below.
- **Zero RAG.** No public evidence of embeddings or a vector store in any of the ten modules. The
  only genuine RAG surface in the whole category belongs to Profound (Knowledge Bases), not Searchable.
- **The measurement core is substring matching and division.** Mention detection is
  `content.toLowerCase().includes(alias)` over name + aliases + domains. Position is the ordinal
  index of first appearance. Share of voice is one number over another. The LLM is what's being
  *measured*, not what does the measuring.
- **The hard part is data acquisition.** ChatGPT and Google AI Mode have no public API returning
  citations, so the category buys scraped consumer surfaces from licensed vendors.
- **MongoDB is an untested assumption** — no evidence for it anywhere. Recommendation: Postgres.

**Company context:** SEARCHABLE LIMITED incorporated 14 Jul 2025 (formerly CLICKER AI LIMITED).
~$18M raised ($4M seed / Freestyle, then $14M / Headline at $85M valuation). Reports ~1,000
customers and $2M revenue in 4.5 months. One engineering role listed; SDR ad states a 996
schedule. This surface is ~12–18 months of build produced very fast by a small, hard-driven team.

## The ten modules

In-app left-nav (leaked on a public feature page):
`Start · Analytics · Prompts · Actions · On Page · AEO Insights · Shopping · Traffic`.
Their marketing `/features` index is stale and omits Agent, Agency and Actions.

| # | Module | Feats | What it is |
|---|---|---|---|
| 1 | AEO Insights | 7 | Visibility · Mentions & Citations · Sentiment · Sources · Topics · Query Fanout · Location |
| 2 | Prompts | 4 | Prompt Tracking · Prompt Research · Topic Coverage · Answer Evidence |
| 3 | Searchable Agent | 5 | Plan Mode (plan card before acting) · Skills (saved workflows) · inline data viz · connector context · suggested tasks |
| 4 | Content Studio | 4 | Plan Mode brief · Content Canvas · AI Search Optimization Score · Content Library |
| 5 | On Page | 4 | Site Health (Technical + AEO) · monitored pages · issue trends · grouped issues |
| 6 | Actions | 2 | Opportunities (impact/severity/relevance) · Tasks (owner, priority, Linear export) |
| 7 | AI Search Traffic | 4 | GA4 + GSC OAuth · AI vs organic · page performance · trends |
| 8 | LLM Analytics (beta) | 6 | Tabs: Overview · Pages · AI Crawlers · Visitors · Logs · Setup |
| 9 | AI Shopping | 6 | Product share, brand visibility, competitor coverage, merchant share, categories, shopping prompts |
| 10 | Agency | 4 | Pitch Workspaces · white-label reports · report templates · workspace API keys |

**Cross-cutting:** MCP 2.0 server with 7 tools (`list_projects`, `get_visibility_summary`,
`get_visibility_details`, `get_website_issues`, `get_page_audits`, `get_monitored_pages`,
`get_issue_details`; keys prefixed `sea_`); a ChatGPT Connector exposing the same MCP surface;
integrations for GA4, GSC, HubSpot, Salesforce, WordPress, Sanity, Contentful, Linear, Jira.
Engines: ChatGPT, Claude, Perplexity, Gemini, AI Overviews, AI Mode, Copilot, Grok, DeepSeek —
their own pages contradict each other on which are included vs paid add-ons.

## Technique per feature — the answer to "is it RAG?"

| Module | Data acquisition | Processing | RAG | Conf |
|---|---|---|---|---|
| AEO Insights | Scraped consumer surfaces via licensed vendor (ChatGPT, AI Overviews, AI Mode) + official APIs (Claude, Grok, DeepSeek, Perplexity Sonar); every run locale-stamped | Per-provider parsers → lowercase substring match on brand + aliases + domains → ordinal index for position → LLM structured output for sentiment → URL parse + domain set membership for sources → daily ratio rollups | No | inferred |
| Prompts | Homepage fetch at onboarding; GSC queries once connected | ONE structured-output LLM call returns brand name, aliases, competitors and ~30 prompts. Branded/unbranded computed from text afterwards, not asked of the model. Query Fanout captures the engine's own grounding searches | No | inferred |
| Searchable Agent | Own tracking tables + connected GA4/GSC | Tool-use agent over internal report APIs (same 7 ops exposed as MCP). Plan Mode = plan-then-execute. Skills = saved prompt+tool workflows. Charts are structured output rendered client-side | No | verified |
| Content Studio | Brief, customer's pages, own citation data | Plan Mode → structured brief; generation is plain LLM completion. The "AI Search Optimization Score" is a **deterministic rubric** (structure, depth, citations, source coverage, schema, internal links, readability, overlap), not a model judgment | No | inferred |
| On Page | Own crawler, **static HTML, no JS rendering** (deliberate — mirrors how real AI crawlers read) | RFC 9309 robots.txt parser vs ~21 AI user-agent tokens · llms.txt · sitemap enumerate+diff · JSON-LD parse and `@type` validate · canonical/noindex/`X-Robots-Tag` · raw-HTML content volume · heading hierarchy | No | inferred |
| Actions | Deterministic SQL digest: visibility overall + per platform, per-prompt standing vs leading competitor 7d/30d, citation landscape | **One structured LLM completion over the digest. No web search, no agent loop.** Typed categorised opportunities with a plain-language why; persisted and cached so a page load never hits a model; real prompt IDs and page URLs re-attached server-side | No | inferred |
| AI Search Traffic | GA4 Data API + Search Console API over OAuth | Referrer regex classifying chatgpt.com / perplexity.ai / gemini.google.com / copilot.microsoft.com into an "AI Search" channel, **ordered above GA4's generic Referral rule** or it gets swallowed. Zero AI | No | verified |
| LLM Analytics | Customer server logs via CDN — Cloudflare Logpush or Worker, Vercel, CloudFront, Fastly, Netlify, Akamai, WordPress/Shopify plugin. **Cannot be a JS pixel — bots don't run JS** | Bot verification is four stacked checks: reverse DNS → published IP range → ASN → behavioural heuristics (UA strings are plain text and scrapers impersonate GPTBot constantly). Then crawl-to-click joins log hits to pixel/GA4 sessions | No | verified |
| AI Shopping | Scraped shopping surfaces / product cards — same vendor pipeline pointed at shopping-intent prompts | Identical extraction with the entity swapped brand → product/merchant. Needs a product feed | No | inferred |
| Agency | Nothing external | Pure app logic: workspace type with own quota, branding config record, server-rendered report templates, scoped API keys | No | verified |

**Where RAG actually lives in this category:** exactly one place, and it isn't Searchable —
Profound's Knowledge Bases (`query` + `top_k` → snippets over the customer's connected CMS:
Contentful, Sanity, WordPress, AEM, Drupal, plus G2, Gong, Looker) feeding a real tool-use agent.
All three open-source implementations of this product have **no vector DB, no embeddings, no
pgvector**. Building one into the replica adds a component the market leader appears not to have.

## The pipeline, end to end

1. **Onboard** — fetch homepage → one strict-schema LLM call → brand name, aliases, extra domains,
   ≤10 competitors with their own domains/aliases, ~30 tagged prompts. Human approves.
2. **Schedule** — one job per prompt, fanned out over engines × locales. Idempotency key per prompt.
   **Queue-level retry limit zero**: by the time a fan-out job fails it has already paid for the runs
   that succeeded, and a queue retry re-submits all of them. Recover with a handler-level backoff
   reschedule instead.
3. **Collect** — per-engine adapters. Scraped surfaces are async submit-and-poll (a live ChatGPT run
   takes ~1 min; never hold a synchronous connection). Force ChatGPT to browse or some runs silently
   measure the non-browsing model. **AI Overviews come from the ordinary organic SERP endpoint as an
   `ai_overview` item; AI Mode is a different endpoint.** Confusing the two is the classic bug.
4. **Store raw, immutably** — write the provider's full JSON to a run row before deriving anything.
   Single most important schema decision in the category: every metric can be recomputed and audited,
   and a formula change can be backfilled. Every call, success or failure, also writes one
   billing-grade usage row with an estimated cost.
5. **Extract and roll up** — substring match, ordinal index, per-provider citation parser, URL
   normalisation + set membership, LLM sentiment. Aggregate to workspace × date × engine.
   **Every dashboard reads the rollup, never raw runs.**
6. **Advise** — scheduled job builds the digest, one structured LLM call, persist, cache.

Two side-channels hang off this and share almost nothing with it: log ingestion (CDN → verification →
crawler analytics) and the site crawler (static fetch → checks → issues). Both feed step 6's digest;
neither touches step 3.

## Stack

| Layer | Finding | Conf | Evidence |
|---|---|---|---|
| Frontend | Next.js App Router (RSC) | verified | `vary: RSC, Next-Router-State-Tree…`, `x-matched-path`, `Disallow: /_next/data/` |
| Hosting | Vercel — edge Dublin, compute US-East | verified | `server: Vercel`, `x-vercel-id: dub1::iad1::…`, Vercel nameservers |
| CDN | Vercel Edge, **not** Cloudflare | verified | no `cf-ray` on any host |
| Cloud | AWS for compute/data | verified | named subprocessor in privacy policy; AWS ACM CAA record |
| Backend | TypeScript/Node in Next.js route handlers, no separate API service | inferred | `Disallow: /api/`; `api.searchable.com` → `DEPLOYMENT_NOT_FOUND` (wildcard DNS, nothing attached); no Express/FastAPI/nginx signature |
| Database | unidentified | unknown | see below |
| Queue | unidentified | unknown | must exist; nothing public |
| Auth | own session cookies, no third-party IdP fingerprint | inferred | no Clerk/Auth0/WorkOS/Supabase signature |
| Analytics | PostHog | verified | cookie policy |
| Billing | Stripe | verified | privacy + cookie policies |
| Inference | OpenAI + Anthropic + Google (multi-provider) | verified | their AI policy; "OpenAI, L.L.C. and other LLM providers" |
| Scraper vendor | unidentified | unknown | not disclosed either way |
| Vector store | none found | unknown | no evidence anywhere |

Also: Google Workspace email, Ashby ATS, lemlist outbound, no CSP (security grade B), internal
tooling named in job ads includes Claude Code and Lovable.

### MongoDB verdict: no evidence

Checked the subprocessor list, cookie policy, AI policy, all eight live job descriptions, blog,
GitHub, npm, and every tech-profiler database — MongoDB appears in none of them. Their subprocessor
list names third-party processors specifically (Stripe, OpenAI by legal entity) and Atlas would
normally have to be disclosed; but that list *omits Vercel*, which is provably in use, so it is
demonstrably incomplete, and a self-managed Mongo on their own AWS account needs no disclosure.
Not formally excluded — just unsupported.

**Practically it doesn't matter what they use.** The workload is append-heavy immutable runs +
daily rollups + strict per-workspace isolation + billing-grade usage counting. That is relational.
The reference open-source implementation of this exact product runs on **Postgres alone with a
Postgres-backed queue (pg-boss)** and benchmarked the alternative before deciding. Add a column
store (ClickHouse) only if per-run time-series volume actually becomes the bottleneck.

## The decision that shapes everything

The PRD currently commits to 3 engines, official APIs only, no scraping, no vendors. Consequences:

**Option A — official model APIs only (current PRD).** ~$0.03–0.05 per prompt per full run across
three engines including extraction. Legal, stable, no vendor. But: **no citations** (model APIs
don't return the citation chips the consumer UI shows), so Sources, citation gaps and most of AEO
Insights are unavailable; no AI Overviews or AI Mode at all (Gemini grounding is a proxy that must
be labelled as one); no shopping cards, so no Shopping module ever; and API answers genuinely
differ from app answers.

**Option B — licensed scraped surfaces (what the category does).** Per prompt per month at ~300
evaluations: Oxylabs ~$0.15, Bright Data ~$0.45, Cloro ~$0.65, DataForSEO ~$1.20, Olostep ~$2.25.
Known buyers: Cloro is behind Ahrefs, Scrunch and Evertune; Olostep is behind Profound, AthenaHQ
and AirOps. Unlocks citations, AI Overviews, AI Mode, shopping cards, query fan-out — six of the
ten modules. Cloro returns parsed structured citations; with everyone else you write and maintain
a parser per provider. Latency, not price, is the constraint. **DIY scraping is not a third
option**: Cloudflare JA4 fingerprinting kills datacenter IPs within a few requests and vendors
estimate 8–15 engineer-hours/month chasing UI changes.

**Third path:** DataForSEO's LLM Mentions API sells a standing corpus of ~333M already-collected
prompts/responses (24.9M ChatGPT, 308M AI Overview, back to Aug 2025) at ~$0.001/row + $0.10/request.
No collection infrastructure at all. Useful for a free "AI visibility snapshot" lead-magnet page
even if the real product runs live.

## Where trysearch stands

| Their module | Ours | Status | What's hard |
|---|---|---|---|
| AEO Insights | Visibility · Answers · Sources | partial | Citations need Option B. The run-strip is a real differentiator — nobody shows stochasticity |
| Prompts | Prompts | partial | One LLM call; the hard part is the prompt rules (majority unbranded, short fragments, aliases that aren't substrings) |
| Agent | Agent | not built | Tool-use over own report APIs. Build the internal API surface first; the agent is thin over it |
| Content Studio | Content Studio | not built | Highest per-unit AI cost. Scoring rubric is cheap and deterministic; generation is what costs |
| On Page | Site health | not built | 2015 SEO crawler + RFC 9309 parser + 21 AI UA tokens. Grind, not risk |
| Actions | Actions | partial | One cached LLM call over a SQL digest. "Evidence above advice" is the right differentiator |
| AI Search Traffic | Integrations | not built | OAuth plumbing + a regex. Low risk, high perceived value |
| LLM Analytics | Crawlers & referrals | not built | Only real engineering is bot verification: rDNS → IP range → ASN → heuristics |
| AI Shopping | Shopping | not built | Blocked on Option B + a product feed. Correctly parked at Enterprise |
| Agency | Client brands · white-label | partial | Pure app logic. Their **Pitch Workspace** (separate prompt budget for pitching a prospect, one-click convert to a real project) is their best agency idea and is cheap to copy |

**The four places the real engineering is:** (1) per-provider response parsers and the scraper
vendor relationship; (2) the domain classification corpus deciding whether a citation is editorial,
social, forum, yours or a competitor's — one OSS implementation carries ~25,000 curated editorial
domains, kept server-only so it doesn't bloat the bundle; (3) run scheduling and spend ceilings
around paid, non-idempotent, minute-long calls — **count usage rows, not run rows, because a retry
storm writes no runs but burns money**; (4) honest sample sizes and confidence intervals, which
almost nobody ships and which we've already chosen as our position.

## Could not determine

Database, queue, auth provider (their one engineering job ad names zero technologies — deliberate).
Which scraper vendor, if any. Whether engines are included or paid add-ons (their own pages
contradict each other). Tier gating for Shopping, LLM Analytics and Actions. The Enterprise and
Agency price ladders — the pricing page renders client-side and only serves the two-card view to a
crawler; third parties report a $50 Starter, a $999+ Enterprise, an "AI answers/month" credit
system, and per-engine add-ons of $25–$100, all unverified. The app itself beyond the login wall.
Headcount, and who leads engineering.

## Sources

**Theirs:** searchable.com `/features/*`, `/pricing`, `/about`, `/llms.txt`, `/sitemap.xml`,
`/robots.txt`, `/ai-policy`, privacy + cookie policies, blog launch posts (Actions, Agent Plan Mode,
Agent Skills, Agent Data Visualization, ChatGPT Connector, MCP, AI Traffic, Pitch Workspaces),
`/compare/*`.

**Corporate:** UK Companies House (SEARCHABLE LIMITED, 16579753), jobs.ashbyhq.com/searchable,
PR Newswire, UKTN, EU-Startups, LXA.

**Infrastructure:** HTTP headers on www and app hosts, securityheaders.com, viewdns.info, Netcraft.

**Technique.** No vendor in this category has published an engineering blog post or given an
architecture talk, so implementation detail comes from open-source production implementations read
directly: `github.com/elmohq/elmo` (MIT, commercially operated — providers, job orchestration,
scoring and opportunity generation all in code), `github.com/aryamantodkar/oneglanse` (opposite
architecture: Postgres + ClickHouse + Playwright/Camoufox), geo-aeo-tracker. Plus methodology pages
from Peec, Profound, Evertune, Ahrefs, Semrush, Gumshoe, Scrunch, Otterly; DataForSEO, Cloro,
Bright Data, Olostep pricing docs; and Vercel's "How we built AEO tracking for coding agents" — the
one genuine engineering write-up in the category.
