# trySearch Replica with Backend

This repository contains a static frontend replica of the trySearch homepage plus a simple Flask backend.

## Local setup

1. Install dependencies:

```bash
cd /Users/ariyannath/Desktop/trySearch
python3 -m pip install -r requirements.txt
```

2. Start the app (SQLite is used only for local development when `DATABASE_URL` is not set):

```bash
python3 server_pg.py
```

3. Open `http://127.0.0.1:8000` in your browser.

## Database configuration

The production backend is **PostgreSQL**, using `server_pg.py`. It keeps all data in a managed database and uses a resilient connection pool. SQLite remains a zero-configuration fallback for local development only.

For production, set all of these environment variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Managed PostgreSQL connection string |
| `SECRET_KEY` | Long, random Flask session-signing secret |
| `APP_ENV` | Set to `production` |

`server_pg.py` refuses to start in production if `DATABASE_URL` or `SECRET_KEY` is missing. The included Render blueprint sets these automatically without storing database credentials in the repository.

### API Endpoints

- `GET /api/contacts` — fetch saved contact submissions
- `POST /api/contacts` — save a contact submission
- `GET /api/health` — check database connection status
- `POST /api/register` — create a user account
- `POST /api/login` — log in with username and password
- `POST /api/logout` — log out
- `GET /api/me` — get current login status
- `GET /admin/contacts` — view all contacts (requires login)

## Production deployment with Render PostgreSQL

For a new deployment, connect this repository to Render as a Blueprint. The provided `render.yaml` creates a managed PostgreSQL database, injects its connection string as `DATABASE_URL`, generates `SECRET_KEY`, and runs `server_pg:app`.

For an existing live SQLite deployment, migrate before you switch the web service:

1. Back up `searchable.db`.
2. In Render, create a PostgreSQL database named `trysearch-postgres` in the same region as the web service.
3. Temporarily allow your development machine to connect, then copy the database's **external** connection URL.
4. From this project directory, run:

```bash
DATABASE_URL="postgresql://..." python3 migrate_sqlite_to_postgres.py --sqlite-file searchable.db
```

5. Confirm the migration completed, then deploy this repository with the updated `render.yaml`.
6. Check `https://your-service.onrender.com/api/health`; it should return `{"status":"ok","db":"postgresql"}`.
7. Remove the temporary external IP allow-list entry so the deployed app uses only Render's private network.

Do not commit `DATABASE_URL` or `SECRET_KEY` to Git. Render's `fromDatabase` reference and generated secret handle those values at deploy time.

## Authentication

The site includes username/email and password authentication backed by the database.

- **Register**: `/register`
- **Login**: `/login`
- Sessions use signed cookies (Flask). When "Remember me" is checked, the session lasts 30 days.
- Set `SECRET_KEY` environment variable to a strong random string for secure cookies.

## Files

- `server.py` — SQLite-only local development server
- `server_pg.py` — PostgreSQL production server, with a SQLite local fallback
- `migrate_sqlite_to_postgres.py` — one-time migration script from SQLite → PostgreSQL
- `Procfile` and `render.yaml` — start the PostgreSQL production server
- `requirements.txt` — Python dependencies (Flask, gunicorn, pymongo, SQLAlchemy, psycopg)

## Troubleshooting

**Database connection error**: Verify `DATABASE_URL` points to PostgreSQL and, on Render, that the app and database are in the same region.

**Data not persisting**: Make sure the deployed start command is `gunicorn server_pg:app`, not `server.py`.

**Migration cannot connect**: Temporarily add your current IP address to the database external access allow list, migrate, then remove it again.

## Support

For issues or questions, check the Render and Flask documentation.
