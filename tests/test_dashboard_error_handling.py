"""A failed analytics request must be visible, not silent.

The dashboard's report handler was `if (!res.ok) return;`. A 400 therefore
left the page on its initial empty render, which reads exactly like "you
have no data" - that is why a query-string parsing bug took two rounds of
investigation to find rather than being reported by the page itself.

Two halves are asserted here:

* The server contract the guard depends on: a bad analytics request returns
  a JSON error with a real status code, never an HTML 500 or a stack trace.
  These are ordinary end-to-end tests.
* The client guard itself. There is no JavaScript runtime in this project
  (no node, no package.json), so the browser behaviour cannot be executed
  here. These are source-level assertions over static/js - weaker than a
  DOM test, and narrowly aimed at the exact regression: the silent return
  coming back, the server's raw message being painted into the page, or the
  console detail being dropped.
"""

import os
import re
import unittest
from datetime import datetime

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'dashboard-error-handling-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import users  # noqa: E402

PASSWORD = 'dashboard-error-handling-password-123'

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_JS = os.path.join(REPO_ROOT, 'static', 'js', 'pages', 'dashboard.js')
API_JS = os.path.join(REPO_ROOT, 'static', 'js', 'api.js')


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


class FailedAnalyticsRequestContractTests(unittest.TestCase):
    """What the browser actually receives when an analytics request fails."""

    def setUp(self):
        self.username = f'dash_err_{self.id().rsplit(".", 1)[-1]}'
        self.user_id = make_user(self.username)
        self.workspace_id = create_workspace(
            user_id=self.user_id, domain=f'{self.username}.example', brand_name='DashErr')

    def login(self, client):
        response = client.post('/api/login',
                               json={'username': self.username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200)

    def get(self, query_string, authenticated=True):
        with server_pg.app.test_client() as client:
            if authenticated:
                self.login(client)
            return client.get(
                f'/api/analytics/projects/{self.workspace_id}/report{query_string}')

    def test_malformed_engine_ids_is_a_json_400(self):
        response = self.get('?engine_ids=perplexity')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.mimetype, 'application/json')
        self.assertIn('error', response.get_json())

    def test_malformed_range_is_a_json_400(self):
        response = self.get('?range=last-quarter')
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.get_json())

    def test_malformed_dates_are_a_json_400(self):
        response = self.get('?start_date=not-a-date')
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.get_json())

    def test_a_failed_request_never_returns_a_stack_trace(self):
        body = self.get('?engine_ids=perplexity').get_data(as_text=True)
        for leak in ('Traceback', 'File "', 'sqlalchemy', 'SELECT ', 'psycopg'):
            self.assertNotIn(leak, body, f'{leak!r} must not reach the browser')

    def test_anonymous_is_401_not_a_redirect_to_html(self):
        response = self.get('?range=today', authenticated=False)
        self.assertEqual(response.status_code, 401)
        self.assertIn('error', response.get_json())

    def test_a_workspace_the_user_cannot_see_is_404(self):
        other_user = make_user(f'{self.username}_other')
        other_workspace = create_workspace(
            user_id=other_user, domain=f'{self.username}-other.example', brand_name='Other')
        with server_pg.app.test_client() as client:
            self.login(client)
            response = client.get(f'/api/analytics/projects/{other_workspace}/report')
        self.assertEqual(response.status_code, 404)
        self.assertIn('error', response.get_json())

    def test_the_previously_fixed_comma_form_still_returns_200(self):
        """Regression lock on the parse fix: filters.js sends this shape."""
        response = self.get('?range=today&engine_ids=1,4,11')
        self.assertEqual(response.status_code, 200,
                         'engine_ids=1,4,11 must stay parseable')
        self.assertIn('scan_summary', response.get_json())

    def test_the_repeated_form_still_returns_200(self):
        response = self.get('?range=today&engine_ids=1&engine_ids=4&engine_ids=11')
        self.assertEqual(response.status_code, 200)

    def test_both_forms_still_agree(self):
        comma = self.get('?range=today&engine_ids=1,4,11').get_json()
        repeated = self.get('?range=today&engine_ids=1&engine_ids=4&engine_ids=11').get_json()
        self.assertEqual(comma['scan_summary'], repeated['scan_summary'])
        self.assertEqual(comma['visibility']['n'], repeated['visibility']['n'])


class DashboardClientGuardTests(unittest.TestCase):
    """Source-level guards over static/js - see the module docstring for why
    these are not DOM tests."""

    @classmethod
    def setUpClass(cls):
        cls.dashboard = read(DASHBOARD_JS)
        cls.api = read(API_JS)

    def report_handler(self):
        """The body of loadWorkspace()'s getReport callback."""
        match = re.search(
            r'TS\.api\.getReport\([^)]*\)\.then\(function \(res\) \{(.*?)\n    \}\);',
            self.dashboard, re.S)
        self.assertIsNotNone(match, 'loadWorkspace must still call TS.api.getReport')
        return match.group(1)

    def test_the_report_handler_no_longer_returns_silently(self):
        handler = self.report_handler()
        self.assertNotRegex(
            handler, r'if \(!res\.ok\) return;',
            'a non-2xx report response must not be swallowed - this is the '
            'exact line that hid a 400 behind an empty dashboard')

    def test_the_report_handler_surfaces_the_failure(self):
        self.assertIn('showLoadError', self.report_handler())

    def test_the_evidence_handler_surfaces_the_failure(self):
        self.assertIn("showLoadError('GET evidence'", self.dashboard,
                      'a failed evidence request must also be reported')

    def test_there_is_a_visible_error_region_in_the_markup(self):
        self.assertIn('id="load-error"', self.dashboard)
        self.assertIn('role="alert"', self.dashboard)

    def test_the_error_state_is_cleared_on_a_successful_load(self):
        self.assertIn('hideLoadError', self.report_handler())

    def test_detail_is_logged_to_the_console(self):
        self.assertIn('console.error', self.dashboard)
        self.assertRegex(self.dashboard, r'function logApiFailure')

    def test_the_servers_own_message_is_never_painted_into_the_page(self):
        """The 400 body says things like 'engine_ids must be integers.' -
        developer copy. The page must render curated text keyed off the
        status instead."""
        match = re.search(r'function showLoadError\(.*?\n  \}\n', self.dashboard, re.S)
        self.assertIsNotNone(match, 'showLoadError must exist')
        body = match.group(0)
        self.assertNotIn('res.body.error', body)
        self.assertNotIn('body.error', body)
        self.assertIn('errorCopyFor', body)

    def test_every_handled_status_has_user_facing_copy(self):
        for status in ('400', '401', '403', '404', '429'):
            self.assertRegex(
                self.dashboard, r'\n    ' + status + r': \{',
                f'status {status} needs its own copy')
        self.assertIn('GENERIC_ERROR', self.dashboard)

    def test_a_dropped_connection_resolves_instead_of_rejecting(self):
        """Otherwise the caller's .then() never runs and the failure is
        silent again, this time without even a status to report."""
        self.assertIn('networkError', self.api)
        self.assertRegex(self.api, r'\}\)\.catch\(function \(err\) \{')

    def test_successful_rendering_is_untouched(self):
        """The success path must still do exactly what it did before."""
        handler = self.report_handler()
        for call in ('state.report = res.body',
                     'renderFilterBar(',
                     'renderDashboard(res.body)',
                     'refreshEvidence(id)'):
            self.assertIn(call, handler)


if __name__ == '__main__':
    unittest.main()
