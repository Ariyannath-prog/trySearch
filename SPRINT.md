# trySearch — 2-week sprint to a demo-ready product

Written 26 Aug 2026. Companion to `CLAUDE.md` (the rules) and the project docs
`docs/architecture-spec.md` (the DDL and contracts) and
`docs/prototype-audit.md` (why each of these is here).

**Goal at the end of day 10:** a multi-tenant product tracking real prompts across 3 engines, with a
correct Visibility Score carrying confidence intervals, a generated starter prompt set, a citation
view, and a white-label PDF report you can send to a prospect.

**Not in these two weeks:** alerts, GA4, sentiment, Stripe, agent chat, crawler log analytics,
scraper vendors. None of those are why someone buys. Week 3+.

**Database is empty.** No data migration anywhere in this plan. Drop legacy tables freely.

---

## Day 0 — your 90 minutes. Nothing below starts without this.

| # | Do this | Time | Blocks |
|---|---|---|---|
| 1 | Google AI Studio → create a Gemini API key | 5 min | T13. Free tier gives 5,000 grounded prompts/month **per Google Cloud project** |
| 2 | OpenAI platform → API key, add $5 credit | 10 min | T13 |
| 3 | Anthropic console → API key | 10 min | T13 + extraction |
| 4 | Confirm the Perplexity key still works | 5 min | T12 |
| 5 | Google Cloud Console → OAuth 2.0 client, add the Search Console + Analytics scopes, set the redirect URI | 40 min | Week 3 GA4, and switches on the GSC module you already built |
| 6 | Render → provision a second Postgres as staging; put its URL in the agent's env, never prod's | 10 min | everything |

Put every key in Render env vars and in a local `.env`. Never let a key reach the repo.

---

## Getting started

Commit these files at the repo root, then open Claude Code in the repo and start with:

> Read CLAUDE.md and SPRINT.md. Do T1 only — split the monolith and delete the Mongo branch.
> Also untrack searchable.db and .DS_Store. Open a PR when the acceptance criteria pass.

Then one task per session, in order. Do not let it run ahead — **T10 is worthless if T9 is wrong.**

Files in place after unzipping:

```
CLAUDE.md                      # Claude Code reads this automatically every session
SPRINT.md                      # this file — point it here, task by task
docs/architecture-spec.md      # DDL, adapter contract, job rules, extraction pipeline
docs/prototype-audit.md        # what already exists and why each task is here
docs/searchable-teardown.md    # how the competitor works, feature by feature
docs/PRD.md                    # acceptance criteria, Visibility Score spec, cost model
docs/handover-plan.md          # yours only — account migration notes, not for the agent
```

## How to run this with full delegation

One task per branch. One PR per task. **Review means reading the test output and the PR
description, not the diff** — that only works because every task below has a machine-checkable
acceptance criterion, so build T1–T4 first even though they ship nothing visible.

If a task's tests don't pass, don't merge and don't move on. The plan compounds: T10 is meaningless
if T9 is wrong.

---

# Week 1 — foundation

Nothing here is client-visible. All of it becomes impossible to retrofit once there's real data.

### T1 · Split the monolith · ~4h
`server_pg.py` (4,756 lines) becomes the module tree in `CLAUDE.md`. Pure move — no behaviour
changes, no renames beyond the file boundary. Delete `server.py`, `server_mongo.py`,
`mongo_config.py`, `migrate_postgres_to_mongo.py`, and drop `pymongo` + `dnspython` from
`requirements.txt`.

**Accept:** every existing route responds identically before and after (snapshot test hitting all
56 routes) · `pytest` green · `grep -ri mongo` returns nothing outside `SPRINT.md` · app boots on
Render.

### T2 · Alembic · ~2h
Introduce Alembic. Baseline migration captures the current schema. Delete
`ensure_database_column` and any `create_all` in app code.

**Accept:** `alembic upgrade head` builds the schema from empty · `alembic downgrade base` tears it
down · no schema DDL left outside `migrations/`.

### T3 · Fixture harness · ~3h
`scripts/record_fixture.py` makes one real call to a provider with a real key and writes the raw
JSON to `tests/fixtures/<engine>/<case>.json`. Add a `pytest` fixture that replays them. Record one
Perplexity search + one Perplexity agent response now.

