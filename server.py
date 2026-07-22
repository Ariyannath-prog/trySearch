import os
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory, abort

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'searchable.db')

app = Flask(__name__, static_folder='.', static_url_path='')


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(BASE_DIR, exist_ok=True)
    with get_db_connection() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.commit()


@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')


@app.route('/<path:path>')
def static_files(path):
    filepath = os.path.join(BASE_DIR, path)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        return send_from_directory(BASE_DIR, path)
    abort(404)


@app.route('/api/contacts', methods=['GET', 'POST'])
def contacts():
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid JSON payload.'}), 400

        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip()
        message = (data.get('message') or '').strip()

        if not name or not email or not message:
            return jsonify({'error': 'Name, email, and message are required.'}), 400

        created_at = datetime.utcnow().isoformat() + 'Z'
        with get_db_connection() as conn:
            conn.execute(
                'INSERT INTO contacts (name, email, message, created_at) VALUES (?, ?, ?, ?)',
                (name, email, message, created_at),
            )
            conn.commit()

        return jsonify({'status': 'success', 'message': 'Contact request submitted.'}), 201

    with get_db_connection() as conn:
        rows = conn.execute(
            'SELECT id, name, email, message, created_at FROM contacts ORDER BY created_at DESC LIMIT 100'
        ).fetchall()
        contacts = [dict(row) for row in rows]
    return jsonify({'status': 'success', 'contacts': contacts})


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


@app.route('/admin/contacts')
def admin_contacts():
    with get_db_connection() as conn:
        rows = conn.execute(
            'SELECT id, name, email, message, created_at FROM contacts ORDER BY created_at DESC'
        ).fetchall()
        contacts = [dict(row) for row in rows]

    rows_html = ''.join(
        f"<tr><td>{c['id']}</td><td>{c['name']}</td><td>{c['email']}</td><td>{c['message']}</td><td>{c['created_at']}</td></tr>"
        for c in contacts
    )
    html = f"""
    <!DOCTYPE html>
    <html lang='en'>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1'>
        <title>Contact submissions</title>
        <style>
          body {{ font-family: system-ui, sans-serif; background: #0b1220; color: #eef3ff; margin: 0; padding: 2rem; }}
          table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
          th, td {{ border: 1px solid rgba(255,255,255,0.12); padding: 0.75rem 1rem; text-align: left; }}
          th {{ background: rgba(255,255,255,0.07); }}
          tr:nth-child(even) {{ background: rgba(255,255,255,0.03); }}
          h1 {{ margin: 0; font-size: 1.75rem; }}
          .note {{ color: #9cb2d3; margin-top: 0.5rem; }}
          a {{ color: #4fbafc; text-decoration: none; }}
        </style>
      </head>
      <body>
        <h1>Saved contact submissions</h1>
        <p class='note'>This page reads directly from the SQLite database file stored on the Render instance.</p>
        <p><a href='/'>Back to homepage</a></p>
        <table>
          <thead>
            <tr><th>ID</th><th>Name</th><th>Email</th><th>Message</th><th>Created at</th></tr>
          </thead>
          <tbody>
            {rows_html or '<tr><td colspan="5">No submissions yet.</td></tr>'}
          </tbody>
        </table>
      </body>
    </html>
    """
    return html


# Ensure the SQLite database exists before the app starts
init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
