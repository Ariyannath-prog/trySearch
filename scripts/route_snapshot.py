"""Record the response of every registered route, for before/after refactor comparison.

T1 splits server_pg.py into the app/ module tree. That refactor is only safe if every
route answers identically afterwards. This script drives the Flask test client over the
whole url_map against a throwaway SQLite database and writes a normalised JSON snapshot.

    python scripts/route_snapshot.py before.json
    # ...refactor...
    python scripts/route_snapshot.py after.json
    diff before.json after.json

It imports server_pg:app, which stays the entrypoint on both sides of the split, so the
same script runs unchanged before and after.
"""

import json
import os
import re
import sys
import tempfile

# A throwaway database per run, so POST routes that write rows cannot make two
# snapshots differ for reasons that have nothing to do with the refactor.
_TMP_DB = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_TMP_DB.close()
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP_DB.name}'
os.environ['APP_ENV'] = 'development'
os.environ.setdefault('SECRET_KEY', 'route-snapshot-fixed-key')
# Empty provider credentials keep every outbound call on its unconfigured path.
for _key in ('PERPLEXITY_API_KEY', 'HF_TOKEN', 'GOOGLE_CLIENT_ID',
             'GOOGLE_CLIENT_SECRET', 'OAUTH_TOKEN_ENCRYPTION_KEY'):
    os.environ[_key] = ''

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Before T1 these lived on server_pg; after the split they live in app.db. Resolving
# both keeps one script usable on either side of the refactor.
try:
    from app import models  # noqa: F401 - registers the tables on `metadata`
    from app.db import engine as _engine, metadata as _metadata
    # As of T2 the app issues no DDL, and building it reads the database identity,
    # so the schema has to exist before server_pg is imported.
    _metadata.create_all(_engine)
    import server_pg  # noqa: E402
except ImportError:  # pre-T1 monolith
    import server_pg  # noqa: E402
    _engine, _metadata = server_pg.engine, server_pg.metadata

app = server_pg.app

# Values substituted into <converter:name> placeholders when building a concrete URL.
SAMPLE_BY_CONVERTER = {
    'int': '1',
    'float': '1.0',
    'path': 'index.html',
    'string': 'sample',
    'default': 'sample',
}

# Anything that legitimately changes between two runs of identical code. Left in place
# would drown the real signal; each is replaced by a stable token.
NOISE = [
    (re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?'), '<TS>'),
    (re.compile(r'\d{4}-\d{2}-\d{2}'), '<DATE>'),
    (re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I), '<UUID>'),
    (re.compile(r'\b[0-9a-f]{32,}\b', re.I), '<HEX>'),
    (re.compile(r'(state=)[^&"\s]+'), r'\1<STATE>'),
    (re.compile(r'(nonce=)[^&"\s]+'), r'\1<NONCE>'),
]


def build_url(rule):
    """Turn a rule like /api/x/<int:project_id> into a concrete, requestable path."""
    url = rule.rule
    for argument in rule.arguments:
        converter = 'default'
        for candidate in ('int', 'float', 'path', 'string'):
            if f'<{candidate}:{argument}>' in url:
                converter = candidate
                break
        sample = SAMPLE_BY_CONVERTER[converter]
        url = re.sub(rf'<(?:[a-z]+:)?{re.escape(argument)}>', sample, url)
    return url


def scrub(text):
    for pattern, replacement in NOISE:
        text = pattern.sub(replacement, text)
    return text


def normalise_body(response):
    """JSON is compared by structure; everything else by length and a scrubbed prefix."""
    raw = response.get_data(as_text=True)
    content_type = (response.headers.get('Content-Type') or '').split(';')[0]
    if content_type == 'application/json':
        try:
            return {'json': json.loads(scrub(raw))}
        except ValueError:
            pass
    # HTML and static assets are large and mostly irrelevant; their size plus a short
    # scrubbed head is enough to catch a route that started serving something else.
    return {'len': len(raw), 'head': scrub(raw[:200])}


def probe(client, rule, method):
    payload = {} if method in ('POST', 'PUT', 'PATCH') else None
    try:
        response = client.open(build_url(rule), method=method, json=payload)
    except Exception as exc:  # a route that raises is itself a fact worth recording
        return {'error': f'{type(exc).__name__}: {scrub(str(exc))}'}
    entry = {
        'status': response.status_code,
        'content_type': (response.headers.get('Content-Type') or '').split(';')[0],
        'body': normalise_body(response),
    }
    if response.status_code in (301, 302, 303, 307, 308):
        entry['location'] = scrub(response.headers.get('Location') or '')
    return entry


CREDENTIALS = {
    'username': 'snapshot_user',
    'email': 'snapshot@example.com',
    'password': 'snapshot-password-123',
}

# Seeded so the <int:id> routes exercise real handler bodies instead of all
# returning 404. Each resource lands at id 1 in a fresh database.
SEEDS = [
    ('/api/analytics/projects', {'domain': 'example.com', 'brand_name': 'Example'}),
    ('/api/analytics/projects/1/topics', {'name': 'Example Topic'}),
    ('/api/analytics/projects/1/competitors', {'name': 'Rival', 'domain': 'rival.com'}),
    ('/api/analytics/projects/1/tracked-prompts',
     {'prompt': 'best example tools for teams', 'intent': 'Discovery'}),
    # prompt-intelligence and visibility-tracking were removed in T5 with the
    # mock-era tables behind them.
    ('/api/content-studio/documents',
     {'workspace_id': 1, 'title': 'Draft', 'brand_name': 'Example',
      'keyword': 'example tools'}),
]


def login(client):
    client.post('/api/login', json={
        'username': CREDENTIALS['username'], 'password': CREDENTIALS['password'],
    })


def reset_and_seed(client):
    """Return the database to a known state, logged in with every seed present.

    Run before *every* authenticated probe. Without it the probe order decides the
    result: /api/logout un-authenticates everything sorted after it, and
    DELETE /api/analytics/projects/1 sorts ahead of the routes nested beneath it, so
    those would all record a 404 that says nothing about the handler.
    """
    _metadata.drop_all(_engine)
    _metadata.create_all(_engine)
    client.post('/api/register', json=CREDENTIALS)
    login(client)
    statuses = {}
    for path, payload in SEEDS:
        statuses[path] = client.post(path, json=payload).status_code
    return statuses


def snapshot_for(label, authenticate):
    results = {}
    seed_status = None
    with app.test_client() as client:
        for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
            for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
                if authenticate:
                    seed_status = reset_and_seed(client)
                results[f'{method} {rule.rule}'] = probe(client, rule, method)
    if seed_status:
        print('  seed statuses:', sorted(set(seed_status.values())))
    return {label: results}


def main():
    destination = sys.argv[1] if len(sys.argv) > 1 else 'route_snapshot.json'
    snapshot = {'route_count': len(list(app.url_map.iter_rules()))}
    snapshot.update(snapshot_for('anonymous', authenticate=False))
    snapshot.update(snapshot_for('authenticated', authenticate=True))
    with open(destination, 'w') as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=True)
    probes = len(snapshot['anonymous']) + len(snapshot['authenticated'])
    print(f'{snapshot["route_count"]} routes, {probes} probes -> {destination}')
    os.unlink(_TMP_DB.name)


if __name__ == '__main__':
    main()
