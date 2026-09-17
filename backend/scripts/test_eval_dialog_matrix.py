"""离线验证组合下发、产品隔离、定时复用、上下文与统计分母。"""
import itertools
import json
import unittest
from collections import Counter, defaultdict
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import eval_task
from app.core.deps import get_current_user
from app.core.enums import EvalRunStatus
from app.db.session import Base, get_db
from app.main import app
from app.models import EvalQuery, EvalRun, EvalTask, Project, User
from app.services.eval_dialog_matrix import clean_matrix, expand_matrix
from app.services.eval_experiment import samples_with_missing, trial_metrics
from app.services.eval_snapshot import context_of


class DialogMatrixTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(id=1, username='test', name='T', password_hash='x', is_platform_admin=True)
        self.task = EvalTask(id=1, project_id=1, name='组合测试', query_ids='[1,2,3]', target_engines='["namiwork"]')
        self.db.add_all([self.user, Project(id=1, name='P', code='P'), self.task,
            EvalQuery(id=1, project_id=1, title='首问', prompt='分析数据', conversation_group='g', turn_index=0),
            EvalQuery(id=2, project_id=1, title='续问', prompt='继续总结', conversation_group='g', turn_index=1),
            EvalQuery(id=3, project_id=1, title='单轮', prompt='生成图表')])
        self.db.commit()
        self.matrix = {'chatMode': ['边想边做', '先规划，再执行'], 'model': ['GLM-5.3', '豆包', '测试模型'], 'thinkingDepth': ['标准', '高']}
        self.overrides = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)
        self.db.close()
        self.engine.dispose()

    def dispatch(self, **kwargs):
        options = dict(runner=['r1', 'r2'], target_engines=['namiwork'], target_device=None,
                       opts={}, opts_b=None, user_id=1, dialog_options_matrix=self.matrix)
        options.update(kwargs)
        ids, bid = eval_task.dispatch_task_runs(self.db, self.task, **options)
        self.db.commit()
        return [self.db.get(EvalRun, i) for i in ids], bid

    def test_cartesian_product_multiturn_and_workbuddy_are_isolated(self):
        rows, bid = self.dispatch(target_engines=['namiwork', 'workbuddy'], opts={'model': 'WB专用模型'}, trial_count=2)
        self.assertEqual(len(rows), 78)  # (12 Nami configurations + 1 WorkBuddy) × 3 turns × 2 trials
        groups, configs, loads = defaultdict(list), set(), Counter()
        for row in rows:
            p = json.loads(row.payload)
            loads[row.runner] += 1
            self.assertTrue(all(isinstance(v, str) for v in p['dialog_options'].values()))
            if row.target_engine == 'workbuddy':
                self.assertEqual(p['dialog_options'], {'model': 'WB专用模型'})
                self.assertNotIn('configuration_id', p)
            else:
                configs.add(tuple(p['dialog_options'][k] for k in ('chatMode', 'model', 'thinkingDepth')))
                self.assertEqual(p['configuration_count'], 12)
            if p['conversation_group']:
                groups[(row.target_engine, p['conversation_group'])].append(row)
            if p['turn_index'] == 1:
                prior = context_of(self.db, row)['previous_turns']
                self.assertEqual(len(prior), 1)
                prev = self.db.get(EvalRun, prior[0]['run_id'])
                self.assertEqual(prev.runner, row.runner)
                self.assertEqual(json.loads(prev.payload).get('configuration_id'), p.get('configuration_id'))
                self.assertEqual(json.loads(prev.payload)['trial_index'], p['trial_index'])
            row.status, row.verdict, row.score = EvalRunStatus.judged, 'pass', 5
        self.db.commit()
        self.assertEqual(configs, set(itertools.product(*self.matrix.values())))
        self.assertEqual(len(groups), 26)
        self.assertTrue(all(len(group) == 2 and len({r.runner for r in group}) == 1 for group in groups.values()))
        self.assertLessEqual(max(loads.values()) - min(loads.values()), 2)
        metrics = trial_metrics(rows)
        self.assertEqual(len(metrics['tasks']), 26)  # 2 cases × 13 configurations
        self.assertEqual(len(metrics['by_engine_variant']), 13)
        self.assertTrue(all(m['trial_count'] == 2 for m in metrics['tasks']))
        self.assertTrue(all(len(a['run_ids']) in (1, 2) for m in metrics['tasks'] for a in m['attempts']))
        lost_id = rows[0].id
        self.db.delete(rows[0])
        self.db.commit()
        samples, manifest = samples_with_missing(self.db, bid, 1)
        self.assertEqual(len(samples), 78)
        self.assertEqual(len(manifest['planned_runs']), 78)
        affected = [m for m in trial_metrics(samples)['tasks'] if any(lost_id in a['run_ids'] for a in m['attempts'])]
        self.assertEqual(len(affected), 1)
        self.assertEqual(affected[0]['coverage_rate'], 50)

    def test_defaults_and_legacy_scalar_behavior(self):
        self.task.query_ids = '[3]'
        self.db.get(EvalQuery, 3).dialog_options = '{"model":"题目模型"}'
        self.db.commit()
        rows, _ = self.dispatch(dialog_options_matrix={})
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0].payload)['dialog_options'], {'model': '题目模型'})
        rows, _ = self.dispatch(dialog_options_matrix={'model': [' GLM-5.3 ', 'glm-5.3', '', '豆包']})
        self.assertEqual([json.loads(r.payload)['dialog_options'] for r in rows], [{'model': 'GLM-5.3'}, {'model': '豆包'}])
        rows, _ = self.dispatch(dialog_options_matrix=None, opts={'model': 'A'}, opts_b={})
        self.assertEqual([json.loads(r.payload)['dialog_options'] for r in rows], [{'model': 'A'}, {}])
        self.assertEqual([json.loads(r.payload)['compare_group'] for r in rows], ['A', 'B'])

        rows, _ = self.dispatch(dialog_options_matrix=None, target_engines=['workbuddy'], opts={'model': 'WB'})
        self.assertEqual(len(rows), 1)
        self.assertNotIn('configuration_id', json.loads(rows[0].payload))

    def test_qwork_combination_isolation_and_unsupported_options(self):
        rows, _ = self.dispatch(target_engines=['namiwork', 'workbuddy', 'qwork'], opts={'model': 'DesktopModel'})
        self.assertEqual(Counter(r.target_engine for r in rows), {'namiwork': 36, 'workbuddy': 3, 'qwork': 3})
        for row in rows:
            if row.target_engine == 'qwork':
                payload = json.loads(row.payload)
                self.assertEqual(payload['dialog_options'], {'model': 'DesktopModel'})
                self.assertNotIn('configuration_id', payload)
        from app.services.eval_engines import validate_dialog_options
        with self.assertRaisesRegex(ValueError, 'QWork'):
            validate_dialog_options('qwork', {'thinkingDepth': '高'})

    def test_claim_and_retry_only_operate_on_one_configuration_conversation(self):
        from app.core.deps import RunnerCtx, require_runner_ctx
        from app.api.eval_queue import reset_conversation_for_retry
        app.dependency_overrides[require_runner_ctx] = lambda: RunnerCtx(device=None)
        rows, _ = self.dispatch(trial_count=2)
        first = rows[0]
        group = json.loads(first.payload)['conversation_group']
        expected = {r.id for r in rows if json.loads(r.payload)['conversation_group'] == group}
        self.assertEqual(len(expected), 2)
        response = self.client.post(f'/api/eval-queue/{first.id}/claim', params={
            'runner': first.runner, 'engine': 'namiwork', 'whole_group': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()['data']['run_ids']), expected)
        self.db.expire_all()
        self.assertEqual({r.id for r in rows if r.status == EvalRunStatus.running}, expected)
        for row in rows:
            row.status, row.verdict = EvalRunStatus.judged, 'pass'
        first.status = EvalRunStatus.failed
        self.db.commit()
        retried = reset_conversation_for_retry(self.db, first)
        self.db.commit()
        self.assertEqual({r.id for r in retried}, expected)
        self.assertTrue(all(r.verdict == 'pass' for r in rows if r.id not in expected))

    def test_api_saves_normalized_matrix_and_scheduler_reuses_it(self):
        matrix = {'chatMode': ['边想边做', '先规划，再执行'], 'model': [' GLM-5.3 ', 'glm-5.3', '豆包'], 'thinkingDepth': ['标准', '高']}
        response = self.client.post('/api/eval-tasks/1/run', json={'runners': ['r1'], 'trial_count': 2,
            'target_engines': ['namiwork', 'workbuddy'], 'dialog_options': {'model': 'WB'}, 'dialog_options_matrix': matrix})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['data']['run_ids']), 54)
        saved = json.loads(self.task.dialog_options)
        self.assertEqual(saved['matrix'], clean_matrix(matrix))
        self.assertEqual(saved['model'], 'WB')
        self.assertEqual(saved['trial_count'], 2)
        for row in self.db.query(EvalRun).all():
            row.status = EvalRunStatus.done
        self.task.target_engines = '["namiwork", "workbuddy"]'
        self.task.schedule_enabled, self.task.schedule_runner = True, 'r1'
        self.db.commit()
        before = self.task.last_batch_id
        from app.services.scheduler import run_eval_task_job
        with patch('app.db.session.SessionLocal', side_effect=lambda: Session(self.engine)):
            run_eval_task_job(1)
        self.db.expire_all()
        self.assertNotEqual(self.task.last_batch_id, before)
        rows = self.db.query(EvalRun).filter_by(batch_id=self.task.last_batch_id).all()
        self.assertEqual(len(rows), 54)
        self.assertEqual(Counter(r.target_engine for r in rows), {'namiwork': 48, 'workbuddy': 6})

    def test_validation_is_atomic(self):
        invalid = [{'model': 'GLM'}, {'model': [1]}, {'model': ['x' * 65]}, {'chatMode': ['不存在']},
                   {'thinkingDepth': ['深度思考']}, {'unknown': []},
                   {**self.matrix, 'model': [f'M{i}' for i in range(76)]}]
        for matrix in invalid:
            with self.subTest(matrix=matrix):
                resp = self.client.post('/api/eval-tasks/1/run', json={'runner': 'r1', 'dialog_options_matrix': matrix})
                self.assertEqual(resp.status_code, 400, resp.text)
                self.assertEqual(self.db.query(EvalRun).count(), 0)
        for extra in ({'dialog_options_b': {}}, {'target_engines': ['workbuddy']}):
            resp = self.client.post('/api/eval-tasks/1/run', json={'runner': 'r1', 'dialog_options_matrix': self.matrix, **extra})
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(self.db.query(EvalRun).count(), 0)
        with patch('app.services.eval_dialog_matrix.MAX_RUNS', 10):
            with self.assertRaisesRegex(ValueError, '单批最多'):
                self.dispatch()
        self.assertEqual(self.db.query(EvalRun).count(), 0)
        self.assertIsNone(self.task.last_batch_id)

    def test_summary_keeps_configuration_evidence(self):
        from app.services.claude_runner import render_eval_summary_items
        rows, _ = self.dispatch()
        items = eval_task._summary_items(self.db, rows[:1])
        rendered = render_eval_summary_items(items)
        self.assertIn(items[0]['configuration_label'], rendered)
        self.assertIn('GLM-5.3', rendered)
        self.assertIn('下发选项', rendered)

    def test_empty_dimensions_and_model_deduplication(self):
        self.assertEqual(expand_matrix(clean_matrix({})), [{}])
        self.assertEqual(expand_matrix(clean_matrix({'model': [' GLM-5.3 ', 'glm-5.3', '豆包']})),
                         [{'model': 'GLM-5.3'}, {'model': '豆包'}])
        self.assertEqual(len(expand_matrix(clean_matrix(self.matrix))), 12)


if __name__ == '__main__':
    unittest.main()
