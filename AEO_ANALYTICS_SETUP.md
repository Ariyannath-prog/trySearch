# trySearch AI Search Analytics setup

The dashboard keeps measured sources separate from analysis layers:

1. **Website crawl** — public HTML, robots.txt, sitemaps, metadata, headings, JSON-LD, canonical and indexing signals.
2. **Google Search Console** — query, page, clicks, impressions, CTR and position from a property the signed-in user authorizes.
3. **Perplexity evidence** — the exact tracked prompt, ranked Search API results, Agent API answer, source annotations, provider IDs, returned model and scan time.
4. **Crawl-grounded RAG** — retrieval over the normalized public copy saved by one website audit, with every generated insight linked to one or more `chunk:<id>` records.
5. **Open-weight analysis** — an optional Hugging Face-hosted model that converts stored crawl or provider evidence into grounded actions. It never produces visibility metrics.

An unavailable page, revoked OAuth token, missing API key, or failed provider request is stored as an error state. It is not converted into a measured 0% score.

## Local setup

Install dependencies and export the environment variables you need:

```bash
python -m pip install -r requirements.txt
cp .env.example .env
```

The Flask app does not automatically read `.env`; export the values through your shell, IDE launch configuration, or deployment environment. Never put real tokens in source control or browser JavaScript.

Start the app:

```bash
flask --app server_pg run --port 8000
```

## Perplexity source and answer evidence

Create a Perplexity API key and set `PERPLEXITY_API_KEY`. The implementation calls these server-side endpoints:

- `POST https://api.perplexity.ai/search` for ranked source results;
- `POST https://api.perplexity.ai/v1/agent` with `PERPLEXITY_AGENT_PRESET` for a web-grounded answer and source annotations.

Add topics, competitors, and tracked prompts in **Analytics → Prompts**, then run a Perplexity scan. A scan stores the original response JSON and normalized sources before calculating:

- answer mention rate;
- citation rate;
- ranked-source presence rate;
- brand share of voice, calculated from answer-level brand and explicitly saved competitor mentions.

These metrics describe the configured Perplexity APIs and prompt set. They are not claims about Perplexity.com, ChatGPT, Claude, or Google AI Overviews.

The default cost guard allows 25 active prompts per provider scan and retains up to 100 tracked prompts per project. Both limits are configurable with `PERPLEXITY_MAX_PROMPTS_PER_SCAN` and `ANALYTICS_MAX_TRACKED_PROMPTS`.

### Exact dashboard metric definitions

- **Answer visibility** is the percentage of non-empty saved Agent answers that mention a configured or domain-derived tracked-brand alias. Failed or Search-only results are unavailable, not negative answers.
- **Evidence rankings** order the tracked brand and the competitor set saved with that scan by answer visibility. Each entity can contribute at most one mention per answer. The table also exposes share of voice and source position from the same saved cohort.
- **Share of voice** is the tracked brand's entity-answer mentions divided by all entity-answer mentions for the tracked brand plus the scan-time competitor set. It is not general market share.
- **Average source position** is the arithmetic mean of the tracked domain's best rank in saved Perplexity Search result sets where the domain appeared. Missing appearances are excluded and the dashboard shows the appearance count.

Historical changes are comparable only when provider, returned model, region, prompt text set, and competitor set match. The API emits a `cohort_id` for this purpose. Partial runs retain metric-specific denominators and should not be presented as full-cohort changes.

## Crawl-grounded RAG deep audit

Every successful multi-page audit stores a bounded copy of the visible page text, splits it into overlapping chunks, and creates a local sparse BM25-style retrieval index. Scripts, styles, and raw HTML are not added to the RAG corpus. The API is authenticated and project-scoped:

```text
GET  /api/v1/analytics/projects/<project_id>/rag?query=<question>
POST /api/v1/analytics/projects/<project_id>/rag
     {"question": "What sourceable proof does this site expose?", "audit_id": optional}
```

When Hugging Face or Ollama is configured, the model receives only the retrieved crawl chunks. A response is accepted only if it cites one or more supplied `chunk:<id>` references. If the model is unavailable or returns an invalid response, trySearch falls back to an extractive answer that preserves the same source references.

