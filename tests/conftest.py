"""Test schema setup, recorded-fixture replay, and the no-network guard.

Application code no longer creates tables — Alembic owns the schema as of T2. The
tests run against in-memory SQLite, where replaying the migration chain for every
session would be slow and would exercise Alembic rather than the code under test,
so the schema is built straight from the models here.

This runs at import, not in a fixture, and that ordering matters: pytest imports
conftest before the test modules, and each test module calls `import server_pg` at
*its* import time, which builds the app and reads the database identity. The tables
have to exist before that happens.

`alembic upgrade head` from empty is verified separately, against staging Postgres.
"""

import json
import os
import socket

import pytest

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('SECRET_KEY', 'test-secret')
# Bind the engine to in-memory SQLite before anything imports app.db. The test
# modules set this to the same value; whichever runs first wins and they agree.
os.environ['DATABASE_URL'] = 'sqlite://'

from app import models  # noqa: E402,F401 - registers the tables on `metadata`
from app.db import engine, metadata  # noqa: E402

metadata.create_all(engine)


def _seed_engines():
    """Mirror the T12 migration's seed.

    The schema here is built from the models rather than by replaying migrations,
    so the seeded rows that ship inside a migration have to be repeated. Without
    this the engines table is empty and every scan correctly refuses to run.
    """
    from sqlalchemy import insert, select

    from app.models import engines as engines_table

    with engine.begin() as conn:
        exists = conn.execute(
            select(engines_table.c.id).where(engines_table.c.key == 'perplexity')
        ).scalar_one_or_none()
        if not exists:
            conn.execute(insert(engines_table).values(
                key='perplexity', display_name='Perplexity', source_type='api',
                adapter_version='2026.08.1', enabled=True,
            ))


_seed_engines()

FIXTURE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


class NetworkAccessDenied(RuntimeError):
    """Raised when a test tries to open a socket."""


@pytest.fixture(autouse=True)
def database_schema():
    """Guarantee the schema exists before each test, whatever ran before.

    metadata.create_all is idempotent. This is belt-and-braces after a
    tearDownClass calling engine.dispose() silently emptied the shared in-memory
    database for every test that sorted after it.
    """
    metadata.create_all(engine)
    _seed_engines()
    yield


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any test that attempts a real connection.

    CLAUDE.md forbids paid calls from tests, and SPRINT T3 asks for proof that the
    suite runs with no network at all. Enforcing it on every run is stronger than
    proving it once: a test that starts reaching the network fails immediately
    rather than passing slowly and quietly costing money.
    """
    def deny(*args, **kwargs):
        raise NetworkAccessDenied(
            'This test tried to open a network connection. Tests replay recorded '
            'fixtures from tests/fixtures/; re-record with scripts/record_fixture.py.'
        )

    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    yield


def load_fixture(engine_name, case):
    """Return a recorded provider response verbatim.

    Recorded by scripts/record_fixture.py. Never edit these by hand — when a
    provider changes its format, re-record.
    """
    path = os.path.join(FIXTURE_ROOT, engine_name, f'{case}.json')
    if not os.path.exists(path):
        raise AssertionError(
            f'Missing fixture {engine_name}/{case}.json. Record it with:\n'
            f'  python scripts/record_fixture.py {engine_name} {case.split("_")[0]} "<prompt>"'
        )
    with open(path) as handle:
        return json.load(handle)


@pytest.fixture
def provider_fixture():
    """Fixture-replay helper: provider_fixture('perplexity', 'search_basic')."""
    return load_fixture


def create_workspace(*, user_id, domain='example.com', brand_name='Example',
                     industry='Software', created_at=None):
    """Create an org, a membership for user_id, and a workspace inside it.

    T5 replaced `analytics_projects.user_id` with membership in the owning org, so
    a test that wants a reachable workspace has to create all three rows.
    """
    from datetime import datetime

    from sqlalchemy import insert

    from app.db import engine as _engine
    from app.models import memberships, organizations, workspaces

    now = created_at or datetime.utcnow()
    with _engine.begin() as conn:
        org_id = conn.execute(insert(organizations).values(
            name=f'Org for {brand_name}', created_at=now,
        )).inserted_primary_key[0]
        conn.execute(insert(memberships).values(
            org_id=org_id, user_id=user_id, role='owner',
        ))
        workspace_id = conn.execute(insert(workspaces).values(
            org_id=org_id, brand_name=brand_name, domains=[domain], geo='US',
            language='en', kind='project', status='active', created_at=now,
            domain=domain, website_url=f'https://{domain}/', industry=industry,
            updated_at=now,
        )).inserted_primary_key[0]
    return workspace_id


def set_extraction(answer_id, *, brand_mentioned, brand_cited, brand_rank=None,
                   version='test'):
    """Record the facts T9 moved out of analytics_provider_answers.

    Tests written before T9 asserted on brand_mentioned / brand_cited as columns on
    the answer row. Those live in the versioned extractions table now; this puts the
    same facts in their new home so the assertions keep their original meaning.
    """
    from datetime import datetime

    from sqlalchemy import insert, update

    from app.db import engine as _engine
    from app.models import extractions

    with _engine.begin() as conn:
        conn.execute(update(extractions)
                     .where((extractions.c.answer_id == answer_id)
                            & (extractions.c.is_current))
                     .values(is_current=False))
        return conn.execute(insert(extractions).values(
            answer_id=answer_id, extractor_version=version, is_current=True,
            brand_mentioned=brand_mentioned, brand_rank=brand_rank,
            brand_cited=brand_cited, created_at=datetime.utcnow(),
        )).inserted_primary_key[0]
