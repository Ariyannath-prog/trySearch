"""Static pages, health and the contact form."""

from flask import Blueprint
from datetime import date, datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory, abort, session, redirect
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Boolean,
    Float,
    Integer,
    String,
    Text,
    DateTime,
    UniqueConstraint,
    select,
    insert,
    update,
    desc,
    func,
    text,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import os

from app.config import BASE_DIR
from app import db
from app.db import engine
from app.models import contacts
from app.utils import row_to_dict

pages_bp = Blueprint('pages', __name__)

@pages_bp.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@pages_bp.route('/<path:path>')
def static_files(path):
    filepath = os.path.join(BASE_DIR, path)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        return send_from_directory(BASE_DIR, path)
    abort(404)

@pages_bp.route('/api/contacts', methods=['GET', 'POST'])
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

@pages_bp.route('/api/health', methods=['GET'])
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
    except SQLAlchemyError:
        return jsonify({'status': 'error', 'db': engine.url.get_backend_name()}), 503
    return jsonify({
        'status': 'ok',
        'db': engine.url.get_backend_name(),
        'database_identity': db.DATABASE_IDENTITY,
    })

@pages_bp.route('/analytics')
def analytics_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'analytics.html')

@pages_bp.route('/prompt-intelligence')
def prompt_intelligence_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'prompt_intelligence.html')

@pages_bp.route('/visibility-tracking')
def visibility_tracking_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'visibility_tracking.html')

@pages_bp.route('/content-studio')
def content_studio_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'content_studio.html')

@pages_bp.route('/workspace')
def workspace_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'workspace.html')

@pages_bp.route('/profile')
def profile_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'profile.html')

@pages_bp.route('/login')
def login_page():
    # simple HTML page that posts to /api/login via fetch
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Login</title>
        <style>*{box-sizing:border-box}body{font-family:system-ui,sans-serif;min-height:100vh;margin:0;padding:clamp(1rem,5vw,2rem);display:grid;align-content:center;background:#0b1220;color:#eef3ff}form{width:min(100%,26rem)}label{display:grid;gap:.4rem;margin:.8rem 0}input{padding:.7rem;width:100%;border-radius:8px;border:1px solid #333;background:#071018;color:#eef3ff;font-size:16px}button{margin-top:1rem;padding:.75rem 1rem;border-radius:8px;background:#ffba08;border:none;color:#061018;font-weight:700;cursor:pointer}a{color:#6eaff0}@media(max-width:400px){button{width:100%}}</style>
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

@pages_bp.route('/register')
def register_page():
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Register</title>
        <style>*{box-sizing:border-box}body{font-family:system-ui,sans-serif;min-height:100vh;margin:0;padding:clamp(1rem,5vw,2rem);display:grid;align-content:center;background:#0b1220;color:#eef3ff}form{width:min(100%,26rem)}label{display:grid;gap:.4rem;margin:.8rem 0}input{padding:.7rem;width:100%;border-radius:8px;border:1px solid #333;background:#071018;color:#eef3ff;font-size:16px}button{margin-top:1rem;padding:.75rem 1rem;border-radius:8px;background:#ffba08;border:none;color:#061018;font-weight:700;cursor:pointer}a{color:#6eaff0}@media(max-width:400px){button{width:100%}}</style>
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

@pages_bp.route('/admin/contacts')
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
          * {{ box-sizing: border-box; }}
          body {{ font-family: system-ui, sans-serif; background: #0b1220; color: #eef3ff; margin: 0; padding: clamp(1rem, 4vw, 2rem); }}
          .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
          table {{ width: 100%; min-width: 720px; border-collapse: collapse; margin-top: 1rem; }}
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
