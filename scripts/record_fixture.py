"""Record one real provider response as a test fixture.

Fixtures are recorded, never hand-written. When a provider changes its response
format you re-record; you do not edit the JSON. Adapter tests replay these files, so
the test suite never makes a paid call.

    python scripts/record_fixture.py perplexity search "best crm for startups"
    python scripts/record_fixture.py perplexity answer "best crm for startups"

Writes tests/fixtures/<engine>/<case>.json. Requires the provider's real API key in
the environment or .env, and costs one call per run.
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_ROOT = os.path.join(REPO_ROOT, 'tests', 'fixtures')
sys.path.insert(0, REPO_ROOT)


def load_dotenv():
    """Populate os.environ from .env. Existing variables always win."""
    path = os.path.join(REPO_ROOT, '.env')
    if not os.path.exists(path):
        return
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip())


# engine -> {case-kind: (callable path, required env var)}
RECORDERS = {
    'perplexity': {
        'search': ('app.engines.perplexity:call_perplexity_search', 'PERPLEXITY_API_KEY'),
        'answer': ('app.engines.perplexity:call_perplexity_answer', 'PERPLEXITY_API_KEY'),
    },
}


def resolve(dotted):
    module_name, _, attribute = dotted.partition(':')
    module = __import__(module_name, fromlist=[attribute])
    return getattr(module, attribute)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine', choices=sorted(RECORDERS))
    parser.add_argument('kind', help='which call to record, e.g. search or answer')
    parser.add_argument('prompt', help='the prompt to send')
    parser.add_argument('--case', help='fixture filename stem (default: <kind>_basic)')
    args = parser.parse_args()

    load_dotenv()

    recorders = RECORDERS[args.engine]
    if args.kind not in recorders:
        parser.error(f'{args.engine} supports: {", ".join(sorted(recorders))}')
    dotted, env_var = recorders[args.kind]

    if not os.environ.get(env_var):
        parser.error(f'{env_var} is not set. Put it in .env or the environment.')

    case = args.case or f'{args.kind}_basic'
    destination = os.path.join(FIXTURE_ROOT, args.engine, f'{case}.json')

    print(f'calling {args.engine}.{args.kind} (one real, billable request)...')
    payload = resolve(dotted)(args.prompt)

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, 'w') as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write('\n')

    size = os.path.getsize(destination)
    relative = os.path.relpath(destination, REPO_ROOT)
    print(f'wrote {relative} ({size:,} bytes)')
    print('prompt recorded:', args.prompt)


if __name__ == '__main__':
    main()
