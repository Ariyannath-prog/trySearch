"""
MongoDB Atlas connection helpers.

Prefer split env vars on Render (avoids copy-paste errors in URIs):
  MONGODB_USER, MONGODB_PASSWORD, MONGODB_HOST, MONGODB_DB_NAME

Or set a single MONGODB_URI (normalized before use).
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote_plus, urlparse

DEFAULT_DB_NAME = 'trysearch'
DEFAULT_APP_NAME = 'trysearch'

# Common hostname / username mistakes seen in env configs
_HOST_REWRITES = {
    'trysearch.tqp@qa.mongodb.net': 'trysearch.tpq8vag.mongodb.net',
    'trysearch.tqp8qa.mongodb.net': 'trysearch.tpq8vag.mongodb.net',
    'trysearch.tqpqa.mongodb.net': 'trysearch.tpq8vag.mongodb.net',
}


def _database_from_uri(uri: str) -> str | None:
    """Return database name from mongodb URI path, if any."""
    # urlparse needs a scheme it understands
    for prefix in ('mongodb+srv://', 'mongodb://'):
        if uri.startswith(prefix):
            rest = uri[len(prefix) :]
            break
    else:
        return None
    if '/' not in rest:
        return None
    path = rest.split('/', 1)[1]
    name = path.split('?')[0].strip('/').split('/')[0]
    return name or None


def _build_srv_uri(
    user: str,
    password: str,
    host: str,
    db_name: str,
    app_name: str,
) -> str:
    host = host.strip()
    host = host.removeprefix('mongodb+srv://').removeprefix('mongodb://')
    host = host.split('/')[0].split('?')[0]
    query = f'retryWrites=true&w=majority&appName={quote_plus(app_name)}'
    return (
        f'mongodb+srv://{quote_plus(user)}:{quote_plus(password)}'
        f'@{host}/{db_name}?{query}'
    )


def normalize_mongodb_uri(uri: str) -> str:
    uri = uri.strip()
    uri = uri.replace('nathaniyan97_db_user', 'nathariyan97_db_user')
    for wrong, right in _HOST_REWRITES.items():
        uri = uri.replace(wrong, right)

    # Broken paste: mongodb+srv://user:pass@trysearch.tqp@qa.mongodb.net/...
    if uri.count('@') > 1:
        override_host = os.environ.get('MONGODB_HOST', '').strip()
        host = override_host or 'trysearch.tpq8vag.mongodb.net'
        match = re.match(r'mongodb\+srv://([^:]+):([^@]+)@', uri)
        db_name = _database_from_uri(uri) or os.environ.get('MONGODB_DB_NAME', DEFAULT_DB_NAME)
        app_name = os.environ.get('MONGODB_APP_NAME', DEFAULT_APP_NAME)
        if match:
            user, password = match.group(1), match.group(2)
            return _build_srv_uri(user, password, host, db_name, app_name)

    if 'retryWrites=' not in uri:
        uri += ('&' if '?' in uri else '?') + 'retryWrites=true&w=majority'
    return uri


def resolve_mongodb_settings() -> tuple[str, str]:
    """
    Returns (connection_uri, database_name).
    """
    db_name = (os.environ.get('MONGODB_DB_NAME') or DEFAULT_DB_NAME).strip() or DEFAULT_DB_NAME
    app_name = (os.environ.get('MONGODB_APP_NAME') or DEFAULT_APP_NAME).strip() or DEFAULT_APP_NAME

    host = os.environ.get('MONGODB_HOST', '').strip()
    user = os.environ.get('MONGODB_USER', '').strip()
    password = os.environ.get('MONGODB_PASSWORD', '')

    if host and user and password:
        uri = _build_srv_uri(user, password, host, db_name, app_name)
        return uri, db_name

    raw_uri = os.environ.get('MONGODB_URI', '').strip()
    if not raw_uri:
        raise ValueError(
            'MongoDB is not configured. Set MONGODB_URI, or set '
            'MONGODB_HOST, MONGODB_USER, and MONGODB_PASSWORD.'
        )

    uri = normalize_mongodb_uri(raw_uri)
    uri_db = _database_from_uri(uri)
    if uri_db:
        db_name = uri_db
    return uri, db_name


def create_mongo_client():
    from pymongo import MongoClient

    uri, _db_name = resolve_mongodb_settings()
    return MongoClient(
        uri,
        serverSelectionTimeoutMS=20000,
        connectTimeoutMS=20000,
        tls=True,
    )


def get_database(client):
    _, db_name = resolve_mongodb_settings()
    return client[db_name]


def safe_uri_for_logs(uri: str) -> str:
    """Hide password in connection string for logging."""
    return re.sub(r':([^:@/]+)@', ':****@', uri)
