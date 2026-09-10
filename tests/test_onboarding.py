"""T14: the onboarding wizard.

The prompt-set fixture is a real recorded Gemini response, not hand-written - the
same rule as provider fixtures. Re-record with scripts/record_fixture.py when the
prompt or the model changes.
"""

import json
import os
import unittest

os.environ['APP_ENV'] = 'development'
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'onboarding-test-secret'

import server_pg  # noqa: E402,F401
from conftest import load_fixture  # noqa: E402

from app import onboarding  # noqa: E402
from app.onboarding import OnboardingError  # noqa: E402

BRAND = 'Linear'


def recorded_profile():
    return load_fixture('gemini', 'onboarding_profile')


class GeneratedPromptQualityTests(unittest.TestCase):
    """Against the recorded model output, not a hand-built ideal."""

    @classmethod
    def setUpClass(cls):
        cls.profile = onboarding.validate_profile(recorded_profile())

    def test_majority_of_generated_prompts_are_unbranded(self):
        fraction = self.profile['unbranded_fraction']
        self.assertGreater(
            fraction, onboarding.MIN_UNBRANDED_FRACTION,
            f'only {fraction:.0%} of prompts are unbranded. The point is to test '
            f'whether an engine names the brand organically; a branded prompt '
            f'cannot answer that.')

    def test_no_generated_alias_is_a_substring_of_the_brand(self):
        for alias in self.profile['aliases']:
            self.assertNotIn(alias.lower(), BRAND.lower())

    def test_no_kept_alias_is_redundant_with_the_brand(self):
        """The rule the spec's own example describes.

        An alias containing the brand as a whole word can never match anywhere the
        brand does not, so keeping it inflates nothing and costs a comparison.
        """
        for alias in self.profile['aliases']:
            self.assertIsNone(
                onboarding.alias_offsets(alias, [BRAND]),
                f'{alias!r} contains {BRAND!r} as a word and is redundant')

    def test_prompts_are_search_fragments_not_sentences(self):
        for prompt in self.profile['prompts']:
            text = prompt['text']
            self.assertLessEqual(len(text.split()), onboarding.MAX_PROMPT_WORDS)
            self.assertEqual(text, text.lower(), 'prompts are lowercase')
            self.assertNotIn('?', text)

    def test_every_prompt_has_a_known_category(self):
        for prompt in self.profile['prompts']:
            self.assertIn(prompt['category'], onboarding.CATEGORIES)

    def test_at_most_ten_competitors(self):
        self.assertLessEqual(len(self.profile['competitors']),
                             onboarding.MAX_COMPETITORS)


class BrandedFlagTests(unittest.TestCase):
    def test_branded_flag_is_computed_not_trusted(self):
        """A model claiming the opposite of the truth must be ignored."""
        payload = {
            'brand_name': 'Linear',
            'aliases': [],
            'domains': ['linear.app'],
            'competitors': [],
            'prompts': [
                # Model says unbranded; the text plainly names the brand.
                {'text': 'linear vs jira', 'category': 'comparison', 'branded': False},
                # Model says branded; the text names no brand at all.
                {'text': 'best issue tracker', 'category': 'discovery', 'branded': True},
            ],
        }
        profile = onboarding.validate_profile(payload)
        by_text = {p['text']: p['branded'] for p in profile['prompts']}
        self.assertTrue(by_text['linear vs jira'],
                        'computed from the text, not from the model')
        self.assertFalse(by_text['best issue tracker'])

    def test_branded_uses_the_same_matcher_as_extraction(self):
        # Word-boundary: "linearity" must not count as a Linear mention.
        self.assertFalse(onboarding.is_branded('linearity of scaling', ['Linear']))
        self.assertTrue(onboarding.is_branded('linear vs jira', ['Linear']))

    def test_domains_count_as_brand_tokens(self):
        payload = {
            'brand_name': 'Linear', 'aliases': [], 'domains': ['linear.app'],
            'competitors': [],
            'prompts': [{'text': 'linear.app pricing', 'category': 'brand'}],
        }
        profile = onboarding.validate_profile(payload)
        self.assertTrue(profile['prompts'][0]['branded'])


class RepairAndFallbackTests(unittest.TestCase):
    def test_malformed_model_output_retries_once_then_falls_back(self):
        calls = []

        def always_garbage(system, user):
            calls.append(user)
            return 'not json at all'

        with self.assertRaises(OnboardingError):
            onboarding.generate_profile('linear.app', 'page text',
                                        call_model=always_garbage)
        self.assertEqual(len(calls), 2, 'exactly one repair retry, then give up')
        self.assertIn('rejected', calls[1],
                      'the retry tells the model what was wrong')

    def test_repair_attempt_can_succeed(self):
        good = json.dumps(recorded_profile())
        replies = ['{"broken": true}', good]

        def flaky(system, user):
            return replies.pop(0)

        profile = onboarding.generate_profile('linear.app', 'page text',
                                              call_model=flaky)
        self.assertEqual(profile['brand_name'], BRAND)

    def test_profile_without_prompts_is_rejected(self):
        with self.assertRaises(OnboardingError):
            onboarding.validate_profile(
                {'brand_name': 'Linear', 'prompts': []})

    def test_profile_without_brand_name_is_rejected(self):
        with self.assertRaises(OnboardingError):
            onboarding.validate_profile({'prompts': [{'text': 'x'}]})



