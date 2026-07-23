import os
from datetime import datetime, timedelta

from flask import Flask, jsonify, request, send_from_directory, abort, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Integer,
    String,
    Text,
    DateTime,
    select,
    insert,
    desc,
)
from sqlalchemy.exc import IntegrityError

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SQLITE_PATH = os.path.join(BASE_DIR, 'searchable.db')

# Use DATABASE_URL env var for hosted Postgres, otherwise fall back to local sqlite
DB_URL = os.environ.get('DATABASE_URL') or f'sqlite:///{SQLITE_PATH}'

# Create engine
engine = create_engine(DB_URL, future=True)
metadata = MetaData()

# Table definitions (SQLAlchemy Core) - compatible with Postgres and SQLite
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

# Create tables if they don't exist
metadata.create_all(engine)

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
app.permanent_session_lifetime = timedelta(days=30)


def to_iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat() + 'Z'


def row_to_dict(row):
    d = dict(row)
    if 'created_at' in d and d['created_at'] is not None:
        d['created_at'] = to_iso(d['created_at'])
    return d


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
def contacts_endpoint():
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'Invalid JSON payload.'}), 400

        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip()
        message = (data.get('message') or '').strip()

        if not name or not email or not message:
            return jsonify({'error': 'Name, email, and message are required.'}), 400

        created_at = datetime.utcnow()
        with engine.begin() as conn:
            conn.execute(
                insert(contacts).values(name=name, email=email, message=message, created_at=created_at)
            )
        return jsonify({'status': 'success', 'message': 'Contact request submitted.'}), 201

    # GET
    with engine.connect() as conn:
        stmt = select(contacts.c.id, contacts.c.name, contacts.c.email, contacts.c.message, contacts.c.created_at).order_by(desc(contacts.c.created_at)).limit(100)
        result = conn.execute(stmt)
        rows = [row_to_dict(r) for r in result.mappings().all()]
    return jsonify({'status': 'success', 'contacts': rows})


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'db': DB_URL.split('://')[0]})


# Authentication endpoints
@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid payload'}), 400
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip()
    password = (data.get('password') or '').strip()

    if not username or not email or not password:
        return jsonify({'error': 'username, email and password required'}), 400

    password_hash = generate_password_hash(password)
    created_at = datetime.utcnow()
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(users).values(username=username, email=email, password_hash=password_hash, created_at=created_at)
            )
    except IntegrityError:
        return jsonify({'error': 'User with that username or email already exists.'}), 400

    return jsonify({'status': 'success', 'message': 'User registered.'}), 201


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid payload'}), 400
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    remember = bool(data.get('remember'))

    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400

    with engine.connect() as conn:
        stmt = select(users.c.id, users.c.username, users.c.password_hash).where((users.c.username == username) | (users.c.email == username)).limit(1)
        row = conn.execute(stmt).mappings().first()
        if not row:
            return jsonify({'error': 'Invalid credentials'}), 401
        user = dict(row)
        if not check_password_hash(user['password_hash'], password):
            return jsonify({'error': 'Invalid credentials'}), 401

    # login success
    session.clear()
    session['user_id'] = user['id']
    session['username'] = user['username']
    session.permanent = remember
    return jsonify({'status': 'success', 'message': 'Logged in', 'username': user['username']})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'status': 'success', 'message': 'Logged out'})


@app.route('/api/me', methods=['GET'])
def api_me():
    user_id = session.get('user_id')
    if user_id:
        with engine.connect() as conn:
            stmt = select(users.c.id, users.c.username, users.c.email, users.c.created_at).where(
                users.c.id == user_id
            ).limit(1)
            row = conn.execute(stmt).mappings().first()
        if row:
            return jsonify({'logged_in': True, 'user': row_to_dict(row)})
        session.clear()
    return jsonify({'logged_in': False})


@app.route('/profile')
def profile_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'profile.html')


