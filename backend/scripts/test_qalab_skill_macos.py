"""Portable tests for the macOS helper; does not claim native Keychain coverage."""
import copy
import io
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'tools/skills/qalab-requirement-test/scripts'
sys.path.insert(0, str(SCRIPTS))
import qalab


class Store:
    def __init__(self): self.value = None
    def load(self): return copy.deepcopy(self.value)
    def save(self, value): self.value = copy.deepcopy(value)


class FakeApi:
    origin = qalab.DEFAULT_ORIGIN
    def __init__(self): self.calls = []; self.payload = None; self.tamper = False; self.interface = True; self.v2 = False; self.conflict = False
    def __call__(self, method, path, body=None, token=None):
        self.calls.append((method, path, body, token))
        if path == '/api/auth/login': return {'access_token': 'a', 'refresh_token': 'r'}
        if path == '/api/auth/refresh': return {'access_token': 'new', 'refresh_token': 'rotated'}
        if path == '/api/auth/me': return {'user': {'username': 'tester'}}
        if path == '/api/projects': return [{'id': 1, 'name': 'PC'}]
        if path == '/api/devices': return [{'id': 2, 'runner_id': 'mac', 'token': 'never-display'}]
        if path == '/openapi.json': return {'paths': {'/api/verified-imports': {'post': {}}, '/api/verified-imports/preview': {'post': {}}}} if self.interface else {}
        if path == '/api/verified-imports/preview': return {'ready': False, 'cases': [{'match': 'ambiguous', 'candidates': [{'case_id': 9}]}]}
        if path == '/api/verified-imports':
            self.payload = copy.deepcopy(body)
            return {'project_id': 1, 'batch_id': 'batch', 'records': [{'case_id': 9, 'run_id': 10, 'title': 'Logo', **({'disposition': 'reused'} if self.v2 else {})}]}
        if path == '/api/exec-queue/10':
            case = copy.deepcopy(self.payload['cases'][0])
            if self.tamper: case['script'][0]['target']['selector'] = '.wrong'
            return {'project_id': 1, 'test_case_id': 9, 'verdict': 'pass', 'payload': case, 'report': case['report']}
        if path == '/api/ai/testcases/9':
            case = copy.deepcopy(self.payload['cases'][0]); case['project_id'] = 1
            if self.tamper: case['script'][0]['target']['selector'] = '.wrong'
            case['script'] = json.dumps(case['script'])
            if self.v2: case['title'] = 'canonical original title'
            return case
        raise AssertionError(path)


def payload():
    return {'project_id': 1, 'runner_device_id': 2, 'external_id': 'test-stable-id', 'cases': [{
        'title': 'Logo', 'steps': 'Click and check home', 'expected': 'home', 'verdict': 'pass',
        'script': [{'action': 'assert_visible', 'target': {'selector': '.home'}, 'desc': 'check home'}],
        'report': [{'action': 'assert_visible', 'ok': True, 'check': {'actual': True, 'expected': True}}]}]}


class TestMacHelper(unittest.TestCase):
    def setUp(self): self.api = FakeApi(); self.store = Store(); self.client = qalab.Client(self.api, self.store)
    def test_https_origin_and_credential_boundary(self):
        self.assertEqual(qalab.normalize_origin('https://QALAB.claw.qihoo.net:443/'), qalab.DEFAULT_ORIGIN)
        for url in ['http://localhost:8000', 'https://u:p@host', 'https://host/path', 'https://host/?token=x', 'https://host/#x']:
            with self.subTest(url=url), self.assertRaises(RuntimeError): qalab.normalize_origin(url)
    def test_login_refresh_does_not_save_password(self):
        self.assertEqual(self.client.login('tester', 'do-not-store'), 'a')
        self.assertNotIn('do-not-store', json.dumps(self.store.value))
        self.assertEqual(self.client.token(), 'new')
        self.assertEqual(self.store.value['refresh_token'], 'rotated')
    def test_status_does_not_expose_device_token(self):
        self.assertNotIn('never-display', json.dumps(self.client.status('a')))
    def test_import_preserves_external_id_and_checks_complete_script(self):
        source = payload()
        self.assertEqual(self.client.import_cases(source, 'a')['batch_id'], 'batch')
        self.assertEqual(self.api.payload, source)
        self.api.tamper = True
        with self.assertRaisesRegex(RuntimeError, '读回复核'): self.client.import_cases(source, 'a')
    def test_reused_case_checks_actual_execution_and_preview_does_not_import(self):
        self.api.v2 = True
        self.assertEqual(self.client.import_cases(payload(), 'a')['batch_id'], 'batch')
        self.api.tamper = True
        with self.assertRaisesRegex(RuntimeError, '读回复核'): self.client.import_cases(payload(), 'a')
        self.api.calls.clear()
        self.assertFalse(self.client.preview(payload(), 'a')['ready'])
        self.assertFalse(any(c[1] == '/api/verified-imports' for c in self.api.calls))
    def test_confirmation_conflict_is_actionable(self):
        api = qalab.Api(qalab.DEFAULT_ORIGIN)
        class Opener:
            def open(self, req, timeout):
                body = {'data': {'reason': 'duplicate_confirmation_required', 'cases': [{'candidates': [{'case_id': 9}]}]}}
                raise HTTPError(req.full_url, 409, 'conflict', {}, io.BytesIO(json.dumps(body).encode()))
        api.opener = Opener()
        with self.assertRaisesRegex(RuntimeError, '需确认疑似重复') as raised: api('POST', '/api/verified-imports', payload(), 'a')
        self.assertIn('case_id', str(raised.exception))
    def test_missing_interface_and_wrong_device_never_post(self):
        self.api.interface = False
        with self.assertRaises(RuntimeError): self.client.import_cases(payload(), 'a')
        self.api.interface = True
        wrong = payload(); wrong['runner_device_id'] = 999
        with self.assertRaises(RuntimeError): self.client.import_cases(wrong, 'a')
        self.assertFalse(any(c[1] == '/api/verified-imports' for c in self.api.calls))
    def test_failed_or_partial_report_never_posts(self):
        for mode in ['fail', 'partial', 'no_actual', 'failed_check']:
            source = payload()
            if mode == 'fail': source['cases'][0]['verdict'] = 'fail'
            if mode == 'partial': source['cases'][0]['report'] = []
            if mode == 'no_actual': del source['cases'][0]['report'][0]['check']['actual']
            if mode == 'failed_check': source['cases'][0]['report'][0]['check']['pass'] = False
            with self.subTest(mode=mode), self.assertRaises(RuntimeError): self.client.import_cases(source, 'a')
        self.assertEqual(self.api.calls, [])
    def test_redirect_is_not_followed(self):
        self.assertIsNone(qalab.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other-host/'))
    def test_http_error_does_not_echo_sensitive_response(self):
        api = qalab.Api(qalab.DEFAULT_ORIGIN)
        class Opener:
            def open(self, req, timeout): raise HTTPError(req.full_url, 401, 'secret-token', {}, None)
        api.opener = Opener()
        with self.assertRaisesRegex(RuntimeError, 'HTTP 401') as raised: api('GET', '/api/projects', token='secret-token')
        self.assertNotIn('secret-token', str(raised.exception))


if __name__ == '__main__': unittest.main()
