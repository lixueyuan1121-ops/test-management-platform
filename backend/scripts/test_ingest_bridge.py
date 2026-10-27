"""Mac 提测桥接脚本的鉴权、项目定位、逐项结果与未上线保护。"""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ingest_bridge', Path(__file__).resolve().parents[2] / 'scripts/ingest_submissions.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def test_missing_endpoint_does_not_login_or_create_legacy_assigned_tasks(self):
        with patch.object(bridge, 'request', return_value={'paths': {}}) as request:
            with self.assertRaisesRegex(RuntimeError, '尚未部署'):
                bridge.ingest_batch({'baseUrl': 'http://localhost'}, [{'source_message_id': '123'}])
            self.assertEqual(request.call_count, 1)

    def test_partial_failure_keeps_ids_and_forces_configured_project(self):
        config = dict(baseUrl='http://localhost', username='u', password='test', projectCode='nami_pc')
        replies = [{'paths': {'/api/tasks/ingest': {'post': {}}}}, {'access_token': 'test-token'},
                   [{'id': 7, 'code': 'nami_pc'}], {'task': {'id': 10}, 'created': True}, RuntimeError('HTTP 503')]
        with patch.object(bridge, 'request', side_effect=replies) as request:
            result = bridge.ingest_batch(config, [{'source_message_id': '123', 'project_id': 999}, {'source_message_id': '124'}])
            self.assertFalse(result['ok'])
            self.assertTrue(result['results'][0]['ok'])
            self.assertEqual(result['results'][1]['source_message_id'], '124')
            self.assertEqual(request.call_args_list[3].kwargs['body']['project_id'], 7)
            self.assertEqual(request.call_args_list[3].kwargs['body']['source_group_id'], '7652669649127481')

    def test_probe_never_writes_task(self):
        config = dict(baseUrl='http://localhost', username='u', password='test', projectCode='nami_pc')
        replies = [{'paths': {'/api/tasks/ingest': {'post': {}}}}, {'access_token': 'test-token'},
                   [{'id': 7, 'code': 'nami_pc', 'name': '纳米 PC'}]]
        with patch.object(bridge, 'request', side_effect=replies) as request:
            self.assertTrue(bridge.ingest_batch(config, [], probe_only=True)['ready'])
            self.assertEqual(request.call_count, 3)


    def test_description_becomes_title_without_truncation_or_mutating_input(self):
        config = dict(baseUrl='http://localhost', username='u', password='test', projectCode='nami_pc')
        content = '问题与修复说明\n' + '中文🙂' * 1000
        entry = {'source_message_id': '123', 'title': '旧摘要', 'description': content}
        replies = [{'paths': {'/api/tasks/ingest': {'post': {}}}}, {'access_token': 'test-token'},
                   [{'id': 7, 'code': 'nami_pc'}], {'task': {'id': 10}, 'created': True}]
        with patch.object(bridge, 'request', side_effect=replies) as request:
            self.assertTrue(bridge.ingest_batch(config, [entry])['ok'])
            payload = request.call_args.kwargs['body']
            self.assertEqual(payload['title'], content)
            self.assertEqual(payload['description'], content)
            self.assertNotIn('assigned_to', payload)
            self.assertEqual(entry['title'], '旧摘要')


if __name__ == '__main__':
    unittest.main()
