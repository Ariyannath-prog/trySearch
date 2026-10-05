# TrySearch — Claude Code Project Rules

## Project
TrySearch is a production Flask SaaS application for AI-search/AEO intelligence.

Production domain:
https://trysearch.aevix.xyz

Repository/app root:
 /home/trysearch.aevix.xyz/app

Python environment:
 /home/trysearch.aevix.xyz/app/venv

Production service:
 trysearch.service

Gunicorn:
 127.0.0.1:8000

Web server:
 OpenLiteSpeed / CyberPanel

Database:
 PostgreSQL database: trysearch

## CRITICAL SAFETY RULES

This is a live production server.

NEVER run:
- git reset --hard
- git clean -fd
- destructive database resets
- DROP DATABASE
- DROP TABLE
- truncate production tables
- mass deletion of production data
- blind git pull
- overwrite production files without inspecting them first

NEVER modify:
- unrelated websites/domains on this VPS
- MariaDB unless explicitly required
- Redis unless explicitly required
- OpenLiteSpeed global configuration unless explicitly required
- CyberPanel configuration unless explicitly required

NEVER expose:
- API keys
- OAuth client secrets
- database passwords
- Fernet encryption keys
- session secrets
- .env contents
- decrypted provider credentials

Never print secrets in terminal output.

## DEVELOPMENT WORKFLOW

Before changing an important subsystem:

1. Inspect the existing implementation.
2. Understand all callers/importers.
3. Identify backward-compatibility requirements.
4. Make a small plan.
5. Create a backup before risky edits.
6. Make the smallest coherent change.
7. Run syntax/import checks.
8. Run relevant tests.
9. Review git diff.
10. Restart production service only when necessary.
11. Verify service health after restart.

Prefer incremental changes over rewriting files.

Do not replace existing modules wholesale unless their complete dependency surface has been inspected.

## EXISTING ARCHITECTURE

Main Flask app:
app/

Important areas include:
- app/routes/
- app/engines/
- app/integrations/
- app/models.py
- app/db.py
- app/scanning.py
- app/crawler/
- app/rag/
- app/reports/
- app/jobs/
- templates/
- migrations/

Authentication:
session-based Flask auth.

Multi-tenancy:
organizations
memberships
workspaces

Platform admin is separate from organization roles.

## ENGINE ARCHITECTURE

Engine adapters live in:

app/engines/

Current registered adapters:
- perplexity
- openai
- google_gemini
- anthropic
- xai
- deepseek
- meta

Registry:
app/engines/registry.py

Adapter contract:
app/engines/base.py

Adapters must:
- accept runtime credentials
- not import app.db or app.models
- use EngineResult
- use guard()
- preserve legacy helper functions where existing callers depend on them

IMPORTANT:
A previous Gemini change broke production because a legacy
call_gemini_text import was removed.

Therefore:
Before changing an engine module, search for all imports/usages.

Provider credentials are encrypted and injected at runtime.

## PROVIDERS

Current provider catalog:
- OpenAI
- Google Gemini
- Perplexity
- Anthropic
- Microsoft Copilot
- xAI
- DeepSeek
- Meta

Microsoft Copilot is NOT fully implemented yet.
Do not implement or enable Copilot unless explicitly requested.

Copilot requires a different Microsoft Entra/delegated OAuth architecture.

## ADMIN SYSTEM

Admin provider/API-key management exists.

Important routes:
- /admin/api-keys
- /admin/engines
- /api/admin/providers
- /api/admin/api-keys
- /api/admin/api-keys/<id>/test
- /api/admin/engines

Provider credentials are stored encrypted.

Connection tests must return useful structured JSON.

## CURRENT PRODUCT DIRECTION

The immediate goal is to finish the actual TrySearch product, not continue adding providers.

Core product flow:

Website
→ Project
→ Prompts
→ Scheduled prompt scans
→ AI engine execution
→ AI answers
→ Mention extraction
→ Citation extraction
→ Competitor detection
→ Sentiment
→ Visibility metrics
→ Dashboard

Then:
- competitors
- sources/citations
- site crawler/AEO audit
- analytics
- recommendations/actions
- content generation
- TrySearch Agent
- reports

## PRIORITY BUILD ORDER

Build in this order unless explicitly changed:

1. Project setup/onboarding
2. Prompt management
3. Prompt execution/job system
4. Answer storage/processing
5. Mention/citation extraction
6. Visibility calculations
7. Main dashboard
8. Competitor intelligence
9. Sources/citation intelligence
10. Sentiment
11. Site crawler/AEO audit
12. Analytics
13. Recommendations/actions
14. Content Studio
15. Brand knowledge/RAG improvements
16. TrySearch Agent
17. Reports

Business expansion such as:
- billing
- agency/white-label
- public API
- MCP

comes later.

## DATA PRINCIPLES

Prefer existing tables and architecture when possible.

Do not create duplicate concepts when an existing model can be extended.

Maintain organization/workspace isolation.

Every workspace-scoped feature must verify access using the existing tenancy/access helpers.

Store raw AI responses when appropriate for audit/debugging, while avoiding secret leakage.

Make metrics reproducible from stored underlying data.

## TESTING

At minimum, after meaningful backend changes run:
- python syntax checks
- relevant unit/integration tests
- import checks
- targeted endpoint checks

Before declaring a feature complete:
- verify production service starts
- verify Gunicorn worker boots
- verify relevant endpoint returns expected status
- check recent logs
- inspect git diff

Never claim a feature works unless it was actually tested.

## UI PRINCIPLES

TrySearch should feel like a professional SaaS product.

Prefer:
- clear navigation
- responsive layouts
- useful empty states
- loading states
- actionable errors
- consistent terminology
- accessible controls
- charts/tables that expose the underlying evidence

Do not create fake metrics or placeholder analytics and present them as real data.

## WHEN STARTING A LARGE TASK

First:
1. Inspect the repository.
2. Inspect existing related routes/models/templates/tests.
3. Identify what already exists.
4. Produce a concise implementation plan.
5. Then implement incrementally.

Do not immediately rewrite large sections of the application.

## GIT

Review:
git status
git diff

before and after major changes.

Preserve unrelated local changes.

Never assume all local modifications are disposable.

## PRODUCTION DATABASE

Production PostgreSQL is:
database: trysearch

Do not point tests at production.

Use the existing test database/test configuration for tests.

## COMMUNICATION

When a task is large:
- state what you inspected
- state the implementation plan
- implement in milestones
- report tests/results
- explicitly mention anything not completed

Do not silently skip requirements.

## MAIN GOAL

Build TrySearch into a functional AI-search visibility/AEO platform.

The goal is a working product, not merely a collection of APIs.
