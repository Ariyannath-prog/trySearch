"""Login routing and onboarding-stage derivation.

The reported bug: a newly registered and verified account logged in and landed on
/analytics, which showed "No projects yet / Add your website to get started". It
should have gone to /onboarding.

The hard part is not sending new users to onboarding - it is not sending
*established* users there by mistake. Production workspaces predate the
engine-selection step, so they carry zero `workspace_engines` rows and zero
competitors. A naive "has the user completed every step?" check would classify a
real customer's scanned workspace as half-built. ScannedWorkspaceIsCompleteTests
pins that case specifically.
"""

import os
import unittest
from datetime import datetime, timedelta

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'login-routing-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import delete, func, insert, select, update  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import onboarding_state as ostate  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    analytics_audit_jobs,
    analytics_prompt_scan_runs,
    analytics_provider_answers,
    analytics_tracked_prompts,
    engines as engines_table,
    memberships,
    organizations,
    rate_limit_counters,
    users,
    workspace_engines,
    workspaces,
)

PASSWORD = 'login-routing-password'


def clear_rate_limits():
    with engine.begin() as conn:
        conn.execute(delete(rate_limit_counters))


def make_user(username, *, verified=True):
    """An account with no org and no workspace: a fresh signup."""
    now = datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(delete(users).where(users.c.username == username))
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@routing.test',
            password_hash=generate_password_hash(PASSWORD), created_at=now,
            is_platform_admin=False, is_active=True,
            email_verified_at=now if verified else None,
        )).inserted_primary_key[0]


def login(client, username):
    return client.post('/api/login',
                       json={'username': username, 'password': PASSWORD})


def add_prompt(workspace_id, text='best crm for startups'):
    now = datetime.utcnow()
    with engine.begin() as conn:
        return conn.execute(insert(analytics_tracked_prompts).values(
            workspace_id=workspace_id, topic_id=None, prompt=text,
            intent='Discovery', active=True, created_at=now, updated_at=now,
        )).inserted_primary_key[0]


def save_engine_selection(workspace_id):
    """What onboarding's engine step writes."""
    now = datetime.utcnow()
    with engine.begin() as conn:
        engine_id = conn.execute(
            select(engines_table.c.id).where(engines_table.c.key == 'perplexity')
        ).scalar_one()
        existing = conn.execute(select(workspace_engines.c.id).where(
            (workspace_engines.c.workspace_id == workspace_id)
            & (workspace_engines.c.engine_id == engine_id))).scalar_one_or_none()
        if existing is None:
            conn.execute(insert(workspace_engines).values(
                workspace_id=workspace_id, engine_id=engine_id, enabled=True,
                created_at=now, updated_at=now))


def queue_scan_job(workspace_id, status='queued'):
    """What onboarding's final step requests, before any worker has run."""
    now = datetime.utcnow()
    with engine.begin() as conn:
        return conn.execute(insert(analytics_audit_jobs).values(
            workspace_id=workspace_id, job_type='prompt_scan', run_type='on_demand',
            provider='Perplexity', status=status, progress=0, total_items=0,
            completed_items=0, created_at=now,
        )).inserted_primary_key[0]


def record_scan_run(workspace_id, *, with_answer=True):
    """A scan that actually executed: what makes a workspace established."""
    now = datetime.utcnow()
    prompt_id = add_prompt(workspace_id, 'recorded run prompt')
    with engine.begin() as conn:
        run_id = conn.execute(insert(analytics_prompt_scan_runs).values(
            workspace_id=workspace_id, job_id=None, provider='Perplexity',
            model='m', status='succeeded', prompt_count=1, completed_count=1,
            created_at=now, completed_at=now,
        )).inserted_primary_key[0]
        if with_answer:
            conn.execute(insert(analytics_provider_answers).values(
                scan_run_id=run_id, prompt_id=prompt_id, provider='Perplexity',
                model='m', status='succeeded', answer_text='an answer',
                created_at=now, completed_at=now))
    return run_id


# --- stage derivation -------------------------------------------------------

