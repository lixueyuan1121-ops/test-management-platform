"""Offline contract and cross-device regression; never contacts the live platform."""
import copy
import json
import unittest
from app.models import RunnerDevice, ExecRun, TestCase, User, VerifiedImportJob
from app.api.verified_import_jobs import router
from app.services.verified_import_jobs import drain_once
from app.services.execution_evidence import fingerprint, normalize_script
from scripts import test_verified_dedup_pagination as fixtures
source = fixtures.source


def seal(body):
    for case in body['cases']:
        case['codex_evidence']['script_sha256'] = fingerprint(normalize_script(case['script']))
        case['codex_evidence']['report_sha256'] = fingerprint(case['report'])
    return body


def codex_source(failed=False, external='codex-fixture-01'):
    body = source(external)
    body.pop('runner_device_id')
    body['import_mode'] = 'codex'
    c = body['cases'][0]
    c.update(executor='codex', verdict='fail' if failed else 'pass')
    for i, r in enumerate(c['report']):
        r.update(no=i+1, observation='Offline fixture observation: 页面中文证据', evidence_ref='log-1')
        if 'check' in r:
            r['check']['pass'] = True
    c['codex_evidence'] = {'version': 'qalab-codex-v1', 'tool': 'offline test fixture',
                           'artifacts': [{'id': 'log-1', 'kind': 'log', 'reference': 'fixture://observations'}]}
    if failed:
        c['report'][-1].update(ok=False, fail_kind='business', error='首页未展示', check={'actual': False, 'expected': True, 'pass': False})
        # Keep the full regression, but never claim unexecuted steps passed.
        c['script'].append({'action': 'assert_text', 'target': {'selector': '#title'}, 'args': {'expected': '首页'}})
    return seal(body)


class CodexTests(unittest.TestCase):
    tearDown = fixtures.ApiTests.tearDown
    count = fixtures.ApiTests.count

    def setUp(self):
        fixtures.ApiTests.setUp(self)
        self.client.app.include_router(router)
        with self.factory() as db:
            db.delete(db.get(RunnerDevice, 1))  # Computer A / account A has no Runner.
            db.add_all([User(id=i, username=f'u{i}', name=f'u{i}', password_hash='fixture', is_platform_admin=i in (1, 2)) for i in (1, 2, 3)])
            db.commit()

    def submit(self, body, user=1):
        return self.client.post('/api/verified-imports/jobs', json=body, headers={'x-user': str(user)})

    def imported(self, body):
        r = self.submit(body)
        self.assertEqual(r.status_code, 202, r.text)
        jid = r.json()['data']['job_id']
        drain_once(self.factory)
        job = self.client.get(f'/api/verified-imports/jobs/{jid}').json()['data']
        self.assertEqual(job['status'], 'completed', job)
        return job['items'][0]['receipt']

    def test_no_runner_import_fail_then_other_account_replays(self):
        body = codex_source(True)
        receipt = self.imported(body)
        self.assertEqual(receipt['verdict'], 'fail')
        with self.factory() as db:
            run = db.get(ExecRun, receipt['run_id'])
            self.assertEqual((run.runner, run.runner_device_id, run.status, run.verdict, run.fail_kind), ('codex', None, 'failed', 'fail', 'business'))
            self.assertEqual(json.loads(run.report), body['cases'][0]['report'])
            self.assertEqual(normalize_script(json.loads(db.get(TestCase, receipt['case_id']).script)), normalize_script(body['cases'][0]['script']))
        dispatch = {'project_id': 1, 'runner': 'mac', 'test_case_ids': [receipt['case_id']], 'auto_prepare': False}
        queued = self.client.post('/api/exec-queue/enqueue-cases', json=dispatch, headers={'x-user': '2'})
        self.assertEqual(queued.status_code, 200, queued.text)
        with self.factory() as db:
            new = db.query(ExecRun).filter(ExecRun.id != receipt['run_id']).one()
            self.assertEqual((new.runner, new.runner_device_id, new.status), ('mac', 2, 'pending'))
            payload = json.loads(new.payload)
            self.assertNotIn('verified_import', payload)
            self.assertEqual(normalize_script(payload['script']), normalize_script(body['cases'][0]['script']))
            self.assertEqual(db.get(ExecRun, receipt['run_id']).verdict, 'fail')

    def test_pass_and_idempotency(self):
        body = codex_source()
        receipt = self.imported(body)
        self.assertEqual(receipt['verdict'], 'pass')
        self.assertEqual(self.submit(body).status_code, 202)
        drain_once(self.factory)
        self.assertEqual(self.count(ExecRun), 1)
        changed = copy.deepcopy(body); changed['requirement'] = 'changed'
        self.assertEqual(self.submit(changed).status_code, 409)

    def test_reject_invalid_provenance_and_false_results(self):
        mutations = [
            lambda b: b.update(runner_device_id=2),
            lambda b: b.update(import_mode='unknown'),
            lambda b: b['cases'][0]['report'][0].update(observation=''),
            lambda b: b['cases'][0]['report'][0].update(evidence_ref='missing'),
            lambda b: b['cases'][0]['report'][-1]['check'].update(actual=False),
            lambda b: b['cases'][0].update(selector_registry={}),
            lambda b: b['cases'][0].update(verdict='fail'),
        ]
        for mutate in mutations:
            b = codex_source(); mutate(b); seal(b)
            with self.subTest(body=b):
                self.assertEqual(self.submit(b).status_code, 422)
        b = codex_source(); b['cases'][0]['script'][0]['target']['selector'] = '#tampered'
        self.assertEqual(self.submit(b).status_code, 422)
        b = codex_source(True); b['cases'][0]['report'][-1]['fail_kind'] = 'selector'; seal(b)
        self.assertEqual(self.submit(b).status_code, 422)
        self.assertEqual(self.count(VerifiedImportJob), 0)

    def test_project_permissions_and_legacy_still_require_device(self):
        self.assertEqual(self.submit(codex_source(), user=3).status_code, 403)
        b = source(); b.pop('runner_device_id')
        self.assertEqual(self.submit(b).status_code, 422)

    def test_portable_python_client_does_not_query_devices(self):
        from scripts.test_qalab_skill_macos import qalab, FakeApi, Store
        api = FakeApi()
        supported = True
        def request(method, path, body=None, token=None):
            if path == '/api/verified-imports/capabilities':
                return {'import_modes': ['codex'] if supported else [], 'codex_evidence_protocol': 'qalab-codex-v1'}
            if path == '/api/devices':
                raise AssertionError('Codex import must not require devices')
            return api(method, path, body, token)
        request.origin = api.origin
        client = qalab.Client(request, Store())
        self.assertEqual(client.import_cases(codex_source(True), 'fixture-token')['job_id'], 7)
        self.assertFalse(any('/devices' in call[1] or '/exec-queue' in call[1] for call in api.calls))
        supported = False
        with self.assertRaises(RuntimeError):
            client.import_cases(codex_source(), 'fixture-token')


if __name__ == '__main__':
    unittest.main()
