"""Engine, metadata and startup identity check.

Schema is owned by Alembic (migrations/). No DDL is issued from application code.
"""

import os
import uuid

from sqlalchemy import MetaData, create_engine, insert, select
from sqlalchemy.exc import IntegrityError



def normalize_database_url(database_url):
    """Use SQLAlchemy's psycopg 3 dialect with common Postgres URL formats."""
    if database_url.startswith('postgres://'):
        return database_url.replace('postgres://', 'postgresql+psycopg://', 1)
    if database_url.startswith('postgresql://'):
        return database_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    return database_url


# `DATABASE_URL` is the portable application setting.  The Vercel Neon
# integration in this project also supplies its managed URL as
# `DATABASE2_DATABASE_URL`.  A previous manual setting contains the literal
# Supabase placeholder `REGION`, which can never resolve; prefer the connected
# Neon URL only in that broken-placeholder case so production can boot while
# retaining normal DATABASE_URL behaviour everywhere else.
database_url = os.environ.get('DATABASE_URL')
if database_url and 'REGION.pooler.supabase.com' in database_url:
    database_url = os.environ.get('DATABASE2_DATABASE_URL') or database_url
if not database_url:
    raise RuntimeError('DATABASE_URL must be set to a PostgreSQL connection URL.')

DB_URL = normalize_database_url(database_url)
if not DB_URL.startswith('postgresql'):
    raise RuntimeError('DATABASE_URL must use a PostgreSQL connection URL.')

# Keep a small, resilient connection pool for managed Postgres.
engine_options = {
    'future': True,
    'pool_pre_ping': True,
    'pool_size': 5,
    'max_overflow': 10,
    'pool_recycle': 1800,
}
engine = create_engine(DB_URL, **engine_options)
metadata = MetaData()


def get_database_identity():
    """Return the database's permanent application identity, creating it once."""
    from app.models import app_metadata
    with engine.connect() as conn:
        row = conn.execute(
            select(app_metadata.c.value).where(app_metadata.c.key == 'database_identity')
        ).scalar_one_or_none()
    if row:
        return row

    database_identity = str(uuid.uuid4())
    try:
        with engine.begin() as conn:
            conn.execute(insert(app_metadata).values(
                key='database_identity', value=database_identity
            ))
        return database_identity
    except IntegrityError:
        # Another gunicorn worker initialized the row at the same time. Query
        # in a new transaction because PostgreSQL marks the failed one aborted.
        with engine.connect() as conn:
            return conn.execute(
                select(app_metadata.c.value).where(app_metadata.c.key == 'database_identity')
            ).scalar_one()



# Set by bootstrap_database(). Read it as db.DATABASE_IDENTITY rather than importing
# the name, so callers see the value bound at startup rather than this placeholder.
DATABASE_IDENTITY = None


def bootstrap_database():
    """Pin the database identity at startup.

    Schema creation lives in migrations/ as of T2: run `alembic upgrade head`
    before booting against an empty database. This function no longer issues DDL.
    """
    global DATABASE_IDENTITY
    DATABASE_IDENTITY = get_database_identity()
    expected_database_identity = os.environ.get('DATABASE_INSTANCE_ID')
    if expected_database_identity and expected_database_identity != DATABASE_IDENTITY:
        raise RuntimeError(
            'DATABASE_INSTANCE_ID does not match the connected database. Refusing to start '
            'against an unexpected database.'
        )
    return DATABASE_IDENTITY