class StageDerivationTests(unittest.TestCase):

    def test_no_workspace_is_not_started(self):
        user = make_user('route_fresh')
        state = ostate.onboarding_state(user)
        self.assertEqual(state['stage'], ostate.STAGE_NOT_STARTED)
        self.assertEqual(state['next'], '/onboarding')
        self.assertEqual(state['resume'], 'domain')
        self.assertIsNone(state['workspace_id'])
        self.assertFalse(state['onboarding_complete'])

    def test_workspace_without_engine_selection_needs_engines(self):
        user = make_user('route_engines')
        workspace = create_workspace(user_id=user, domain='engines.routing.test',
                                     brand_name='NeedsEngines')
        add_prompt(workspace)
        state = ostate.onboarding_state(user)
        self.assertEqual(state['stage'], ostate.STAGE_NEEDS_ENGINES)
        self.assertEqual(state['next'], '/onboarding')
        self.assertEqual(state['resume'], 'engines')
        self.assertEqual(state['workspace_id'], workspace)

    def test_engine_selection_saved_but_no_scan_needs_scan(self):
        user = make_user('route_scan')
        workspace = create_workspace(user_id=user, domain='scan.routing.test',
                                     brand_name='NeedsScan')
        add_prompt(workspace)
        save_engine_selection(workspace)
        state = ostate.onboarding_state(user)
        self.assertEqual(state['stage'], ostate.STAGE_NEEDS_SCAN)
        self.assertEqual(state['resume'], 'analysis')

    def test_a_queued_first_scan_is_the_scanning_stage(self):
        user = make_user('route_scanning')
        workspace = create_workspace(user_id=user, domain='scanning.routing.test',
                                     brand_name='Scanning')
        add_prompt(workspace)
        save_engine_selection(workspace)
        queue_scan_job(workspace)
        state = ostate.onboarding_state(user)
        self.assertEqual(state['stage'], ostate.STAGE_SCANNING)
        self.assertEqual(state['next'], '/onboarding')
        self.assertEqual(state['resume'], 'analysis')

    def test_a_scanned_workspace_is_complete(self):
        user = make_user('route_done')
        workspace = create_workspace(user_id=user, domain='done.routing.test',
                                     brand_name='Done')
        save_engine_selection(workspace)
        record_scan_run(workspace)
        state = ostate.onboarding_state(user)
        self.assertEqual(state['stage'], ostate.STAGE_COMPLETE)
        self.assertEqual(state['next'], '/analytics')
        self.assertIsNone(state['resume'])
        self.assertTrue(state['onboarding_complete'])

    def test_one_complete_workspace_is_enough(self):
        """A half-built second project must not drag the user back."""
        user = make_user('route_mixed')
        finished = create_workspace(user_id=user, domain='finished.routing.test',
                                    brand_name='Finished')
        record_scan_run(finished)
        # A newer, unfinished workspace in the same org.
        with engine.connect() as conn:
            org_id = conn.execute(select(workspaces.c.org_id).where(
                workspaces.c.id == finished)).scalar_one()
        now = datetime.utcnow()
        with engine.begin() as conn:
            conn.execute(insert(workspaces).values(
                org_id=org_id, brand_name='Halfbuilt', domains=['half.routing.test'],
                geo='US', language='en', kind='project', status='active',
                created_at=now, updated_at=now + timedelta(minutes=5),
                domain='half.routing.test', website_url='https://half.routing.test/',
                industry='Software'))
        state = ostate.onboarding_state(user)
        self.assertEqual(state['stage'], ostate.STAGE_COMPLETE)
        self.assertEqual(state['next'], '/analytics')

    def test_a_soft_deleted_workspace_does_not_count(self):
        user = make_user('route_deleted')
        workspace = create_workspace(user_id=user, domain='deleted.routing.test',
                                     brand_name='Deleted')
        record_scan_run(workspace)
        with engine.begin() as conn:
            conn.execute(update(workspaces).where(workspaces.c.id == workspace)
                         .values(status='soft_deleted'))
        state = ostate.onboarding_state(user)
        self.assertEqual(state['stage'], ostate.STAGE_NOT_STARTED,
                         'a deleted workspace must not count as onboarded')

    def test_another_users_workspace_does_not_count(self):
        """The stage is membership-scoped, via tenancy.workspaces_for_user()."""
        owner = make_user('route_owner')
        stranger = make_user('route_stranger')
        workspace = create_workspace(user_id=owner, domain='owned.routing.test',
                                     brand_name='Owned')
        record_scan_run(workspace)
        self.assertEqual(ostate.onboarding_state(owner)['stage'],
                         ostate.STAGE_COMPLETE)
        self.assertEqual(ostate.onboarding_state(stranger)['stage'],
                         ostate.STAGE_NOT_STARTED)


