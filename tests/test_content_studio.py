"""Content Studio page (/content-studio) and the pre-existing
app/routes/content.py endpoints, which had zero test coverage before this
file. No backend logic changes - this only adds coverage for what already
existed (create/list/patch/generate/delete) and the new page's auth gate.
"""

import os
import unittest
from datetime import datetime

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'content-studio-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import users  # noqa: E402

PASSWORD = 'content-studio-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


class ContentStudioTestCase(unittest.TestCase):
    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def new_workspace(self, tag):
        user_id = make_user(f'cs_{tag}')
        workspace_id = create_workspace(user_id=user_id, domain=f'{tag}.example', brand_name=tag)
        return workspace_id, f'cs_{tag}'


class DocumentCRUDTests(ContentStudioTestCase):

    def test_create_requires_a_workspace_the_user_can_reach(self):
        workspace_id, username = self.new_workspace('createreq')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            response = client.post('/api/content-studio/documents', json={
                'title': 'Best CRM for startups', 'brand_name': 'Acme',
                'keyword': 'best crm', 'content_type': 'Blog post', 'tone': 'Expert',
            })
        self.assertEqual(response.status_code, 400)

    def test_create_then_appears_in_the_cross_workspace_list(self):
        workspace_id, username = self.new_workspace('createlist')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            create = client.post('/api/content-studio/documents', json={
                'title': 'Best CRM for startups', 'brand_name': 'Acme',
                'keyword': 'best crm', 'content_type': 'Blog post', 'tone': 'Expert',
                'workspace_id': workspace_id,
            })
            self.assertEqual(create.status_code, 201)
            document = create.get_json()['document']
            self.assertEqual(document['status'], 'Brief')
            self.assertEqual(document['content'], '')

            listing = client.get('/api/content-studio/documents')
            titles = [d['title'] for d in listing.get_json()['documents']]
            self.assertIn('Best CRM for startups', titles)

    def test_rejects_invalid_content_type_or_tone(self):
        workspace_id, username = self.new_workspace('invalidtype')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            response = client.post('/api/content-studio/documents', json={
                'title': 'X', 'brand_name': 'Acme', 'keyword': 'crm',
                'content_type': 'Not a real type', 'tone': 'Expert',
                'workspace_id': workspace_id,
            })
        self.assertEqual(response.status_code, 400)

    def test_patch_edits_the_draft_but_not_the_brief(self):
        workspace_id, username = self.new_workspace('patchbrief')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            create = client.post('/api/content-studio/documents', json={
                'title': 'Original title', 'brand_name': 'Acme', 'keyword': 'crm',
                'content_type': 'Blog post', 'tone': 'Expert', 'workspace_id': workspace_id,
            })
            document_id = create.get_json()['document']['id']

            patch = client.patch(f'/api/content-studio/documents/{document_id}', json={
                'title': 'Updated title', 'content': 'New body copy.',
                'brand_name': 'Someone Else', 'keyword': 'changed keyword',
            })
            self.assertEqual(patch.status_code, 200)
            document = patch.get_json()['document']
            self.assertEqual(document['title'], 'Updated title')
            self.assertEqual(document['content'], 'New body copy.')
            # The brief is immutable through PATCH - unrecognised fields are
            # silently ignored, not applied.
            self.assertEqual(document['brand_name'], 'Acme')
            self.assertEqual(document['keyword'], 'crm')
            self.assertEqual(document['version'], 1)

    def test_generate_populates_a_structured_draft(self):
        workspace_id, username = self.new_workspace('generate')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            create = client.post('/api/content-studio/documents', json={
                'title': 'Best CRM for startups', 'brand_name': 'Acme',
                'keyword': 'best crm', 'content_type': 'Blog post', 'tone': 'Expert',
                'workspace_id': workspace_id,
            })
            document_id = create.get_json()['document']['id']

            generated = client.post(f'/api/content-studio/documents/{document_id}/generate')
        self.assertEqual(generated.status_code, 200)
        document = generated.get_json()['document']
        self.assertEqual(document['status'], 'Draft')
        self.assertIn('Acme', document['content'])
        self.assertIn('best crm', document['content'])
        self.assertTrue(document['seo_title'])
        self.assertTrue(document['outline'])
        self.assertTrue(document['recommendations'])

    def test_delete_removes_the_document(self):
        workspace_id, username = self.new_workspace('delete')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            create = client.post('/api/content-studio/documents', json={
                'title': 'To delete', 'brand_name': 'Acme', 'keyword': 'crm',
                'content_type': 'Blog post', 'tone': 'Expert', 'workspace_id': workspace_id,
            })
            document_id = create.get_json()['document']['id']

            delete = client.delete(f'/api/content-studio/documents/{document_id}')
            self.assertEqual(delete.status_code, 200)

            get_after = client.get(f'/api/content-studio/documents/{document_id}')
            self.assertEqual(get_after.status_code, 404)

    def test_cross_tenant_document_is_not_found(self):
        workspace_a, user_a = self.new_workspace('tenanta')
        _workspace_b, user_b = self.new_workspace('tenantb')
        with server_pg.app.test_client() as client:
            self.login(client, user_a)
            create = client.post('/api/content-studio/documents', json={
                'title': 'Tenant A doc', 'brand_name': 'Acme', 'keyword': 'crm',
                'content_type': 'Blog post', 'tone': 'Expert', 'workspace_id': workspace_a,
            })
            document_id = create.get_json()['document']['id']

        with server_pg.app.test_client() as client:
            self.login(client, user_b)
            response = client.get(f'/api/content-studio/documents/{document_id}')
        self.assertEqual(response.status_code, 404)


class ContentStudioPageTests(unittest.TestCase):

    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def test_content_studio_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/content-studio')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_content_studio_returns_200_for_logged_in_user(self):
        make_user('pages_content_studio_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_content_studio_user')
            response = client.get('/content-studio')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


if __name__ == '__main__':
    unittest.main()
