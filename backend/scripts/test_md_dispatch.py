"""Dispatch-to-report regression tests. The skill posts to a loopback tracker."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import md_runs
from app.core.config import settings
from app.core.deps import RunnerCtx, get_current_user, require_runner_ctx
from app.db.session import Base, get_db
from app.models import MdRun, Project, User
from app.services import md_bug_skill
from app.services.md_result import analyze_result


NEGATIVE_PATH = """| 用例 | 结论 | 说明 |
| --- | --- | --- |
| C-001 点击一个不存在的按钮 | fail | 按用例约定记为失败。 |

## 缺陷

**C-001 失败路径复现：找不到「这个按钮不存在」**

1. 打开纳米Work桌面客户端，停留在首页。
2. 在主窗口和浮窗的辅助功能树中查找按钮，匹配数均为 0。

该用例专门验证按钮不存在时的失败记录。提测文档未要求客户端提供此按钮，本次未发现需向极库云上报的产品缺陷。
"""
OLD_FINDINGS = [
    {'title': 'C-001 点击一个不存在的按钮', 'detail': '按用例约定记为失败。', 'severity': 'major'},
    {'title': '打开纳米Work桌面客户端，停留在首页。', 'detail': '', 'severity': 'major'},
    {'title': '在主窗口和浮窗的辅助功能树中查找按钮，匹配数均为 0。', 'detail': '', 'severity': 'major'},
]


class MdDispatchTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        sessions = sessionmaker(engine)
        self.sessions = sessions
        with sessions.begin() as db:
            db.add_all([Project(id=1, name='QA', code='qa', geelib_sub_id=81),
                        User(id=1, name='QA', username='qa', password_hash='x',
                             email='qa@example.test', is_platform_admin=True)])
        def database():
            with sessions() as db:
                yield db
        self.user = User(id=1, is_platform_admin=True, email='qa@example.test')
        app = FastAPI()
        app.include_router(md_runs.router)
        app.dependency_overrides.update({get_db: database, get_current_user: lambda: self.user,
                                         require_runner_ctx: lambda: RunnerCtx()})
        self.client = TestClient(app)
        self.document = dict(title='测试', req_no='42', env_group='pre', content='# 文档', project_id=1)
        self.addCleanup(self.client.close)

    def post(self, path, **payload):
        response = self.client.post('/api/md-runs' + path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data']

    def test_multi_runner_validation_and_queue_isolation(self):
        rows = self.post('/batch', **self.document, runners=[' a ', 'b', 'a'])
        self.assertEqual([r['runner'] for r in rows], ['a', 'b'])
        for invalid in (dict(runners=[]), dict(runners=['  ']), dict(runners=['x'], env_group='custom')):
            self.assertEqual(self.client.post('/api/md-runs/batch', json={**self.document, **invalid}).status_code, 422)
        legacy = self.post('', **{**self.document, 'env_group': 'work-test'}, runner='legacy')
        self.assertEqual(legacy['runner'], 'legacy')
        for row in rows:
            rid, runner = row['id'], row['runner']
            queue = self.client.get('/api/md-runs/queue', params={'runner': runner}).json()['data']
            self.assertEqual([r['id'] for r in queue], [rid])
            self.assertEqual(self.client.post(f'/api/md-runs/queue/{rid}/claim?runner=other').status_code, 403)
            self.post(f'/queue/{rid}/claim?runner={runner}')
            self.assertEqual(self.client.post(f'/api/md-runs/queue/{rid}/claim?runner={runner}').status_code, 409)

    def finished_run(self):
        rid = self.post('', **self.document, runner='a')['id']
        self.post(f'/queue/{rid}/claim?runner=a')
        self.results = dict(verdict='fail', findings=[{'title': '按钮无响应', 'detail': '点 <确认>\n仍停留', 'severity': 'major'}])
        self.callback = f'/api/md-runs/queue/{rid}?runner=a'
        self.assertEqual(self.client.patch(self.callback, json=self.results).status_code, 200)
        return rid

    def test_negative_smoke_run_from_new_and_old_runners(self):
        for callback in (dict(verdict='completed'), dict(verdict='fail', findings=[]),
                         dict(verdict='fail', reason='3 条用例失败', findings=OLD_FINDINGS)):
            rid = self.post('', **self.document, runner='a')['id']
            response = self.client.patch(f'/api/md-runs/queue/{rid}?runner=a',
                                         json={**callback, 'result_md': NEGATIVE_PATH})
            self.assertEqual(response.status_code, 200, response.text)
            run = response.json()['data']
            self.assertEqual((run['status'], run['reason']), ('failed', '1 条用例失败'))
            self.assertEqual([f['title'] for f in run['findings']], [OLD_FINDINGS[0]['title']])
            self.assertIn('2. 在主窗口', run['findings'][0]['detail'])

    def test_historical_failure_can_be_reported_once_with_its_steps(self):
        for old in (OLD_FINDINGS, []):
            rid = self.finished_run()
            with self.sessions.begin() as db:
                run = db.get(MdRun, rid)
                run.reason = '3 条用例失败' if old else '1 条用例失败'
                run.result_md, run.findings = NEGATIVE_PATH, json.dumps(old)
            for url in (f'/api/md-runs/{rid}', '/api/md-runs'):
                data = self.client.get(url).json()['data']
                row = data[0] if isinstance(data, list) else data
                self.assertEqual(row['reason'], '1 条用例失败')
                self.assertEqual([f['title'] for f in row['findings']], [OLD_FINDINGS[0]['title']])
            with patch.object(settings, 'GEELIB_ENABLED', True), \
                 patch('app.services.geelib_account.get_user_app_token', return_value='local-test-token'), \
                 patch.object(md_bug_skill, 'create_case', return_value='geelib#123') as submit:
                self.assertEqual(self.post(f'/{rid}/report-bugs', indexes=[1, 2])['reported'], 0)
                self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 1)
                self.assertEqual(self.post(f'/{rid}/report-bugs', indexes=[0])['reported'], 0)
                submit.assert_called_once()
                self.assertIn('2. 在主窗口', submit.call_args.args[2])
                self.assertEqual(self.client.get(f'/api/md-runs/{rid}').json()['data']['findings'][0]['external_ref'], 'geelib#123')

        # A previous submission keeps its receipt, even if the two preceding false candidates disappear.
        with self.sessions.begin() as db:
            db.get(MdRun, rid).findings = json.dumps([*OLD_FINDINGS[:2], {**OLD_FINDINGS[2], 'external_ref': 'geelib#123'}])
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch('app.services.geelib_account.get_user_app_token', return_value='local-test-token'), \
             patch.object(md_bug_skill, 'create_case') as submit:
            result = self.post(f'/{rid}/report-bugs', indexes=[0])
            self.assertEqual(result['results'], [dict(index=0, ok=True, skipped=True, external_ref='geelib#123')])
            self.assertEqual(len(result['run']['findings']), 1)
            submit.assert_not_called()

    def test_confirmed_product_defects_keep_steps_and_count_failed_cases_separately(self):
        rid = self.post('', **self.document, runner='a')['id']
        md = """| 用例 | 结论 | 说明 |