class ScannedWorkspaceIsCompleteTests(unittest.TestCase):
    """The regression that matters most for existing customers.

    Production workspaces were created before the engine-selection step existed:
    they have scan history but zero workspace_engines rows and zero competitors.
    They must read as complete.
    """

    def test_a_legacy_workspace_with_no_engine_rows_is_still_complete(self):
        user = make_user('route_legacy')
        workspace = create_workspace(user_id=user, domain='legacy.routing.test',
                                     brand_name='Legacy')
        record_scan_run(workspace)
        with engine.connect() as conn:
            engine_rows = conn.execute(
                select(func.count()).select_from(workspace_engines)
                .where(workspace_engines.c.workspace_id == workspace)).scalar_one()
        self.assertEqual(engine_rows, 0, 'fixture must mirror production')
        state = ostate.onboarding_state(user)
        self.assertEqual(state['stage'], ostate.STAGE_COMPLETE)
        self.assertEqual(state['next'], '/analytics')

    def test_a_run_with_no_answers_still_counts_as_scanned(self):
        """A failed first scan must not trap the user in the wizard."""
        user = make_user('route_failedscan')
        workspace = create_workspace(user_id=user, domain='failed.routing.test',
                                     brand_name='Failed')
        record_scan_run(workspace, with_answer=False)
        self.assertEqual(ostate.onboarding_state(user)['stage'],
                         ostate.STAGE_COMPLETE)

    def test_completeness_is_checked_before_step_level_signals(self):
        """Ordering is the whole defence; assert it directly."""
        user = make_user('route_ordering')
        workspace = create_workspace(user_id=user, domain='order.routing.test',
                                     brand_name='Ordering')
        record_scan_run(workspace)
        # No engine rows and no prompts beyond the run's own: step-level checks
        # would say "needs_engines". Completeness must win.
        state = ostate.onboarding_state(user)
        self.assertEqual(state['stage'], ostate.STAGE_COMPLETE)


# --- login redirect ---------------------------------------------------------

class LoginRedirectTests(unittest.TestCase):

    def setUp(self):
        clear_rate_limits()

    def test_a_new_verified_user_is_sent_to_onboarding(self):
        """The reported bug, as a test."""
        make_user('route_login_new')
        with server_pg.app.test_client() as client:
            response = login(client, 'route_login_new')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['next'], '/onboarding',
                         'a new verified account must not land on /analytics')

    def test_an_incomplete_onboarding_resumes_onboarding(self):
        user = make_user('route_login_partial')
        workspace = create_workspace(user_id=user, domain='partial.routing.test',
                                     brand_name='Partial')
        add_prompt(workspace)
        with server_pg.app.test_client() as client:
            response = login(client, 'route_login_partial')
        self.assertEqual(response.get_json()['next'], '/onboarding')

    def test_a_completed_user_is_sent_to_the_dashboard(self):
        user = make_user('route_login_done')
        workspace = create_workspace(user_id=user, domain='logindone.routing.test',
                                     brand_name='LoginDone')
        record_scan_run(workspace)
        with server_pg.app.test_client() as client:
            response = login(client, 'route_login_done')
        self.assertEqual(response.get_json()['next'], '/analytics')

    def test_an_unverified_user_goes_to_verification_first(self):
        """Verification outranks onboarding: the onboarding API would refuse them."""
        user = make_user('route_login_unverified', verified=False)
        create_workspace(user_id=user, domain='unver.routing.test',
                         brand_name='Unverified')
        with server_pg.app.test_client() as client:
            response = login(client, 'route_login_unverified')
        self.assertEqual(response.get_json()['next'], '/verify-email')

    def test_the_login_page_follows_the_backend_destination(self):
        with server_pg.app.test_client() as client:
            html = client.get('/login').get_data(as_text=True)
        self.assertIn('j.next', html,
                      'the page must use the destination the backend chose')

    def test_me_reports_the_same_destination_as_login(self):
        user = make_user('route_me')
        workspace = create_workspace(user_id=user, domain='me.routing.test',
                                     brand_name='MeUser')
        record_scan_run(workspace)
        with server_pg.app.test_client() as client:
            login_next = login(client, 'route_me').get_json()['next']
            me = client.get('/api/me').get_json()
        self.assertEqual(me['onboarding']['next'], login_next,
                         '/api/me and /api/login must not disagree')
        self.assertTrue(me['onboarding']['complete'])

    def test_me_omits_onboarding_state_for_an_unverified_user(self):
        make_user('route_me_unverified', verified=False)
        with server_pg.app.test_client() as client:
            login(client, 'route_me_unverified')
            me = client.get('/api/me').get_json()
        self.assertNotIn('onboarding', me)
        self.assertFalse(me['email_verified'])


