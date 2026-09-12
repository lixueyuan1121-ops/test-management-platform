"""Offline regressions for context, immutable judgments and transparent denominators."""
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.session import Base
from app.models import EvalQuery, EvalRun, EvalTask, Project, User
from app.models.ai_eval import EvalJudgment, EvalJudgmentReview
from app.core.enums import EvalRunStatus
from app.api import eval_judge, eval_queue, eval_task
from app.services import eval_judge as judge, claude_runner
from app.services.eval_snapshot import context_of


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(id=1, username='test', name='T', password_hash='x', is_platform_admin=True)
        self.query = EvalQuery(id=1, project_id=1, title='Q', prompt='仅使用第二页', expected='分析', dimension='tool_use')
        self.task = EvalTask(id=1, project_id=1, name='T', last_batch_id='b')
        self.db.add_all([self.user, Project(id=1, name='P', code='P'), self.query, self.task])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def row(self, **kw):
        defaults = dict(project_id=1, eval_query_id=1, eval_task_id=1, batch_id='b', target_engine='namiwork',
            status=EvalRunStatus.done, answer='已完成', payload=json.dumps(eval_queue._payload_of(self.query)))
        defaults.update(kw)
        r = EvalRun(**defaults)
        self.db.add(r)
        self.db.commit()
        return r

    def judge(self, row, votes=1, error=None):
        def ballot(*args):
            return ({**{k: {'pass': True} for k in claude_runner._JUDGE_DIM_KEYS},
                     'score': 5, 'summary': '完成', '_raw_output': 'raw ballot'}, error)
        with patch.object(judge.generators, 'get_provider') as engine, patch.object(judge, '_judge_once', side_effect=ballot):
            engine.return_value.is_available.return_value = True
            return judge.judge_run(self.db, row, votes=votes)

    def test_context_keeps_original_and_only_earlier_matching_trial_and_engine(self):
        def payload(turn, **kw):
            return json.dumps({'prompt': '首轮限制100字' if turn == 0 else '结合上一轮继续',
                              'turn_index': turn, 'conversation_group': 'g', **kw})
        first = self.row(payload=payload(0), answer='首轮回答')
        self.row(payload=payload(0), target_engine='workbuddy', answer='其他产品')
        self.row(payload=payload(0, trial_index=2), answer='其他尝试')
        self.row(payload=payload(0), batch_id='old', answer='旧批次')
        self.row(payload=payload(2), answer='未来回答')
        current = self.row(payload=payload(1))
        context = context_of(self.db, current)
        self.assertEqual([x['run_id'] for x in context['previous_turns']], [first.id])
        rendered = claude_runner.build_eval_judge_prompt({'evaluation_context': context}, '分析')
        self.assertIn('首轮限制100字', rendered)
        self.assertIn('结合上一轮继续', rendered)
        self.assertNotIn('其他尝试', rendered)

    def test_snapshot_context_survives_edit_and_deletion(self):
        row = self.row()
        self.query.prompt = '新提问'
        self.db.commit()
        self.assertEqual(context_of(self.db, row)['prompt'], '仅使用第二页')
        self.db.delete(self.query)
        self.db.commit()
        self.assertEqual(context_of(self.db, row)['prompt'], '仅使用第二页')

    def test_dimension_history_and_engine_stats_survive_edit_and_deletion(self):
        self.row(verdict='pass')
        self.query.dimension = 'creativity'
        self.db.commit()
        for delete in (False, True):
            if delete:
                self.db.delete(self.query)
                self.db.commit()
            out = eval_judge.eval_dimension_stats(1, 30, True, self.db, self.user)['data']
            self.assertEqual(out['dims'][0]['dimension'], 'tool_use')
            self.assertEqual(out['by_engine'][0]['dims'][0]['dimension'], 'tool_use')

    def test_explicit_null_dimension_does_not_fall_back(self):
        self.row(verdict='pass', payload='{"dimension":null}')
        out = eval_judge.eval_dimension_stats(1, 30, False, self.db, self.user)['data']
        self.assertEqual(out['dims'][0]['dimension'], '未标注')

    def test_rejudge_preserves_old_review_and_all_votes_and_input(self):
        row = self.row(status=EvalRunStatus.judged, verdict='fail', review_mark='false_positive', review_note='旧复核')
        self.judge(row, votes=3)
        versions = self.db.query(EvalJudgment).order_by(EvalJudgment.id).all()
        self.assertEqual(len(versions), 2)
        self.assertEqual(json.loads(versions[0].result)['verdict'], 'fail')
        self.assertEqual(row.verdict, 'pass')
        self.assertIsNone(row.review_mark)
        self.assertIsNone(row.review_note)
        old_review = self.db.query(EvalJudgmentReview).one()
        self.assertEqual((old_review.judgment_id, old_review.note), (versions[0].id, '旧复核'))
        self.assertEqual(len(json.loads(versions[1].ballots)), 3)
        self.assertIn('仅使用第二页', versions[1].input_json)
        self.assertEqual(len(versions[1].input_hash), 64)
        history = eval_judge.judgment_history(row.id, self.db, self.user)['data']
        self.assertEqual(history[1]['reviews'][0]['note'], '旧复核')

    def test_stale_review_rejected_and_clear_restores_abnormal(self):
        row = self.row(status=EvalRunStatus.judged, verdict='fail')
        first = eval_judge.review_run(row.id, eval_judge.ReviewMarkIn(mark='false_positive'), self.db, self.user)
        original_id = first['data']['judgment_id']
        self.assertFalse(row.is_abnormal)
        eval_judge.review_run(row.id, eval_judge.ReviewMarkIn(mark=None, judgment_id=original_id), self.db, self.user)
        self.assertTrue(row.is_abnormal)
        self.judge(row)
        with self.assertRaises(HTTPException) as error:
            eval_judge.review_run(row.id, eval_judge.ReviewMarkIn(mark='confirmed', judgment_id=original_id), self.db, self.user)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNone(row.review_mark)

    def test_failed_judgment_is_versioned_without_old_review(self):
        row = self.row(status=EvalRunStatus.judged, verdict='pass', review_mark='confirmed')
        self.judge(row, votes=3, error='provider timeout')
        current = self.db.get(EvalJudgment, row.judgment_id)
        self.assertEqual(json.loads(current.result)['verdict'], 'error')
        self.assertEqual(len(json.loads(current.ballots)), 3)
        self.assertIsNone(row.review_mark)

    def test_coverage_counts_all_runs_and_each_engine(self):
        self.row(verdict='pass')
        for _ in range(9):
            self.row(verdict='error')
        out = eval_queue.batch_trend(1, 30, self.db, self.user)['data']['batches'][0]
        for metrics in (out, out['by_engine']['namiwork']):
            self.assertEqual(metrics['pass_rate'], 100)
            self.assertEqual(metrics['coverage_rate'], 10)
            self.assertEqual(metrics['confirmed_success_rate'], 10)
            self.assertEqual(metrics['judge_errors'], 9)

    def test_decimal_cost_and_unknown_coverage(self):
        self.assertEqual(eval_task._parse_bean('1,234.50'), Decimal('1234.50'))
        self.assertIsNone(eval_task._parse_bean('—'))
        for cost in ('23.5', '0.8', None):
            self.row(bean_cost=cost)
        metrics = eval_task._batch_totals(self.db, self.task)
        self.assertEqual(metrics['total_bean_cost'], 24.3)
        self.assertEqual(metrics['bean_known_count'], 2)
        self.assertEqual(metrics['bean_coverage_rate'], 66.7)

    def test_empty_b_options_do_not_inherit_saved_question_model(self):
        self.query.dialog_options = '{"model":"旧模型"}'
        self.task.query_ids = '[1]'
        self.db.commit()
        self.assertEqual(eval_queue._payload_of(self.query)['dialog_options'], {'model': '旧模型'})
        self.assertEqual(eval_queue._payload_of(self.query, {})['dialog_options'], {})
        ids, _ = eval_task.dispatch_task_runs(self.db, self.task, ['r1'], ['namiwork'],
                                            None, {'model': '模型A'}, {}, 1)
        payloads = [json.loads(self.db.get(EvalRun, i).payload) for i in ids]
        self.assertEqual([p['dialog_options'] for p in payloads], [{'model': '模型A'}, {}])

    def test_trials_fan_out_isolate_conversations_and_freeze_missing_denominator(self):
        from app.models.ai_eval import EvalExperiment
        from app.services.eval_experiment import samples_with_missing, trial_metrics
        self.query.conversation_group, self.query.turn_index = 'conversation', 0
        second = EvalQuery(id=2, project_id=1, title='续问', prompt='继续', conversation_group='conversation', turn_index=1)
        self.task.query_ids = '[1,2]'
        self.db.add(second)
        self.db.commit()
        ids, batch = eval_task.dispatch_task_runs(self.db, self.task, ['r1', 'r2'],
            ['namiwork', 'workbuddy'], None, {}, {}, 1, trial_count=3)
        self.db.commit()
        self.assertEqual(len(ids), 24)  # 2 turns × 2 engines × A/B × 3 trials
        rows, manifest = samples_with_missing(self.db, batch, 1)
        self.assertEqual(len(manifest['planned_runs']), 24)
        self.assertEqual(len(trial_metrics(rows)['tasks']), 4)
        for row in rows:
            p = json.loads(row.payload)
            if p['turn_index'] == 1:
                prior = context_of(self.db, row)['previous_turns']
                self.assertEqual(len(prior), 1)
                self.assertEqual(self.db.get(EvalRun, prior[0]['run_id']).runner, row.runner)
            row.verdict, row.status, row.score = 'pass', EvalRunStatus.judged, 5
        self.db.commit()
        stable = trial_metrics(rows)
        self.assertTrue(all(m['success_rate'] == 100 for m in stable['by_engine_variant']))
        self.assertTrue(all(t['trial_count'] == 3 for t in stable['tasks']))
        self.db.delete(rows[0])
        self.query.prompt = '修改题库'
        self.db.commit()
        samples, frozen = samples_with_missing(self.db, batch, 1)
        self.assertEqual(len(samples), 24)
        self.assertEqual(sum(r.status == 'missing' for r in samples), 1)
        self.assertEqual(manifest, frozen)
        self.assertEqual(self.db.query(EvalExperiment).filter_by(batch_id=batch).one().dataset_hash, frozen['dataset_hash'])

    def test_execution_retry_keeps_judgment_versions_separate(self):
        row = self.row()
        self.judge(row)
        previous = row.judgment_id
        row.status = EvalRunStatus.failed
        self.db.commit()
        eval_queue.reset_conversation_for_retry(self.db, row)
        self.db.commit()
        self.assertIsNone(row.judgment_id)
        row.status, row.answer = EvalRunStatus.done, '新执行回答'
        self.db.commit()
        self.judge(row)
        self.assertNotEqual(previous, row.judgment_id)
        self.assertEqual(self.db.get(EvalJudgment, row.judgment_id).attempt, 2)
        self.assertEqual(self.db.get(EvalJudgment, previous).attempt, 1)


if __name__ == '__main__':
    unittest.main()