**Accept:** `pytest` passes with no network access at all (run it with networking disabled to
prove it) · at least two recorded Perplexity fixtures committed.

### T4 · CLAUDE.md + CI · ~1h
Commit `CLAUDE.md`. GitHub Actions running `pytest` on every PR, blocking merge on red.

**Accept:** a deliberately failing test blocks a PR.

### T5 · Tenancy schema — clean slate · ~6h
Create `organizations`, `memberships`, `workspaces`, `brand_aliases`, `competitors` per the
architecture spec §2.1. Add `workspace_id` to every workspace-scoped table and drop `user_id` from
them. **Drop outright:** `analytics_runs`, `analytics_engine_metrics`, `analytics_prompts`,
`prompt_collections`, `prompt_queries`, `prompt_query_results`, `visibility_watchlists`,
`visibility_scans`, `visibility_engine_results`, `visibility_mentions`, `master_workspaces`.
`analytics_projects` becomes `workspaces`.

**Accept:** `alembic upgrade head` from empty produces the new schema · table count is ~26, not 36 ·
`is_legacy_mock_analytics_run` is gone along with the tables that needed it.

### T6 · One isolation guard · ~5h
`app/tenancy.py` exposes `require_workspace(workspace_id)` and `require_org(org_id)`, resolving the
current user's membership and role. Delete `project_for_user`, `watchlist_for_user`,
`prompt_collection_for_user`, `content_document_for_user` and every hand-written
`WHERE user_id = ?`. Every workspace-scoped route goes through the guard.

**Accept:** an isolation test creates two orgs with one workspace each and asserts **every**
workspace-scoped route returns 404 for the wrong org · `grep -r "user_id ==" app/routes/` returns
nothing · role checks reject `client_viewer` on writes.

### T7 · Jobs off the web process · ~4h
Delete `start_background_analytics_job` and `threading.Thread`. All execution moves to the CLI
worker. Keep the 45-minute stale-lease recovery — it's correct. Replace the 25-prompt `409` with
batching: a 300-prompt workspace becomes 12 sequential batches. Fan-out parents never retry;
individual answers retry 3× with jittered backoff and mark the run `partial`.

**Accept:** an on-demand scan returns immediately with a job id and completes in the worker · a
300-prompt workspace scans without a 409 · killing the worker mid-run leaves the job recoverable,
not lost · a forced single-answer failure produces `status='partial'`, not `failed`.

### T8 · usage_ledger and ceilings · ~4h
Table per architecture spec §2.4, with `org_id` denormalised. One row per provider call including
failures, with an estimated cost. Ceiling checked **before** dispatch against this month's rows for
the org. Alert at 60%, refuse at 100%.

**Accept:** a scan of N prompts across E engines writes exactly N×E engine rows plus extraction
rows · a forced provider failure still writes a row · an org over its ceiling gets refused before
any paid call is made.

---

# Week 2 — the demo surface

## Ground rules for this week

1. **One branch per task, and merge it before starting the next.** T8 sat open while T9 was being
   planned; that is how a stack of stale PRs forms. Merge, delete the branch, then start.
2. **Order is not negotiable.** T10 consumes T9's output, T11 consumes T10's, T15 consumes T9's
   citation categories. A wrong number in T9 propagates silently through everything after it.
3. **Read the "Do NOT" list before writing code.** Each one is a trap that looks like an
   improvement.
4. **Every task ends with named tests.** The tests listed are the minimum, not the ceiling.

### Two amendments to `docs/architecture-spec.md`, decided against the real code

- **§2.2 `citations` table: do not create it.** `analytics_answer_sources` already stores
  `answer_id, rank, url, domain, source_kind, title, snippet, published_at`. Add a `category`
  column to it instead. A parallel table would leave two sources of truth for the same evidence.
- **§2.2 `answers` table: it already exists as `analytics_provider_answers`.** Do not rename it in
  Week 2. Add to it; do not migrate it.

---

### T9 · Extraction v2 · ~6h

**Goal.** Derive rank, per-competitor mention rows and classified citations from stored answers,
versioned so extraction can be re-run over immutable evidence at zero provider cost.