@app.route('/login')
def login_page():
    # simple HTML page that posts to /api/login via fetch
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Login</title>
        <style>body{font-family:system-ui, sans-serif;padding:2rem;background:#0b1220;color:#eef3ff}label{display:block;margin:0.5rem 0}input{padding:0.6rem;width:100%;max-width:320px;border-radius:8px;border:1px solid #333;background:#071018;color:#eef3ff}button{margin-top:1rem;padding:0.6rem 1rem;border-radius:8px;background:#ffba08;border:none;color:#061018;font-weight:700}a{color:#3f88c5}</style>
      </head>
      <body>
        <h1>Login</h1>
        <form id='login-form'>
          <label>Username or email<input name='username' required></label>
          <label>Password<input name='password' type='password' required></label>
          <label><input type='checkbox' name='remember'> Remember me</label>
          <button type='submit'>Log in</button>
        </form>
        <p>New? <a href='/register'>Create an account</a></p>
        <p id='note'></p>
        <script>
          const form=document.getElementById('login-form');
          form.addEventListener('submit', async e=>{
            e.preventDefault();
            const data={
              username: form.username.value,
              password: form.password.value,
              remember: form.remember.checked
            };
            const res=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
            const j=await res.json();
            const note=document.getElementById('note');
            if(res.ok){ note.textContent='Logged in. Redirecting...'; setTimeout(()=>location.href='/profile',400); } else { note.textContent = j.error || 'Login failed'; }
          });
        </script>
      </body>
    </html>
    """
    return html


@app.route('/register')
def register_page():
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Register</title>
        <style>body{font-family:system-ui, sans-serif;padding:2rem;background:#0b1220;color:#eef3ff}label{display:block;margin:0.5rem 0}input{padding:0.6rem;width:100%;max-width:320px;border-radius:8px;border:1px solid #333;background:#071018;color:#eef3ff}button{margin-top:1rem;padding:0.6rem 1rem;border-radius:8px;background:#ffba08;border:none;color:#061018;font-weight:700}a{color:#3f88c5}</style>
      </head>
      <body>
        <h1>Create an account</h1>
        <form id='reg-form'>
          <label>Username<input name='username' required></label>
          <label>Email<input name='email' type='email' required></label>
          <label>Password<input name='password' type='password' required></label>
          <button type='submit'>Register</button>
        </form>
        <p>Have an account? <a href='/login'>Log in</a></p>
        <p id='note'></p>
        <script>
          const form=document.getElementById('reg-form');
          form.addEventListener('submit', async e=>{
            e.preventDefault();
            const data={username:form.username.value,email:form.email.value,password:form.password.value};
            const res=await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
            const j=await res.json();
            const note=document.getElementById('note');
            if(res.ok){ note.textContent='Registered. Redirecting to login...'; setTimeout(()=>location.href='/login',800); } else { note.textContent = j.error || 'Registration failed'; }
          });
        </script>
      </body>
    </html>
    """
    return html


@app.route('/admin/contacts')
def admin_contacts():
    # require login
    if not session.get('user_id'):
        return redirect('/login')

    with engine.connect() as conn:
        stmt = select(contacts.c.id, contacts.c.name, contacts.c.email, contacts.c.message, contacts.c.created_at).order_by(desc(contacts.c.created_at))
        result = conn.execute(stmt)
        rows = [row_to_dict(r) for r in result.mappings().all()]

    rows_html = ''.join(
        f"<tr><td>{c['id']}</td><td>{c['name']}</td><td>{c['email']}</td><td>{c['message']}</td><td>{c['created_at']}</td></tr>"
        for c in rows
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
          a {{ color: #3f88c5; text-decoration: none; }}
        </style>
      </head>
      <body>
        <h1>Saved contact submissions</h1>
        <p class='note'>This page reads directly from the database used by the app (Postgres or SQLite depending on configuration).</p>
        <p><a href='/'>Back to homepage</a></p>
        <div class='table-wrap'>
          <table>
            <thead>
              <tr><th>ID</th><th>Name</th><th>Email</th><th>Message</th><th>Created at</th></tr>
            </thead>
            <tbody>
              {rows_html or '<tr><td colspan="5">No submissions yet.</td></tr>'}
            </tbody>
          </table>
        </div>
      </body>
    </html>
    """
    return html


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
