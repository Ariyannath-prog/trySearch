"""Environment-derived settings and tuning constants."""

import os

# The repo root, not app/. The page routes serve index.html, analytics.html and the
# rest of the static frontend from here, so this has to stay one level above this
# package.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

APP_ENV = os.environ.get('APP_ENV', 'development').lower()
IS_PRODUCTION = APP_ENV == 'production'


def _bounded_int(name, default, minimum, maximum):
    """Read an optional integer setting without making startup fragile.

    Hosting dashboards commonly create an environment variable with an empty
    value. Treat an empty or malformed optional value exactly like an omitted
    value, then retain the existing lower/upper safety bounds.
    """
    raw_value = os.environ.get(name)
    try:
        value = int(raw_value) if raw_value and raw_value.strip() else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _bounded_float(name, default, minimum, maximum):
    """Read an optional decimal setting, falling back safely when it is blank."""
    raw_value = os.environ.get(name)
    try:
        value = float(raw_value) if raw_value and raw_value.strip() else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


AUDIT_USER_AGENT = os.environ.get('AUDIT_USER_AGENT', 'trySearch-Audit/2.0 (+https://trysearch.example/audit)')
AUDIT_MAX_PAGES = _bounded_int('AUDIT_MAX_PAGES', 12, 1, 50)
AUDIT_PAGE_BYTES = _bounded_int('AUDIT_PAGE_BYTES', 800_000, 100_000, 2_000_000)
AUDIT_SITEMAP_BYTES = _bounded_int('AUDIT_SITEMAP_BYTES', 2_000_000, 200_000, 5_000_000)
AUDIT_REQUEST_DELAY_SECONDS = _bounded_float('AUDIT_REQUEST_DELAY_SECONDS', 0.05, 0.0, 2.0)
ANALYTICS_MAX_TRACKED_PROMPTS = _bounded_int('ANALYTICS_MAX_TRACKED_PROMPTS', 100, 1, 500)
PERPLEXITY_MAX_PROMPTS_PER_SCAN = _bounded_int('PERPLEXITY_MAX_PROMPTS_PER_SCAN', 25, 1, 100)
RAG_DOCUMENT_MAX_CHARS = _bounded_int('RAG_DOCUMENT_MAX_CHARS', 60_000, 5_000, 200_000)
RAG_CHUNK_WORDS = _bounded_int('RAG_CHUNK_WORDS', 180, 80, 400)
RAG_CHUNK_OVERLAP_WORDS = _bounded_int('RAG_CHUNK_OVERLAP_WORDS', 30, 0, RAG_CHUNK_WORDS // 2)
RAG_MAX_CHUNKS_PER_PAGE = _bounded_int('RAG_MAX_CHUNKS_PER_PAGE', 40, 1, 100)
RAG_DEFAULT_TOP_K = _bounded_int('RAG_DEFAULT_TOP_K', 6, 1, 12)
RAG_MAX_CONTEXT_CHARS = _bounded_int('RAG_MAX_CONTEXT_CHARS', 16_000, 4_000, 40_000)