**Why it matters.** `brand_rank` does not exist today, so `PositionScore` cannot be computed, so the
PRD §13 Visibility Score is impossible. T10 depends entirely on this task being right.

**Files.** `app/extraction/mentions.py` (extend) · new `app/extraction/rank.py` · new
`app/extraction/citations.py` · new `app/extraction/pipeline.py` · `app/models.py` ·
`app/scanning.py` (`persist_provider_answer`, line ~82) · new migration ·
`tests/test_extraction_v2.py`

**Do this.**

1. Add `alias_offsets(text, aliases) -> int | None` beside the existing `text_mentions_alias`,
   returning the character offset of the **earliest** word-boundary match or `None`. Same regex,
   same flags. Keep `text_mentions_alias` (other modules import it) but reimplement it as
   `alias_offsets(...) is not None`, so there is exactly one regex in the codebase.
2. Read aliases from the `brand_aliases` table added in T5. `project_brand_aliases()` currently
   synthesises them from the domain — keep that as the seed when the table is empty, but the table
   is the source of truth.
3. New tables:

```sql
extractions (
  id                 SERIAL PRIMARY KEY,
  answer_id          INTEGER NOT NULL,
  extractor_version  TEXT    NOT NULL,
  is_current         BOOLEAN NOT NULL DEFAULT true,
  brand_mentioned    BOOLEAN NOT NULL,
  brand_rank         INTEGER NULL,
  brand_cited        BOOLEAN NOT NULL,
  sentiment          TEXT    NULL,
  sentiment_conf     REAL    NULL,
  summary            TEXT    NULL,
  created_at         TIMESTAMP NOT NULL
);
CREATE UNIQUE INDEX ON extractions (answer_id) WHERE is_current;

mentions (
  id            SERIAL PRIMARY KEY,
  extraction_id INTEGER NOT NULL,
  entity_type   TEXT    NOT NULL CHECK (entity_type IN ('brand','competitor')),
  competitor_id INTEGER NULL,
  rank          INTEGER NOT NULL,
  char_offset   INTEGER NOT NULL
);
```

   The partial unique index is what enforces "exactly one current extraction per answer" in the
   database rather than in application code. Do not enforce it in Python instead.

4. Add `category TEXT` to `analytics_answer_sources`, values
   `own | competitor | editorial | social | forum | developer | other`. T15 fills the lists; T9 just
   needs `own` and `competitor` working.
5. **Rank algorithm.** Collect the first-match offset for the brand and for every competitor. Sort
   ascending. `brand_rank` is the brand's 1-based index in that ordering. Brand absent → `NULL`.
   Brand present with no competitor named → rank 1. Write one `mentions` row per named entity with
   its offset, so the ordering is auditable after the fact.
6. `EXTRACTOR_VERSION` is a module constant, e.g. `'2026.08.2'`. Re-running inserts a new row and
   sets the previous one's `is_current = false` **in the same transaction**.
7. Add a backfill entry point — `python -m app.worker reextract --workspace <id>` — that re-runs
   extraction over stored `raw_response`. It must make zero provider calls and write
   `category='extraction'` rows to `usage_ledger` at zero cost (T8 already handles the pricing).
8. Drop `brand_mentioned`, `brand_cited`, `source_present`, `best_source_rank` from
   `analytics_provider_answers` in the same migration. `app/scanning.py` and `app/metrics.py` are
   their only readers; update both.

**Do NOT.**

- Do not replace the regex with NER, fuzzy matching, or an LLM call. This is settled in `CLAUDE.md`.
- Do not update an existing `extractions` row. A new version is always a new row.
- Do not touch `analytics_provider_answers.raw_response`, ever, for any reason.
- Do not create a separate `citations` table — see the amendment above.

**Tests — `tests/test_extraction_v2.py`.**

- `test_rank_orders_by_first_mention` — an answer naming Beta, then Alpha, then Gamma, with Alpha as
  the brand, yields `brand_rank == 2` and three `mentions` rows with ascending offsets.
