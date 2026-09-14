"""Streaming visibility uses synthetic content, a temporary DB and no model calls."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.ai_jobs import _authorize, _to_out
from app.db.session import Base
from app.models import AiJob, RequirementAnalysis, RequirementSource, User
from app.services import ai_jobs, requirement_analysis as review
from app.services.ai_progress import JobProgress, progress_of
from app.services.claude_runner import _build_cmd, _parse_line
from app.services import claude_runner
from scripts.test_requirement_analysis import draft


class ClaudePartialTests(unittest.TestCase):
    def test_interrupted_stream_does_not_echo_hidden_protocol_records(self):
        proc = Mock()
        proc.stdout = iter([json.dumps({'type': 'stream_event', 'event': {
            'type': 'content_block_delta', 'delta': {'type': 'thinking_delta', 'thinking': 'hidden synthetic reasoning'}}}) + '\n'])
        proc.poll.return_value = 0
        with patch.object(claude_runner, 'is_available', return_value=True), patch.object(claude_runner, '_claude_env', return_value={}), patch.object(claude_runner, '_acquire_slot', return_value=True), patch.object(claude_runner, '_slots', Mock()), patch.object(claude_runner.subprocess, 'Popen', return_value=proc):
            events = list(claude_runner.stream_generate('', prompt_builder=lambda: 'synthetic prompt'))
        self.assertEqual(events[-1]['type'], 'error')
        self.assertNotIn('hidden synthetic reasoning', json.dumps(events))

    def test_text_chunks_no_duplicate_snapshot_or_thinking(self):
        state = {}
        events = [
            {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "a"}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "private reasoning"}}},
            {"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "text", "text": "你"}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "好"}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "tool input"}}},
            {"type": "assistant", "message": {"id": "a", "content": [{"type": "text", "text": "你好"}]}},
            {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "b"}}},
            {"type": "assistant", "message": {"id": "b", "content": [{"type": "text", "text": "！"}]}},
        ]
        parsed = [_parse_line(json.dumps(event), state) for event in events]
        self.assertEqual(''.join(e['text'] for e in parsed if e), '你好！')
        self.assertIn('--include-partial-messages', _build_cmd('synthetic'))

    def test_final_result_and_nonstreaming_provider_still_work(self):
        class Engine:
            def stream_generate(self, *a, **k):
                yield {"type": "delta", "text": '{"summary":"半'}
                yield {"type": "heartbeat"}
                yield {"type": "result", "text": '{"summary":"完整"}'}
        previews = []
        result = review.collect(Engine(), 'synthetic', on_progress=previews.append)
        self.assertEqual(previews, ['{"summary":"半', None, result])
        self.assertEqual(review.parse_object(result), {"summary": "完整"})


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f'sqlite:///{Path(self.temp.name) / "test.db"}', connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as db:
            db.add(AiJob(id=1, kind='requirement_analysis', provider='claude', status='running', user_id=1,
                         input='{"analysis_id":1}'))
            db.commit()
        self.interval = patch.object(JobProgress, 'interval', 0)
        self.interval.start()

    def tearDown(self):
        self.interval.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def snapshot(self):
        with self.factory() as db:
            return progress_of(db.get(AiJob, 1).result)

    def test_parallel_snapshots_size_limit_terminal_race_and_auth(self):
        progress = JobProgress(self.factory, 1)
        def output(index):
            key = str(index)
            progress.unit(key, '图片' * 120, status='running', raw='中\\\n"' * 6000)
            progress.unit(key, status='done')
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(output, range(110)))
        progress.unit('0', status='running', raw='当前仍在返回的内容')
        with self.factory() as db:
            job = db.get(AiJob, 1)
            self.assertLessEqual(len(job.result.encode('utf-8')), 60000)
            snap = self.snapshot()
            self.assertEqual((snap['completed'], snap['total']), (109, 110))
            self.assertIn('0', [u['id'] for u in snap['units']])
            self.assertGreaterEqual(snap['omitted'], 10)
            self.assertTrue(any(u['truncated'] for u in snap['units']))
            self.assertIsNone(_to_out(db, job)['result'])  # preview is never a final result
            with self.assertRaises(HTTPException):
                _authorize(db, User(id=2, is_platform_admin=False), job)
            _authorize(db, User(id=1, is_platform_admin=False), job)
            job.status = 'done'; job.result = '{"analysis_id":1}'; db.commit()
        progress.unit('late', status='running', raw='late chunk')
        with self.factory() as db:
            self.assertEqual(json.loads(db.get(AiJob, 1).result), {'analysis_id': 1})

    def test_progress_failure_does_not_fail_generation_and_recovers(self):
        def unavailable():
            raise RuntimeError('database offline')
        progress = JobProgress(unavailable, 1)
        progress.unit('analysis', status='running', raw='{"summary":"草稿"}')
        progress.factory = self.factory
        progress.phase('validating')
        self.assertIn('草稿', self.snapshot()['units'][0]['text'])

    def test_throttled_chunks_flush_latest_output(self):
        progress = JobProgress(self.factory, 1)
        with patch.object(JobProgress, 'interval', 3600):
            progress.unit('analysis', status='running')
            progress.unit('analysis', raw='first chunk')
            self.assertEqual(self.snapshot()['units'][0]['text'], 'first chunk')
            for i in range(20):
                progress.unit('analysis', raw=f'newest chunk {i}')
            self.assertEqual(self.snapshot()['units'][0]['text'], 'first chunk')
            progress.flush()
            self.assertEqual(self.snapshot()['units'][0]['text'], 'newest chunk 19')

    def test_baseline_case_generation_exposes_output_before_return(self):
        payload = draft()
        payload['rules'][0]['status'] = 'confirmed'
        progress = JobProgress(self.factory, 1)
        progress.phase('generating')
        test = self
        raw = json.dumps([{'title': '合成删除用例', 'steps': '点击删除后取消', 'expected': '保留文件',
                           'criterion_ids': ['R1-C1', 'R1-C2']}], ensure_ascii=False)
        class Engine:
            def build_testcase_prompt(self, *a, **k):
                return 'synthetic prompt'
            def stream_generate(self, *a, **k):
                yield {'type': 'delta', 'text': raw[:24]}
                snap = test.snapshot()
                test.assertEqual(snap['stage'], 'generating')
                test.assertIn('合成删除用例', snap['units'][0]['text'])
                test.assertEqual(snap['completed'], 0)
                yield {'type': 'result', 'text': raw}
            def parse_testcases(self, text, **kw):
                return json.loads(text)
        result = review.generate_from_baseline(Engine(), payload, None, None, '', progress=progress)
        self.assertEqual(len(result['cases']), 1)
        self.assertEqual(self.snapshot()['completed'], 1)

    def prepare_analysis(self, images=False):
        with self.factory() as db:
            if images:
                db.add(RequirementSource(id=1, created_by=1, text='synthetic', materials=json.dumps([
                    {'id': 'IMG1', 'location': '合成图片', 'mime_type': 'image/png', 'data': 'synthetic-base64-not-sent'}])))
            db.add(RequirementAnalysis(id=1, project_id=1, task_id=1, created_by=1, source_text='保护目录删除必须确认',
                   source_hash='synthetic', provider='claude', job_id=1, source_id=1 if images else None))
            db.commit()

    def test_image_and_analysis_visible_before_draft_finalized(self):
        self.prepare_analysis(images=True)
        snapshots = []
        test = self
        class Engine:
            def stream_generate(self, *a, **kwargs):
                data = {'text': '保护目录必须确认', 'kind': '文字', 'uncertainties': ''} if kwargs.get('images') else draft()
                raw = json.dumps(data, ensure_ascii=False)
                yield {'type': 'delta', 'text': raw[:20]}
                snapshots.append(test.snapshot())
                with test.factory() as db:
                    assert db.get(RequirementAnalysis, 1).draft is None
                yield {'type': 'delta', 'text': raw[20:]}
                yield {'type': 'result', 'text': raw}
        with patch('app.services.generators.get_provider', return_value=Engine()), patch('app.services.requirement_memory.context', return_value=[]):
            ai_jobs.run_job(self.factory, 1)
        self.assertEqual([s['stage'] for s in snapshots], ['reading_images', 'analyzing'])
        self.assertIn('保护', snapshots[0]['units'][0]['text'])
        self.assertNotIn('synthetic-base64', json.dumps(snapshots))
        with self.factory() as db:
            job = db.get(AiJob, 1)
            self.assertEqual(job.status, 'done', job.error)
            self.assertEqual(json.loads(job.result), {'analysis_id': 1})
            saved = json.loads(db.get(RequirementAnalysis, 1).draft)
            self.assertTrue(all(r['status'] == 'pending' for r in saved['rules']))
            self.assertTrue(all(not s['reviewed'] for s in saved['scenarios']))

    def test_interrupted_output_retained_without_draft(self):
        self.prepare_analysis()
        class Engine:
            def stream_generate(self, *a, **kw):
                yield {'type': 'delta', 'text': '{"summary":"尚未完成'}
                yield {'type': 'delta', 'text': '，保留最后一段'}
                yield {'type': 'error', 'msg': '模型超时'}
        with patch('app.services.generators.get_provider', return_value=Engine()), patch('app.services.requirement_memory.context', return_value=[]), patch.object(JobProgress, 'interval', 3600):
            ai_jobs.run_job(self.factory, 1)
        with self.factory() as db:
            self.assertEqual(db.get(AiJob, 1).status, 'failed')
            self.assertIsNone(db.get(RequirementAnalysis, 1).draft)
        self.assertIn('尚未完成，保留最后一段', self.snapshot()['units'][0]['text'])


if __name__ == '__main__':
    unittest.main()
