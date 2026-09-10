# Supabase setup

trySearch uses PostgreSQL through Supabase. SQLite is not supported.

## 1. Create the project

1. Open [supabase.com](https://supabase.com) and create a project.
2. Set a strong database password and wait for the database to finish provisioning.
3. In Supabase, open **Connect** and copy the **Session pooler** URI for application traffic.
4. Replace the password placeholder and keep the URI private.

The URI should look like this:

```text
postgresql://postgres.[project-ref]:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
```

## 2. Run the schema migration

From the repository root, run the migration against the new project:

```bash
DATABASE_URL="postgresql://..." APP_ENV=production alembic upgrade head
```

This creates the complete application schema. Do not use `create_all` against the deployed database.

## 3. Configure Vercel

In **Vercel -> Project -> Settings -> Environment Variables**, add these for **Production**:

```text
APP_ENV=production
DATABASE_URL=postgresql://...
SECRET_KEY=<long-random-secret>
```

```bash
openssl rand -base64 48
```

Redeploy after saving the variables. The `/api/health` endpoint should return `status: ok`.

## 4. Run tests

Use a separate Supabase project or database. Never run tests against production:

```bash
TEST_DATABASE_URL="postgresql://..." pytest
```

The test fixture creates tables in that dedicated database and seeds the test engine row.