# --- /analytics server-side guard -------------------------------------------

class AnalyticsGuardTests(unittest.TestCase):

    def setUp(self):
        clear_rate_limits()

    def test_a_user_with_no_workspace_is_redirected_to_onboarding(self):
        make_user('route_an_none')
        with server_pg.app.test_client() as client:
            login(client, 'route_an_none')
            response = client.get('/analytics')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/onboarding'))

    def test_a_completed_user_reaches_the_dashboard(self):
        user = make_user('route_an_done')
        workspace = create_workspace(user_id=user, domain='andone.routing.test',
                                     brand_name='AnDone')
        record_scan_run(workspace)
        with server_pg.app.test_client() as client:
            login(client, 'route_an_done')
            response = client.get('/analytics')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.content_type)

    def test_a_partially_onboarded_user_may_still_open_the_dashboard(self):
        """Only 'no workspace at all' redirects; partial data is worth showing."""
        user = make_user('route_an_partial')
        workspace = create_workspace(user_id=user, domain='anpartial.routing.test',
                                     brand_name='AnPartial')
        add_prompt(workspace)
        with server_pg.app.test_client() as client:
            login(client, 'route_an_partial')
            response = client.get('/analytics')
        self.assertEqual(response.status_code, 200,
                         'a workspace in progress must remain viewable')

    def test_anonymous_still_goes_to_login_not_onboarding(self):
        with server_pg.app.test_client() as client:
            response = client.get('/analytics')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_the_dashboard_empty_state_points_at_onboarding(self):
        """Already true before this change; asserted so it stays true."""
        with open('static/js/pages/dashboard.js') as handle:
            source = handle.read()
        self.assertIn("href=\"/onboarding\"", source)


# --- state endpoint ---------------------------------------------------------

class OnboardingStateEndpointTests(unittest.TestCase):

    def setUp(self):
        clear_rate_limits()

    def test_anonymous_is_401(self):
        with server_pg.app.test_client() as client:
            self.assertEqual(client.get('/api/onboarding/state').status_code, 401)

    def test_unverified_is_403(self):
        make_user('route_state_unver', verified=False)
        with server_pg.app.test_client() as client:
            login(client, 'route_state_unver')
            response = client.get('/api/onboarding/state')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'email_unverified')

    def test_a_verified_user_gets_their_stage(self):
        make_user('route_state_ok')
        with server_pg.app.test_client() as client:
            login(client, 'route_state_ok')
            body = client.get('/api/onboarding/state').get_json()
        self.assertEqual(body['stage'], ostate.STAGE_NOT_STARTED)
        self.assertEqual(body['resume'], 'domain')

    def test_the_endpoint_reports_the_resume_workspace(self):
        user = make_user('route_state_resume')
        workspace = create_workspace(user_id=user, domain='resume.routing.test',
                                     brand_name='Resume')
        add_prompt(workspace)
        save_engine_selection(workspace)
        with server_pg.app.test_client() as client:
            login(client, 'route_state_resume')
            body = client.get('/api/onboarding/state').get_json()
        self.assertEqual(body['workspace_id'], workspace)
        self.assertEqual(body['resume'], 'analysis')

    def test_the_wizard_resumes_from_the_endpoint(self):
        with open('static/js/pages/onboarding.js') as handle:
            source = handle.read()
        self.assertIn('getOnboardingState', source)
        self.assertIn("info.resume === 'engines'", source)
        self.assertIn("info.resume === 'analysis'", source)


if __name__ == '__main__':
    unittest.main()
