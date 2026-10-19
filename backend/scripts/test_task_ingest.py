"""提测自动录入 / 管理员派单 / 持久通知重试；全部使用隔离库与模拟推推。"""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app.api import stats, task_ingest, tasks
from app.core.config import settings
from app.core.enums import ProjectRole
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.db import migrate
from app.db.session import Base, get_db
from app.models import Project, ProjectMember, Task, TaskAssignmentNotice, User
from app.services import notify, task_assignment


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f'sqlite:///{self.tmp.name}/test.db', connect_args={'check_same_thread': False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add_all([User(id=i, username=f'u{i}', name=f'用户{i}', password_hash='x', is_platform_admin=i == 1)
                        for i in range(1, 7)])
            db.add_all([Project(id=1, name='提测项目', code='test'), Project(id=2, name='另一项目', code='other')])
            db.add_all([ProjectMember(project_id=1, user_id=i, role=role)
                        for i, role in [(2, ProjectRole.admin), (3, ProjectRole.member), (4, ProjectRole.guest), (6, ProjectRole.member)]])
            db.commit()
        app = FastAPI()
        register_exception_handlers(app)
        for router in [task_ingest.router, tasks.router, stats.router]:
            app.include_router(router)
        def get_test_db():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = get_test_db
        self.client = TestClient(app)
        self.headers = {'Authorization': f'Bearer {create_access_token("2")}'}
        self.patches = [patch.object(task_assignment, 'SessionLocal', self.sessions),
                        patch.object(task_assignment, 'send_tuitui_message', return_value=True),
                        patch.object(settings, 'TUITUI_TASK_ASSIGN_GROUP', '7652944526714832'),
                        patch.object(settings, 'TUITUI_ACCOUNT_MAP', {'u2': 'domain2', 'u3': 'domain3', 'u6': 'domain6'}),
                        patch.object(settings, 'PLATFORM_BASE_URL', 'https://platform.example'),
                        patch.object(notify, '_tuitui_send')]
        for p in self.patches:
            p.start()
        self.sender = task_assignment.send_tuitui_message
        self.payload = dict(project_id=1, source_message_id='7652944526719730123', title='提测：新增搜索',
                            description='来源群：开发群\n开发：张三\n原消息内容：搜索功能已部署到测试环境',
                            developer='张三', requirement_url='https://example.com/requirement', target_version='3.28')

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):
            p.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def ingest(self, **overrides):
        response = self.client.post('/api/tasks/ingest', json={**self.payload, **overrides}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['data']

    def update(self, tid, body, uid=2):
        return self.client.patch(f'/api/tasks/{tid}', json=body,
                                 headers={'Authorization': f'Bearer {create_access_token(str(uid))}'})

    def test_ingest_is_unassigned_idempotent_and_preserves_manual_edits(self):
        first = self.ingest()
        task = first['task']
        self.assertTrue(first['created'])
        self.assertIsNone(task['assigned_to'])
        self.assertEqual(task['status'], 'unassigned')
        self.assertEqual(task['description'], self.payload['description'])
        self.assertEqual(task['source_message_id'], self.payload['source_message_id'])
        self.sender.assert_not_called()
        result = self.update(task['id'], {'title': '管理员修订', 'assigned_to': 3})
        self.assertEqual(result.status_code, 200, result.text)
        retry = self.ingest()
        self.assertFalse(retry['created'])
        self.assertEqual(retry['task']['title'], '管理员修订')
        self.assertEqual(retry['task']['assigned_to'], 3)
        self.assertEqual(self.sender.call_count, 1)
        self.assertTrue(self.ingest(source_message_id='different-message')['created'])

    def test_assignment_sends_once_to_dedicated_group(self):
        tid = self.ingest()['task']['id']
        response = self.update(tid, {'assigned_to': 3, 'status': 'unassigned'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['status'], 'pending')
        self.assertTrue(response.json()['data']['assignment_notification_queued'])
        self.assertEqual(self.sender.call_count, 1)
        content, group = self.sender.call_args.args
        self.assertEqual(group, '7652944526714832')
        self.assertEqual(self.sender.call_args.kwargs['at_accounts'], ['domain3'])
        for fragment in ['【测试任务派单】', '用户3', '张三', self.payload['title'], self.payload['description'],
                         f'https://platform.example/tasks?project_id=1&task_id={tid}']:
            self.assertIn(fragment, content)
        for body in [{'assigned_to': 3}, {'progress_note': '补充信息'}, {'status': 'testing'}]:
            self.assertEqual(self.update(tid, body).status_code, 200)
        task_assignment.dispatch_assignment_notices()
        self.assertEqual(self.sender.call_count, 1)
        self.update(tid, {'assigned_to': 6})
        self.assertEqual(self.sender.call_count, 2)

    def test_ingest_auth_and_no_automatic_assignment(self):
        self.assertEqual(self.client.post('/api/tasks/ingest', json=self.payload).status_code, 401)
        for uid in (3, 4, 5):
            response = self.client.post('/api/tasks/ingest', json=self.payload,
                                        headers={'Authorization': f'Bearer {create_access_token(str(uid))}'})
            self.assertEqual(response.status_code, 403)
        with patch.object(settings, 'TASK_INGEST_TOKEN', 'test-token'), patch.object(settings, 'TASK_INGEST_USER_ID', 2):
            response = self.client.post('/api/tasks/ingest', json=self.payload, headers={'X-Task-Ingest-Token': 'test-token'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.post('/api/tasks/ingest', json={**self.payload, 'project_id': 2},
                                              headers={'X-Task-Ingest-Token': 'test-token'}).status_code, 403)
            self.assertEqual(self.client.patch(f'/api/tasks/{response.json()["data"]["task"]["id"]}', json={'assigned_to': 3},
                                               headers={'X-Task-Ingest-Token': 'test-token'}).status_code, 403)
        for extra in [{'assigned_to': 3}, {'status': 'testing'}, {'source_group_id': 'wrong'},
                      {'source_message_id': 7652944526719730123}, {'title': '  '}, {'requirement_url': 'javascript:alert(1)'}]:
            self.assertEqual(self.client.post('/api/tasks/ingest', json={**self.payload, **extra}, headers=self.headers).status_code, 422)

    def test_only_admin_may_assign_and_only_valid_project_members(self):
        tid = self.ingest()['task']['id']
        self.assertEqual(self.update(tid, {'assigned_to': 3}, uid=3).status_code, 403)
        self.assertEqual(self.update(tid, {'progress_note': '信息补充'}, uid=3).status_code, 200)
        for uid in (4, 5, 999):
            self.assertEqual(self.update(tid, {'assigned_to': uid}).status_code, 404 if uid == 999 else 400)
        self.sender.assert_not_called()
        self.assertEqual(self.update(tid, {'assigned_to': 2}).status_code, 200)
        self.assertEqual(self.sender.call_count, 1)  # 管理员给自己派单也通知

    def test_multi_owner_selection_notifies_once_and_rejects_member_changes(self):
        tid = self.ingest()['task']['id']
        response = self.update(tid, {'assigned_to_ids': [3, 6]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['assigned_to_ids'], [3, 6])
        self.assertEqual(self.sender.call_count, 1)
        self.assertIn('测试负责人：用户3、用户6', self.sender.call_args.args[0])
        self.assertEqual(self.sender.call_args.kwargs['at_accounts'], ['domain3', 'domain6'])
        for body in [{'assigned_to_ids': [3, 6]}, {'assigned_to_ids': [6, 3]}, {'assigned_to': 6, 'title': '旧表单更新'}]:
            self.assertEqual(self.update(tid, body).status_code, 200)
        self.assertEqual(self.sender.call_count, 1)
        self.assertEqual(self.update(tid, {'assigned_to_ids': [3]}, uid=3).status_code, 403)
        self.assertEqual(self.update(tid, {'assigned_to_ids': [3, 4]}).status_code, 400)
        self.assertEqual(self.update(tid, {'assigned_to_ids': []}).status_code, 200)
        self.assertEqual(self.sender.call_count, 1)
        with self.sessions() as db:
            self.assertEqual(db.get(Task, tid).assigned_to_ids, [])

    def test_failed_delivery_is_persistent_and_retried(self):
        self.sender.return_value = False
        tid = self.ingest()['task']['id']
        self.assertEqual(self.update(tid, {'assigned_to': 3}).status_code, 200)
        with self.sessions() as db:
            notice = db.query(TaskAssignmentNotice).one()
            self.assertIsNone(notice.sent_at)
            self.assertEqual(notice.attempts, 1)
            self.assertTrue(notice.last_error)
            notice.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        self.sender.return_value = True
        task_assignment.dispatch_assignment_notices()
        task_assignment.dispatch_assignment_notices()
        self.assertEqual(self.sender.call_count, 2)
        self.assertTrue(all(call.kwargs['at_accounts'] == ['domain3'] for call in self.sender.call_args_list))
        with self.sessions() as db:
            self.assertIsNotNone(db.query(TaskAssignmentNotice).one().sent_at)

    def test_missing_owner_mapping_waits_then_retries_with_all_mentions(self):
        tid = self.ingest()['task']['id']
        with patch.object(settings, 'TUITUI_ACCOUNT_MAP', {'u3': 'domain3'}):
            self.assertEqual(self.update(tid, {'assigned_to_ids': [3, 6]}).status_code, 200)
        self.sender.assert_not_called()
        with self.sessions() as db:
            notice = db.query(TaskAssignmentNotice).one()
            self.assertIsNone(notice.sent_at)
            self.assertIn('#6', notice.last_error)
            notice.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        task_assignment.dispatch_assignment_notices()
        self.sender.assert_called_once()
        self.assertEqual(self.sender.call_args.kwargs['at_accounts'], ['domain3', 'domain6'])

    def test_invalid_or_all_member_mentions_are_not_sent(self):
        for account in ['', '@用户3', 'all', 'domain3,domain6']:
            with self.subTest(account=account), patch.object(settings, 'TUITUI_ACCOUNT_MAP', {'u3': account}):
                tid = self.ingest(source_message_id='invalid-' + account)['task']['id']
                self.assertEqual(self.update(tid, {'assigned_to': 3}).status_code, 200)
        self.sender.assert_not_called()

    def test_transport_uses_native_at_field_and_keeps_plain_messages_compatible(self):
        response = Mock(status_code=200)
        response.json.return_value = {'errcode': 0}
        with patch.object(settings, 'TUITUI_BOT_APPID', 'test'), patch.object(settings, 'TUITUI_BOT_SECRET', 'secret'), \
                patch.object(notify.requests, 'post', return_value=response) as post:
            self.assertTrue(notify.send_tuitui_message('派单', '7652944526714832', at_accounts=['domain3', 'domain6', 'domain3']))
            self.assertEqual(post.call_args.kwargs['json'], {
                'togroups': ['7652944526714832'], 'msgtype': 'text',
                'text': {'content': '派单'}, 'at': ['domain3', 'domain6'],
            })
            self.assertTrue(notify.send_tuitui_message('普通通知', '7652944526714832'))
            self.assertNotIn('at', post.call_args.kwargs['json'])

    def test_old_pending_notice_cancelled_on_reassignment_or_clear(self):
        self.sender.return_value = False
        tid = self.ingest()['task']['id']
        self.update(tid, {'assigned_to': 3})
        self.update(tid, {'assigned_to': 6})
        self.update(tid, {'assigned_to': None})
        with self.sessions() as db:
            self.assertTrue(all(n.cancelled_at for n in db.query(TaskAssignmentNotice).all()))
            self.assertIsNone(db.get(Task, tid).assigned_to)
        self.assertEqual(self.sender.call_count, 2)

    def test_unassigned_tasks_excluded_from_personal_work_and_copy(self):
        self.ingest()
        response = self.client.get('/api/stats/daily', params={'project_id': 1, 'date': date.today().isoformat()}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['should_submit'], 0)
        response = self.client.get('/api/stats/workload', params={'project_id': 1, 'from': '2000-01-01', 'to': '2100-01-01'}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['total_tasks'], 0)
        self.ingest(source_message_id='yesterday', assigned_date=(date.today() - timedelta(days=1)).isoformat())
        response = self.client.post('/api/tasks/copy', params={'project_id': 1, 'target_date': date.today().isoformat()}, headers=self.headers)
        self.assertEqual(response.json()['data']['copied'], 0)

    def test_transport_requires_explicit_success_ack(self):
        with patch.object(settings, 'TUITUI_BOT_APPID', 'test'), patch.object(settings, 'TUITUI_BOT_SECRET', 'secret'):
            for value, expected in [({'errcode': 0}, True), ({'errcode': '0'}, True), ({'errcode': 1}, False), ({}, False), ([], False)]:
                response = Mock(status_code=200)
                response.json.return_value = value
                with patch.object(notify.requests, 'post', return_value=response):
                    self.assertEqual(notify.send_tuitui_message('测试', '7652944526714832'), expected)


class MigrationTests(unittest.TestCase):
    def test_sqlite_nullable_migration_preserves_related_rows_and_indexes(self):
        engine = create_engine('sqlite://')
        @event.listens_for(engine, 'connect')
        def enable_fk(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
        with engine.begin() as c:
            c.exec_driver_sql('CREATE TABLE user (id INTEGER PRIMARY KEY)')
            c.exec_driver_sql('INSERT INTO user VALUES (1)')
            c.exec_driver_sql('CREATE TABLE task (id INTEGER PRIMARY KEY, assigned_to INTEGER NOT NULL REFERENCES user(id), title TEXT)')
            c.exec_driver_sql('CREATE INDEX legacy_task_title ON task(title)')
            c.exec_driver_sql("INSERT INTO task VALUES (7,1,'历史任务')")
            c.exec_driver_sql('CREATE TABLE report (id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES task(id) ON DELETE CASCADE)')
            c.exec_driver_sql('INSERT INTO report VALUES (9,7)')
        with patch.object(migrate, 'engine', engine):
            migrate.ensure_task_columns()
            migrate.ensure_task_columns()
        with engine.begin() as c:
            self.assertEqual(c.execute(text('SELECT title FROM task WHERE id=7')).scalar(), '历史任务')
            self.assertEqual(c.execute(text('SELECT task_id FROM report')).scalar(), 7)
            self.assertEqual(c.exec_driver_sql('PRAGMA foreign_keys').scalar(), 1)
            self.assertEqual(c.exec_driver_sql('PRAGMA foreign_key_check').all(), [])
            c.exec_driver_sql("INSERT INTO task (id,assigned_to,title) VALUES (8,NULL,'待分配')")
        self.assertIn('legacy_task_title', {i['name'] for i in inspect(engine).get_indexes('task')})
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
