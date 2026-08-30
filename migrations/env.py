"""Alembic environment.

The database URL comes from DATABASE_URL, never from alembic.ini, so a migration
can only ever reach the database the environment already points at. There is no
connection string committed to the repo.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def _load_dotenv():
    """Populate os.environ from .env so `alembic upgrade head` works locally.

    Render injects real environment variables, so this is a no-op there. Kept here
    rather than in app/config.py to avoid changing how the application itself
    resolves configuration. Existing variables always win.
    """
    path = os.path.join(REPO_ROOT, '.env')
    if not os.path.exists(path):
        return
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

from app.db import DB_URL, metadata  # noqa: E402
from app import models  # noqa: E402,F401 - registers every table on `metadata`

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# app.db already applied the psycopg-3 dialect fix and the SQLite fallback.
config.set_main_option('sqlalchemy.url', DB_URL.replace('%', '%%'))

target_metadata = metadata


def run_migrations_offline():
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite cannot ALTER a column in place; batch mode rewrites the table.
            render_as_batch=connection.dialect.name == 'sqlite',
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