- `test_brand_only_mention_is_rank_one`
- `test_absent_brand_has_null_rank` — `NULL`, not 0.
- `test_alias_matches_on_word_boundary_only` — brand "Aspire" must not match "Aspireship".
- `test_reextraction_writes_new_version_and_flips_is_current`
- `test_reextraction_makes_zero_provider_calls` — assert with the existing `no_network` fixture.
- `test_two_current_extractions_are_rejected_by_the_database`

**Accept.** All tests green · `SELECT COUNT(*) FROM extractions WHERE is_current` equals the number
of answers with `status='ok'` · re-running extraction changes no row in
`analytics_provider_answers`.

---

### T10 · metrics_daily and the Visibility Score · ~5h

**Goal.** The first number in this build that is checkable against something outside the code.

**Why it matters.** This is the product. Also: `app/metrics.py:analytics_report` currently
fabricates an "engines" list out of site-crawl sub-scores — Metadata, Content, Crawlability,
Structured data — and renders them where AI engines belong. That is a crawl score wearing a
visibility score's clothes and it must not survive this task.

**Files.** `app/metrics.py` (rewrite `analytics_report`) · new `app/rollup.py` · `app/models.py` ·
migration · `app/worker.py` · `app/scanning.py` · `tests/test_visibility_score.py`

**Do this.**

1. `metrics_daily`, primary key `(workspace_id, date, engine_id)` with `engine_id NULL` meaning
   blended. Columns: `visibility_score, mention_rate, position_score, citation_rate, sov,
   sentiment_index, answer_count`.
2. Compute from `extractions` + `mentions` + `analytics_answer_sources`. Never from
   `analytics_provider_answers`.
3. Formula, exactly PRD §13:

```
MentionRate   = mentioned answers / total answers
PositionScore = mean(1 / brand_rank) over mentioned answers only; 0 if never mentioned
CitationRate  = answers with >=1 own-domain citation / total answers
VS            = 100 * (0.5*MentionRate + 0.3*PositionScore + 0.2*CitationRate)
```

4. The weights are named constants in one module, with a comment pointing at PRD §13 and noting
   that changing them requires recomputing history.
5. **Exclude on-demand runs.** There is no run-type column yet — `analytics_prompt_scan_runs` is the
   run table. Add `run_type TEXT NOT NULL DEFAULT 'scheduled'` in this migration and set it
   correctly where runs are created in `app/scanning.py`.
6. `NULL`, never `0`, on an empty denominator — everywhere, including the blended row.
7. Blend across engines as a **simple average of per-engine VS**, not weighted by answer count, so a
   low-volume engine cannot be drowned out.
8. Rewrite `analytics_report` to read `metrics_daily`. **Delete the site-audit compatibility block.**
   Site health keeps its own section in the response; it is not an engine.
9. The rollup runs at the end of `run_prompt_scan_job` and is idempotent — recomputing a date
   overwrites, never duplicates.
10. While in `app/metrics.py`: it still carries an unused `create_engine, MetaData, Table, Column…`
    import block left over from the T1 split. Remove it.

**Do NOT.**

- No read path may touch `analytics_provider_answers`. Write a test that asserts this.
- Do not round intermediate values. Round once, at display.
- Do not weight the blend by answer count.

**Tests — `tests/test_visibility_score.py`.**

- `test_prd_worked_example` — **100 answers; brand mentioned in 40; among those, rank 1 in 20, rank
  2 in 12, rank 3 in 8; own domain cited in 15. Expect MentionRate 0.40, PositionScore 0.717,
  CitationRate 0.15, and VS == 44.5** to one decimal place. This is the anchor test for the whole
  build.
- `test_never_mentioned_gives_zero_position_score`
- `test_empty_denominator_is_null_not_zero`
- `test_blend_is_simple_average` — a 1-answer engine moves the blend as much as a 100-answer engine.
- `test_on_demand_runs_are_excluded`
- `test_rollup_is_idempotent` — running it twice for one date leaves one row, unchanged.
- `test_no_dashboard_read_touches_provider_answers`

**Accept.** The worked example returns exactly 44.5. If it returns 44.4 or 45.0, something in T9 is
wrong — fix T9, do not adjust the formula.

---

### T11 · Honest numbers in the UI · ~4h

**Goal.** No bare number anywhere in the product.

