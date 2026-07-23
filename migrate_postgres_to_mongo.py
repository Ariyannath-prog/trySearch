"""
Migration helper: copy users and contacts from Postgres into MongoDB.
Useful for migrating existing data from server_pg.py to server_mongo.py

Usage:
  MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/dbname?retryWrites=true" \\
  DATABASE_URL="postgresql://..." \\
  python3 migrate_postgres_to_mongo.py

Make a backup before running.
"""

import os
import sys
from datetime import datetime
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Text, DateTime, select
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

MONGODB_URI = os.environ.get('MONGODB_URI')
DATABASE_URL = os.environ.get('DATABASE_URL')

if not MONGODB_URI:
    print('ERROR: MONGODB_URI environment variable must be set')
    sys.exit(1)

if not DATABASE_URL:
    print('ERROR: DATABASE_URL environment variable must be set')
    sys.exit(1)

print(f'Source (Postgres): {DATABASE_URL}')
print(f'Target (MongoDB): {MONGODB_URI}')

# Connect to Postgres
engine = create_engine(DATABASE_URL, future=True)
metadata = MetaData()

contacts = Table(
    'contacts',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('name', String(255), nullable=False),
    Column('email', String(255), nullable=False),
    Column('message', Text, nullable=False),
    Column('created_at', DateTime, nullable=False),
)

users = Table(
    'users',
    metadata,
    Column('id', Integer, primary_key=True),
    Column('username', String(150), nullable=False, unique=True),
    Column('email', String(255), nullable=False, unique=True),
    Column('password_hash', String(255), nullable=False),
    Column('created_at', DateTime, nullable=False),
)

# Connect to MongoDB
mongo_client = MongoClient(MONGODB_URI)
mongo_db = mongo_client['trysearch']
contacts_col = mongo_db['contacts']
users_col = mongo_db['users']

# Ensure indexes
users_col.create_index([('username', 1)], unique=True)
users_col.create_index([('email', 1)], unique=True)

with engine.connect() as conn:
    trans = conn.begin()
    try:
        # Migrate contacts
        if engine.dialect.has_table(engine.connect(), 'contacts'):
            print('Migrating contacts...')
            stmt = select(contacts)
            result = conn.execute(stmt)
            rows = result.fetchall()
            print(f'Found {len(rows)} contacts')
            
            for row in rows:
                doc = dict(row)
                doc['created_at'] = doc.get('created_at') or datetime.utcnow()
                # MongoDB will generate _id, so we don't set it
                doc.pop('id', None)  # remove Postgres id
                try:
                    contacts_col.insert_one(doc)
                except DuplicateKeyError:
                    print(f"  Skipping duplicate contact: {doc.get('email')}")
            print(f'Migrated contacts to MongoDB')
        else:
            print('No contacts table in Postgres (skipping)')

        # Migrate users
        if engine.dialect.has_table(engine.connect(), 'users'):
            print('Migrating users...')
            stmt = select(users)
            result = conn.execute(stmt)
            rows = result.fetchall()
            print(f'Found {len(rows)} users')
            
            for row in rows:
                doc = dict(row)
                doc['created_at'] = doc.get('created_at') or datetime.utcnow()
                # MongoDB will generate _id, so we don't set it
                doc.pop('id', None)  # remove Postgres id
                try:
                    users_col.insert_one(doc)
                except DuplicateKeyError:
                    print(f"  Skipping duplicate user: {doc.get('username')}")
            print(f'Migrated users to MongoDB')
        else:
            print('No users table in Postgres (skipping)')

        trans.commit()
        print('\nMigration completed successfully!')
        
    except Exception as e:
        trans.rollback()
        print(f'Migration failed: {e}')
        raise

mongo_client.close()
