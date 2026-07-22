"""
Simple migration helper: copy users and contacts from a local SQLite searchable.db into a target database
specified by the DATABASE_URL environment variable (Postgres). Uses SQLAlchemy Core (no ORM) so it works
with the same table definitions used by the app.

Usage:
  DATABASE_URL="postgresql://user:pw@host:5432/dbname" python3 migrate_sqlite_to_postgres.py --sqlite-file searchable.db

If DATABASE_URL is not set the script will exit.

This script is intended for one-time migrations for small demo datasets. Back up your databases before running.
"""

import os
import argparse
import sys
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Text, DateTime, Boolean, select
from sqlalchemy import insert
from sqlalchemy.exc import SQLAlchemyError
import datetime


def get_tables(metadata):
    contacts = Table(
        "contacts",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(256)),
        Column("email", String(256)),
        Column("message", Text),
        Column("created_at", DateTime),
    )

    users = Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("username", String(80), unique=True, nullable=False),
        Column("password", String(256), nullable=False),
        Column("created_at", DateTime),
    )

    return contacts, users


def migrate(sqlite_url, target_url):
    print("Connecting to source (sqlite):", sqlite_url)
    src_engine = create_engine(sqlite_url)
    print("Connecting to target (DATABASE_URL):", target_url)
    dst_engine = create_engine(target_url)

    src_meta = MetaData()
    dst_meta = MetaData()

    # define table structures used by app
    src_contacts, src_users = get_tables(src_meta)
    dst_contacts, dst_users = get_tables(dst_meta)

    try:
        # reflect existing data from sqlite by reflecting tables if they exist
        src_meta.reflect(bind=src_engine)
    except Exception:
        # ignore, we'll still try to select if tables exist
        pass

    # ensure destination tables exist
    dst_meta.create_all(bind=dst_engine)

    with src_engine.connect() as src_conn, dst_engine.connect() as dst_conn:
        trans = dst_conn.begin()
        try:
            # migrate contacts if table exists in source
            if src_engine.dialect.has_table(src_engine.connect(), 'contacts'):
                print('Reading contacts from sqlite...')
                res = src_conn.execute(select(src_contacts))
                rows = res.fetchall()
                print(f'Found {len(rows)} contact rows')
                if rows:
                    for r in rows:
                        data = dict(r)
                        # Some sqlite rows may have created_at as text; normalize
                        if isinstance(data.get('created_at'), str):
                            try:
                                data['created_at'] = datetime.datetime.fromisoformat(data['created_at'])
                            except Exception:
                                data['created_at'] = datetime.datetime.utcnow()
                        dst_conn.execute(insert(dst_contacts).values(**data))

            else:
                print('No contacts table found in sqlite (skipping)')

            # migrate users if table exists in source
            if src_engine.dialect.has_table(src_engine.connect(), 'users'):
                print('Reading users from sqlite...')
                res = src_conn.execute(select(src_users))
                rows = res.fetchall()
                print(f'Found {len(rows)} user rows')
                if rows:
                    for r in rows:
                        data = dict(r)
                        if isinstance(data.get('created_at'), str):
                            try:
                                data['created_at'] = datetime.datetime.fromisoformat(data['created_at'])
                            except Exception:
                                data['created_at'] = datetime.datetime.utcnow()
                        dst_conn.execute(insert(dst_users).values(**data))
            else:
                print('No users table found in sqlite (skipping)')

            trans.commit()
            print('Migration finished successfully')
        except SQLAlchemyError as e:
            trans.rollback()
            print('Migration failed, rolled back. Error:', e)
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Migrate sqlite searchable.db into a DATABASE_URL target (Postgres)')
    parser.add_argument('--sqlite-file', default='searchable.db', help='Path to sqlite file (default: searchable.db)')
    args = parser.parse_args()

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print('ERROR: DATABASE_URL environment variable must be set to the target Postgres database URL')
        sys.exit(2)

    sqlite_path = args.sqlite_file
    if not os.path.exists(sqlite_path):
        print(f'ERROR: sqlite file not found: {sqlite_path}')
        sys.exit(2)

    sqlite_url = f'sqlite:///{os.path.abspath(sqlite_path)}'
    migrate(sqlite_url, database_url)
