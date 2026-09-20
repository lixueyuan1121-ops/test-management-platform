"""个人授权隔离回归；内存 DB 和模拟外部请求，不创建真实缺陷。"""
import time
import tempfile
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.api import geelib_account, issues
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.db.session import Base, get_db
from app.models import User, Project, ProjectMember, RemainingIssue, GeelibAccount
from app.core.enums import ProjectRole
from app.services import geelib, geelib_account as accounts


class PersonalAuthTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config_file = Path(directory.name) / 'sso.json'
        config_file.write_text('{}')
        p = patch.object(settings, 'GEELIB_SSO_CONFIG_FILE', str(config_file)); p.start(); self.addCleanup(p.stop)
        p = patch.object(settings, 'GEELIB_TOKEN_KEY_FILE', str(Path(directory.name) / 'geelib.key')); p.start(); self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'SSO_URL': '', 'SSO_CLIENT_ID': '', 'SSO_CLIENT_SECRET': ''}); p.start(); self.addCleanup(p.stop)
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        for uid in range(1, 5):
            self.db.add(User(id=uid, username=f'user{uid}', name=f'User {uid}', email=f'user{uid}@example.com', password_hash='unused'))
        self.db.add(Project(id=1, name='Project', code='test', geelib_sub_id=419))
        self.db.flush()
        for uid, role in [(1, ProjectRole.admin), (2, ProjectRole.member), (3, ProjectRole.guest)]:
            self.db.add(ProjectMember(project_id=1, user_id=uid, role=role))
        for iid in range(1, 8):
            self.db.add(RemainingIssue(id=iid, project_id=1, title=f'Bug {iid}', owner=1))
        self.db.commit()
        for name, value in dict(GEELIB_ENABLED=True, GEELIB_OAUTH_CLIENT_ID='test-client', GEELIB_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode()).items():
            p = patch.object(settings, name, value); p.start(); self.addCleanup(p.stop)
        app = FastAPI(); register_exception_handlers(app)
        app.include_router(geelib_account.router); app.include_router(issues.router)
        def session():
            yield self.db
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app)
        self.addCleanup(self.engine.dispose); self.addCleanup(self.db.close); self.addCleanup(self.client.close)
        p = patch.object(geelib, 'get_app_token', side_effect=AssertionError('manual operation used shared token'))
        self.shared = p.start(); self.addCleanup(p.stop)

    def request(self, method, path, uid=1, **kwargs):
        return self.client.request(method, path, headers={'Authorization': f'Bearer {create_access_token(str(uid))}'}, **kwargs)

    def bind(self, uid=1, expires_in=3600):
        flow = self.request('POST', '/api/auth/geelib/authorize', uid).json()['data']
        with patch.object(accounts, '_post', return_value={'access_token': f'access-{uid}', 'refresh_token': f'refresh-{uid}', 'expires_in': expires_in, 'username': f'corporate-{uid}'}) as exchange:
            result = self.request('POST', '/api/auth/geelib/complete', uid, json={'state': flow['state'], 'code': f'code-{uid}'})
        self.assertEqual(result.status_code, 200, result.text)
        params = parse_qs(urlparse(flow['authorization_url']).query)
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertIn('code_verifier', exchange.call_args.kwargs['data'])
        return flow, result

    def test_existing_deployment_without_new_env_can_bind(self):
        # 回归截图中的真实条件：部署只配置过 GEELIB_ENABLED，没有新增 OAuth/key 环境变量。
        with patch.object(settings, 'GEELIB_OAUTH_CLIENT_ID', ''), patch.object(settings, 'GEELIB_SSO_URL', ''), patch.object(settings, 'GEELIB_TOKEN_ENCRYPTION_KEY', ''):
            status = self.request('GET', '/api/auth/geelib').json()['data']
            self.assertTrue(status['configured'], status)
            flow = self.request('POST', '/api/auth/geelib/authorize').json()['data']
            params = parse_qs(urlparse(flow['authorization_url']).query)
            self.assertEqual(params['client_id'], ['a14ca4e6e00a488991b15501901fafbd'])
            self.assertEqual(urlparse(flow['authorization_url']).hostname, 'sts.login.ops.qihoo.net')
            # 下一请求/重建 cipher 后仍能解密绑定会话，不依赖进程缓存。
            with patch.object(accounts, '_post', return_value={'access_token': 'personal', 'username': 'own-account'}):
                result = self.request('POST', '/api/auth/geelib/complete', json={'state': flow['state'], 'code': 'code'})
                self.assertEqual(result.status_code, 200, result.text)
            self.assertNotIn('personal', self.db.get(GeelibAccount, 1).credentials)
            self.shared.assert_not_called()

    def test_binding_encrypted_and_private(self):
        _, result = self.bind()
        row = self.db.get(GeelibAccount, 1)
        self.assertNotIn('access-1', row.credentials); self.assertNotIn('refresh-1', row.credentials)
        self.assertIsNone(row.pending_auth); self.assertNotIn('token', result.text)
        self.assertFalse(self.request('GET', '/api/auth/geelib', 2).json()['data']['bound'])
        self.assertEqual(self.client.get('/api/auth/geelib').status_code, 403)

    def test_state_owner_expiry_and_replay(self):
        flow, _ = self.bind()
        self.assertEqual(self.request('POST', '/api/auth/geelib/complete', json={'state': flow['state'], 'code': 'replay'}).status_code, 409)
        flow = self.request('POST', '/api/auth/geelib/authorize').json()['data']
        self.request('POST', '/api/auth/geelib/authorize', 2)
        with patch.object(accounts, '_post') as post:
            for uid, state in [(2, flow['state']), (1, 'wrong-state')]:
                self.assertEqual(self.request('POST', '/api/auth/geelib/complete', uid, json={'state': state, 'code': 'code'}).status_code, 409)
            row = self.db.get(GeelibAccount, 1)
            pending = accounts._decrypt(row.pending_auth); pending['expires_at'] = time.time() - 1
            row.pending_auth = accounts._encrypt(pending); self.db.commit()
            self.assertEqual(self.request('POST', '/api/auth/geelib/complete', json={'state': flow['state'], 'code': 'code'}).status_code, 409)
            post.assert_not_called()

    def test_two_users_report_with_their_own_tokens(self):
        self.bind(1); self.bind(2)
        headers = []
        def authorize(path, **kwargs):
            uid = kwargs['headers']['Authorization'].split('-')[-1]
            return {'errcode': 0, 'app_token': f'personal-{uid}'}
        def post(url, **kwargs):
            headers.append(kwargs['headers']['X-Agent-Auth'])
            response = Mock(status_code=200, content=b'x')
            response.json.return_value = {'errno': 2000, 'data': len(headers)}
            return response
        with patch.object(accounts, '_post', side_effect=authorize), patch.object(geelib.requests, 'post', side_effect=post):
            for uid in (1, 2):
                r = self.request('POST', f'/api/issues/{uid}/report-geelib', uid)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()['data']['external_ref'], f'geelib#{uid}')
            self.assertTrue(self.request('POST', '/api/issues/1/report-geelib').json()['data']['already_reported'])
        self.assertEqual(headers, ['Bearer personal-1', 'Bearer personal-2'])
        self.shared.assert_not_called()

    def test_unbound_guest_and_nonmember_cannot_report(self):
        with patch.object(geelib.requests, 'post') as post:
            for uid, status in [(1, 409), (2, 409), (3, 403), (4, 403)]:
                r = self.request('POST', '/api/issues/1/report-geelib', uid)
                self.assertEqual(r.status_code, status, r.text)
            post.assert_not_called()
        self.assertIsNone(self.db.get(RemainingIssue, 1).external_ref)

    def test_refresh_is_scoped_and_preserves_rotated_token(self):
        self.bind(1, -1); self.bind(2)
        before = self.db.get(GeelibAccount, 2).credentials
        with patch.object(accounts, '_post', side_effect=[{'access_token': 'new-access', 'refresh_token': 'rotated', 'expires_in': 3600}, {'errcode': 0, 'app_token': 'new-app'}]) as post:
            self.assertEqual(accounts.get_user_app_token(self.db, 1), 'new-app')
            self.assertEqual(post.call_args_list[0].kwargs['data']['refresh_token'], 'refresh-1')
            self.assertEqual(post.call_args_list[1].kwargs['headers']['Authorization'], 'Bearer new-access')
        self.assertEqual(accounts._decrypt(self.db.get(GeelibAccount, 1).credentials)['refresh_token'], 'rotated')
        self.assertEqual(self.db.get(GeelibAccount, 2).credentials, before)

    def test_auth_failure_never_falls_back_or_sets_ref(self):
        self.bind(1, -1)
        with patch.object(accounts, '_post', side_effect=accounts.GeelibAccountError('个人授权失效')):
            self.assertEqual(self.request('POST', '/api/issues/1/report-geelib').status_code, 409)
        self.assertIsNone(self.db.get(RemainingIssue, 1).external_ref)
        self.shared.assert_not_called()

    def test_unbind_only_affects_self(self):
        self.bind(1); self.bind(2)
        self.request('DELETE', '/api/auth/geelib')
        self.assertIsNone(self.db.get(GeelibAccount, 1))
        self.assertIsNotNone(self.db.get(GeelibAccount, 2).credentials)
        self.assertEqual(self.request('POST', '/api/issues/1/report-geelib').status_code, 409)

    def test_resolve_uses_personal_identity_and_retains_local_success(self):
        self.bind()
        issue = self.db.get(RemainingIssue, 1); issue.external_ref = 'geelib#99'; self.db.commit()
        with patch.object(accounts, '_post', return_value={'errcode': 0, 'app_token': 'personal-1'}), patch.object(geelib, '_post_matter_edit_status') as post:
            r = self.request('PATCH', '/api/issues/1', json={'status': 'resolved'})
            self.assertTrue(r.json()['data']['geelib_sync']['ok'])
            self.assertEqual(post.call_args.kwargs['app_token'], 'personal-1')
        self.request('DELETE', '/api/auth/geelib')
        self.request('PATCH', '/api/issues/1', json={'status': 'open'})
        r = self.request('PATCH', '/api/issues/1', json={'status': 'resolved'})
        self.assertEqual(r.json()['data']['status'], 'resolved')
        self.assertFalse(r.json()['data']['geelib_sync']['ok'])

    def test_empty_explicit_token_does_not_use_server_account(self):
        with self.assertRaises(geelib.GeelibError):
            geelib.report_defect(419, 'test', app_token='')
        self.shared.assert_not_called()

    def test_sso_failure_does_not_expose_upstream_tokens(self):
        response = Mock(status_code=400)
        response.json.return_value = {'access_token': 'secret', 'error_description': 'secret'}
        with patch.object(accounts.requests, 'post', return_value=response):
            with self.assertRaises(accounts.GeelibAccountError) as raised:
                accounts._post('/oauth/token', data={'code': 'secret'})
        self.assertNotIn('secret', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