class ApprovalGateTests(unittest.TestCase):
    """A scan costs money and an unapproved prompt set is a bad first impression."""

    @classmethod
    def setUpClass(cls):
        from datetime import datetime

        from sqlalchemy import insert
        from werkzeug.security import generate_password_hash

        from app.db import engine
        from app.models import users
        with engine.begin() as conn:
            cls.user = conn.execute(insert(users).values(
                username='onboard_user', email='onboard@example.com',
                password_hash=generate_password_hash('onboard-password-1'),
                created_at=datetime.utcnow(),
            )).inserted_primary_key[0]

    def login(self, client):
        response = client.post('/api/login', json={
            'username': 'onboard_user', 'password': 'onboard-password-1'})
        self.assertEqual(response.status_code, 200)

    def test_no_scan_runs_before_approval(self):
        from unittest.mock import patch

        from sqlalchemy import select

        from app.db import engine
        from app.models import analytics_audit_jobs, analytics_prompt_scan_runs

        profile = onboarding.validate_profile(recorded_profile())
        profile['domain'] = 'linear.app'

        with engine.connect() as conn:
            jobs_before = len(conn.execute(select(analytics_audit_jobs.c.id)).all())
            runs_before = len(conn.execute(select(analytics_prompt_scan_runs.c.id)).all())

        with patch('app.scanning.run_prompt_scan_job') as scan:
            with server_pg.app.test_client() as client:
                self.login(client)
                response = client.post('/api/onboarding/approve',
                                       json={'profile': profile})

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertFalse(body['scan_started'],
                         'approval persists the prompt set; it does not scan')
        scan.assert_not_called()

        with engine.connect() as conn:
            jobs_after = len(conn.execute(select(analytics_audit_jobs.c.id)).all())
            runs_after = len(conn.execute(select(analytics_prompt_scan_runs.c.id)).all())
        self.assertEqual(jobs_after, jobs_before, 'no job queued by approval')
        self.assertEqual(runs_after, runs_before, 'no run created by approval')

    def test_approval_persists_the_reviewed_set(self):
        from sqlalchemy import select

        from app.db import engine
        from app.models import (
            analytics_tracked_prompts, brand_aliases, competitors as competitors_table,
        )

        profile = onboarding.validate_profile(recorded_profile())
        profile['domain'] = 'linear.app'
        # The user edits before approving: two prompts only.
        profile['prompts'] = profile['prompts'][:2]

        with server_pg.app.test_client() as client:
            self.login(client)
            body = client.post('/api/onboarding/approve',
                               json={'profile': profile}).get_json()
        workspace_id = body['workspace']['id']

        with engine.connect() as conn:
            prompts = conn.execute(select(analytics_tracked_prompts).where(
                analytics_tracked_prompts.c.workspace_id == workspace_id)).mappings().all()
            rivals = conn.execute(select(competitors_table).where(
                competitors_table.c.workspace_id == workspace_id)).mappings().all()
            aliases = conn.execute(select(brand_aliases).where(
                brand_aliases.c.workspace_id == workspace_id)).mappings().all()

        self.assertEqual(len(prompts), 2, 'only what the user approved is stored')
        self.assertEqual(len(rivals), len(profile['competitors']))
        self.assertEqual(len(aliases), len(profile['aliases']))

    def test_preview_writes_nothing(self):
        from unittest.mock import patch

        from sqlalchemy import select

        from app.db import engine
        from app.models import workspaces

        with engine.connect() as conn:
            before = len(conn.execute(select(workspaces.c.id)).all())

        with patch('app.routes.onboarding.onboarding_service.fetch_homepage',
                   return_value='<html>Linear is an issue tracker.</html>'), \
             patch('app.routes.onboarding.call_gemini_text',
                   return_value=json.dumps(recorded_profile())):
            with server_pg.app.test_client() as client:
                self.login(client)
                response = client.post('/api/onboarding/preview',
                                       json={'domain': 'linear.app'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('profile', response.get_json())
        with engine.connect() as conn:
            after = len(conn.execute(select(workspaces.c.id)).all())
        self.assertEqual(after, before, 'preview must not create a workspace')

if __name__ == '__main__':
    unittest.main()
