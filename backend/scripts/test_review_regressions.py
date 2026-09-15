"""Regression: no silent full-analysis restart, stable counters, partial confirmation."""
import copy
import json
import unittest
from app.models import AiJob, RequirementAnalysisPart, RequirementBaseline
from app.services.ai_progress import progress_of
from app.services.requirement_output import OutputError
from app.services.requirement_pipeline import build_draft
from scripts import test_requirement_recovery as recovery
from scripts import test_requirement_analysis as fixtures

class AnalysisRestartTests(unittest.TestCase):
    setUp = recovery.PipelineTests.setUp
    tearDown = recovery.PipelineTests.tearDown

    def snapshot(self):
        self.progress.flush()
        with self.factory() as db:
            return progress_of(db.get(AiJob, 1).result)

    def test_snapshot_replacement_does_not_erase_received_count(self):
        self.progress.unit('analysis', status='running', raw='a' * 8224)
        self.progress.unit('analysis', raw='b' * 6902)
        snap = self.snapshot()
        self.assertEqual(snap['received_chars'], 8224)
        self.assertEqual(snap['chars'], 6902)
        self.assertEqual(snap['units'][0]['text'], 'b' * 6902)

    def test_retry_counts_new_output_once_and_preserves_completed_units(self):
        self.progress.unit('image', status='done', raw='image')
        self.progress.unit('scene', status='running', raw='abcd')
        self.progress.unit('scene', attempt=2, note='重试当前步骤')
        self.progress.unit('scene', raw='x')
        self.assertEqual(self.snapshot()['received_chars'], 10)
        self.progress.unit('scene', raw='xyz')
        self.progress.unit('scene', status='done', raw='xyz')
        snap = self.snapshot()
        self.assertEqual(snap['received_chars'], 12)
        self.assertEqual(snap['completed'], 2)
        self.assertEqual(snap['units'][1]['attempt'], 2)

    def test_full_analysis_failure_requires_explicit_resume_and_reuses_success(self):
        class Engine:
            calls = 0
            supports_structured_output = staticmethod(lambda: True)
            def stream_generate(inner, *a, **kw):
                inner.calls += 1
                self.assertNotIn('output_schema', kw)
                yield {'type': 'result', 'text': '{"summary":' if inner.calls == 1 else json.dumps(fixtures.draft())}
        engine = Engine()
        with self.assertRaises(OutputError):
            build_draft(self.parts, engine, '保护目录删除必须确认', [], {})
        self.assertEqual(engine.calls, 1)
        with self.factory() as db:
            failed = db.query(RequirementAnalysisPart).one()
            self.assertEqual(failed.status, 'failed')
            self.assertEqual(failed.raw, '{"summary":')
        result = build_draft(self.parts, engine, '保护目录删除必须确认', [], {})
        self.assertEqual(engine.calls, 2)
        self.assertEqual(len(result.rules), 2)
        build_draft(self.parts, engine, '保护目录删除必须确认', [], {})
        self.assertEqual(engine.calls, 2)

class PartialScopeTests(unittest.TestCase):
    setUp = fixtures.ReviewAPITests.setUp
    tearDown = fixtures.ReviewAPITests.tearDown
    analyze = fixtures.ReviewAPITests.analyze
    detail = fixtures.ReviewAPITests.detail
    save = fixtures.ReviewAPITests.save
    confirm = fixtures.ReviewAPITests.confirm

    def test_pending_and_excluded_without_notes_do_not_block_confirm_or_enqueue(self):
        analysis = self.analyze(source_warnings=['部分附件尚未读取'])
        pending = copy.deepcopy(analysis['draft']['rules'][1])
        pending['id'] = 'R3'
        analysis['draft']['rules'].append(pending)
        analysis['draft']['rules'][1]['status'] = 'excluded'
        analysis = self.save(analysis)
        self.assertEqual(self.confirm(analysis, scope_reviewed=False).status_code, 422)
        response = self.confirm(analysis)
        self.assertEqual(response.status_code, 200, response.text)
        bid = response.json()['data']['baseline_id']
        self.db.expire_all()
        payload = json.loads(self.db.get(RequirementBaseline, bid).payload)
        self.assertEqual([r['id'] for r in payload['rules'] if r['status'] == 'confirmed'], ['R1'])
        self.assertEqual([r['status'] for r in payload['rules']], ['confirmed', 'excluded', 'pending'])
        response = self.client.post('/api/ai/testcases', json={'project_id': 1, 'task_id': 1,
            'requirement': analysis['source_text'], 'baseline_id': bid})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('job_id', response.json()['data'])

    def test_global_uncertainty_still_cannot_create_a_baseline(self):
        analysis = self.analyze()
        analysis['draft']['questions'][0]['rule_ids'] = []
        analysis = self.save(analysis)
        response = self.confirm(analysis)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.db.query(RequirementBaseline).count(), 0)
