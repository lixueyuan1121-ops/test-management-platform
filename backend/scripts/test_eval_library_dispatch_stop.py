"""用例库创建任务、单对话停止及设备释放的 API 回归（仅内存数据库）。"""
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.deps import RunnerCtx, get_current_user, require_runner_ctx
from app.db.session import Base, get_db
from app.main import app
from app.models import EvalQuery, EvalRun, Project, ProjectMember, RunnerDevice, User
from app.models.ai_eval import EvalTask


class DispatchStopTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)
        with self.session.begin() as db:
            db.add_all([User(id=1, username='admin', name='A', password_hash='x', is_platform_admin=True),
                        User(id=2, username='guest', name='G', password_hash='x'),
                        Project(id=1, name='P', code='p'), Project(id=2, name='Other', code='o')])
            db.flush()
            db.add(ProjectMember(user_id=2, project_id=1, role='guest'))
            db.add(RunnerDevice(id=1, owner_id=1, runner_id='test-win', name='Windows', token='local-test'))
            for i in range(1, 5):
                db.add(EvalQuery(id=i, project_id=2 if i == 4 else 1, title=f'Q{i}', prompt='hello', expected='ok'))
        self.user_id = 1
        def get_session():
            with self.session() as db:
                yield db
        def get_user():
            with self.session() as db:
                return db.get(User, self.user_id)
        def get_runner():
            with self.session() as db:
                return RunnerCtx(db.get(RunnerDevice, 1))
        app.dependency_overrides[get_db] = get_session
        app.dependency_overrides[get_current_user] = get_user
        app.dependency_overrides[require_runner_ctx] = get_runner
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def enqueue(self, **extra):
        return self.client.post('/api/eval-queue/enqueue', json={
            'project_id': 1, 'runner': 'test-win', 'eval_query_ids': [1, 2, 1],
            'task_name': '  本次测评  ', **extra})

    def test_dispatch_creates_task_and_preserves_configuration(self):
        d = self.enqueue(dialog_options={'model': 'test-model'}, target_device='vm-test', trial_count=2).json()['data']
        self.assertEqual(len(d['run_ids']), 4)
        task = self.client.get(f"/api/eval-tasks/{d['eval_task_id']}").json()['data']
        self.assertEqual(task['name'], '本次测评')
        self.assertEqual(task['query_ids'], [1, 2])
        self.assertEqual(task['last_batch_id'], d['batch_id'])
        self.assertEqual(task['dialog_options'], {'model': 'test-model', 'trial_count': 2})
        runs = self.client.get(f"/api/eval-tasks/{task['id']}/runs").json()['data']['runs']
        self.assertEqual(len(runs), 4)
        self.assertTrue(all(r['target_device'] == 'vm-test' and r['payload']['dialog_options']['model'] == 'test-model' for r in runs))

    def test_invalid_dispatch_does_not_leave_task(self):
        for extra in ({'eval_query_ids': [4]}, {'eval_query_ids': [999]}, {'task_name': '   '}):
            self.assertNotEqual(self.enqueue(**extra).json()['code'], 0)
        with self.session() as db:
            self.assertEqual(db.query(EvalTask).count(), 0)
            self.assertEqual(db.query(EvalRun).count(), 0)

    def test_dispatch_rolls_back_task_when_freeze_fails(self):
        with patch('app.services.eval_experiment.freeze', side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):
                self.enqueue()
        with self.session() as db:
            self.assertEqual(db.query(EvalTask).count(), 0)
            self.assertEqual(db.query(EvalRun).count(), 0)

    def test_legacy_enqueue_remains_compatible(self):
        d = self.enqueue(task_name=None).json()['data']
        self.assertIsNone(d['eval_task_id'])
        self.assertEqual(len(d['run_ids']), 2)

    def test_stop_old_run_releases_device_and_rejects_late_writes(self):
        first = self.enqueue(eval_query_ids=[1]).json()['data']
        rid = first['run_ids'][0]
        params = {'runner': 'test-win', 'whole_group': True, 'engine': 'namiwork'}
        token = self.client.post(f'/api/eval-queue/{rid}/claim', params=params).json()['data']['claim_token']
        next_id = self.enqueue(eval_query_ids=[2]).json()['data']['run_ids'][0]
        self.assertEqual(self.client.post(f'/api/eval-queue/{next_id}/claim', params=params).json()['code'], 409)
        # 历史批次不能依赖任务的 last_batch_id，也不能改掉新批次状态。
        with self.session.begin() as db:
            t = db.get(EvalTask, first['eval_task_id'])
            t.last_batch_id = 'newer-batch'
        result = self.client.post(f'/api/eval-queue/{rid}/stop').json()['data']
        self.assertEqual(result['run_ids'], [rid])
        self.assertEqual(self.client.post(f'/api/eval-queue/{rid}/stop').json()['data']['cancelled_count'], 0)
        late = {'runner': 'test-win', 'claim_token': token}
        self.assertEqual(self.client.patch(f'/api/eval-queue/{rid}', params=late, json={'status': 'done', 'answer': 'late'}).json()['code'], 409)
        self.assertEqual(self.client.post(f'/api/eval-queue/{rid}/heartbeat', params=late).json()['code'], 409)
        self.assertEqual(self.client.post(f'/api/eval-queue/{next_id}/claim', params=params).json()['code'], 0)
        with self.session() as db:
            self.assertEqual(db.get(EvalTask, first['eval_task_id']).status, 'running')
            self.assertIsNone(db.get(EvalRun, rid).claim_token)
            self.assertIsNotNone(db.get(EvalRun, rid).finished_at)

    def test_stop_group_preserves_finished_turn_and_other_conversations(self):
        with self.session.begin() as db:
            for i in (1, 2, 3):
                q = db.get(EvalQuery, i)
                q.conversation_group, q.turn_index = 'group', i - 1
        d = self.enqueue(eval_query_ids=[1, 2, 3], trial_count=2).json()['data']
        with self.session.begin() as db:
            runs = db.query(EvalRun).filter(EvalRun.batch_id == d['batch_id']).all()
            first_trial = [r for r in runs if json.loads(r.payload)['trial_index'] == 1]
            for r in first_trial:
                r.status = 'running'
            first_trial[0].status, first_trial[0].answer = 'done', 'keep'
            rid, done_id = first_trial[1].id, first_trial[0].id
        stopped = self.client.post(f'/api/eval-queue/{rid}/stop').json()['data']
        self.assertEqual(stopped['cancelled_count'], 2)
        with self.session() as db:
            self.assertEqual(db.get(EvalRun, done_id).answer, 'keep')
            self.assertEqual(db.get(EvalRun, done_id).status, 'done')
            self.assertEqual(db.query(EvalRun).filter_by(status='pending').count(), 3)
            self.assertEqual(db.get(EvalTask, d['eval_task_id']).status, 'running')
        with self.session.begin() as db:
            for run in db.query(EvalRun).filter_by(status='pending'):
                run.status = 'done'
        result = self.client.get(f"/api/eval-tasks/{d['eval_task_id']}/runs").json()['data']
        self.assertEqual(result['task']['status'], 'done', '取消部分轮次后，其余完成仍应收口任务')

    def test_stop_all_marks_task_stopped(self):
        d = self.enqueue(eval_query_ids=[1]).json()['data']
        self.client.post(f"/api/eval-queue/{d['run_ids'][0]}/stop")
        with self.session() as db:
            self.assertEqual(db.get(EvalTask, d['eval_task_id']).status, 'stopped')

    def test_guest_cannot_dispatch_or_stop(self):
        rid = self.enqueue().json()['data']['run_ids'][0]
        self.user_id = 2
        self.assertEqual(self.enqueue().json()['code'], 403)
        self.assertEqual(self.client.post(f'/api/eval-queue/{rid}/stop').json()['code'], 403)
        with self.session() as db:
            self.assertEqual(db.get(EvalRun, rid).status, 'pending')


if __name__ == '__main__':
    unittest.main()
