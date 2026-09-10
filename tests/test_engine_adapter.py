"""T12: the engine adapter contract."""

import ast
import os
import pathlib
import unittest
from decimal import Decimal
from unittest.mock import patch

os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'engine-adapter-test-secret'

import server_pg  # noqa: E402,F401
from conftest import create_workspace, load_fixture  # noqa: E402

from sqlalchemy import insert, select  # noqa: E402

from app import scanning  # noqa: E402
from app.db import engine  # noqa: E402
from app.engines import perplexity as perplexity_engine  # noqa: E402
from app.engines.base import EngineAdapter, EngineResult  # noqa: E402
from app.engines.registry import adapter_for, registered_keys  # noqa: E402
from app.http_client import ProviderAPIError  # noqa: E402
from app.models import (  # noqa: E402
    analytics_prompt_scan_runs,
    analytics_tracked_prompts,
    engines as engines_table,
)

ENGINES_DIR = pathlib.Path(__file__).resolve().parent.parent / 'app' / 'engines'


class AdapterBoundaryTests(unittest.TestCase):
    def test_adapter_never_raises(self):
        """Transport blows up -> status='failed', not an exception."""
        adapter = adapter_for('perplexity')
        for boom in (ProviderAPIError('provider down'),
                     RuntimeError('unexpected'),
                     KeyError('missing field'),
                     ValueError('bad json')):
            with self.subTest(error=type(boom).__name__):
                with patch.object(perplexity_engine, 'call_perplexity_search',
                                  side_effect=boom):
                    result = adapter.run('a prompt')
                self.assertIsInstance(result, EngineResult)
                self.assertEqual(result.status, 'failed')
                self.assertIn(type(boom).__name__, result.error)

    def test_adapter_satisfies_the_protocol(self):
        adapter = adapter_for('perplexity')
        self.assertIsInstance(adapter, EngineAdapter)
        self.assertEqual(adapter.source_type, 'api')
        self.assertIsInstance(adapter.estimate_cost('x'), Decimal)

    def test_result_is_frozen(self):
        result = EngineResult(status='ok', answer_text='x')
        with self.assertRaises(Exception):
            result.answer_text = 'mutated'

    def test_unknown_engine_key_is_skipped_not_fatal(self):
        self.assertIsNone(adapter_for('not-deployed-yet'))


class EngineIsolationTests(unittest.TestCase):
    def test_engine_modules_do_not_import_db(self):
        """An adapter that can write a row stops being replaceable."""
        offenders = []
        for path in sorted(ENGINES_DIR.glob('*.py')):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or '']
                for name in names:
                    if name in ('app.db', 'app.models') or name.startswith(('app.db.', 'app.models.')):
                        offenders.append(f'{path.name} imports {name}')
        self.assertEqual(offenders, [], 'engines must not reach the database')

    def test_no_provider_name_branching_outside_the_registry(self):
        """The table is the registry - no `if provider == 'perplexity'` anywhere."""
        offenders = []
        for path in sorted((ENGINES_DIR.parent).rglob('*.py')):
            if path.name == 'registry.py':
                continue
            text = path.read_text()
            for needle in ("== 'perplexity'", '== "perplexity"', "PROVIDERS = ["):
                if needle in text:
                    offenders.append(f'{path.relative_to(ENGINES_DIR.parent)}: {needle}')
        self.assertEqual(offenders, [])


class FixtureReplayTests(unittest.TestCase):
    def test_perplexity_adapter_parses_recorded_fixture(self):
        search = load_fixture('perplexity', 'search_basic')
        answer = load_fixture('perplexity', 'answer_basic')
        adapter = adapter_for('perplexity')

        with patch.object(perplexity_engine, 'call_perplexity_search',
                          return_value=search), \
             patch.object(perplexity_engine, 'call_perplexity_answer',
                          return_value=answer):
            result = adapter.run('best ai search visibility tools for brands')

        self.assertEqual(result.status, 'ok')
        self.assertGreater(len(result.answer_text), 0)
        self.assertGreater(len(result.citations), 0)
        for citation in result.citations:
            self.assertTrue(citation.url.startswith('http'))
            self.assertGreaterEqual(citation.position, 1)
        self.assertEqual(result.raw_response['search'], search,
                         'raw_response carries the provider payload verbatim')

    def test_empty_answer_is_empty_not_failed(self):
        """A provider that answers with nothing is a fact, not a transport error."""
        adapter = adapter_for('perplexity')
        with patch.object(perplexity_engine, 'call_perplexity_search',
                          return_value={'results': []}), \
             patch.object(perplexity_engine, 'call_perplexity_answer',
                          return_value={'status': 'completed', 'output_text': ''}):
            result = adapter.run('x')
        self.assertEqual(result.status, 'empty')