| --- | --- | --- |
| C-001 | fail | 保存失败，重试后崩溃 |

## 产品缺陷
### 保存返回错误
1. 打开设置。
2. 点击保存。
### 重试导致崩溃
- 点击重试后客户端退出。

## 附件
- 截图
"""
        response = self.client.patch(f'/api/md-runs/queue/{rid}?runner=a', json=dict(verdict='completed', result_md=md))
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()['data']
        self.assertEqual(row['reason'], '1 条用例失败')
        self.assertEqual([(f['title'], f['detail']) for f in row['findings']], [
            ('保存返回错误', '1. 打开设置。\n2. 点击保存。'), ('重试导致崩溃', '- 点击重试后客户端退出。')])

    def test_unheaded_steps_and_case_expectations_stay_in_one_report(self):
        md = """| 用例 | 预期 | 结论 | 实际 |
| --- | --- | --- | --- |
| C-001 登录 | 显示首页 | 失败 | 白屏 |

## 缺陷
- 打开客户端。
- 点击登录。
"""
        report = analyze_result(md)
        self.assertEqual([f['title'] for f in report.findings], ['C-001 登录'])
        for part in ('预期：显示首页', '实际：白屏', '- 打开客户端。', '- 点击登录。'):
            self.assertIn(part, report.findings[0]['detail'])
        for verdict in ('pass', 'blocked'):
            result = analyze_result(md.split('## 缺陷')[0].replace('失败', verdict))
            self.assertEqual((result.verdict, result.findings), (verdict, []))

    def test_result_tables_and_fenced_evidence(self):
        table = '| 用例 | 结论 | 说明 |\n| --- | --- | --- |\n'
        for md, verdict in [(table, 'blocked'), ('| 配置 | 状态 |\n| 系统 | pass |', 'blocked'),
                            (table + '| 登录 | **通过** | |', 'pass'), (table + '| 登录 | blocked | |', 'blocked')]:
            with self.subTest(md=md):
                self.assertEqual(analyze_result(md).verdict, verdict)
        log = '```md\n## 产品缺陷\n### 日志中的示例标题\n' + table + '| 示例 | fail | |\n```'
        detail = '关联用例：登录、重试登录\n' + log
        md = table + '| 登录 | fail | |\n| 重试登录 | fail | |\n## 产品缺陷\n### 登录崩溃\n' + detail
        parsed = analyze_result(md)
        self.assertEqual(parsed.reason, '2 条用例失败')
        self.assertEqual(parsed.findings, [dict(title='登录崩溃', detail=detail, severity='major')])

    def test_old_report_with_a_real_issue_and_a_negative_case(self):
        md = NEGATIVE_PATH.replace('## 缺陷\n', '## 缺陷\n**设置保存后丢失**\n**复现步骤**\n1. 保存后重开。\n')
        report = analyze_result(md)
        self.assertFalse(report.explicit_no_defects)
        self.assertEqual([f['title'] for f in report.findings], ['设置保存后丢失', OLD_FINDINGS[0]['title']])
        self.assertIn('1. 保存后重开。', report.findings[0]['detail'])

    def test_only_uncovered_failures_create_additional_candidates(self):
        md = """| 用例 | 结论 | 说明 |
