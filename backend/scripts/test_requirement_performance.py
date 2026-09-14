"""Synthetic workload, cache compatibility, and scheduler regressions (no live AI)."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import claude_runner
from app.services.requirement_analysis import prepare_draft
from app.services.requirement_output import OutputError
from app.services.requirement_pipeline import (Parts, build_draft, legacy_scene_request,
    plan_scene_batches, run_scene_batches, scene_request)
from scripts import test_requirement_recovery as recovery
from scripts import test_requirement_analysis as fixtures


def workload(count=108):
    draft = fixtures.draft()
    template = draft['rules'][0]
    draft.update(rules=[], questions=[], scenarios=[])
    for start in range(0, count, 12):
        rule = copy.deepcopy(template)
        rule.update(id=f'R{start // 12 + 1}', criteria=[{'id': f'C{i + 1}',
            'text': f'合成记录 {i + 1}：删除前展示确认弹窗，取消后该记录保留'} for i in range(start, min(count, start + 12))])
        draft['rules'].append(rule)
    return prepare_draft(draft, '保护目录删除必须确认', []).model_dump()


def entries(draft):
    return [(r, c) for r in draft['rules'] for c in r['criteria']]


def legacy_output(batch):
    return {'scenarios': [{'id': f'S{i + 1}', 'rule_id': r['id'], 'criterion_ids': [c['id']],
        'actor': '文件所有者', 'given': r['condition'], 'when': r['action'], 'then': c['text'],
        'counterexample': '', 'kind': 'boundary', 'reviewed': False} for i, (r, c) in enumerate(batch)]}


class PerformanceTests(unittest.TestCase):
    def test_effort_override_is_limited_to_the_requested_child_process(self):
        original = {'CLAUDE_CODE_EFFORT_LEVEL': 'high', 'SYNTHETIC_AUTH': 'fixture'}
        environments = []
        def launch(*args, **kwargs):
            environments.append(kwargs['env'])
            proc = Mock()
            proc.stdout = iter([json.dumps({'type': 'result', 'result': '{}'}) + '\n'])
            proc.poll.return_value = 0
            return proc
        with patch.object(claude_runner, 'is_available', return_value=True), \
             patch.object(claude_runner, '_claude_env', side_effect=lambda: original.copy()), \
             patch.object(claude_runner, '_acquire_slot', return_value=True), \
             patch.object(claude_runner, '_slots', Mock()), \
             patch.object(claude_runner.subprocess, 'Popen', side_effect=launch):
            list(claude_runner.stream_generate('', prompt_builder=lambda: 'synthetic', effort='low'))
            list(claude_runner.stream_generate('', prompt_builder=lambda: 'synthetic'))
        self.assertEqual([e['CLAUDE_CODE_EFFORT_LEVEL'] for e in environments], ['low', 'high'])
        self.assertEqual(original['CLAUDE_CODE_EFFORT_LEVEL'], 'high')
        self.assertTrue(all(e['SYNTHETIC_AUTH'] == 'fixture' for e in environments))

    def test_legacy_fingerprint_matches_deployed_request(self):
        draft = workload(48)
        prompt, schema, _ = legacy_scene_request(draft, entries(draft)[:6], 0, 48, 8)
        # Captured against abbb36f9, the released six-condition implementation.
        self.assertEqual(Parts(None, 1, 1, 'claude', None).fingerprint(prompt, schema),
                         '34db93bbfb420a5ea3672eb5a5c9cb72f51ad2b4d61b7d5b277e31ef39b80971')

    def test_108_criteria_take_five_compact_calls_with_same_obligations(self):
        draft = workload()
        draft['questions'] = [{'id': 'Q1', 'rule_ids': ['R9'], 'question': '仅规则九的问题',
            'evidence': '合成说明' * 400, 'options': [], 'blocking': True, 'answer': ''}]
        old = [entries(draft)[i:i + 6] for i in range(0, 108, 6)]
        new = plan_scene_batches(draft, entries(draft))
        self.assertEqual([len(b) for b in new], [24, 24, 24, 24, 12])
        self.assertEqual({c['id'] for b in new for _, c in b}, {f'C{i + 1}' for i in range(108)})
        old_chars = sum(len(legacy_scene_request(draft, b, i, 108, len(old))[0]) for i, b in enumerate(old))
        new_chars = sum(len(scene_request(draft, b)[0]) for b in new)
        self.assertLess(new_chars, old_chars * .6)
        first = json.loads(scene_request(draft, new[0])[0].split('\n')[-1])
        self.assertEqual(first['questions'], [])
        self.assertEqual(first['rules'][0]['criteria'], draft['rules'][0]['criteria'])
        for field in ('condition', 'action', 'expected', 'forbidden', 'boundaries', 'source_quote'):
            self.assertEqual(first['rules'][0][field], draft['rules'][0][field])

    def test_long_context_splits_without_truncating_rules_or_global_conflicts(self):
        draft = workload(48)
        for rule in draft['rules']:
            rule['source_quote'] = '完整原文' * 700
            rule['boundaries'] = '必须保留的例外' * 250
            rule['expected'] = '预期结果' * 700
            rule['action'] = '操作条件' * 450
        draft['questions'] = [{'id': 'Q1', 'rule_ids': [], 'question': '所有规则的冲突', 'evidence': '全局',
            'options': [], 'blocking': True, 'answer': ''}]
        batches = plan_scene_batches(draft, entries(draft))
        self.assertGreater(len(batches), 2)
        for batch in batches:
            data = json.loads(scene_request(draft, batch)[0].split('\n')[-1])
            self.assertEqual(data['questions'], draft['questions'])
            self.assertTrue(all(r['source_quote'] == '完整原文' * 700 for r in data['rules']))
        self.assertEqual(sum(len(b) for b in batches), 48)

    def test_shared_context_over_target_does_not_explode_into_single_condition_calls(self):
        draft = workload()
        for field in ('summary', 'scope', 'out_of_scope', 'flow'):
            draft[field] = '完整公共上下文' * 1000
        batches = plan_scene_batches(draft, entries(draft))
        self.assertEqual([len(b) for b in batches], [24, 24, 24, 24, 12])
        data = json.loads(scene_request(draft, batches[0])[0].split('\n')[-1])
        self.assertEqual(data['flow'], draft['flow'])

    def test_timeout_stops_unscheduled_batches_and_drains_active_successes(self):
        barrier, draining = threading.Barrier(3), threading.Event()
        started, saved = [], []
        progress = Mock()
        progress.unit.side_effect = lambda *a, **kw: draining.set() if kw.get('status') == 'paused' else None
        tasks = [{'key': str(i)} for i in range(18)]
        def generate(task):
            started.append(task['key'])
            barrier.wait(timeout=3)
            if task['key'] == '0':
                raise OutputError('provider_error', '合成超时')
            self.assertTrue(draining.wait(timeout=3))
            saved.append(task['key'])
            return [task]
        with self.assertRaises(OutputError):
            run_scene_batches(SimpleNamespace(progress=progress), tasks, generate, 3)
        self.assertEqual(set(started), {'0', '1', '2'})
        self.assertEqual(set(saved), {'1', '2'})
        self.assertEqual(progress.unit.call_count, 15)


class LegacyCacheTests(unittest.TestCase):
    setUp = recovery.PipelineTests.setUp
    tearDown = recovery.PipelineTests.tearDown

    def test_full_108_condition_workflow_uses_six_calls_then_no_calls_on_resume(self):
        interpretation, calls = workload(), []
        class Engine:
            supports_structured_output = staticmethod(lambda: True)
            supports_effort = staticmethod(lambda: True)
            def stream_generate(self, *a, **kw):
                prompt = kw['prompt_builder']()
                calls.append(prompt)
                if prompt.startswith('[需求具体场景]'):
                    if 'output_schema' in kw or 'timeout' in kw or kw.get('effort') != 'low':
                        raise AssertionError('Claude scene formatting uses plain JSON and the configured deadline')
                    data = json.loads(prompt.split('\n响应结构(JSON Schema)：')[0].split('\n')[-1])
                    obj = {'scenarios': [{'criterion_id': cid, 'actor': '文件所有者', 'given': '已有记录',
                        'when': '点击删除后取消', 'then': '记录保留', 'kind': 'boundary'} for cid in data['assigned_criteria']]}
                else:
                    if 'output_schema' not in kw or 'effort' in kw:
                        raise AssertionError('full rule interpretation retains structured output')
                    obj = interpretation
                yield {'type': 'result', 'text': json.dumps(obj)}
        for _ in range(2):
            result = build_draft(self.parts, Engine(), '保护目录删除必须确认', [], {})
            self.assertEqual(len(calls), 6)  # one rule interpretation + five scene calls
            self.assertEqual(len(result.scenarios), 108)
            self.assertTrue(all(s.kind == 'boundary' and not s.reviewed for s in result.scenarios))
            self.assertEqual({cid for s in result.scenarios for cid in s.criterion_ids}, {f'C{i}' for i in range(1, 109)})

    def test_gateway_failure_does_not_retry_as_json_repair_or_accept_partial_body(self):
        class Engine:
            calls = 0
            def stream_generate(inner, *args, **kwargs):
                inner.calls += 1
                yield {'type': 'delta', 'text': '{"ok":true}'}
                yield {'type': 'error', 'msg': 'API returned an empty or malformed response (HTTP 200); no_events'}
        engine = Engine()
        with self.assertRaises(OutputError) as error:
            self.parts.run('probe', '合成批次', engine, 'synthetic', {}, lambda x: x)
        self.assertEqual(error.exception.code, 'gateway_error')
        self.assertIn('模型网关', str(error.exception))
        self.assertEqual(engine.calls, 1)
        self.assertIsNone(self.parts.read_checkpoint('probe', 'synthetic', {}, lambda x: x))

    def test_unsupported_envelope_saves_failure_without_reusing_partial_object(self):
        class Engine:
            def stream_generate(self, *args, **kwargs):
                yield {'type': 'delta', 'text': '{"ok":true}'}
                yield {'type': 'delta', 'text': [{'unknown': 'payload'}]}
        with self.assertRaises(OutputError) as error:
            self.parts.run('probe', '合成批次', Engine(), 'synthetic', {}, lambda x: x)
        self.assertEqual(error.exception.code, 'unsupported_text')
        self.assertEqual(error.exception.raw, '{"ok":true}')
        self.assertIsNone(self.parts.read_checkpoint('probe', 'synthetic', {}, lambda x: x))

    def test_old_successes_survive_new_batching_and_rule_changes_invalidate_them(self):
        interpretation = workload(48)
        old_batch = entries(interpretation)[:6]
        prompt, schema, validate = legacy_scene_request(interpretation, old_batch, 0, 48, 8)
        class OldEngine:
            def stream_generate(self, *a, **kw):
                yield {'type': 'result', 'text': json.dumps(legacy_output(old_batch))}
        self.parts.run('scenes-1', '旧版成功批次', OldEngine(), prompt, schema, validate)
        requests = []
        class NewEngine:
            def stream_generate(self, *a, **kw):
                prompt = kw['prompt_builder']()
                if prompt.startswith('[需求具体场景]'):
                    data = json.loads(prompt.split('\n响应结构(JSON Schema)：')[0].split('\n')[-1])
                    requests.append(data['assigned_criteria'])
                    obj = {'scenarios': [{'criterion_id': cid, 'actor': '文件所有者', 'given': '已有记录',
                        'when': '点击删除后取消', 'then': '记录保留'} for cid in data['assigned_criteria']]}
                else:
                    obj = interpretation
                yield {'type': 'result', 'text': json.dumps(obj)}
        result = build_draft(self.parts, NewEngine(), '保护目录删除必须确认', [], {})
        self.assertEqual(len(requests), 2)
        self.assertEqual({cid for batch in requests for cid in batch}, {f'C{i}' for i in range(7, 49)})
        self.assertEqual(result.scenarios[0].kind, 'boundary')
        self.assertEqual(result.scenarios[0].then, old_batch[0][1]['text'])
        self.assertTrue(all(not s.reviewed for s in result.scenarios))
        # Changing the source/rule context must not reuse old scene semantics.
        requests.clear()
        interpretation['rules'][0]['expected'] = '合成需求已修订'
        build_draft(self.parts, NewEngine(), '已修订的合成正文', [], {})
        self.assertIn('C1', {cid for batch in requests for cid in batch})


if __name__ == '__main__':
    unittest.main()
