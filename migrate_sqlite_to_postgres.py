"""Copy the current SQLite users and contacts tables into PostgreSQL.

Usage:
  DATABASE_URL="postgresql://user:password@host:5432/database" \
    python3 migrate_sqlite_to_postgres.py --sqlite-file searchable.db

Run this once after backing up the SQLite file and before switching the deployed
application to PostgreSQL. Existing user IDs are intentionally not copied: this
application has no foreign keys that depend on them, and PostgreSQL allocates
correct IDs for future registrations.
"""

import argparse
import os
import sys
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, inspect, insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


def normalize_database_url(database_url):
    if database_url.startswith('postgres://'):
        return database_url.replace('postgres://', 'postgresql+psycopg://', 1)
    if database_url.startswith('postgresql://'):
        return database_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    return database_url


def destination_tables(metadata):
    contacts = Table(
        'contacts', metadata,
        Column('id', Integer, primary_key=True),
        Column('name', String(255), nullable=False),
        Column('email', String(255), nullable=False),
        Column('message', Text, nullable=False),
        Column('created_at', DateTime, nullable=False),
    )
    users = Table(
        'users', metadata,
        Column('id', Integer, primary_key=True),
        Column('username', String(150), nullable=False, unique=True),
        Column('email', String(255), nullable=False, unique=True),
        Column('password_hash', String(255), nullable=False),
        Column('created_at', DateTime, nullable=False),
    )
    return contacts, users


def normalize_datetime(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.utcnow()


def migrate(sqlite_url, target_url):
    source_engine = create_engine(sqlite_url, future=True)
    target_engine = create_engine(normalize_database_url(target_url), future=True, pool_pre_ping=True)
    source_metadata = MetaData()
    target_metadata = MetaData()
    destination_contacts, destination_users = destination_tables(target_metadata)
    target_metadata.create_all(target_engine)

    source_inspector = inspect(source_engine)
    required_tables = {'contacts', 'users'}
    missing_tables = required_tables - set(source_inspector.get_table_names())
    if missing_tables:
        raise RuntimeError(f'Source SQLite database is missing table(s): {", ".join(sorted(missing_tables))}')

    source_contacts = Table('contacts', source_metadata, autoload_with=source_engine)
    source_users = Table('users', source_metadata, autoload_with=source_engine)
    required_user_columns = {'username', 'email', 'password_hash', 'created_at'}
    missing_columns = required_user_columns - set(source_users.c.keys())
    if missing_columns:
        raise RuntimeError(
            'Source users table does not match the current application schema; missing: '
            + ', '.join(sorted(missing_columns))
        )

    with source_engine.connect() as source_conn, target_engine.begin() as target_conn:
        existing_usernames = set(target_conn.execute(select(destination_users.c.username)).scalars())
        existing_emails = set(target_conn.execute(select(destination_users.c.email)).scalars())
        users_migrated = 0
        users_skipped = 0

        for row in source_conn.execute(select(source_users)).mappings():
            user = dict(row)
            if user['username'] in existing_usernames or user['email'] in existing_emails:
                users_skipped += 1
                continue
            target_conn.execute(insert(destination_users).values(
                username=user['username'],
                email=user['email'],
                password_hash=user['password_hash'],
                created_at=normalize_datetime(user.get('created_at')),
            ))
            existing_usernames.add(user['username'])
            existing_emails.add(user['email'])
            users_migrated += 1

        contacts_migrated = 0
        for row in source_conn.execute(select(source_contacts)).mappings():
            contact = dict(row)
            target_conn.execute(insert(destination_contacts).values(
                name=contact['name'],
                email=contact['email'],
                message=contact['message'],
                created_at=normalize_datetime(contact.get('created_at')),
            ))
            contacts_migrated += 1

    print(f'Migration complete: {users_migrated} users migrated, {users_skipped} users skipped, {contacts_migrated} contacts migrated.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Migrate searchable.db from SQLite to PostgreSQL.')
    parser.add_argument('--sqlite-file', default='searchable.db', help='Path to the SQLite file (default: searchable.db)')
    args = parser.parse_args()

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        sys.exit('ERROR: Set DATABASE_URL to the target PostgreSQL connection URL.')
    if not os.path.exists(args.sqlite_file):
        sys.exit(f'ERROR: SQLite file not found: {args.sqlite_file}')

    try:
        migrate(f'sqlite:///{os.path.abspath(args.sqlite_file)}', database_url)
    except (RuntimeError, IntegrityError, SQLAlchemyError) as error:
        sys.exit(f'Migration failed: {error}')
