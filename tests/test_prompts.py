"""Topics, tracked prompts and the tracking payload (app/routes/prompts.py).

Cross-tenant isolation for these routes is covered by
tests/test_tenancy_isolation.py's generic workspace_id sweep - this file is
about correctness and validation, not access control.
"""

import os
import unittest
from datetime import datetime

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'prompts-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import users  # noqa: E402
from sqlalchemy import insert  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

PASSWORD = 'prompts-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


class PromptsTestCase(unittest.TestCase):
    """Shared login helper. Each test gets its own user/workspace."""

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def new_workspace(self, tag):
        user_id = make_user(f'prompts_{tag}')
        workspace_id = create_workspace(user_id=user_id, domain=f'{tag}.example', brand_name=tag)
        return user_id, workspace_id, f'prompts_{tag}'


class TrackingPayloadTests(PromptsTestCase):

    def test_tracking_payload_shape_for_a_fresh_workspace(self):
        _user_id, workspace_id, username = self.new_workspace('freshws')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            response = client.get(f'/api/analytics/projects/{workspace_id}/tracking')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        tracking = body['tracking']
        self.assertEqual(tracking['topics'], [])
        self.assertEqual(tracking['competitors'], [])
        self.assertEqual(tracking['prompts'], [])
        self.assertIsNone(tracking['schedule'])
        self.assertIn('providers', tracking)


class TrackedPromptCRUDTests(PromptsTestCase):

    def test_create_prompt_appears_in_tracking(self):
        _user_id, workspace_id, username = self.new_workspace('createprompt')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            create = client.post(
                f'/api/analytics/projects/{workspace_id}/tracked-prompts',
                json={'prompt': 'best expense tools for startups'},
            )
            self.assertEqual(create.status_code, 201)
            prompt_id = create.get_json()['prompt_id']

            tracking = client.get(f'/api/analytics/projects/{workspace_id}/tracking').get_json()
            prompts = tracking['tracking']['prompts']
            self.assertEqual(len(prompts), 1)
            self.assertEqual(prompts[0]['id'], prompt_id)
            self.assertEqual(prompts[0]['prompt'], 'best expense tools for startups')
            self.assertEqual(prompts[0]['intent'], 'Discovery')
            self.assertTrue(prompts[0]['active'])
            self.assertIsNone(prompts[0]['topic_name'])

    def test_create_prompt_rejects_out_of_range_length(self):
        _user_id, workspace_id, username = self.new_workspace('shortprompt')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            response = client.post(
                f'/api/analytics/projects/{workspace_id}/tracked-prompts', json={'prompt': 'short'},
            )
        self.assertEqual(response.status_code, 400)

    def test_patch_toggles_active(self):
        _user_id, workspace_id, username = self.new_workspace('toggleactive')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            create = client.post(
                f'/api/analytics/projects/{workspace_id}/tracked-prompts',
                json={'prompt': 'best expense tools for startups'},
            )
            prompt_id = create.get_json()['prompt_id']

            patch = client.patch(
                f'/api/analytics/projects/{workspace_id}/tracked-prompts/{prompt_id}',
                json={'active': False},
            )
            self.assertEqual(patch.status_code, 200)

            tracking = client.get(f'/api/analytics/projects/{workspace_id}/tracking').get_json()
            self.assertFalse(tracking['tracking']['prompts'][0]['active'])

    def test_patch_updates_topic_and_intent(self):
        """The extension this milestone adds: PATCH now accepts topic_id/intent,
        not just active/prompt."""
        _user_id, workspace_id, username = self.new_workspace('patchtopic')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            topic = client.post(
                f'/api/analytics/projects/{workspace_id}/topics', json={'name': 'Pricing'},
            ).get_json()['topic']
            create = client.post(
                f'/api/analytics/projects/{workspace_id}/tracked-prompts',
                json={'prompt': 'best expense tools for startups'},
            )
            prompt_id = create.get_json()['prompt_id']

            patch = client.patch(
                f'/api/analytics/projects/{workspace_id}/tracked-prompts/{prompt_id}',
                json={'topic_id': topic['id'], 'intent': 'Comparison'},
            )
            self.assertEqual(patch.status_code, 200)

            tracking = client.get(f'/api/analytics/projects/{workspace_id}/tracking').get_json()
            row = tracking['tracking']['prompts'][0]
            self.assertEqual(row['topic_id'], topic['id'])
            self.assertEqual(row['topic_name'], 'Pricing')
            self.assertEqual(row['intent'], 'Comparison')

    def test_patch_rejects_a_topic_from_another_workspace(self):
        _u1, workspace_a, user_a = self.new_workspace('crosstopica')
        _u2, workspace_b, user_b = self.new_workspace('crosstopicb')
        with server_pg.app.test_client() as client:
            self.login(client, user_b)
            foreign_topic = client.post(
                f'/api/analytics/projects/{workspace_b}/topics', json={'name': 'Foreign'},
            ).get_json()['topic']

        with server_pg.app.test_client() as client:
            self.login(client, user_a)
            create = client.post(
                f'/api/analytics/projects/{workspace_a}/tracked-prompts',
                json={'prompt': 'best expense tools for startups'},
            )
            prompt_id = create.get_json()['prompt_id']
            patch = client.patch(
                f'/api/analytics/projects/{workspace_a}/tracked-prompts/{prompt_id}',
                json={'topic_id': foreign_topic['id']},
            )
        self.assertEqual(patch.status_code, 400)

    def test_delete_removes_the_prompt(self):
        _user_id, workspace_id, username = self.new_workspace('deleteprompt')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            create = client.post(
                f'/api/analytics/projects/{workspace_id}/tracked-prompts',
                json={'prompt': 'best expense tools for startups'},
            )
            prompt_id = create.get_json()['prompt_id']

            delete = client.delete(f'/api/analytics/projects/{workspace_id}/tracked-prompts/{prompt_id}')
            self.assertEqual(delete.status_code, 200)

            tracking = client.get(f'/api/analytics/projects/{workspace_id}/tracking').get_json()
            self.assertEqual(tracking['tracking']['prompts'], [])


