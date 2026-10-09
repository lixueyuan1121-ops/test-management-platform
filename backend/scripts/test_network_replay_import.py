"""Use a real browser fixture packet in an isolated SQLite API, never production."""
import json
import os
import unittest
from pathlib import Path
from app.models import TestCase, ExecRun
from app.services.verified_import_jobs import drain_once
from app.services.claude_runner import _validate_script
from app.services.script_targets import validate_targets
from scripts.test_verified_import_jobs import AsyncTests


class NetworkReplayTests(AsyncTests):
    def test_real_browser_packet_import_and_queue(self):
        path = os.environ.get('QALAB_NETWORK_TEST_PACKET')
        if not path:
            self.skipTest('Set QALAB_NETWORK_TEST_PACKET to the browser regression output')
        body = json.loads(Path(path).read_text(encoding='utf-8'))
        self.assertEqual(len(body['cases']), 5)
        response = self.submit(body)
        self.assertEqual(response.status_code, 202, response.text)
        jid = response.json()['data']['job_id']
        drain_once(self.factory)
        job = self.job(jid)
        self.assertEqual(job['counts']['done'], 5, job)
        ids = [i['receipt']['case_id'] for i in job['items']]
        result = self.client.post('/api/exec-queue/enqueue-cases', json={
            'project_id': 1, 'test_case_ids': ids, 'runner': 'windows', 'auto_prepare': False,
        })
        self.assertEqual(result.status_code, 200, result.text)
        with self.factory() as db:
            queued = db.query(ExecRun).filter(ExecRun.status == 'pending').order_by(ExecRun.id).all()
            self.assertEqual(len(queued), 5)
            for case, row in zip(body['cases'], queued):
                from app.services.execution_evidence import normalize_script
                payload = json.loads(row.payload)
                self.assertEqual(payload['script'], normalize_script(case['script']))
                self.assertTrue(payload['strict_replay'])
                self.assertFalse(payload.get('auto_prepare'))
                self.assertFalse(payload.get('precondition'))
                self.assertEqual(json.loads(db.get(TestCase, row.test_case_id).script), payload['script'])
            output = os.environ.get('QALAB_NETWORK_QUEUE_OUTPUT')
            if output:
                Path(output).write_text(json.dumps([json.loads(r.payload) for r in queued], ensure_ascii=False), encoding='utf-8')

    def test_capability_and_invalid_references(self):
        caps = self.client.get('/api/verified-imports/capabilities')
        self.assertEqual(caps.status_code, 200)
        self.assertIn('network-scenarios-v1', caps.json()['data']['replay_protocols'])
        for script in [
            [{'action': 'assert_network_count', 'args': {'id': 'missing', 'expected': 0}}],
            [{'action': 'fault_route', 'args': {'id': 'f', 'path': '**/*', 'method': 'GET', 'frame': 'shell', 'mode': 'network_error'}}],
            [{'action': 'watch_network', 'args': {'id': 'w', 'path': '/list', 'method': 'GET', 'frame': 'shell', 'response_path': 'data.list', 'item_field': '__proto__'}}],
        ]:
            with self.assertRaises(ValueError):
                validate_targets(script)
            self.assertIsNotNone(_validate_script(script)[1])


if __name__ == '__main__':
    unittest.main()
