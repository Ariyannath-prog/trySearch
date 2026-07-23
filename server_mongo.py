import os
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory, abort, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

from mongo_config import create_mongo_client, get_database, resolve_mongodb_settings, safe_uri_for_logs

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

MONGODB_URI, MONGODB_DB_NAME = resolve_mongodb_settings()
print(f'MongoDB: connecting to db={MONGODB_DB_NAME} ({safe_uri_for_logs(MONGODB_URI)})')

client = create_mongo_client()
db = get_database(client)
contacts_col = db['contacts']
users_col = db['users']

# Ensure indexes for unique constraints
users_col.create_index([('username', 1)], unique=True)
users_col.create_index([('email', 1)], unique=True)

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
app.permanent_session_lifetime = timedelta(days=30)


def serialize_doc(doc):
    """Convert MongoDB ObjectId to string for JSON serialization"""
    if doc is None:
        return None
    if isinstance(doc, dict):
        doc['id'] = str(doc.get('_id', ''))
        if 'created_at' in doc and isinstance(doc['created_at'], datetime):
            doc['created_at'] = doc['created_at'].isoformat() + 'Z'
        # remove MongoDB's _id from response
        doc.pop('_id', None)
    return doc


def serialize_docs(docs):
    """Convert list of MongoDB docs to serializable format"""
    return [serialize_doc(doc) for doc in docs]


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

        contact_doc = {
            'name': name,
            'email': email,
            'message': message,
            'created_at': datetime.utcnow(),
        }
        result = contacts_col.insert_one(contact_doc)
        return jsonify({'status': 'success', 'message': 'Contact request submitted.', 'id': str(result.inserted_id)}), 201

    # GET
    docs = list(contacts_col.find().sort('created_at', -1).limit(100))
    contacts = serialize_docs(docs)
    return jsonify({'status': 'success', 'contacts': contacts})


@app.route('/api/health', methods=['GET'])
def health():
    try:
        # ping the database to verify connection
        client.admin.command('ping')
        return jsonify({'status': 'ok', 'db': 'mongodb'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


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
    user_doc = {
        'username': username,
        'email': email,
        'password_hash': password_hash,
        'created_at': datetime.utcnow(),
    }

    try:
        result = users_col.insert_one(user_doc)
        return jsonify({'status': 'success', 'message': 'User registered.', 'id': str(result.inserted_id)}), 201
    except DuplicateKeyError:
        return jsonify({'error': 'User with that username or email already exists.'}), 400


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

    # find user by username or email
    user = users_col.find_one({'$or': [{'username': username}, {'email': username}]})
    if not user:
        return jsonify({'error': 'Invalid credentials'}), 401
    if not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Invalid credentials'}), 401

    # login success
    session.clear()
    session['user_id'] = str(user['_id'])
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
    username = session.get('username')
    if user_id and username:
        return jsonify({'logged_in': True, 'user_id': user_id, 'username': username})
    return jsonify({'logged_in': False})


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
            if(res.ok){ note.textContent='Logged in. Redirecting...'; setTimeout(()=>location.href='/',800); } else { note.textContent = j.error || 'Login failed'; }
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

    docs = list(contacts_col.find().sort('created_at', -1))
    contacts = serialize_docs(docs)

    rows_html = ''.join(
        f"<tr><td>{c['id'][:8]}</td><td>{c['name']}</td><td>{c['email']}</td><td>{c['message']}</td><td>{c['created_at']}</td></tr>"
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
          a {{ color: #3f88c5; text-decoration: none; }}
        </style>
      </head>
      <body>
        <h1>Saved contact submissions</h1>
        <p class='note'>This page reads directly from MongoDB Atlas cluster.</p>
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