class TopicCRUDTests(PromptsTestCase):

    def test_create_and_duplicate_topic(self):
        _user_id, workspace_id, username = self.new_workspace('topicdup')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            first = client.post(f'/api/analytics/projects/{workspace_id}/topics', json={'name': 'Pricing'})
            self.assertEqual(first.status_code, 201)
            duplicate = client.post(f'/api/analytics/projects/{workspace_id}/topics', json={'name': 'Pricing'})
        self.assertEqual(duplicate.status_code, 409)

    def test_delete_topic_clears_it_from_prompts_without_deleting_them(self):
        _user_id, workspace_id, username = self.new_workspace('topicdelete')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            topic = client.post(
                f'/api/analytics/projects/{workspace_id}/topics', json={'name': 'Pricing'},
            ).get_json()['topic']
            create = client.post(
                f'/api/analytics/projects/{workspace_id}/tracked-prompts',
                json={'prompt': 'best expense tools for startups', 'topic_id': topic['id']},
            )
            prompt_id = create.get_json()['prompt_id']

            delete = client.delete(f'/api/analytics/projects/{workspace_id}/topics/{topic["id"]}')
            self.assertEqual(delete.status_code, 200)

            tracking = client.get(f'/api/analytics/projects/{workspace_id}/tracking').get_json()
            self.assertEqual(tracking['tracking']['topics'], [])
            prompts = tracking['tracking']['prompts']
            self.assertEqual(len(prompts), 1)
            self.assertEqual(prompts[0]['id'], prompt_id)
            self.assertIsNone(prompts[0]['topic_id'])

    def test_delete_nonexistent_topic_is_404(self):
        _user_id, workspace_id, username = self.new_workspace('topic404')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            response = client.delete(f'/api/analytics/projects/{workspace_id}/topics/999999')
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
