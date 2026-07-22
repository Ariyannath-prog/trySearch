# trySearch Replica with Backend

This repository contains a static frontend replica of the trySearch homepage plus a simple Flask backend using SQLite.

## Local setup

1. Install dependencies:

```bash
cd /Users/ariyannath/Desktop/trySearch
python3 -m pip install -r requirements.txt
```

2. Start the app:

```bash
python3 server.py
```

3. Open `http://127.0.0.1:8000` in your browser.

## Backend

- `server.py` serves the frontend and provides the API endpoints:
  - `GET /api/contacts` — fetch saved contact submissions
  - `POST /api/contacts` — save a contact submission to SQLite
- `searchable.db` is created automatically when the app first runs.

## Deployment

GitHub Pages can host only static files and cannot run the Flask backend.

To run the full website online with the backend and SQLite database, deploy the repository to a web host that supports Python server apps, such as Render, Railway, Fly, or Heroku.

### Example hosting options

- Render: create a new web service using this repo and set the build command to `pip install -r requirements.txt` and start command to `gunicorn server_pg:app`.
- Railway: connect the repo and use the default Python deployment.
- Heroku: add `Procfile` and deploy the app.

A `render.yaml` file is included so Render can create the service settings automatically when you connect this repository.

### Viewing saved submissions

- JSON API: `https://<your-render-url>/api/contacts`
- Browser view (admin): `https://<your-render-url>/admin/contacts` (requires login)

### Authentication

The site now includes a simple username/email password system backed by a database. By default the app will use the `DATABASE_URL` environment variable (Postgres) if provided; otherwise it falls back to the local SQLite file for development.

- Register: `https://<your-render-url>/register`
- Login: `https://<your-render-url>/login`
- API endpoints:
  - `POST /api/register` — JSON {username,email,password}
  - `POST /api/login` — JSON {username,password,remember}
  - `POST /api/logout`
  - `GET /api/me` — returns login state

Sessions are stored in a signed cookie (Flask session). When 'Remember me' is checked during login, the session is made permanent for 30 days.

### Using Postgres in production

- Set `DATABASE_URL` in your Render (or other host) environment to a Postgres connection string, e.g. `postgres://username:password@host:5432/dbname`.
- Set `SECRET_KEY` to a strong random string in the environment so session cookies are secure.
- The app will automatically create the required tables on first run when connected to Postgres.

SQLite is suitable for development and small demos, but for production you should use a hosted Postgres (or another managed database) for persistence across deploys and scaling.