| --- | --- | --- |
| C-1 保存 | fail | 应显示成功提示，实际报错 |
| C-10 搜索 | fail | 应返回结果，实际为空 |
| C-2 重新保存 | fail | 同一保存错误 |
| C-3 取消 | pass | 正常 |
| C-4 设置 | blocked | 缺少账号 |
## 产品缺陷
### 保存报错
关联用例：C-1、C-2
1. 点击保存。
"""
        result = analyze_result(md)
        self.assertEqual(result.reason, '3 条用例失败')
        self.assertEqual([f['title'] for f in result.findings], ['保存报错', 'C-10 搜索'])
        self.assertIn('应返回结果，实际为空', result.findings[1]['detail'])

    def test_skill_receipt_and_replay(self):
        rid = self.finished_run()
        calls = []
        class Tracker(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append((self.path, self.headers['X-Agent-Auth'], json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"errno":2000,"data":"729"}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Tracker)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(settings, 'GEELIB_API_URL', f'http://127.0.0.1:{server.server_port}'), \
             patch.object(settings, 'GEELIB_ENABLED', True), \
             patch('app.services.geelib_account.get_user_app_token', return_value='local-test-token'):
            self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 1)
            self.client.patch(self.callback, json=self.results)
            self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 0)
        self.assertEqual(len(calls), 1)
        path, header, body = calls[0]
        self.assertEqual((path, header, body['sub_id'], body['title']),
                         ('/openapi/Matter/add', 'Bearer local-test-token', 81, '[42] 按钮无响应'))
        self.assertIn('&lt;确认&gt;</p><p>', body['content'])
        self.assertEqual(self.client.get(f'/api/md-runs/{rid}').json()['data']['findings'][0]['external_ref'], 'geelib#729')

    def test_tracker_rejection_can_retry_but_lost_receipt_is_not_resubmitted(self):
        rid = self.finished_run()
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch('app.services.geelib_account.get_user_app_token', return_value='local-test-token'), \
             patch.object(md_bug_skill, 'create_case', side_effect=[md_bug_skill.GeelibError('字段无效'),
                          md_bug_skill.SubmissionUncertain('回执丢失')]) as submit:
            for _ in range(3):
                self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 0)
            self.assertEqual(submit.call_count, 2)