**Why it matters.** This is the stated differentiator — every competitor renders one bold number
with a green arrow over a handful of runs. It is also the only thing that stops the product lying
when a client has 12 answers.

**Files.** New `app/stats.py` · `app/metrics.py` · `analytics.html`, `analytics.js` ·
`tests/test_stats.py`

**Do this.**

1. `wilson_interval(successes, trials, z=1.96) -> (low, high)`, returning `(None, None)` when
   `trials == 0`.
2. Every metric in the API response becomes `{value, low, high, n}` rather than a scalar.
3. **Delta rule:** if `abs(delta) < (high - low) / 2`, render "no measurable change" — no arrow, no
   colour, no percentage.
4. **Three empty states as three distinct API states**, never one:
   `not_yet_run` (no completed scan) · `absent` (ran, brand never mentioned) ·
   `insufficient` (n < 20). Three different messages in the UI.
5. Suppress the Visibility Score below 20 answers in the period; show `collecting data · n/20`.
6. Sample size renders next to the number, visible without a hover.

**Do NOT.**

- Do not use the normal approximation `p ± 1.96·sqrt(p(1-p)/n)`. It breaks at small n and near 0 or
  1, which is exactly this product's regime. Wilson or nothing.
- Do not collapse the three empty states into one "no data" message.

**Tests.** `test_wilson_matches_known_values` (40/100 at 95% ≈ 0.307–0.501) ·
`test_zero_trials_returns_none` · `test_delta_inside_interval_is_no_measurable_change` ·
`test_score_hidden_below_twenty_answers` · `test_three_empty_states_are_distinguishable`

---

### T12 · Engine adapter interface · ~4h

**Goal.** Perplexity stops being the shape of the system and becomes the first implementation of a
contract.

**Files.** New `app/engines/base.py` · new `app/engines/registry.py` ·
`app/engines/perplexity.py` (wrap, do not rewrite) · `app/models.py` · migration + seed ·
`app/scanning.py` · `tests/test_engine_adapter.py`

**Do this.**

1. `EngineResult` — a frozen dataclass: `answer_text`, `citations: list[Citation]` where
   `Citation(position, url, title)`, `raw_response: dict`, `model_version`, `cost_usd: Decimal`,
   `latency_ms`, `status: 'ok'|'failed'|'empty'`, `error`.
2. `EngineAdapter` protocol: `key`, `source_type: 'api'|'scraper'|'serp_vendor'`,
   `adapter_version`, `supports_citations`, `supports_regions`,
   `run(prompt, *, region, timeout_s) -> EngineResult`, `estimate_cost(prompt) -> Decimal`.
3. `engines` table: `id, key UNIQUE, display_name, source_type, adapter_version, enabled`. Seed
   Perplexity in the migration.
4. `run_prompt_scan_job` iterates enabled engines from the table via the registry. Adding an engine
   is a table row plus a module — never a schema change.
5. Wrap the four existing functions (`call_perplexity_search`, `call_perplexity_answer`,
   `perplexity_answer_text`, `normalise_perplexity_sources`) inside `PerplexityAdapter.run`. Do not
   rewrite them; they work and they have fixtures.
6. **Adapters never touch the database and never raise past their own boundary.** Catch everything
   inside `run` and return `status='failed'` with the message.
7. `analytics_provider_answers.provider` gains an `engine_id` FK. Keep the `provider` string as a
   denormalised label if it simplifies existing queries.
8. The adapter *reports* `cost_usd`; `app/costs.py` *writes* the ledger row. One writer, no
   exceptions.

**Do NOT.**

- Do not let any module under `app/engines/` import `app.db` or `app.models`.
- Do not introduce an engine enum, a `PROVIDERS = [...]` list, or an `if provider == 'perplexity'`
  branch anywhere. The table is the registry.

**Tests.** `test_adapter_never_raises` (transport throws → `status='failed'`) ·
`test_failed_engine_leaves_run_partial_not_failed` ·
`test_engine_modules_do_not_import_db` (inspect module imports) ·
`test_perplexity_adapter_parses_recorded_fixture` ·
`test_enabling_an_engine_row_requires_no_schema_change`

---

