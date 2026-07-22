# Searchable Replica

A simple static replica of the Searchable homepage built with HTML, CSS, and JavaScript.

## Run locally

Install dependencies:

```bash
cd /Users/ariyannath/Desktop/trySearch
python3 -m pip install -r requirements.txt
```

Start the backend server:

```bash
python3 server.py
```

Then open `http://127.0.0.1:8000` in your browser.

## Backend

This replica now includes a simple Flask backend using SQLite. The contact form stores submissions in `searchable.db` and exposes the API endpoint `POST /api/contacts`.

## Files

- `index.html` — homepage markup
- `styles.css` — site styling and responsive layout
- `script.js` — mobile menu toggle behavior
