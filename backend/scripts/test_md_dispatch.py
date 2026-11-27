"""Dispatch-to-report regression tests. The skill posts to a loopback tracker."""
import json
from pathlib import Path
import tempfile
import subprocess
import threading
import unittest
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email import policy
from email.parser import BytesParser
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

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
        self.prepare = patch.object(md_bug_skill, 'prepare_case',
                                    side_effect=lambda sub, title, facts, token: (facts['detail'], 'P2', []))
        self.prepare.start()
        self.addCleanup(self.prepare.stop)

    def post(self, path, **payload):
        response = self.client.post('/api/md-runs' + path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data']

    def test_multi_runner_validation_and_queue_isolation(self):
        created = datetime(2026, 11, 27, 2, 32, 52, tzinfo=timezone.utc)
        with patch.object(md_runs, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = created
            rows = self.post('/batch', **self.document, runners=[' a ', 'b', 'a'])
            clock.now.assert_called_once_with(timezone.utc)
        self.assertEqual([row['created_at'] for row in rows], ['2026-11-27T02:32:52'] * 2)
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

    def test_reported_defect_retest_uses_runner_queue_and_preserves_report(self):
        rid = self.finished_run()
        with self.sessions.begin() as db:
            original = db.get(MdRun, rid)
            original.client_version = '1.0'
            findings = [{**original.findings_list[0], 'external_ref': 'geelib#729'},
                        {'title': '独立缺陷', 'external_ref': 'geelib#730'}]
            original.findings = json.dumps(findings)
        path = f'/{rid}/retests'
        request = dict(external_ref='geelib#729', previous_run_id=0, client_version='2.0')
        result = self.post(path, **request)
        child = result['run_id']
        self.assertTrue(result['created'])
        self.assertEqual(self.post(path, **request), {'run_id': child, 'created': False})
        queued, = self.client.get('/api/md-runs/queue?runner=a').json()['data']
        self.assertEqual((queued['id'], queued['project_id'], queued['req_no'], queued['env_group'], queued['client_version']),
                         (child, 1, '42', 'pre', '2.0'))
        self.assertIn('点 <确认>', queued['content'])
        self.assertIn('2.0', queued['content'])
        self.assertIn('# 文档', queued['content'])
        self.assertNotIn('独立缺陷', queued['content'])
        self.assertEqual(self.client.get('/api/md-runs/queue?runner=b').json()['data'], [])
        self.post(f'/queue/{child}/claim?runner=a')
        again = {**request, 'previous_run_id': child}
        self.assertFalse(self.post(path, **again)['created'])
        callback = f'/api/md-runs/queue/{child}?runner=a'
        upload = self.client.post(f'/api/md-runs/queue/{child}/evidence?runner=a',
                                  files={'file': ('retest.log', b'fresh evidence', 'text/plain')})
        self.assertEqual(upload.status_code, 200)
        self.assertEqual(self.client.patch(callback, json=self.results).json()['data']['status'], 'failed')
        self.assertFalse(self.post(path, **request)['created'])  # lost response for a run that already finished
        child_detail = self.client.get(f'/api/md-runs/{child}').json()['data']
        self.assertEqual(child_detail['retest'], {'source_run_id': rid, 'external_ref': 'geelib#729'})
        self.assertEqual([f['name'] for f in child_detail['evidence']], ['retest.log'])
        self.assertEqual(self.client.post(f'/api/md-runs/{child}/report-bugs', json={}).status_code, 409)
        next_run = self.post(path, **again)['run_id']
        self.assertNotEqual(child, next_run)
        self.post(f'/queue/{next_run}/claim?runner=a')
        response = self.client.patch(f'/api/md-runs/queue/{next_run}?runner=a', json={
            'verdict': 'completed', 'result_md': '| 用例 | 结论 | 说明 |\n| --- | --- | --- |\n| 确认按钮 | pass | 已恢复 |'})
        self.assertEqual(response.json()['data']['status'], 'passed')
        old = self.client.get(f'/api/md-runs/{rid}').json()['data']
        self.assertEqual((old['status'], old['client_version'], old['evidence']), ('failed', '1.0', []))
        self.assertEqual(old['findings'][0]['external_ref'], 'geelib#729')
        self.assertEqual([(r['id'], r['status']) for r in old['findings'][0]['retests']],
                         [(next_run, 'passed'), (child, 'failed')])
        self.assertEqual(old['findings'][1]['retests'], [])
        second = self.post(path, external_ref='geelib#730', previous_run_id=0)
        self.assertEqual(self.client.get(f"/api/md-runs/{second['run_id']}").json()['data']['client_version'], '1.0')

    def test_retest_requires_reported_defect_and_project_write_access(self):
        rid = self.finished_run()
        url = f'/api/md-runs/{rid}/retests'
        body = {'external_ref': 'geelib#731', 'previous_run_id': 0}
        self.assertEqual(self.client.post(url, json=body).status_code, 409)
        self.assertEqual(self.client.post(url, json={**body, 'external_ref': 'external'}).status_code, 422)
        self.user.is_platform_admin = False
        self.assertEqual(self.client.post(url, json=body).status_code, 403)
        self.user.is_platform_admin = True
        with self.sessions() as db:
            self.assertEqual(db.query(MdRun).count(), 1)

    def test_concurrent_retest_dispatch_enqueues_once(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine('sqlite:///' + str(Path(directory) / 'retests.db'))
            try:
                Base.metadata.create_all(engine)
                sessions = sessionmaker(engine)
                with sessions.begin() as db:
                    db.add(Project(id=1, name='QA', code='qa'))
                    db.add(MdRun(id=1, project_id=1, title='冒烟', runner='win', env_group='pre', content='# 文档',
                                 status='failed', findings=json.dumps([{'title': '保存失败', 'external_ref': 'geelib#731'}])))
                barrier = threading.Barrier(2, timeout=5)
                def dispatch():
                    with sessions() as db:
                        body = md_runs.MdRunRetestIn(external_ref='geelib#731', previous_run_id=0)
                        barrier.wait()
                        return md_runs.create_retest(1, body, db, self.user)['data']
                with ThreadPoolExecutor(max_workers=2) as workers:
                    futures = [workers.submit(dispatch) for _ in range(2)]
                    results = [f.result(timeout=10) for f in futures]
                self.assertEqual(sorted(r['created'] for r in results), [False, True])
                self.assertEqual(len({r['run_id'] for r in results}), 1)
                with sessions() as db:
                    self.assertEqual(db.query(MdRun).filter_by(status='pending').count(), 1)
            finally:
                engine.dispose()

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
                 patch.object(md_bug_skill, 'create_case', return_value={'external_ref': 'geelib#123'}) as submit:
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
        if not (md_bug_skill.PACKAGE / md_bug_skill.ENTRY).exists():
            self.skipTest('安装私有技能包后运行原包集成测试')
        self.prepare.stop()
        rid = self.finished_run()
        upload = self.client.post(f'/api/md-runs/queue/{rid}/evidence?runner=a',
                                  files={'file': ('client.log', b'client evidence', 'text/plain')})
        self.assertEqual(upload.status_code, 200, upload.text)
        calls = []
        failure = None
        class Tracker(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                if self.path.endswith('/addAttachmentFile'):
                    packet = BytesParser(policy=policy.default).parsebytes(
                        ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + raw)
                    parts = {p.get_param('name', header='Content-Disposition'): p for p in packet.iter_parts()}
                    body = {'file_name': parts['file'].get_filename(), 'bytes': parts['file'].get_payload(decode=True),
                            'matter_id': parts['id'].get_content(), 'sub_id': parts['sub_id'].get_content()}
                else:
                    body = json.loads(raw)
                calls.append((self.path, self.headers['X-Agent-Auth'], body))
                if self.path.endswith('/getList'):
                    data = {'matterList': [], 'total': 0}
                elif self.path.endswith('/getMatterTypeCustomAttributeList'):
                    opts = [{'text': '测试员', 'mail': 'qa@example.test'}, {'text': '开发', 'mail': 'dev@example.test'}]
                    data = [{'matterCustomAttribute': {'cf_key': 'executor', 'type_options': json.dumps(opts)}}]
                elif self.path.endswith('/add'):
                    if failure == 'lost':
                        self.close_connection = True
                        return
                    if failure == 'rejected':
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b'{"errno":3000,"errmsg":"invalid field"}')
                        return
                    data = {'id': '729'}
                elif self.path.endswith('/addAttachmentFile'):
                    data = [{'file_name': body['file_name'], 'id': 101}]
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'errno': 2000, 'data': data}).encode())
            def do_GET(self):
                calls.append((self.path.split('?')[0], self.headers['X-Agent-Auth'], {}))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"errno":2000,"data":{"matterInfo":{"id":729,"type_id":218,"sub_id":81}}}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Tracker)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        run_process = subprocess.run
        def without_fetch(args, **options):
            if '--require' not in args:
                return run_process(args, **options)
            self.assertEqual(Path(args[3]).read_bytes(), (md_bug_skill.PACKAGE / md_bug_skill.ENTRY).read_bytes())
            return run_process([args[0], '--no-experimental-fetch', *args[1:]], **options)
        def draft(_, *, system_prompt, prompt_builder, **kwargs):
            self.assertIn((md_bug_skill.PACKAGE / md_bug_skill.RULES).read_text(), system_prompt)
            self.assertIn((md_bug_skill.PACKAGE / md_bug_skill.FIELDS).read_text(), system_prompt)
            self.assertNotIn('local-test-token', prompt_builder() + system_prompt)
            self.assertIn('仍停留', prompt_builder())
            return [{'type': 'result', 'text': json.dumps({'description': '点 <确认>\n仍停留', 'priority': 'P1',
                                                          'keywords': ['确认']})}]
        with patch.object(settings, 'GEELIB_API_URL', f'http://127.0.0.1:{server.server_port}'), \
             patch.object(settings, 'GEELIB_ENABLED', True), \
             patch.object(md_bug_skill.generators, 'get_provider') as engine, \
             patch.object(md_bug_skill.subprocess, 'run', side_effect=without_fetch), \
             patch('app.services.geelib_account.get_user_app_token', return_value='local-test-token'):
            engine.return_value.stream_generate.side_effect = draft
            options = self.client.get(f'/api/md-runs/{rid}/report-executors')
            self.assertEqual(options.status_code, 200, options.text)
            self.assertEqual(options.json()['data'], {'default_email': 'qa@example.test', 'members': [
                {'name': '测试员', 'email': 'qa@example.test'}, {'name': '开发', 'email': 'dev@example.test'}]})
            self.assertEqual(calls.pop(), ('/openapi/Matter/getMatterTypeCustomAttributeList', 'Bearer local-test-token',
                                          {'sub_id': 81, 'type_id': settings.GEELIB_DEFECT_TYPE}))
            report = self.post(f'/{rid}/report-bugs', executor_email='dev@example.test')
            self.assertEqual(report['reported'], 1, report['results'])
            self.client.patch(self.callback, json=self.results)
            self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 0)
            successful_calls = list(calls)
            with self.assertRaisesRegex(md_bug_skill.GeelibError, '成员列表'):
                md_bug_skill.create_case(81, '测试', '测试', 'P2', 'unknown@example.test', 'local-test-token')
            self.assertTrue(calls[-1][0].endswith('getMatterTypeCustomAttributeList'))
            failure = 'rejected'
            with self.assertRaisesRegex(md_bug_skill.GeelibError, 'invalid field') as rejected:
                md_bug_skill.create_case(81, '测试', '测试', 'P2', 'qa@example.test', 'local-test-token')
            self.assertNotIsInstance(rejected.exception, md_bug_skill.SubmissionUncertain)
            failure = 'lost'
            with self.assertRaises(md_bug_skill.SubmissionUncertain):
                md_bug_skill.create_case(81, '测试', '测试', 'P2', 'qa@example.test', 'local-test-token')
        calls = successful_calls
        self.assertEqual([path.rsplit('/', 1)[1] for path, _, _ in calls],
                         ['getList', 'getList', 'getMatterTypeCustomAttributeList', 'add', 'getDetail', 'addAttachmentFile'])
        self.assertTrue(all(header == 'Bearer local-test-token' for _, header, _ in calls))
        attached = calls[-1][2]
        self.assertEqual((attached['bytes'], attached['matter_id'], attached['sub_id']), (b'client evidence', '729', '81'))
        body = calls[-3][2]
        self.assertEqual((body['sub_id'], body['title']), (81, '[42] 按钮无响应'))
        self.assertIn('&lt;确认&gt;</p><p>', body['content'])
        self.assertEqual({f['cf_name']: f['cf_value'] for f in body['data']},
                         {'执行人': 'dev@example.test', '优先级': 'high', '状态': '新建'})
        self.assertFalse({'user_mail', 'sign', 't'}.intersection(body))
        saved = self.client.get(f'/api/md-runs/{rid}').json()['data']['findings'][0]
        self.assertEqual(saved['external_ref'], 'geelib#729')
        self.assertEqual(saved['report_receipt']['skill'], 'jikuyun-bug-report')
        self.assertEqual(saved['report_receipt']['assignee'], 'dev@example.test')
        self.assertTrue(saved['report_receipt']['detail_verified'])
        self.assertEqual(saved['report_receipt']['type_id'], 218)
        self.assertEqual(saved['report_receipt']['attachments'], [
            {'id': '101', 'file_name': 'client.log', 'skill': 'geelib-attachment'}])

    def test_skill_matches_using_details_across_pages_before_creation(self):
        self.prepare.stop()
        present = {'id': 602, 'sub_id': 81, 'type_name': '缺陷', 'title': '点击保存时卡住', 'status_name': '已关闭'}
        other = {**present, 'id': 603, 'title': '点击保存后闪退'}
        def read(method, endpoint, payload, token):
            self.assertEqual(token, 'personal')
            if endpoint == 'getDetail':
                self.assertEqual(method, 'GET')
                return {'matterInfo': {'mkd_content': '点击保存后' + ('卡住' if payload['id'] == 602 else '直接闪退')}}
            if payload['title'] == '保存':
                return {'matterList': [present if payload['page'] == 1 else other], 'total': 2}
            return {'matterList': [], 'total': 0}
        def analyze(_, task, facts):
            if 'existing' not in facts:
                return {'priority': 'P2', 'description': '点击保存后卡住', 'keywords': ['保存']}
            self.assertEqual([r['description'] for r in facts['existing']], ['点击保存后卡住', '点击保存后直接闪退'])
            return {'checks': [{'id': 602, 'match': 'same', 'reason': '点击保存后卡住'},
                               {'id': 603, 'match': 'different', 'reason': '发生闪退'}]}
        with patch.object(md_bug_skill, 'installed_file', return_value=b'skill fixture'), \
             patch.object(md_bug_skill, '_read_tracker', side_effect=read), \
             patch.object(md_bug_skill, 'skill_json', side_effect=analyze):
            _, priority, matches = md_bug_skill.prepare_case(81, '[42] 保存无响应', {'detail': '点击保存后卡住'}, 'personal')
        self.assertEqual(priority, 'P2')
        self.assertEqual([(m['id'], m['status']) for m in matches], [(602, '已关闭')])

    def test_case_evidence_is_private_matched_and_retried_without_new_ticket(self):
        from app.services import md_evidence
        from app.models.md_run import MdRunEvidence, MdEvidenceDelivery
        rid = self.post('', **self.document, runner='a')['id']
        self.post(f'/queue/{rid}/claim?runner=a')
        path = f'/api/md-runs/queue/{rid}/evidence'
        def upload(name, content, case, runner='a'):
            return self.client.post(path, params={'runner': runner}, data={'case_label': case},
                                    files={'file': (name, content, 'application/octet-stream')})
        image = b'\x89PNG\r\n\x1a\n' + b'case1'
        self.assertEqual(upload('case.png', image, 'C-001', 'wrong').status_code, 403)
        self.assertEqual(upload('bad.png', b'not an image', 'C-001').status_code, 422)
        self.assertEqual(upload('../bad.log', b'no', '').status_code, 422)
        first = upload('case.png', image, 'C-001').json()['data']['id']
        self.assertEqual(upload('case.png', image, 'C-001').json()['data']['id'], first)
        upload('任务时段客户端.log', b'task delta', '')
        upload('case2.log', b'second case', 'C-002')
        upload('end.png', image, '')
        callback = self.client.patch(f'/api/md-runs/queue/{rid}?runner=a', json={
            'verdict': 'completed', 'evidence_summary': {'expected': 4, 'notes': []},
            'result_md': '| 用例 | 结论 | 说明 |\n| --- | --- | --- |\n| C-001 | fail | 按钮无响应 |\n| C-002 | fail | 保存失败 |'})
        self.assertEqual(callback.status_code, 200, callback.text)
        detail_path = f'/api/md-runs/{rid}'
        detail = self.client.get(detail_path).json()['data']
        self.assertEqual(detail['evidence_summary']['expected'], 4)
        self.assertEqual([[f['name'] for f in row['evidence']] for row in detail['findings']],
                         [['case.png', '任务时段客户端.log'], ['case2.log']])
        self.assertEqual(self.client.get(f'{detail_path}/evidence/{first}').content, image)
        self.user.is_platform_admin = False
        self.assertEqual(self.client.get(f'{detail_path}/evidence/{first}').status_code, 403)
        self.user.is_platform_admin = True
        names = []
        def send(row, sub, matter, name, token):
            names.append(row.name)
            self.assertEqual((sub, matter, token), (81, 729, 'personal'))
            if row.name == '任务时段客户端.log':
                raise ValueError('lost receipt after upload')
            return '99'
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch('app.services.geelib_account.get_user_app_token', return_value='personal'), \
             patch.object(md_bug_skill, 'create_case', return_value={'external_ref': 'geelib#729'}) as create, \
             patch.object(md_evidence, '_upload', side_effect=send), \
             patch.object(md_evidence, '_reconcile', return_value='101') as reconcile:
            report = self.post(f'/{rid}/report-bugs', indexes=[0])
            self.assertEqual(report['reported'], 1)
            self.assertEqual(report['results'][0]['attachments_sent'], 1)
            self.assertEqual(report['run']['findings'][0]['external_ref'], 'geelib#729')
            retry = self.post(f'/{rid}/report-bugs', indexes=[0], receipt_action='attachments')
            self.assertEqual((retry['reported'], retry['results'][0]['attachments_sent']), (0, 2))
            self.assertEqual(names, ['case.png', '任务时段客户端.log'])
            reconcile.assert_called_once()
            create.assert_called_once()
        self.assertEqual(self.client.delete(detail_path).status_code, 200)
        with self.sessions() as db:
            self.assertEqual(db.query(MdRunEvidence).count(), 0)
            self.assertEqual(db.query(MdEvidenceDelivery).count(), 0)

    def test_original_attachment_skill_uploads_file_to_matter_using_personal_sso(self):
        import platform
        from app.services import md_attachment_skill
        binary = md_attachment_skill.BINARIES.get((platform.system(), platform.machine().lower()))
        if not binary or not (md_bug_skill.PACKAGE / 'geelib-attachment/cli/bin' / binary[0]).is_file():
            self.skipTest('安装私有技能包后运行附件 CLI 集成测试')
        seen = []
        name = 'QALab-5-2-客户端.log'
        class Tracker(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                packet = BytesParser(policy=policy.default).parsebytes(
                    ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() +
                    self.rfile.read(int(self.headers['Content-Length'])))
                parts = {p.get_param('name', header='Content-Disposition'): p for p in packet.iter_parts()}
                seen.append((self.path, self.headers.get('X-Agent-Auth'), sorted(parts),
                             parts['type'].get_content(), parts['sub_id'].get_content(), parts['id'].get_content(),
                             parts['file'].get_filename(), parts['file'].get_payload(decode=True)))
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'errno': 2000, 'data': [{'file_name': name, 'id': 101}]}).encode())
        with ThreadingHTTPServer(('127.0.0.1', 0), Tracker) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with patch.object(settings, 'GEELIB_API_URL', f'http://127.0.0.1:{server.server_port}'):
                    result = md_attachment_skill.upload(81, 729, name, b'client evidence', 'text/plain', 'personal')
            finally:
                server.shutdown()
        self.assertEqual(result[0]['id'], 101)
        self.assertEqual(seen, [(md_attachment_skill.ENDPOINT, 'Bearer personal', ['file', 'id', 'sub_id', 'type'],
                                 'matter', '81', '729', name, b'client evidence')])

    def test_executor_selection_validation_and_project_access(self):
        rid = self.finished_run()
        path = f'/api/md-runs/{rid}'
        self.assertEqual(self.client.post(path + '/report-bugs', json={'executor_email': '同名成员'}).status_code, 422)
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch('app.services.geelib_account.get_user_app_token', return_value='personal') as token, \
             patch.object(md_bug_skill, '_read_tracker') as read:
            self.user.is_platform_admin = False
            self.assertEqual(self.client.get(path + '/report-executors').status_code, 403)
            token.assert_not_called()
            read.assert_not_called()
            self.user.is_platform_admin = True
            read.return_value = [{'matterCustomAttribute': {'cf_key': 'executor', 'type_options': '[]'}}]
            self.assertEqual(self.client.get(path + '/report-executors').json()['data']['members'], [])
            read.return_value = []
            self.assertEqual(self.client.get(path + '/report-executors').status_code, 502)

    def test_missing_skill_does_not_attempt_to_report(self):
        self.prepare.stop()
        rid = self.finished_run()
        with tempfile.TemporaryDirectory() as directory, patch.object(md_bug_skill, 'PACKAGE', Path(directory)), \
             patch.object(settings, 'GEELIB_ENABLED', True), patch.object(md_bug_skill, 'create_case') as create, \
             patch('app.services.geelib_account.get_user_app_token', return_value='personal'):
            result = self.post(f'/{rid}/report-bugs')['results'][0]
            self.assertEqual(result['status'], 'failed')
            self.assertIn('未完整安装', result['msg'])
            create.assert_not_called()

    def test_batch_releases_connections_and_retries_only_database_receipts(self):
        from app.services import md_evidence, geelib_account
        rid = self.finished_run()
        with self.sessions.begin() as db:
            run = db.get(MdRun, rid)
            run.findings = json.dumps([{'title': f'问题 {i}'} for i in range(6)])
            md_evidence.save(db, run, '任务时段客户端.log', '', b'example')
        engine = self.sessions.kw['bind']
        checked_out = set()
        fault = 'from md_run '
        faults = []
        def checkout(conn, record, proxy):
            checked_out.add(record)
        def checkin(conn, record):
            checked_out.discard(record)
        def disconnect(conn, cursor, sql, params, context, many):
            nonlocal fault
            if fault and fault in sql.lower():
                faults.append(fault)
                fault = None
                raise OperationalError(sql, {}, Exception(2013, 'Lost connection during query'))
        for name, callback in [('checkout', checkout), ('checkin', checkin), ('before_cursor_execute', disconnect)]:
            event.listen(engine, name, callback)
            self.addCleanup(event.remove, engine, name, callback)
        def prepare(sub, title, facts, token):
            self.assertFalse(checked_out, '等待查重/生成缺陷描述期间借用了数据库连接')
            return '描述', 'P2', []
        def create(sub, title, description, priority, executor, token):
            nonlocal fault
            self.assertFalse(checked_out)
            self.assertEqual((executor, token), ('dev@example.test', 'personal'))
            fault = 'from md_run ' if title.endswith('0') else None
            return {'external_ref': 'geelib#10' + title[-1]}
        def upload(sub, matter, name, content, mime, token):
            nonlocal fault
            self.assertEqual(content, b'example')
            self.assertFalse(checked_out, '附件上传期间借用了数据库连接')
            fault = 'update md_evidence_delivery' if matter == 100 else None
            return [{'id': matter, 'file_name': name}]
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch.object(geelib_account, 'get_user_app_token', return_value='personal'), \
             patch.object(md_bug_skill, 'prepare_case', side_effect=prepare), \
             patch.object(md_bug_skill, 'create_case', side_effect=create) as submit, \
             patch.object(md_evidence.md_attachment_skill, 'upload', side_effect=upload) as attach:
            result = self.post(f'/{rid}/report-bugs', executor_email='dev@example.test')
            self.assertEqual(result['reported'], 6)
            self.assertEqual([r['attachments_sent'] for r in result['results']], [1] * 6)
            self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 0)
            self.post(f'/{rid}/report-bugs', indexes=[0], receipt_action='attachments')
            self.assertEqual((submit.call_count, attach.call_count), (6, 6))
            self.assertEqual(faults, ['from md_run ', 'from md_run ', 'update md_evidence_delivery'])

    def test_database_commit_ack_lost_does_not_repeat_external_writes(self):
        from app.services import md_evidence, geelib_account
        rid = self.finished_run()
        with self.sessions.begin() as db:
            md_evidence.save(db, db.get(MdRun, rid), 'client.log', '', b'test')
        commits = 0
        def lose_ack(db):
            nonlocal commits
            commits += 1
            # First successful commit for the finding claim, receipt, file claim and file receipt.
            if commits <= 4:
                raise OperationalError(None, None, Exception(2013, 'commit acknowledged late'))
        event.listen(Session, 'after_commit', lose_ack)
        self.addCleanup(event.remove, Session, 'after_commit', lose_ack)
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch.object(geelib_account, 'get_user_app_token', return_value='personal'), \
             patch.object(md_bug_skill, 'create_case', return_value={'external_ref': 'geelib#101'}) as create, \
             patch.object(md_evidence, '_upload', return_value='201') as upload:
            result = self.post(f'/{rid}/report-bugs')
            self.assertEqual((result['reported'], result['results'][0]['attachments_sent']), (1, 1))
            self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 0)
            create.assert_called_once()
            upload.assert_called_once()

    def test_prolonged_disconnect_preserves_partial_batch_for_receipt_review(self):
        from app.services import geelib_account
        rid = self.finished_run()
        with self.sessions.begin() as db:
            db.get(MdRun, rid).findings = json.dumps([{'title': '首条'}, {'title': '次条'}])
        unavailable = False
        def disconnect(conn, cursor, sql, params, context, many):
            if unavailable and 'from md_run ' in sql.lower():
                raise OperationalError(sql, {}, Exception(2013, 'connection lost'))
        engine = self.sessions.kw['bind']
        event.listen(engine, 'before_cursor_execute', disconnect)
        self.addCleanup(event.remove, engine, 'before_cursor_execute', disconnect)
        def create(sub, title, *args):
            nonlocal unavailable
            unavailable = title.endswith('次条')
            return {'external_ref': 'geelib#102' if unavailable else 'geelib#101'}
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch.object(geelib_account, 'get_user_app_token', return_value='personal'), \
             patch.object(md_bug_skill, 'create_case', side_effect=create) as submit:
            response = self.client.post(f'/api/md-runs/{rid}/report-bugs', json={})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('SELECT', response.text)
            unavailable = False
            result = self.post(f'/{rid}/report-bugs')
            first, second = result['run']['findings']
            self.assertEqual(first['external_ref'], 'geelib#101')
            self.assertEqual(second['report_state'], 'sending')
            self.assertEqual((result['reported'], submit.call_count), (0, 2))

    def test_skill_duplicate_review_can_link_without_submitting(self):
        self.prepare.stop()
        rid = self.finished_run()
        match = {'id': 729, 'title': '确认按钮无响应', 'description': '触发条件和行为相同'}
        with patch.object(md_bug_skill, 'prepare_case', return_value=('点确认', 'P2', [match])), \
             patch.object(settings, 'GEELIB_ENABLED', True), patch.object(md_bug_skill, 'create_case') as create, \
             patch('app.services.geelib_account.get_user_app_token', return_value='personal'):
            path = f'/{rid}/report-bugs'
            result = self.post(path)['results'][0]
            self.assertEqual(result['status'], 'review')
            self.assertEqual(result['matches'], [match])
            linked = self.post(path, indexes=[0], receipt_action='link', review_key=result['review_key'], matter_id=729)
            self.assertEqual(linked['results'][0]['external_ref'], 'geelib#729')
            self.assertEqual(self.post(path)['reported'], 0)
            create.assert_not_called()

    def test_tracker_rejection_can_retry_but_lost_receipt_is_not_resubmitted(self):
        rid = self.finished_run()
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch('app.services.geelib_account.get_user_app_token', return_value='local-test-token'), \
             patch.object(md_bug_skill, 'find_title_matches', return_value=[]), \
             patch.object(md_bug_skill, 'create_case', side_effect=[md_bug_skill.GeelibError('字段无效'),
                          md_bug_skill.SubmissionUncertain('回执丢失')]) as submit:
            for _ in range(3):
                self.assertEqual(self.post(f'/{rid}/report-bugs')['reported'], 0)
            self.assertEqual(submit.call_count, 2)

    def test_check_and_link_legacy_receipt_without_creating(self):
        rid = self.finished_run()
        with self.sessions.begin() as db:
            row = db.get(MdRun, rid)
            row.findings = json.dumps([{**row.findings_list[0], 'report_state': 'uncertain'}])
        path = f'/{rid}/report-bugs'
        matches = [dict(id=729, title='[42] 按钮无响应', created_at='2026-11-25 18:56:02')]
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch('app.services.geelib_account.get_user_app_token', return_value='personal'), \
             patch.object(md_bug_skill, 'find_title_matches', return_value=matches) as lookup, \
             patch.object(md_bug_skill, 'create_case') as submit:
            review = self.post(path)['results'][0]
            self.assertEqual((review['status'], review['matches']), ('review', matches))
            lookup.assert_called_once_with(81, '[42] 按钮无响应', 'personal')
            payload = dict(indexes=[0], receipt_action='link', matter_id=729, review_key=review['review_key'])
            lookup.side_effect = md_bug_skill.GeelibError('查询暂不可用')
            self.assertEqual(self.post(path, **payload)['results'][0]['status'], 'check_failed')
            lookup.side_effect = None
            bad = self.client.post('/api/md-runs' + path, json={**payload, 'matter_id': 730})
            self.assertEqual(bad.status_code, 409)
            result = self.post(path, **payload)
            self.assertEqual((result['reported'], result['results'][0]['status']), (0, 'linked'))
            self.assertEqual(result['run']['findings'][0]['external_ref'], 'geelib#729')
            self.assertEqual(self.post(path, **payload)['reported'], 0)
            submit.assert_not_called()

    def test_uncertain_retry_requires_current_review_and_active_requests_stay_pending(self):
        rid = self.finished_run()
        path = f'/{rid}/report-bugs'
        with patch.object(settings, 'GEELIB_ENABLED', True), \
             patch('app.services.geelib_account.get_user_app_token', return_value='personal'), \
             patch.object(md_bug_skill, 'find_title_matches', return_value=[]) as lookup, \
             patch.object(md_bug_skill, 'create_case', side_effect=[md_bug_skill.SubmissionUncertain('超时'),
                          md_bug_skill.SubmissionUncertain('再次超时'), {'external_ref': 'geelib#730'}]) as submit:
            first = self.post(path, executor_email='dev@example.test')
            self.assertEqual(first['results'][0]['status'], 'uncertain')
            self.assertEqual(first['run']['findings'][0]['report_executor'], 'dev@example.test')
            def retry(key):
                return self.post(path, indexes=[0], receipt_action='retry', review_key=key, executor_email='second@example.test')
            key = self.post(path)['results'][0]['review_key']
            self.assertEqual(retry('stale')['results'][0]['status'], 'review')
            self.assertEqual(retry(key)['results'][0]['status'], 'uncertain')
            review = retry(key)['results'][0]
            self.assertEqual(review['status'], 'review')
            self.assertNotEqual(key, review['review_key'])
            self.assertEqual(retry(review['review_key'])['reported'], 1)
            self.assertEqual(retry(review['review_key'])['reported'], 0)
            self.assertEqual(submit.call_count, 3)
            self.assertEqual([c.args[-2] for c in submit.call_args_list],
                             ['dev@example.test', 'second@example.test', 'second@example.test'])

            with self.sessions.begin() as db:
                row = db.get(MdRun, rid)
                row.findings = json.dumps([dict(title='按钮无响应', report_state='sending')])
            lookup.reset_mock()
            self.assertEqual(self.post(path)['results'][0]['status'], 'sending')
            lookup.assert_not_called()
            with self.sessions.begin() as db:
                row = db.get(MdRun, rid)
                row.findings = json.dumps([{**row.findings_list[0], 'report_started_at': 1}])
            self.assertEqual(self.post(path)['results'][0]['status'], 'review')
            self.assertEqual(submit.call_count, 3)

    def test_receipt_lookup_filters_and_requires_a_complete_successful_response(self):
        row = dict(id='729', sub_id='81', type_name='缺陷', title='按钮无响应', mkd_content='点击确认后窗口不变')
        rows = [row, {**row, 'id': '730', 'sub_id': '82'}, {**row, 'id': '731', 'type_name': '需求'},
                {**row, 'id': '732', 'title': '其他按钮无响应'}]
        with patch.object(md_bug_skill.requests, 'post') as query:
            response = query.return_value
            response.json.return_value = {'errno': 2000, 'data': {'total': 4, 'matterList': rows}}
            result = md_bug_skill.find_title_matches(81, '按钮无响应', 'personal')
            self.assertEqual([(m['id'], m['description']) for m in result], [(729, '点击确认后窗口不变')])
            self.assertTrue(query.call_args.args[0].endswith('/openapi/Matter/getList'))
            self.assertFalse(query.call_args.kwargs['allow_redirects'])
            response.json.return_value['data']['total'] = 5
            with self.assertRaises(md_bug_skill.GeelibError):
                md_bug_skill.find_title_matches(81, '按钮无响应', 'personal')
