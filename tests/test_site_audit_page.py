"""Site Audit page (/site-audit) and the endpoints it reads/writes:
GET .../audit (app/jobs.py::latest_site_audit(), unmodified by this
milestone) and POST .../audits (queues a crawl job). Neither endpoint had
any test coverage before this file; the crawler/scoring internals that
produce scores and findings are untouched and out of scope here.
"""

import os
import unittest
from datetime import datetime

os.environ['APP_ENV'] = 'development'
os.environ['SECRET_KEY'] = 'site-audit-test-secret'

import server_pg  # noqa: E402
from conftest import create_workspace  # noqa: E402

from sqlalchemy import insert  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    analytics_audit_findings,
    analytics_audit_jobs,
    analytics_audit_pages,
    analytics_site_audits,
    users,
)

PASSWORD = 'site-audit-password-123'


def make_user(username):
    with engine.begin() as conn:
        return conn.execute(insert(users).values(
            username=username, email=f'{username}@example.com',
            password_hash=generate_password_hash(PASSWORD),
            created_at=datetime.utcnow(),
        )).inserted_primary_key[0]


class SiteAuditTestCase(unittest.TestCase):
    def login(self, client, username):
        response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, f'login failed for {username}')

    def new_workspace(self, tag):
        user_id = make_user(f'siteaudit_{tag}')
        workspace_id = create_workspace(user_id=user_id, domain=f'{tag}.example', brand_name=tag)
        return workspace_id, f'siteaudit_{tag}'


class SiteAuditPageTests(SiteAuditTestCase):

    def test_site_audit_redirects_anonymous_to_login(self):
        with server_pg.app.test_client() as client:
            response = client.get('/site-audit')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/login'))

    def test_site_audit_returns_200_for_logged_in_user(self):
        make_user('pages_site_audit_user')
        with server_pg.app.test_client() as client:
            self.login(client, 'pages_site_audit_user')
            response = client.get('/site-audit')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.content_type)


class AuditReportEndpointTests(SiteAuditTestCase):

    def test_empty_state_has_no_audit(self):
        workspace_id, username = self.new_workspace('emptyaudit')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            response = client.get(f'/api/analytics/projects/{workspace_id}/audit')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIsNone(body['audit'])
        self.assertIsNone(body['active_job'])

    def test_populated_audit_shape(self):
        workspace_id, username = self.new_workspace('fullaudit')
        now = datetime.utcnow()
        with engine.begin() as conn:
            audit_id = conn.execute(insert(analytics_site_audits).values(
                workspace_id=workspace_id, job_id=None, status='succeeded',
                source_type='website_crawl', start_url='https://fullaudit.example/',
                final_url='https://fullaudit.example/', pages_discovered=2,
                pages_audited=2, pages_failed=0, readiness_score=72,
                metadata_score=80, content_score=65, crawlability_score=90,
                structured_data_score=50, summary='ok', created_at=now, completed_at=now,
            )).inserted_primary_key[0]
            page_id = conn.execute(insert(analytics_audit_pages).values(
                audit_id=audit_id, url='https://fullaudit.example/', final_url=None,
                fetched=True, http_status=200, title='Home', description=None,
                headings_count=3, word_count=400, schema_blocks=0, canonical=None,
                noindex=False, language='en', internal_links=5, external_links=1,
                readiness_score=72, metadata_score=80, content_score=65,
                crawlability_score=90, structured_data_score=50, issues_count=1,
                error=None, fetched_at=now,
            )).inserted_primary_key[0]
            conn.execute(insert(analytics_audit_findings).values(
                audit_id=audit_id, page_id=page_id, code='missing_schema',
                area='Structured data', severity='medium',
                evidence='No JSON-LD blocks were found.',
                recommendation='Add valid JSON-LD.',
            ))
        with server_pg.app.test_client() as client:
            self.login(client, username)
            response = client.get(f'/api/analytics/projects/{workspace_id}/audit')
        self.assertEqual(response.status_code, 200)
        audit = response.get_json()['audit']
        self.assertEqual(audit['run']['readiness_score'], 72)
        self.assertEqual(audit['run']['status'], 'succeeded')
        self.assertEqual(len(audit['pages']), 1)
        self.assertEqual(audit['pages'][0]['url'], 'https://fullaudit.example/')
        self.assertEqual(len(audit['findings']), 1)
        self.assertEqual(audit['findings'][0]['severity'], 'medium')
        self.assertEqual(audit['findings'][0]['page_id'], page_id)
        self.assertEqual(len(audit['history']), 1)


class StartAuditEndpointTests(SiteAuditTestCase):

    def test_start_audit_queues_a_job(self):
        workspace_id, username = self.new_workspace('startaudit')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            response = client.post(f'/api/analytics/projects/{workspace_id}/audits', json={})
        self.assertEqual(response.status_code, 202)
        body = response.get_json()
        self.assertEqual(body['status'], 'accepted')
        self.assertIn('job_id', body)

        with engine.connect() as conn:
            from sqlalchemy import select
            job = conn.execute(select(analytics_audit_jobs).where(
                analytics_audit_jobs.c.id == body['job_id']
            )).mappings().first()
        self.assertEqual(job['job_type'], 'site_audit')
        self.assertEqual(job['status'], 'queued')

    def test_start_audit_is_idempotent_while_one_is_active(self):
        workspace_id, username = self.new_workspace('startauditagain')
        with server_pg.app.test_client() as client:
            self.login(client, username)
            first = client.post(f'/api/analytics/projects/{workspace_id}/audits', json={})
            second = client.post(f'/api/analytics/projects/{workspace_id}/audits', json={})
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.get_json()['job_id'], second.get_json()['job']['id'])


if __name__ == '__main__':
    unittest.main()