### T13 · Three more engines · ~6h

**Blocked on API keys.** OpenAI, Anthropic, Perplexity keys must exist before this starts.

**Files.** New `app/engines/openai.py`, `gemini.py`, `anthropic.py` · `tests/fixtures/<engine>/` ·
`tests/test_<engine>_adapter.py` · a daily CI workflow

**Do this.**

1. **Record a real fixture before writing each adapter.**
   `python scripts/record_fixture.py openai "best expense management software for startups"`.
   Same for gemini and anthropic. Do not hand-write a fixture and do not infer a response shape from
   memory — the structure will be roughly right and wrong in exactly the places that matter
   (where citations live, what the model-version field is called, how errors come back).
2. **OpenAI:** Responses API with the `web_search` tool, and **force browsing**. Without forcing,
   the model decides per prompt whether to search, and a share of runs silently measure the
   non-browsing model — which makes the time series meaningless in a way no test will catch.
3. **Gemini:** Flash-Lite with Search grounding. The free tier is 5,000 grounded prompts per month
   **per Google Cloud project, not per workspace** — model it as a project-level allowance, never as
   a per-request price.
4. **Anthropic:** Claude with the web search tool.
5. Set `supports_citations = False` honestly where a provider does not return them. An empty
   citation list is a fact about the source, not a bug to paper over.
6. One contract test per adapter, marked `@pytest.mark.contract`, excluded from the default run, in
   a **daily** CI job that alerts on parse failure. Providers change formats constantly and you want
   to hear it from CI.
7. Expose `source_type` in the API so the UI can label Gemini a **proxy** for Google AI Overviews.

**Do NOT.**

- Do not write an adapter before its fixture exists.
- Do not backfill a missing citation list from another call's search results. If the provider gave
  nothing, store nothing.

**Tests.** `test_<engine>_adapter_parses_recorded_fixture` (×3) ·
`test_every_enabled_engine_has_a_recorded_fixture` · `test_source_type_is_exposed_in_api`

---

### T14 · Onboarding wizard · ~6h

**Goal.** Domain in, approved prompt set out, in under ten minutes. This is what makes the product
demoable.

**Files.** New `app/onboarding.py` · new `app/routes/onboarding.py` · `app/llm.py` (reuse) ·
templates · `tests/test_onboarding.py`

**Do this.**

1. Fetch the homepage through the existing SSRF-guarded `app/crawler/fetch.py`. Do not write a
   second fetcher.
2. One structured call returning: `brand_name`, `aliases[]`, `domains[]`,
   `competitors[{name, domains[], aliases[]}]` (≤10), `prompts[{text, category, tags[]}]` (~25).
3. Categories: `discovery | comparison | purchase | brand`.
4. Prompt rules, stated in the system prompt:
   - **The majority must be unbranded** — generic category and persona queries that do not contain
     the brand name. The point is to test whether the model mentions the brand organically.
   - Each prompt is a short search-style fragment, lowercase, under ~12 words. Not a sentence.
   - Never generate an alias that is a substring of the canonical brand name — matching is
     word-boundary, so it is redundant.
5. **`branded` is computed in Python** from the prompt text against the alias list. Never ask the
   model for it.
6. Schema-validate the response · one repair retry · then a manual-entry fallback.
7. A review screen: edit, toggle and delete before the first run fires.
8. Persist into `workspaces`, `brand_aliases`, `competitors`, `analytics_tracked_prompts`.

**Do NOT.**

- Do not let the model set `branded`.
- Do not fire the first scan without explicit approval — it costs money and it biases nothing yet,
  but an unapproved prompt set is a bad first impression.

**Tests.** `test_majority_of_generated_prompts_are_unbranded` (>50% contain no brand token, against
a recorded LLM fixture) · `test_no_generated_alias_is_a_substring_of_the_brand` ·
`test_branded_flag_is_computed_not_trusted` ·
`test_malformed_model_output_retries_once_then_falls_back` ·
`test_no_scan_runs_before_approval`

---

### T15 · Citation intelligence · ~5h

**Goal.** Tell a client *where* to earn citations, not just that they lack them.

