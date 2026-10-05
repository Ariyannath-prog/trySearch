"""Static pages, health and the contact form."""

from flask import Blueprint
from datetime import date, datetime, timedelta
from flask import Flask, jsonify, render_template, request, send_from_directory, abort, session, redirect
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
from app import terms as terms_doc
from app.accounts import is_verified
from app.onboarding_state import STAGE_NOT_STARTED, onboarding_state
from app.admin_auth import require_platform_admin_page
from app.security import issue_token

pages_bp = Blueprint('pages', __name__)

# Shared chrome for the inline auth pages (/login, /register, /signup,
# /verify-email). Kept as one constant so they cannot drift apart.
_AUTH_PAGE_STYLE = (
    '*{box-sizing:border-box}body{font-family:system-ui,sans-serif;min-height:100vh;margin:0;padding:clamp(1rem,5vw,2rem);display:grid;align-content:center;background:#0b1220;color:#eef3ff}form,.card{width:min(100%,27rem)}label{display:grid;gap:.4rem;margin:.8rem 0}input{padding:.7rem;width:100%;border-radius:8px;border:1px solid #333;background:#071018;color:#eef3ff;font-size:16px}button{margin-top:1rem;padding:.75rem 1rem;border-radius:8px;background:#ffba08;border:none;color:#061018;font-weight:700;cursor:pointer}button.ghost{background:transparent;border:1px solid #333;color:#eef3ff;font-weight:600}a{color:#6eaff0}.terms{display:flex;align-items:center;gap:.5rem;margin:1rem 0;font-size:14px}.terms input{width:auto;flex:none}.note{margin-top:1rem;font-size:14px;line-height:1.5}.err{color:#ffb3bf}.ok{color:#9ff0c4}h1{font-size:1.6rem;margin:0 0 .4rem}.lede{color:#9cb2d3;font-size:14px;line-height:1.6;margin:0 0 1rem}@media(max-width:400px){button{width:100%}}'
)

@pages_bp.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@pages_bp.route('/pricing')
def pricing_page():
    if not request.args:
        return redirect('/pricing?brand')
    return send_from_directory(BASE_DIR, 'pricing.html')

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
    """The real production dashboard.

    A user with no workspace at all has nothing here to render, and the empty
    state's only action is "Get started", which goes to onboarding anyway - so
    send them straight there. This is deliberately the *only* stage that
    redirects: a workspace that exists but has not finished onboarding still has
    partial data worth looking at, and a hard redirect would make it unreachable.
    """
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/login')
    if onboarding_state(user_id)['stage'] == STAGE_NOT_STARTED:
        return redirect('/onboarding')
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

@pages_bp.route('/citations')
def citations_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'citations.html')

@pages_bp.route('/competitors')
def competitors_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'competitors.html')

@pages_bp.route('/site-audit')
def site_audit_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'site_audit.html')

@pages_bp.route('/search-console')
def search_console_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'search_console.html')

@pages_bp.route('/mentions')
def mentions_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'mentions.html')

@pages_bp.route('/sentiment')
def sentiment_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'sentiment.html')

@pages_bp.route('/recommendations')
def recommendations_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'recommendations.html')

@pages_bp.route('/scans')
def scans_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'scans.html')

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

@pages_bp.route('/onboarding')
def onboarding_page():
    if not session.get('user_id'):
        return redirect('/login')
    # An unconfirmed address cannot set up a project. The API enforces this too
    # (routes/onboarding.py); this redirect just avoids showing a page whose
    # every request would be refused.
    if not is_verified(session['user_id']):
        return redirect('/verify-email')
    return send_from_directory(BASE_DIR, 'onboarding.html')