class RegistryTableTests(unittest.TestCase):
    def test_enabling_an_engine_row_requires_no_schema_change(self):
        """Adding an engine is a row plus a module. Nothing else."""
        before = set(registered_keys())
        with engine.begin() as conn:
            conn.execute(insert(engines_table).values(
                key='future-engine', display_name='Future', source_type='api',
                adapter_version='0.1', enabled=True,
            ))
        with engine.connect() as conn:
            rows = scanning.enabled_engines(conn)
        # The row exists and is enabled, but no module is registered for it, so it
        # is skipped rather than breaking the run.
        self.assertNotIn('future-engine', before)
        self.assertNotIn('future-engine', [row['key'] for row, _ in rows])
        self.assertIn('perplexity', [row['key'] for row, _ in rows])

    def test_disabled_engine_is_not_run(self):
        with engine.begin() as conn:
            conn.execute(insert(engines_table).values(
                key='off-engine', display_name='Off', source_type='api',
                adapter_version='0.1', enabled=False,
            ))
        with engine.connect() as conn:
            keys = [row['key'] for row, _ in scanning.enabled_engines(conn)]
        self.assertNotIn('off-engine', keys)

    def test_source_type_is_exposed_for_the_ui_to_label(self):
        with engine.connect() as conn:
            row = conn.execute(select(engines_table).where(
                engines_table.c.key == 'perplexity')).mappings().first()
        self.assertEqual(row['source_type'], 'api')


class FailedEngineRunTests(unittest.TestCase):
    def test_failed_engine_leaves_run_partial_not_failed(self):
        """One engine failing must not fail the run for the others."""
        user = 96001
        workspace_id = create_workspace(user_id=user, domain='partial.example',
                                        brand_name='Partial')
        from datetime import datetime
        now = datetime.utcnow()
        with engine.begin() as conn:
            conn.execute(insert(analytics_tracked_prompts), [
                {'workspace_id': workspace_id, 'topic_id': None,
                 'prompt': f'partial tooling {i}', 'intent': 'Discovery',
                 'active': True, 'created_at': now, 'updated_at': now}
                for i in range(2)
            ])

        from app.jobs import create_analytics_job
        from app.models import workspaces
        with engine.connect() as conn:
            workspace = dict(conn.execute(select(workspaces).where(
                workspaces.c.id == workspace_id)).mappings().first())
        job_id = create_analytics_job(workspace, 'prompt_scan', provider='Perplexity')

        calls = {'n': 0}

        def flaky(prompt, region=None):
            calls['n'] += 1
            if calls['n'] == 1:
                raise ProviderAPIError('first prompt is down')
            return load_fixture('perplexity', 'search_basic')

        env = {'PERPLEXITY_API_KEY': 'k', 'PERPLEXITY_REQUEST_DELAY_SECONDS': '0'}
        with patch.dict(os.environ, env, clear=False), \
             patch.object(scanning, 'retry_delay', lambda attempt: 0), \
             patch.object(scanning, 'ANSWER_RETRY_ATTEMPTS', 1), \
             patch.object(perplexity_engine, 'call_perplexity_search', side_effect=flaky), \
             patch.object(perplexity_engine, 'call_perplexity_answer',
                          return_value=load_fixture('perplexity', 'answer_basic')):
            scanning.run_prompt_scan_job(job_id)

        with engine.connect() as conn:
            status = conn.execute(select(analytics_prompt_scan_runs.c.status).where(
                analytics_prompt_scan_runs.c.job_id == job_id)).scalar_one()
        self.assertEqual(status, 'partial',
                         'one failed answer leaves the run partial, not failed')


if __name__ == '__main__':
    unittest.main()
