"""Dispatch-to-report regression tests. The skill posts to a loopback tracker."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import md_runs
from app.core.config import settings
from app.core.deps import RunnerCtx, get_current_user, require_runner_ctx
from app.db.session import Base, get_db
from app.models import Project, User
from app.services import md_bug_skill


class MdDispatchTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        sessions = sessionmaker(engine)
        with sessions.begin() as db:
            db.add_all([Project(id=1, name='QA', code='qa', geelib_sub_id=81),
                        User(id=1, name='QA', username='qa', password_hash='x',
                             email='qa@example.test', is_platform_admin=True)])
        def database():
            with sessions() as db:
                yield db
        self.user = User(id=1, is_platform_admin=True, email='qa@example.test')
        app = FastAPI()
        app.include_router(md_runs.router)
        app.dependency_overrides.update({get_db: database, get_current_user: lambda: self.user,
                                         require_runner_ctx: lambda: RunnerCtx()})
        self.client = TestClient(app)
        self.document = dict(title='测试', req_no='42', env_group='pre', content='# 文档', project_id=1)
        self.addCleanup(self.client.close)

    def post(self, path, **payload):
        response = self.client.post('/api/md-runs' + path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data']

    def test_multi_runner_validation_and_queue_isolation(self):
        rows = self.post('/batch', **self.document, runners=[' a ', 'b', 'a'])
        self.assertEqual([r['runner'] for r in rows], ['a', 'b'])
        for invalid in (dict(runners=[]), dict(runners=['  ']), dict(runners=['x'], env_group='custom')):
            self.assertEqual(self.client.post('/api/md-runs/batch', json={**self.document, **invalid}).status_code, 422)
        legacy = self.post('', **{**self.document, 'env_group': 'work-test'}, runner='legacy')
        self.assertEqual(legacy['runner'], 'legacy')
        for row in rows:
            rid, runner = row['id'], row['runner']
            queue = self.client.get('/api/md-runs/queue', params={'runner': runner}).json()['data']
            self.assertEqual([r['id'] for r in queue], [rid])
            self.assertEqual(self.client.post(f'/api/md-runs/queue/{rid}/claim?runner=other').status_code, 403)
            self.post(f'/queue/{rid}/claim?runner={runner}')
            self.assertEqual(self.client.post(f'/api/md-runs/queue/{rid}/claim?runner={runner}').status_code, 409)

    def finished_run(self):
        rid = self.post('', **self.document, runner='a')['id']
        self.post(f'/queue/{rid}/claim?runner=a')
        self.results = dict(verdict='fail', findings=[{'title': '按钮无响应', 'detail': '点 <确认>\n仍停留', 'severity': 'major'}])
        self.callback = f'/api/md-runs/queue/{rid}?runner=a'
        self.assertEqual(self.client.patch(self.callback, json=self.results).status_code, 200)
        return rid

    def test_skill_receipt_and_replay(self):
        rid = self.finished_run()
        calls = []
        class Tracker(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append((self.path, self.headers['X-Agent-Auth'], json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"errno":2000,"data":"729"}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Tracker)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(settings, 'GEELIB_API_URL', f'http://127.0.0.1:{server.server_port}'), \
             patch.object(settings, 'GEELIB_ENABLED', True), \
             patch('app.services.geelib_account.get_user_app_token', return_value='local-test-token'):
            self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 1)
            self.client.patch(self.callback, json=self.results)
            self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 0)
        self.assertEqual(len(calls), 1)
        path, header, body = calls[0]
        self.assertEqual((path, header, body['sub_id'], body['title']),
                         ('/openapi/Matter/add', 'Bearer local-test-token', 81, '[42] 按钮无响应'))
        self.assertIn('&lt;确认&gt;</p><p>', body['content'])
        self.assertEqual(self.client.get(f'/api/md-runs/{rid}').json()['data']['findings'][0]['external_ref'], 'geelib#729')

    def test_tracker_rejection_can_retry_but_lost_receipt_is_not_resubmitted(self):
        rid = self.finished_run()
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch('app.services.geelib_account.get_user_app_token', return_value='local-test-token'), \
             patch.object(md_bug_skill, 'create_case', side_effect=[md_bug_skill.GeelibError('字段无效'),
                          md_bug_skill.SubmissionUncertain('回执丢失')]) as submit:
            for _ in range(3):
                self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 0)
            self.assertEqual(submit.call_count, 2)