@pages_bp.route('/signup')
def signup_page():
    """Account creation. Posts to /api/signup, then waits on the inbox."""
    csrf = issue_token()
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Create your trySearch account</title>
        <meta id='csrf' content='__CSRF_TOKEN__'>
        <style>__STYLE__</style>
      </head>
      <body>
        <form id='signup-form'>
          <h1>Create your account</h1>
          <p class='lede'>Confirm your email, then set up your first project.</p>
          <label>Email<input name='email' type='email' autocomplete='email' required></label>
          <label>Password<input name='password' type='password' autocomplete='new-password' required minlength='10'></label>
          <label>Confirm password<input name='password_confirmation' type='password' autocomplete='new-password' required></label>
          <div class='terms'>
            <input id='terms' type='checkbox'>
            <label for='terms' style='margin:0'>I accept the
              <a href='/terms' target='_blank' rel='noopener'>terms of service</a></label>
          </div>
          <button type='submit' id='submit'>Create account</button>
          <p class='note'>Already have an account? <a href='/login'>Log in</a></p>
          <p class='note' id='note'></p>
        </form>
        <script>
          const CSRF=document.getElementById('csrf').content;
          const form=document.getElementById('signup-form');
          const note=document.getElementById('note');
          const submit=document.getElementById('submit');
          form.addEventListener('submit', async e=>{
            e.preventDefault();
            note.className='note'; note.textContent='Creating your account...';
            submit.disabled=true;
            const payload={
              email: form.email.value,
              password: form.password.value,
              password_confirmation: form.password_confirmation.value,
              terms_accepted: document.getElementById('terms').checked
            };
            try{
              const res=await fetch('/api/signup',{method:'POST',credentials:'same-origin',
                headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},
                body:JSON.stringify(payload)});
              const j=await res.json();
              if(res.ok){
                note.className='note ok';
                note.textContent=j.message||'Check your email to confirm your address.';
                setTimeout(()=>location.href='/verify-email',900);
              } else {
                note.className='note err';
                note.textContent=j.error||'Could not create the account.';
                submit.disabled=false;
              }
            }catch(err){
              note.className='note err';
              note.textContent='Network error. Please try again.';
              submit.disabled=false;
            }
          });
        </script>
      </body>
    </html>
    """
    return html.replace('__CSRF_TOKEN__', csrf).replace('__STYLE__', _AUTH_PAGE_STYLE)


@pages_bp.route('/verify-email')
def verify_email_page():
    """Target of the emailed link, and the holding page after signup.

    With ?token=... it spends the token and moves the user into onboarding.
    Without one it explains what to do and offers a resend.
    """
    csrf = issue_token()
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Confirm your email — trySearch</title>
        <meta id='csrf' content='__CSRF_TOKEN__'>
        <style>__STYLE__</style>
      </head>
      <body>
        <div class='card'>
          <h1 id='heading'>Confirm your email</h1>
          <p class='lede' id='lede'>Checking your confirmation link...</p>
          <div id='resend-box' style='display:none'>
            <label>Email<input id='resend-email' type='email' autocomplete='email'></label>
            <button type='button' id='resend'>Send a new link</button>
          </div>
          <p class='note' id='note'></p>
          <p class='note'><a href='/login'>Back to log in</a></p>
        </div>
        <script>
          const CSRF=document.getElementById('csrf').content;
          const heading=document.getElementById('heading');
          const lede=document.getElementById('lede');
          const note=document.getElementById('note');
          const resendBox=document.getElementById('resend-box');
          const token=new URLSearchParams(location.search).get('token');

          function offerResend(message){
            heading.textContent='Confirm your email';
            lede.textContent=message;
            resendBox.style.display='block';
          }

          async function verify(){
            try{
              const res=await fetch('/api/verify-email',{method:'POST',credentials:'same-origin',
                headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},
                body:JSON.stringify({token:token})});
              const j=await res.json();
              if(res.ok){
                heading.textContent='Email confirmed';
                lede.textContent='Taking you to set up your first project...';
                setTimeout(()=>location.href=(j.next||'/onboarding'),900);
              } else {
                offerResend(j.error||'That link did not work.');
              }
            }catch(err){
              offerResend('Network error while confirming. Try sending a new link.');
            }
          }

          document.getElementById('resend').addEventListener('click', async ()=>{
            note.className='note'; note.textContent='Sending...';
            try{
              const res=await fetch('/api/resend-verification',{method:'POST',credentials:'same-origin',
                headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},
                body:JSON.stringify({email:document.getElementById('resend-email').value})});
              const j=await res.json();
              note.className=res.ok?'note ok':'note err';
              note.textContent=(res.ok?j.message:j.error)||'';
            }catch(err){
              note.className='note err'; note.textContent='Network error. Please try again.';
            }
          });

          if(token){ verify(); }
          else{ offerResend('We sent you a confirmation link. Open it to finish setting up your account.'); }
        </script>
      </body>
    </html>
    """
    return html.replace('__CSRF_TOKEN__', csrf).replace('__STYLE__', _AUTH_PAGE_STYLE)


