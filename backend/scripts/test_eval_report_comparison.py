"""Query grouping, frozen report isolation and missing samples (no model/network calls)."""
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.session import Base
from app.models.ai_eval import EvalTask, EvalRun, EvalExperiment, EvalBatchSummary
from app.api.eval_report import _detail_table_html, render_report_page
from app.services.eval_report_details import group_query_runs, render_query_details, detail_row
from app.services.eval_summary_store import summary_view

FIXTURE = Path(__file__).resolve().parents[2] / "frontend/tests/fixtures/eval-report-comparison.json"
ROWS = json.loads(FIXTURE.read_text())


class ReportComparisonTests(unittest.TestCase):
    def test_pair_products_without_losing_trials_or_turns(self):
        groups = group_query_runs(ROWS)
        self.assertEqual([[r['run_id'] for r in g] for g in groups],
                         [[101, 201, 105, 301], [102, 202], [103, 203], [104, 204]])
        rendered = render_query_details(ROWS)
        self.assertEqual(rendered.count('data-run-id='), 10)
        self.assertLess(rendered.index('data-run-id="201"'), rendered.index('data-run-id="102"'))
        for text in ('4 个 query · 10 条执行 · 3 个产品', '纳米Work', 'WorkBuddy', 'QWork',
                     '第 2 次执行', '第 2 轮', '8m8s', '0 算力豆', '0.89 积分', 'API Error', '执行失败', '待判定'):
            self.assertIn(text, rendered)

    def test_do_not_pair_different_frozen_inputs(self):
        for changes in ({'eval_query_id': 99}, {'prompt': 'different'},
                        {'attachments': [{'name': 'a.csv', 'url': '/another'}]},
                        {'source_conversation_group': 'other'}, {'turn_index': 1}):
            row = copy.deepcopy(ROWS[0])
            row['payload'].update(changes)
            self.assertEqual(len(group_query_runs([ROWS[0], row])), 2)
        self.assertEqual(len(group_query_runs([ROWS[0], {**ROWS[0], 'batch_id': 'other'}])), 2)
        self.assertEqual(len(group_query_runs([{'run_id': 1, 'payload': {'title': 'same'}},
                                              {'run_id': 2, 'payload': {'title': 'same'}}])), 2)

    def test_configurations_and_ab_groups_remain_individual(self):
        rows = [{**ROWS[0], 'run_id': i, 'payload': {**ROWS[0]['payload'],
                 'compare_group': tag, 'configuration_index': i, 'configuration_label': f'模型配置{i}'}}
                for i, tag in enumerate(['A', 'B'], 1)]
        result = render_query_details(rows)
        for text in ('1 个 query · 2 条执行', 'A 组', 'B 组', '配置 2', '模型配置2'):
            self.assertIn(text, result)

    def test_escape_text_and_reject_script_links(self):
        row = copy.deepcopy(ROWS[0])
        row.update(share_link='javascript:alert(1)', artifact_share_link='https://example.com/?x="y"',
                   verdict_reason='<img src=x onerror=alert(1)>')
        row['payload']['title'] = '<script>bad()</script>'
        row['payload']['dialog_options']['model'] = '<script>model()</script>'
        result = render_query_details([row])
        for unsafe in ('<script>', '<img src=x', 'javascript:'):
            self.assertNotIn(unsafe, result)
        self.assertIn('&lt;script&gt;', result)
        self.assertIn('rel="noopener noreferrer"', result)
        malformed = SimpleNamespace(id=3, status='pending', payload='[]')
        self.assertEqual(detail_row(malformed)['payload'], {})

    def test_database_report_is_frozen_and_batch_scoped(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            task = EvalTask(id=1, project_id=1, name='对比报告', query_ids='[1,2,3,4]', last_batch_id='b1',
                            summary_status='done', summary_html='<p>综合评价</p>')
            db.add(task)
            for row in ROWS:
                data = {k: v for k, v in row.items() if k != 'run_id'}
                data['payload'] = json.dumps(data['payload'], ensure_ascii=False)
                db.add(EvalRun(id=row['run_id'], project_id=1, eval_task_id=1, **data))
            db.add(EvalRun(id=999, project_id=1, eval_task_id=1, batch_id='other', status='done',
                           payload=json.dumps({'title': '其他批次不可见'})))
            missing = {**ROWS[4], 'run_id': 205}
            db.add(EvalExperiment(project_id=1, batch_id='b1', dataset_hash='test', manifest=json.dumps({
                'dataset_hash': 'test', 'trial_count': 2, 'planned_runs': [*ROWS, missing]})))
            db.commit()
            detail = _detail_table_html(db, task)
            self.assertIn('4 个 query · 11 条执行', detail)
            self.assertIn('记录缺失', detail)
            self.assertIn('计划执行记录缺失', detail)
            self.assertNotIn('其他批次不可见', detail)
            db.add(EvalBatchSummary(eval_task_id=1, batch_id='b1', summary_status='done',
                                   summary_html='<p>冻结评价</p>', detail_html=detail))
            db.commit()
            db.get(EvalRun, 101).bean_cost = '99999'
            db.commit()
            page = render_report_page(task, db)
            self.assertIn(detail, page)
            self.assertNotIn('99999', page)
            old = db.query(EvalBatchSummary).first()
            old.detail_html = '<h2>旧版明细快照</h2><table><tr><td>原始结果</td></tr></table>'
            db.commit()
            page = render_report_page(summary_view(db, task, 'b1'), db)
            self.assertIn('旧版明细快照', page)
            self.assertNotIn('data-run-id=', page)
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
