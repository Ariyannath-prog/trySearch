"""Environment-derived settings and tuning constants."""

import os

# The repo root, not app/. The page routes serve index.html, analytics.html and the
# rest of the static frontend from here, and SQLITE_PATH resolves against it, so this
# has to stay one level above this package.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
SQLITE_PATH = os.path.join(BASE_DIR, 'searchable.db')

APP_ENV = os.environ.get('APP_ENV', 'development').lower()
IS_PRODUCTION = APP_ENV == 'production'


AUDIT_USER_AGENT = os.environ.get('AUDIT_USER_AGENT', 'trySearch-Audit/2.0 (+https://trysearch.example/audit)')
AUDIT_MAX_PAGES = max(1, min(int(os.environ.get('AUDIT_MAX_PAGES', '12')), 50))
AUDIT_PAGE_BYTES = max(100_000, min(int(os.environ.get('AUDIT_PAGE_BYTES', '800000')), 2_000_000))
AUDIT_SITEMAP_BYTES = max(200_000, min(int(os.environ.get('AUDIT_SITEMAP_BYTES', '2000000')), 5_000_000))
AUDIT_REQUEST_DELAY_SECONDS = max(0.0, min(float(os.environ.get('AUDIT_REQUEST_DELAY_SECONDS', '0.05')), 2.0))
ANALYTICS_MAX_TRACKED_PROMPTS = max(1, min(int(os.environ.get('ANALYTICS_MAX_TRACKED_PROMPTS', '100')), 500))
PERPLEXITY_MAX_PROMPTS_PER_SCAN = max(1, min(int(os.environ.get('PERPLEXITY_MAX_PROMPTS_PER_SCAN', '25')), 100))
RAG_DOCUMENT_MAX_CHARS = max(5_000, min(int(os.environ.get('RAG_DOCUMENT_MAX_CHARS', '60000')), 200_000))
RAG_CHUNK_WORDS = max(80, min(int(os.environ.get('RAG_CHUNK_WORDS', '180')), 400))
RAG_CHUNK_OVERLAP_WORDS = max(0, min(int(os.environ.get('RAG_CHUNK_OVERLAP_WORDS', '30')), RAG_CHUNK_WORDS // 2))
RAG_MAX_CHUNKS_PER_PAGE = max(1, min(int(os.environ.get('RAG_MAX_CHUNKS_PER_PAGE', '40')), 100))
RAG_DEFAULT_TOP_K = max(1, min(int(os.environ.get('RAG_DEFAULT_TOP_K', '6')), 12))
RAG_MAX_CONTEXT_CHARS = max(4_000, min(int(os.environ.get('RAG_MAX_CONTEXT_CHARS', '16000')), 40_000))
