# Searchable Replica with Backend

This repository contains a static frontend replica of the Searchable homepage plus a simple Flask backend using SQLite.

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

- Render: create a new web service using this repo and set the build command to `pip install -r requirements.txt` and start command to `gunicorn server:app`.
- Railway: connect the repo and use the default Python deployment.
- Heroku: add `Procfile` and deploy the app.

A `render.yaml` file is included so Render can create the service settings automatically when you connect this repository.

### Viewing saved submissions

- JSON API: `https://<your-render-url>/api/contacts`
- Browser view: `https://<your-render-url>/admin/contacts`

### Important

SQLite is suitable for development and small demos, but for production you should use a hosted database service if you need persistence across deploys, scaling, or multiple instances.