RAG deepens the first-party content audit and recommendations. It does **not** make or update Answer visibility, Evidence rankings, Share of voice, or Average source position; those require stored third-party Perplexity evidence. The RAG corpus is refreshed when a new full website audit runs, not continuously between audits.

## Open-weight model

Set a fine-grained `HF_TOKEN` with permission to call Inference Providers. The default is:

```text
HF_MODEL=openai/gpt-oss-120b:preferred
```

The model receives a small JSON list containing saved evidence IDs and measured booleans. Its output is accepted only when every opportunity cites a supplied `answer:<id>`. If the model is unavailable or returns invalid JSON, trySearch falls back to deterministic recommendations derived directly from the same stored evidence.

For a self-hosted alternative, leave `HF_TOKEN` empty and set `OLLAMA_BASE_URL` (normally `http://127.0.0.1:11434/v1`) plus `OLLAMA_MODEL`. The connector uses Ollama's OpenAI-compatible chat-completions endpoint. A local Ollama process is suitable for development or a dedicated inference host, not the existing stateless Render web service.

## Google Search Console

In Google Cloud:

1. Enable the Search Console API.
2. Configure the OAuth consent screen.
3. Create a **Web application** OAuth client.
4. Add the exact callback in `GOOGLE_OAUTH_REDIRECT_URI` to the client's authorized redirect URIs.
5. Generate a Fernet key and set `OAUTH_TOKEN_ENCRYPTION_KEY`.

trySearch requests only `https://www.googleapis.com/auth/webmasters.readonly`, validates OAuth state, requests offline access, and encrypts access and refresh tokens at rest. After connecting, select one of the properties returned by Google and sync a date range.

Keep `OAUTH_TOKEN_ENCRYPTION_KEY` stable across deploys. If it is intentionally rotated, reconnect each Search Console property so new tokens can be encrypted with the new key.

Search Console can return top/important rows rather than every row. The dashboard labels this as owned Google search data and does not relabel ordinary `web` rows as isolated AI Overview traffic.

## Background and scheduled jobs

On-demand crawl and prompt scans create durable database jobs and start a lightweight background thread for immediate development use. The recovery and scheduling command is:

```bash
flask --app server_pg run-scheduled-analytics
```

Run that command every 10–15 minutes in a separate Render Cron Job or equivalent worker. It:

- recovers stale jobs;
- claims a bounded batch of queued jobs;
- enqueues due daily, weekly, or monthly prompt schedules;
- persists progress, completion, and error states.

For higher scan volume, replace the lightweight thread with a Redis/Celery worker while keeping the same database job contract. Do not embed a scheduler inside each Gunicorn worker because every worker process could run the same schedule.

## Production environment variables

At minimum, retain the managed PostgreSQL `DATABASE_URL`, `APP_ENV=production`, and `SECRET_KEY`. Add only the sources you plan to enable:

```text
PERPLEXITY_API_KEY
HF_TOKEN
HF_MODEL
OLLAMA_BASE_URL
OLLAMA_MODEL
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_OAUTH_REDIRECT_URI
OAUTH_TOKEN_ENCRYPTION_KEY
RAG_DOCUMENT_MAX_CHARS
RAG_CHUNK_WORDS
RAG_CHUNK_OVERLAP_WORDS
RAG_MAX_CHUNKS_PER_PAGE
RAG_DEFAULT_TOP_K
RAG_MAX_CONTEXT_CHARS
```

After deployment, open AI Search Analytics. The source status strip should show each integration as connected, unconfigured, running, or failed independently.

## Official API references

- [Perplexity Search API](https://docs.perplexity.ai/docs/search/quickstart)
- [Perplexity Agent API](https://docs.perplexity.ai/docs/agent-api/quickstart)
- [Google OAuth for web-server applications](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google Search Console Search Analytics API](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)
- [Hugging Face Inference Providers](https://huggingface.co/docs/inference-providers/en/index)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [Render Cron Jobs](https://render.com/docs/cronjobs)