@pages_bp.route('/terms')
def terms_page():
    """Terms of service. Public, unauthenticated, no session required.

    It has to be readable from the signup form before an account exists, so there
    is deliberately no auth check here.

    Rendered from templates/terms.html on the platform design system
    (static/css/tokens.css), so it inherits the brand palette, typography and
    light/dark behaviour rather than carrying its own. The text and version come
    from app/terms.py - the same constant recorded on users.terms_version at
    signup - so the published page and the stored acceptance cannot disagree.
    """
    return render_template(
        'terms.html',
        version=terms_doc.VERSION,
        effective_date=terms_doc.EFFECTIVE_DATE,
        contact_email=terms_doc.CONTACT_EMAIL,
        sections=terms_doc.SECTIONS,
    )


@pages_bp.route('/forgot-password')
def forgot_password_page():
    """Request a reset link. Never confirms whether the address exists."""
    csrf = issue_token()
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Reset your password — trySearch</title>
        <meta id='csrf' content='__CSRF_TOKEN__'>
        <style>__STYLE__</style>
      </head>
      <body>
        <form id='forgot-form'>
          <h1>Reset your password</h1>
          <p class='lede'>Enter your email address and we will send you a link.</p>
          <label>Email<input name='email' type='email' autocomplete='email' required></label>
          <button type='submit' id='submit'>Send reset link</button>
          <p class='note'><a href='/login'>Back to log in</a></p>
          <p class='note' id='note'></p>
        </form>
        <script>
          const CSRF=document.getElementById('csrf').content;
          const form=document.getElementById('forgot-form');
          const note=document.getElementById('note');
          const submit=document.getElementById('submit');
          form.addEventListener('submit', async e=>{
            e.preventDefault();
            note.className='note'; note.textContent='Sending...'; submit.disabled=true;
            try{
              const res=await fetch('/api/forgot-password',{method:'POST',credentials:'same-origin',
                headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},
                body:JSON.stringify({email:form.email.value})});
              const j=await res.json();
              note.className=res.ok?'note ok':'note err';
              note.textContent=(res.ok?j.message:j.error)||'';
            }catch(err){
              note.className='note err'; note.textContent='Network error. Please try again.';
            }finally{ submit.disabled=false; }
          });
        </script>
      </body>
    </html>
    """
    return html.replace('__CSRF_TOKEN__', csrf).replace('__STYLE__', _AUTH_PAGE_STYLE)


@pages_bp.route('/reset-password')
def reset_password_page():
    """Target of the emailed reset link."""
    csrf = issue_token()
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Choose a new password — trySearch</title>
        <meta id='csrf' content='__CSRF_TOKEN__'>
        <style>__STYLE__</style>
      </head>
      <body>
        <form id='reset-form'>
          <h1>Choose a new password</h1>
          <p class='lede' id='lede'>Pick something at least 10 characters long.</p>
          <label>New password<input name='password' type='password' autocomplete='new-password' required minlength='10'></label>
          <label>Confirm password<input name='password_confirmation' type='password' autocomplete='new-password' required></label>
          <button type='submit' id='submit'>Change password</button>
          <p class='note'><a href='/forgot-password'>Request a new link</a></p>
          <p class='note' id='note'></p>
        </form>
        <script>
          const CSRF=document.getElementById('csrf').content;
          const form=document.getElementById('reset-form');
          const note=document.getElementById('note');
          const submit=document.getElementById('submit');
          const token=new URLSearchParams(location.search).get('token');
          if(!token){
            note.className='note err';
            note.textContent='This link is missing its token. Request a new one.';
            submit.disabled=true;
          }
          form.addEventListener('submit', async e=>{
            e.preventDefault();
            note.className='note'; note.textContent='Saving...'; submit.disabled=true;
            try{
              const res=await fetch('/api/reset-password',{method:'POST',credentials:'same-origin',
                headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},
                body:JSON.stringify({token:token,password:form.password.value,
                                     password_confirmation:form.password_confirmation.value})});
              const j=await res.json();
              if(res.ok){
                note.className='note ok'; note.textContent=j.message||'Password changed.';
                setTimeout(()=>location.href=(j.next||'/login'),1200);
              } else {
                note.className='note err'; note.textContent=j.error||'Could not change the password.';
                submit.disabled=false;
              }
            }catch(err){
              note.className='note err'; note.textContent='Network error. Please try again.';
              submit.disabled=false;
            }
          });
        </script>
      </body>
    </html>
    """
    return html.replace('__CSRF_TOKEN__', csrf).replace('__STYLE__', _AUTH_PAGE_STYLE)

