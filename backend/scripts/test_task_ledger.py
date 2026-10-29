"""任务台账：旧数据、字段取值、筛选日报快照、发版幂等与权限回归。
运行：cd backend && .venv/bin/python -m scripts.test_task_ledger
"""
import unittest
import tempfile
from pathlib import Path
from datetime import date, datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import task_ledger, tasks, release, checklist
from app.core.deps import get_current_user
from app.core.enums import TaskStatus, ProjectRole
from app.core.errors import register_exception_handlers
from app.db import migrate
from app.db.session import Base, get_db
from app.models import AiJob, User, Project, ProjectMember, Task, DailyReport, TaskDigest, ReleaseRecord, ChecklistItem


class LedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.test_task_daily_report_skill import install_test_skill
        cls.skill_temp = tempfile.TemporaryDirectory()
        cls.skill_dir, _, _ = install_test_skill(cls.skill_temp.name)
        cls.skill_patch = patch('app.services.task_digest_skill.get_skill_dir', return_value=cls.skill_dir)
        cls.skill_patch.start()
        from scripts.test_task_daily_report_skill import SummaryEngine
        cls.engine_patch = patch('app.services.generators.get_provider', return_value=SummaryEngine())
        cls.engine_patch.start()
        cls.deploy_patch = patch('app.services.report_publishing.publish_html', side_effect=lambda html, fallback, **kwargs: {'url':fallback,'hosting':'platform'})
        cls.deploy_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.skill_patch.stop()
        cls.engine_patch.stop()
        cls.deploy_patch.stop()
        cls.skill_temp.cleanup()

    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add_all([User(id=1, username='admin', name='平台负责人', password_hash='x', is_platform_admin=True),
                         User(id=2, username='member', name='平台成员', password_hash='x'),
                         User(id=3, username='guest', name='访客', password_hash='x'),
                         User(id=4, username='outsider', name='其他人', password_hash='x'),
                         Project(id=1, name='台账项目', code='ledger'), Project(id=2, name='其他项目', code='other'),
                         ProjectMember(project_id=1, user_id=2, role=ProjectRole.member),
                         ProjectMember(project_id=1, user_id=3, role=ProjectRole.guest)])
        self.db.commit()
        self.user_id = 1
        app = FastAPI()
        register_exception_handlers(app)
        for router in (checklist.router, tasks.router, task_ledger.router, release.router):
            app.include_router(router)
        def db_dep():
            try:
                yield self.db
            finally:
                self.db.rollback()
        app.dependency_overrides[get_db] = db_dep
        app.dependency_overrides[get_current_user] = lambda: self.db.get(User, self.user_id)
        self.client = TestClient(app)
        # 台账测试只写隔离库；通知发送由 test_task_ingest 覆盖。
        self.notice_patch = patch('app.services.task_assignment.dispatch_assignment_notices')
        self.notice_patch.start()
        self.addCleanup(self.notice_patch.stop)
        self.today = date.today().isoformat()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def task(self, **kw):
        payload = dict(project_id=1, title='原任务', assigned_to=1, assigned_date=self.today)
        payload.update(kw)
        response = self.client.post('/api/tasks', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data']

    def generate(self, payload):
        from app.services import ai_jobs
        response = self.client.post('/api/task-ledger/digests/generate', json=payload)
        self.assertEqual(response.status_code,200,response.text)
        queued = response.json()['data']
        job = self.db.get(AiJob, queued['job_id'])
        self.assertEqual(job.kind, 'task_digest')
        self.assertEqual(self.client.get(f"/api/task-ledger/digests/generations/{queued['generation_id']}").status_code,409)
        job.status = 'running'; self.db.commit()
        ai_jobs.run_job(sessionmaker(bind=self.engine),job.id)
        self.db.expire_all()
        result = self.client.get(f"/api/task-ledger/digests/generations/{queued['generation_id']}")
        self.assertEqual(result.status_code,200,result.text)
        return result.json()['data']

    def test_published_html_keeps_snapshot_requires_project_access_and_preserves_history(self):
        task = self.task(title='筛选内任务', status='testing')
        outside = self.task(title='筛选外任务', status='online')
        self.client.patch(f"/api/tasks/{task['id']}", json={'status':'testing'})
        selection = dict(project_id=1, task_ids=[task['id']], report_date=self.today)
        generated = self.generate(selection)
        draft = dict(selection, title=generated['title'], content=generated['content'] + '\n人工补充')
        result = self.client.post('/api/task-ledger/digests/publish', json=draft)
        self.assertEqual(result.status_code, 200, result.text)
        saved = result.json()['data']
        url = '/api/task-ledger/digests/shared/' + saved['share_path'].split('/')[-1]
        html = self.client.get(url).json()['data']['html']
        self.assertIn('筛选内任务', html); self.assertNotIn('筛选外任务', html)
        self.assertIn('人工补充', html); self.assertIn('<h4>测试中</h4>', html)
        self.assertNotIn('测试中</h2>',html)
        again = self.client.post('/api/task-ledger/digests/publish', json=saved).json()['data']
        self.assertEqual(again['share_path'], saved['share_path'])
        equivalent = dict(saved, content=saved['content'].replace('**' + saved['title'] + '**', '**另一条测试日报**', 1))
        equivalent_result = self.client.post('/api/task-ledger/digests/publish', json=equivalent).json()['data']
        self.assertTrue(equivalent_result['share_path'])
        self.user_id = 4
        self.assertEqual(self.client.get(url).status_code, 403)
        self.user_id = 3
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post('/api/task-ledger/digests/publish', json=saved).status_code, 403)
        self.user_id = 2
        self.assertEqual(self.client.post('/api/task-ledger/digests/publish', json=saved).status_code, 404)
        self.user_id = 1
        self.db.delete(self.db.get(Task, task['id'])); self.db.commit()
        edited = dict(saved, content=saved['content'] + '\n新的补充说明')
        saved_edit = self.client.post('/api/task-ledger/digests', json=edited).json()['data']
        self.assertIsNone(saved_edit['share_path'])
        revised = self.client.post('/api/task-ledger/digests/publish', json=edited).json()['data']
        self.assertNotEqual(revised['share_path'], saved['share_path'])
        self.assertEqual(self.client.get(url).json()['data']['html'], html)
        self.assertEqual(self.client.get('/api/task-ledger/digests', params={'project_id':1}).json()['data'][0]['share_path'], revised['share_path'])
        # Remove the test identity override: unauthenticated readers must be rejected.
        del self.client.app.dependency_overrides[get_current_user]
        self.assertIn(self.client.get(url).status_code, (401, 403))

    def test_online_publication_reuses_evaluation_service_and_existing_url(self):
        task = self.task()
        draft = dict(project_id=1, task_ids=[task['id']], report_date=self.today, title='在线日报', content='总结正文')
        with patch('app.services.report_publishing.publish_html', return_value={'url':'https://report.zhaomi.cn/','hosting':'nami'}) as publish:
            first = self.client.post('/api/task-ledger/digests/publish', json=draft).json()['data']
            second = self.client.post('/api/task-ledger/digests/publish', json=first).json()['data']
            self.assertEqual(first['share_path'], 'https://report.zhaomi.cn/')
            self.assertEqual(second['share_hosting'], 'nami')
            self.assertEqual(second['share_link_type'], 'short')
            publish.assert_called_once()
            self.assertIn('总结正文', publish.call_args.args[0])
            self.assertTrue(publish.call_args.args[1].startswith('/d/'))
            self.assertTrue(publish.call_args.kwargs['prefer_shortlink'])

    def test_cached_long_link_is_retried_then_short_link_is_reused(self):
        from app.models.task_digest import TaskDigestPublication, TaskDigestDeployment
        task = self.task()
        draft = dict(project_id=1, task_ids=[task['id']], report_date=self.today, title='已有长链的日报', content='完整日报内容')
        long_url = 'https://ns.chat.360.cn/zhaomi-so/client-up/abc/index.html'
        short_url = 'https://report.zhaomi.cn/'
        with patch('app.services.report_publishing.publish_html', side_effect=[
            {'url': long_url, 'hosting': 'nami'},
            {'url': long_url, 'hosting': 'nami'},
            {'url': short_url, 'hosting': 'nami'},
        ]) as publish:
            first = self.client.post('/api/task-ledger/digests/publish', json=draft).json()['data']
            self.assertEqual(first['share_link_type'], 'direct')
            self.assertIsNone(publish.call_args.kwargs['existing_url'])
            failed_retry = self.client.post('/api/task-ledger/digests/publish', json=first).json()['data']
            self.assertEqual(failed_retry['id'], first['id'])
            self.assertEqual(failed_retry['share_path'], long_url)
            self.assertEqual(publish.call_args.kwargs['existing_url'], long_url)
            success = self.client.post('/api/task-ledger/digests/publish', json=first).json()['data']
            self.assertEqual(success['share_path'], short_url)
            self.assertEqual(success['share_link_type'], 'short')
            self.assertEqual(success['content'], draft['content'])
            repeat = self.client.post('/api/task-ledger/digests/publish', json=success).json()['data']
            self.assertEqual(repeat['share_path'], short_url)
            self.assertEqual(publish.call_count, 3)
            self.assertEqual(self.db.query(TaskDigestPublication).count(), 1)
            self.assertEqual(self.db.query(TaskDigestDeployment).count(), 1)
            self.assertEqual(self.client.get('/api/task-ledger/digests', params={'project_id': 1}).json()['data'][0]['share_link_type'], 'short')

    def test_changed_digest_does_not_shorten_the_previous_content_url(self):
        task = self.task()
        draft = dict(project_id=1, task_ids=[task['id']], report_date=self.today, title='日报', content='第一版')
        with patch('app.services.report_publishing.publish_html', return_value={'url': 'https://ns.chat.360.cn/zhaomi-so/client-up/abc/index.html', 'hosting': 'nami'}) as publish:
            first = self.client.post('/api/task-ledger/digests/publish', json=draft).json()['data']
            first['content'] = '修改后的第二版'
            self.client.post('/api/task-ledger/digests/publish', json=first)
            self.assertEqual(publish.call_count, 2)
            self.assertIsNone(publish.call_args.kwargs['existing_url'])

    def test_generation_keeps_input_snapshot_and_is_private_to_author(self):
        from app.services import ai_jobs
        task = self.task(title='入队时标题',target_version='3.27.0')
        self.client.patch(f"/api/tasks/{task['id']}", json={'status':'testing'})
        payload = dict(project_id=1, task_ids=[task['id']], report_date=self.today)
        queued = self.client.post('/api/task-ledger/digests/generate', json=payload).json()['data']
        url = f"/api/task-ledger/digests/generations/{queued['generation_id']}"
        self.client.patch(f"/api/tasks/{task['id']}", json={'title':'后来修改的标题','status':'online','target_version':'3.28.0'})
        self.db.get(Project, 1).name = '入队后改名'
        job = self.db.get(AiJob, queued['job_id']); job.status = 'running'; self.db.commit()
        ai_jobs.run_job(sessionmaker(bind=self.engine), job.id)
        self.db.expire_all()
        result = self.client.get(url).json()['data']
        self.assertEqual(result['title'], '台账项目 3.27.0 测试日报')
        content = result['content']
        self.assertIn('入队时标题', content)
        self.assertNotIn('后来修改的标题', content)
        self.assertIn('### 测试中',content)
        self.assertNotIn('已上线',content)
        self.assertIn('3.27.0 测试日报',content)
        self.assertNotIn('3.28.0',content)
        self.user_id = 2
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_failed_publish_does_not_save_partial_snapshot(self):
        from fastapi import HTTPException
        task = self.task()
        draft = dict(project_id=1, task_ids=[task['id']], report_date=self.today, title='标题', content='正文')
        with patch('app.services.task_digest_skill.render_task_digest_html', side_effect=HTTPException(503, detail='skill missing')):
            self.assertEqual(self.client.post('/api/task-ledger/digests/publish', json=draft).status_code, 503)
        self.assertEqual(self.db.query(TaskDigest).count(), 0)

    def test_reconcile_backed_up_create_close_and_preserve_owners(self):
        from scripts.reconcile_task_ledger import prepare, apply
        primary=self.task(title='Existing primary',developer='平台开发',description='历史验收说明')
        duplicate=self.task(title='Duplicate')
        self.client.patch(f"/api/tasks/{primary['id']}",json={'status':'online'})
        source={'url':'https://example.test/sheet','rows':[
            {'source_row':2,'功能/需求':'飞书需求','状态':'测试中','风险等级':'红','归属版本':'3.27.0'},
            {'source_row':3,'功能/需求':'无人负责','日期':'1016','状态':'测试中','测试负责人':'保留原说明'}]}
        mapping={'primary':{'2':primary['id']},'new_owners':{'3':[]},'duplicates':{str(duplicate['id']):[primary['id']]}}
        plan=prepare(self.db,source,mapping,'台账项目',2026,1)
        with tempfile.TemporaryDirectory() as d:
            backup,result=Path(d)/'backup.json',Path(d)/'result.json'
            outcome=apply(self.db,plan,backup,result)
            self.assertTrue(backup.exists() and result.exists());self.assertTrue(outcome['verified'])
            with self.assertRaises(ValueError):apply(self.db,plan,backup,result)
        old=self.db.get(Task,primary['id'])
        self.assertEqual(old.title,'飞书需求');self.assertEqual(old.status,TaskStatus.online)
        self.assertEqual(old.assigned_to_ids,[1]);self.assertEqual(old.developer,'平台开发')
        self.assertEqual(old.description,'历史验收说明')
        closed=self.db.get(Task,duplicate['id'])
        self.assertEqual(closed.status,TaskStatus.closed);self.assertIn(str(primary['id']),closed.close_note)
        created=self.db.get(Task,outcome['created'][0]['task_id'])
        self.assertIsNone(created.assigned_to);self.assertEqual(created.assigned_to_ids,[])
        self.assertEqual(created.status,TaskStatus.testing);self.assertIn('保留原说明',created.description)

    def test_reconcile_stale_preview_aborts_before_any_write(self):
        from scripts.reconcile_task_ledger import prepare, apply
        primary=self.task(title='Primary')
        source={'url':'https://example.test/sheet','rows':[
            {'source_row':2,'功能/需求':'Primary','状态':'测试中'},
            {'source_row':3,'功能/需求':'New task','日期':'1016'}]}
        mapping={'primary':{'2':primary['id']},'new_owners':{'3':[1]},'duplicates':{}}
        plan=prepare(self.db,source,mapping,'台账项目',2026,1)
        self.client.patch(f"/api/tasks/{primary['id']}",json={'title':'Changed by colleague'})
        with tempfile.TemporaryDirectory() as d:
            backup=Path(d)/'backup.json'
            with self.assertRaises(ValueError):apply(self.db,plan,backup,Path(d)/'result.json')
            self.assertFalse(backup.exists())
        self.assertEqual(self.db.query(Task).count(),1)
        self.assertEqual(self.db.get(Task,primary['id']).title,'Changed by colleague')

    def test_legacy_roundtrip_null_and_status_values(self):
        old = self.task(description='历史说明', developer='平台开发')
        self.assertIsNone(old['risk_level'])
        self.assertIsNone(old['affects_release'])
        fields = dict(target_version=' 3.27.0 ', risk_level='red', affects_release=False,
                      progress_note='第一行\n第二行', expected_completion=self.today,
                      next_action='回归验证', action_owner='平台负责人', feedback_due_at=f'{self.today}T15:00:00', conclusion='待回归')
        r = self.client.patch(f"/api/tasks/{old['id']}", json=fields).json()['data']
        self.assertEqual(r['target_version'], '3.27.0')
        self.assertEqual(r['assigned_to'], old['assigned_to'])
        self.assertEqual(r['description'], '历史说明')
        self.client.patch(f"/api/tasks/{old['id']}", json={'module': '旧客户端更新'})
        self.assertEqual(self.db.get(Task, old['id']).progress_note, fields['progress_note'])
        self.client.patch(f"/api/tasks/{old['id']}", json={'risk_level': None, 'feedback_due_at': None, 'target_version': ''})
        self.db.expire_all()
        self.assertIsNone(self.db.get(Task, old['id']).risk_level)
        self.assertIsNone(self.db.get(Task, old['id']).target_version)
        for value in TaskStatus:
            r = self.client.patch(f"/api/tasks/{old['id']}", json={'status': value.value})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['data']['status'], value.value)
        self.assertEqual(self.client.patch(f"/api/tasks/{old['id']}", json={'risk_level': 'purple'}).status_code, 422)

    def test_copy_keeps_fields_without_release_link(self):
        old = self.task(assigned_date=(date.today()-timedelta(days=1)).isoformat(), target_version='3.27.0', risk_level='yellow', affects_release=False)
        r = self.client.post('/api/tasks/copy', params={'project_id':1, 'target_date':self.today})
        self.assertEqual(r.json()['data']['copied'], 1)
        copies = self.db.query(Task).filter(Task.id != old['id']).all()
        self.assertEqual(copies[0].target_version, '3.27.0')
        self.assertEqual(copies[0].risk_level, 'yellow')
        self.assertIsNone(copies[0].release_record_id)

    def test_filter_selection_digest_save_history_and_report_reuse(self):
        a = self.task(title='筛选内任务', risk_level='yellow', next_action='确认口径', action_owner='平台负责人')
        b = self.task(title='筛选外任务')
        self.db.add(DailyReport(task_id=a['id'], project_id=1, user_id=1, report_date=date.today(), summary='旧日报反馈保留', workload_hours=2))
        self.db.commit()
        payload = dict(project_id=1, task_ids=[a['id'],a['id']], report_date=self.today, filter_summary='风险：黄')
        generated = self.generate(payload)
        self.assertIn('筛选内任务',generated['content'])
        self.assertNotIn('共 1 个任务',generated['content'])
        self.assertNotIn('旧日报反馈保留', generated['content'])
        from app.models.task_digest import TaskDigestGeneration
        stored = __import__('json').loads(self.db.query(TaskDigestGeneration).one().input)
        self.assertEqual(stored, {'product_name':'台账项目', 'tasks':[{'id':a['id'],'title':'筛选内任务','status':a['status'],'target_version':a['target_version']}]})
        self.assertNotIn('filter_summary', stored)
        self.assertEqual(self.db.query(DailyReport).one().summary, '旧日报反馈保留')
        self.assertNotIn(b['title'], generated['content'])
        payload.update(generated, content=generated['content']+'\n手工补充')
        saved = self.client.post('/api/task-ledger/digests', json=payload).json()['data']
        self.assertEqual(saved['task_ids'], [a['id']])
        self.assertTrue(saved['content'].endswith('手工补充'))
        self.assertEqual(self.db.query(DailyReport).one().workload_hours, 2)
        self.client.delete(f"/api/tasks/{a['id']}")
        payload.update(id=saved['id'],content='删除任务后仍可维护历史日报')
        self.assertEqual(self.client.post('/api/task-ledger/digests',json=payload).status_code,200)
        self.user_id = 2
        self.assertEqual(self.client.get('/api/task-ledger/digests',params={'project_id':1}).json()['data'],[])
        self.assertEqual(self.client.post('/api/task-ledger/digests',json=payload).status_code,404)

    def test_permissions_and_cross_project(self):
        own = self.task(); other = self.task(project_id=2)
        payload = dict(project_id=1, task_ids=[own['id'],other['id']], report_date=self.today)
        self.assertEqual(self.client.post('/api/task-ledger/digests/generate',json=payload).status_code,400)
        for uid in (3,4):
            self.user_id = uid
            self.assertEqual(self.client.post('/api/task-ledger/digests/generate',json=payload).status_code,403)
            self.assertEqual(self.client.patch(f"/api/tasks/{own['id']}",json={'risk_level':'green'}).status_code,403)
        self.user_id = 2
        self.assertEqual(self.client.post('/api/task-ledger/releases',json=dict(project_id=1, task_ids=[own['id']],version='1',release_date=self.today,content='内容')).status_code,403)

    def test_release_create_retry_append_and_delete(self):
        a = self.task(target_version='3.27.0'); b = self.task(title='第二项',target_version='3.27.0')
        payload = dict(project_id=1, task_ids=[a['id']], version='3.27.0', release_date=self.today, content='人工确认的上线内容')
        self.assertEqual(self.client.post('/api/task-ledger/releases',json=payload).status_code,400)
        for item in (a,b):
            self.client.patch(f"/api/tasks/{item['id']}",json={'status':'online'})
        result = self.client.post('/api/task-ledger/releases',json=payload)
        self.assertEqual(result.status_code,200,result.text)
        rel = result.json()['data']['release']
        self.assertEqual(rel['req_count'],1)
        self.assertEqual(self.client.post('/api/task-ledger/releases',json=payload).json()['data']['added'],0)
        self.assertEqual(self.db.get(ReleaseRecord,rel['id']).content, payload['content'])
        self.assertEqual(self.client.patch(f"/api/tasks/{a['id']}",json={'target_version':'other'}).status_code,400)
        payload.update(task_ids=[b['id']],content='新增内容')
        self.assertEqual(self.client.post('/api/task-ledger/releases',json=payload).status_code,409)
        payload['release_id'] = rel['id']
        self.assertEqual(self.client.post('/api/task-ledger/releases',json=payload).json()['data']['added'],1)
        detail = self.client.get(f"/api/releases/{rel['id']}").json()['data']
        self.assertEqual(detail['req_count'],2)
        self.assertEqual(detail['content'],'人工确认的上线内容\n\n新增内容')
        self.assertEqual(len(detail['tasks']),2)
        self.client.patch(f"/api/releases/{rel['id']}",json={'version':'3.28.0'})
        self.assertEqual(self.db.get(Task,a['id']).target_version,'3.28.0')
        self.client.delete(f"/api/releases/{rel['id']}")
        self.assertIsNone(self.db.get(Task,a['id']).release_record_id)
        self.assertEqual(self.db.get(Task,a['id']).target_version,'3.28.0')

    def test_digest_backfill_keep_all_platform_assignees_and_custom_products(self):
        from scripts.backfill_task_ledger import make_plan, apply_plan
        task = self.task(title='多人测试任务', assigned_to_ids=[1,2], target_version='3.27.0')
        self.client.patch(f"/api/tasks/{task['id']}", json={'risk_level':'red','assigned_to':1})
        self.assertEqual(self.db.get(Task,task['id']).assigned_to_ids,[1,2])
        digest=self.generate({'project_id':1,'task_ids':[task['id']],'report_date':self.today})
        self.assertNotIn('平台负责人',digest['content'])
        self.assertNotIn('平台成员',digest['content'])
        self.assertIn('3.27.0',digest['content'])
        self.assertEqual(self.db.get(Task,task['id']).assigned_to_ids,[1,2])
        source={'rows':[{'source_row':2,'功能/需求':'来源简称','风险等级':'黄','测试负责人':'未知昵称'}]}
        plan=make_plan(self.db,self.db.get(Project,1),source,{'2':{'task_id':task['id'],'title':task['title']}})
        with tempfile.TemporaryDirectory() as temp:
            apply_plan(self.db,plan,Path(temp)/'backup.json')
        self.assertEqual(self.db.get(Task,task['id']).assigned_to_ids,[1,2])
        self.client.post('/api/releases/products',json={'project_id':1,'name':'平台自定义产品'})
        self.client.patch(f"/api/tasks/{task['id']}",json={'status':'online'})
        result=self.client.post('/api/task-ledger/releases',json={'project_id':1,'task_ids':[task['id']],'version':'3.27.0','release_date':self.today,'sub_product':'平台自定义产品','content':'上线'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['data']['release']['sub_product'],'平台自定义产品')

    def test_legacy_migration_is_additive_and_repeatable(self):
        old_engine = create_engine('sqlite://')
        with old_engine.begin() as c:
            c.execute(text('CREATE TABLE task (id INTEGER PRIMARY KEY, title VARCHAR(255), status VARCHAR(16))'))
            c.execute(text("INSERT INTO task VALUES (1,'历史任务','doing')"))
        with patch.object(migrate,'engine',old_engine):
            migrate.ensure_task_columns(); migrate.migrate_task_status()
            migrate.ensure_task_columns(); migrate.migrate_task_status()
        with old_engine.connect() as c:
            row = c.execute(text('SELECT title,status,risk_level,target_version,affects_release FROM task')).one()
        self.assertEqual(tuple(row),('历史任务','testing',None,None,None))
        self.assertIn('feedback_due_at',{c['name'] for c in inspect(old_engine).get_columns('task')})
        old_engine.dispose()

    def test_checklist_summary_all_dates_and_completed_today(self):
        old_date = date.today() - timedelta(days=2)
        old = self.task(title='较早完成', assigned_date=old_date.isoformat())
        current = self.task(title='今天上线', assigned_date=old_date.isoformat())
        for task_id in (old['id'], current['id']):
            row = self.db.get(Task, task_id)
            row.status = TaskStatus.online
            row.online_at = datetime.combine(old_date if task_id == old['id'] else date.today(), datetime.min.time())
            self.db.add(ChecklistItem(task_id=task_id, test_case_id=task_id, project_id=1))
        self.db.commit()
        all_summary = self.client.get('/api/tasks/checklist-summary', params={'project_id':1})
        self.assertEqual(all_summary.status_code, 200, all_summary.text)
        self.assertEqual(set(all_summary.json()['data']), {str(old['id']),str(current['id'])})
        today_summary = self.client.get('/api/tasks/checklist-summary', params={'project_id':1,'date':self.today}).json()['data']
        self.assertEqual(set(today_summary), {str(current['id'])})

    def test_release_rejects_wrong_project_version_and_oversize_atomically(self):
        task = self.task(target_version='3.27.0')
        self.client.patch(f"/api/tasks/{task['id']}", json={'status':'online'})
        payload = dict(project_id=1,task_ids=[task['id']],version='3.28.0',release_date=self.today,content='上线内容')
        self.assertEqual(self.client.post('/api/task-ledger/releases',json=payload).status_code,400)
        payload.update(project_id=2,version='3.27.0')
        self.assertEqual(self.client.post('/api/task-ledger/releases',json=payload).status_code,400)
        payload.update(project_id=1,content='中'*22000)
        self.assertEqual(self.client.post('/api/task-ledger/releases',json=payload).status_code,400)
        self.assertEqual(self.db.query(ReleaseRecord).count(),0)
        self.assertIsNone(self.db.get(Task,task['id']).release_record_id)

    def test_backfill_keeps_platform_owners_avoids_terminal_regression_and_stale_plan(self):
        from scripts.backfill_task_ledger import make_plan, apply_plan
        a = self.task(title='连接器功能回归测试', developer='平台开发名')
        b = self.task(title='已发布功能回归测试')
        self.client.patch(f"/api/tasks/{b['id']}",json={'status':'online'})
        self.task(title='重复需求'); self.task(title='重复需求')
        source={'rows':[
            {'source_row':2,'功能/需求':a['title'],'测试负责人':'飞书昵称','开发负责人':'开发简称','状态':'测试中','优先级':'P1','归属版本':'3.27.0','风险等级':'黄','动作负责人':'你','最晚反馈时间':'今日 15:00'},
            {'source_row':3,'功能/需求':b['title'],'状态':'测试完成','归属版本':'3.26.0'},
            {'source_row':4,'功能/需求':'重复需求','状态':'测试中'}]}
        plan=make_plan(self.db,self.db.get(Project,1),source)
        self.assertEqual(len(plan['changes']),2)
        self.assertEqual(len(plan['unresolved']),1)
        self.assertNotIn('status',plan['changes'][1]['values'])
        self.assertNotIn('assigned_to',plan['changes'][0]['values'])
        self.assertNotIn('developer',plan['changes'][0]['values'])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(apply_plan(self.db,plan,Path(tmp)/'backup.json'),2)
            self.assertEqual(self.db.get(Task,a['id']).assigned_to,1)
            self.assertEqual(self.db.get(Task,a['id']).developer,'平台开发名')
            self.assertEqual(self.db.get(Task,b['id']).status,TaskStatus.online)
            with self.assertRaisesRegex(ValueError,'已变化'):
                apply_plan(self.db,plan,Path(tmp)/'again.json')
            self.db.rollback()


if __name__ == '__main__':
    unittest.main()
