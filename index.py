"""Vercel's root Flask entrypoint.

Vercel detects a top-level ``index.py`` Flask application and dispatches the
original request path to it. Keeping the entrypoint at the project root avoids
rewriting every request to ``/api``, which discarded the requested path before
Flask could match it.
"""

from server_pg import app

__all__ = ["app"]