@pages_bp.route('/profile')
def profile_page():
    if not session.get('user_id'):
        return redirect('/login')
    return send_from_directory(BASE_DIR, 'profile.html')

@pages_bp.route('/login')
def login_page():
    # simple HTML page that posts to /api/login via fetch.
    # Built as a plain string rather than a Jinja template, so the CSRF token the
    # context processor exposes to templates has to be interpolated by hand here.
    csrf = issue_token()
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Login</title>
        <meta id='csrf' content='__CSRF_TOKEN__'>
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
        <p>New? <a href='/signup'>Create an account</a></p>
        <p><a href='/forgot-password'>Forgot your password?</a></p>
        <p id='note'></p>
        <script>
          const CSRF=document.getElementById('csrf').content;
          const form=document.getElementById('login-form');
          form.addEventListener('submit', async e=>{
            e.preventDefault();
            const data={
              username: form.username.value,
              password: form.password.value,
              remember: form.remember.checked
            };
            const res=await fetch('/api/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},body:JSON.stringify(data)});
            const j=await res.json();
            const note=document.getElementById('note');
            if(res.ok){
              /* The destination is decided server-side (app/onboarding_state.py):
                 unconfirmed -> /verify-email, onboarding unfinished -> /onboarding,
                 otherwise /analytics. The fallback only matters if an older
                 response shape ever reaches this page. */
              const next = j.next || (j.email_verified === false ? '/verify-email' : '/analytics');
              note.textContent='Logged in. Redirecting...';
              setTimeout(()=>location.href=next,400);
            } else { note.textContent = j.error || 'Login failed'; }
          });
        </script>
      </body>
    </html>
    """
    return html.replace('__CSRF_TOKEN__', csrf)

@pages_bp.route('/register')
def register_page():
    csrf = issue_token()
    html = """
    <!doctype html>
    <html>
      <head>
        <meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Register</title>
        <meta id='csrf' content='__CSRF_TOKEN__'>
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
          const CSRF=document.getElementById('csrf').content;
          const form=document.getElementById('reg-form');
          form.addEventListener('submit', async e=>{
            e.preventDefault();
            const data={username:form.username.value,email:form.email.value,password:form.password.value};
            const res=await fetch('/api/register',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},body:JSON.stringify(data)});
            const j=await res.json();
            const note=document.getElementById('note');
            if(res.ok){ note.textContent='Registered. Redirecting to login...'; setTimeout(()=>location.href='/login',800); } else { note.textContent = j.error || 'Registration failed'; }
          });
        </script>
      </body>
    </html>
    """
    return html.replace('__CSRF_TOKEN__', csrf)

@pages_bp.route('/admin/contacts')
def admin_contacts():
    error = require_platform_admin_page()
    if error:
        return error

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
        <p class='note'>This page reads directly from the PostgreSQL database used by the app.</p>
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
