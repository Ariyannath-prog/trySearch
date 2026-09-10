# PostgreSQL setup

trySearch requires a reachable PostgreSQL database. Supabase is optional; Neon,
Railway, Render Postgres, or another PostgreSQL provider works too.

## Vercel

Create a PostgreSQL database and copy its connection string. In Vercel, add these
Production environment variables:

```text
APP_ENV=production
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE
SECRET_KEY=<long-random-secret>
```

The repository's `vercel.json` runs `alembic upgrade head` during deployment, so
the database schema is created automatically before the function starts.

If the provider gives a connection string beginning with `postgres://`, it is also
accepted by the application.

## Local migration

To migrate manually instead of during the Vercel build:

```bash
DATABASE_URL="postgresql://..." APP_ENV=production alembic upgrade head
```

## Tests

Use a separate database, never the production database:

```bash
TEST_DATABASE_URL="postgresql://..." pytest
```