**Files.** `app/extraction/citations.py` · new `app/data/editorial_domains.txt` ·
`app/routes/analytics.py` · templates · `tests/test_citation_classification.py`

**Do this.**

1. Normalise: follow redirects to the final URL, strip `www.`, lowercase, drop the fragment. Reuse
   `normalise_domain` and `normalise_site_host` — do not write new ones.
2. Classify in strict order, first match wins:
   `own → competitor → editorial → social → forum → developer → other`.
3. The editorial list starts at 200–300 domains covering your clients' verticals. Plain text, one
   per line, loaded once at import. **Server-side only.** The reference implementation of this
   product carries ~25,000 curated domains and keeps the list off the client for exactly this
   reason. Grow it every week — it is a real moat.
4. The view: most-cited domains with counts and share, split own vs competitor vs third-party, plus
   **competitor citation gaps** — domains that cite a competitor and never cite you. That list is
   the actionable output of the whole module.
5. Query `analytics_answer_sources` with a single `GROUP BY`. No N+1.

**Do NOT.**

- Do not serialise the domain list into any API response or JS bundle.
- Do not classify by substring (`'reddit' in domain`). Match the registrable domain exactly, or as a
  suffix after a dot. `notreddit.com` is not Reddit.

**Tests.** `test_redirect_chain_resolves_to_final_domain` ·
`test_subdomain_of_own_domain_counts_as_own` · `test_own_beats_competitor_when_both_match` ·
`test_classification_is_first_match_wins` · `test_domain_list_never_appears_in_an_api_response` ·
`test_top_domains_is_a_single_query`

---

### T16 · White-label report · ~6h

**Goal.** The sales artifact. Per PRD §6b the report *is* the pitch, so it has to look right
unbranded.

**Files.** New `app/reports.py` · new `app/routes/reports.py` · a template · `app/models.py`
(`report_shares`, `workspace_branding`) · migration · `tests/test_reports.py`

**Do this.**

1. Sections: visibility with intervals · share of voice · top citations · prompt table ·
   methodology footer. Sections are selectable.
2. Per-workspace branding: brand name, logo URL, accent colour. Below Enterprise the trysearch mark
   stays in the header and footer — see the plan matrix in `docs/PRD.md`.
3. Share link: `report_shares(token, workspace_id, expires_at, created_at)` with
   `secrets.token_urlsafe(32)`. Read-only, no session required, and no write path reachable from it.
4. **Every number on the report carries its sample size**, same rule as T11. A report that hides `n`
   is worse than no report, because it travels further than the dashboard does.
5. If a scan is in progress, use the last complete run and label it with its date.
6. PDF: render the HTML and print to PDF. Prefer a pure-Python path over headless Chrome so the
   Render build stays small — and justify the dependency in the PR description either way.

**Do NOT.**

- Do not fabricate a number to fill a section. An empty section states which of the three empty
  states it is in.
- Do not make the share token sequential or guessable.

**Tests.** `test_report_generates_for_workspace_with_data` ·
`test_report_generates_for_empty_workspace_without_erroring` ·
`test_share_token_is_unguessable_and_read_only` · `test_branding_changes_the_output` ·
`test_every_metric_on_the_report_carries_a_sample_size` ·
`test_in_progress_run_uses_last_complete_and_labels_it`

## If you fall behind — cut in this order

1. **T16 report** → hand-build one in HTML for the first demo. Painful but survivable.
2. **T15 citation view** → keep the classification, drop the dedicated screen.
3. **T13 down to two engines** — keep Gemini (free tier) and OpenAI (the one prospects ask about).
4. **T11 intervals** → *last resort only.* This is the product's stated differentiator; cutting it
   makes you the same as everyone else with worse data.

**Never cut T5–T8.** Tenancy, worker, ledger and extraction are the things that cost five times as
much to retrofit as to build.

## Week 3+ backlog

Alerts · GA4 (mirror the GSC module exactly) · sentiment as a companion index · recommendations v2
with impact × effort and chart annotations · `llms.txt` and robots AI-bot parsing in the crawler ·
crawler page cap 12 → 500 · Stripe and plan gating from `docs/PRD.md (plan matrix section)` ·
the 50-answer extraction eval set · agent chat over the internal report APIs.
