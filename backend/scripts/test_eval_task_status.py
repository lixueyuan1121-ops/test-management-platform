"""Task completion must not depend on opening the results dialog."""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import eval_task, eval_queue
from app.core.deps import get_current_user, get_db, require_runner_ctx, RunnerCtx
from app.core.enums import EvalRunStatus, EvalTaskStatus
from app.db.session import Base
from app.models import EvalRun, EvalTask, Project, User


class TaskStatusTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sf = sessionmaker(bind=self.engine)
        with self.sf() as db:
            db.add_all([User(id=1, username='admin', name='Admin', password_hash='x', is_platform_admin=True),
                        Project(id=1, name='Project', code='project')])
            db.commit()
        app = FastAPI()
        app.include_router(eval_task.router)
        app.include_router(eval_queue.router)
        def session():
            with self.sf() as db:
                yield db
        def user():
            with self.sf() as db:
                return db.get(User, 1)
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = user
        app.dependency_overrides[require_runner_ctx] = lambda: RunnerCtx(device=None)
        self.client = TestClient(app)
        self.spawn = self.enterContext(patch('app.services.eval_pipeline._spawn_pipeline'))

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def seed(self, statuses, *, task_status='running', auto=False):
        with self.sf() as db:
            task = EvalTask(project_id=1, name='Task', query_ids='[]', status=EvalTaskStatus(task_status), auto_pipeline=auto)
            db.add(task); db.flush()
            task.last_batch_id = f'batch-{task.id}'
            runs = [EvalRun(project_id=1, eval_task_id=task.id, batch_id=task.last_batch_id,
                            runner='test-runner', payload='{}', status=EvalRunStatus(s)) for s in statuses]
            db.add_all(runs); db.commit()
            return task.id, [r.id for r in runs]

    def stored_status(self, tid):
        with self.sf() as db:
            return db.get(EvalTask, tid).status.value

    def read(self, endpoint, tid):
        url = '/api/eval-tasks' if endpoint == 'list' else f'/api/eval-tasks/{tid}' + endpoint
        response = self.client.get(url, params={'project_id': 1} if endpoint == 'list' else {})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()['data']
        return next(r for r in result if r['id'] == tid) if endpoint == 'list' else result.get('task', result)

    def test_completed_tasks_reconcile_on_list_detail_and_poll_without_opening_results(self):
        for endpoint in ('list', '', '/summary-status', '/runs'):
            with self.subTest(endpoint=endpoint):
                tid, _ = self.seed(['done', 'judged'])
                result = self.read(endpoint, tid)
                self.assertEqual(result['status'], 'done')
                self.assertEqual(self.stored_status(tid), 'done')

    def test_running_empty_stopped_and_archived_tasks_are_not_closed(self):
        for statuses, state in [(['done', 'pending'], 'running'), (['done', 'running'], 'running'),
                                ([], 'running'), (['done', 'cancelled'], 'stopped'), (['done'], 'archived')]:
            with self.subTest(statuses=statuses, state=state):
                tid, _ = self.seed(statuses, task_status=state)
                self.assertEqual(self.read('list', tid)['status'], state)
                self.assertEqual(self.read('/summary-status', tid)['status'], state)
                self.assertEqual(self.stored_status(tid), state)

    def test_execution_finished_states_and_all_cancelled(self):
        for statuses, expected in [(['failed'], 'done'), (['done', 'cancelled'], 'done'),
                                   (['judging'], 'done'), (['cancelled'], 'stopped')]:
            with self.subTest(statuses=statuses):
                tid, _ = self.seed(statuses)
                self.assertEqual(self.read('list', tid)['status'], expected)

    def test_old_batch_completion_does_not_close_latest_batch(self):
        tid, _ = self.seed(['running'])
        with self.sf() as db:
            db.add(EvalRun(project_id=1, eval_task_id=tid, batch_id='old', status=EvalRunStatus.done))
            db.commit()
        response = self.client.get(f'/api/eval-tasks/{tid}/runs', params={'batch_id': 'old'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['task']['status'], 'running')
        self.assertEqual(self.read('list', tid)['status'], 'running')

    def test_runner_completion_updates_task_without_any_read_for_both_pipeline_modes(self):
        for auto in (False, True):
            with self.subTest(auto=auto):
                tid, runs = self.seed(['done', 'running'], auto=auto)
                response = self.client.patch(f'/api/eval-queue/{runs[-1]}', params={'runner': 'test-runner'},
                                             json={'status': 'done', 'answer': 'finished'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.stored_status(tid), 'done')
                self.assertEqual(self.spawn.call_count, int(auto))

    def test_manual_failure_closes_task_without_opening_results(self):
        tid, runs = self.seed(['running'])
        response = self.client.post(f'/api/eval-tasks/{tid}/runs/{runs[0]}/mark-failed')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.stored_status(tid), 'done')

    def test_stale_task_snapshot_cannot_override_new_batch_stop_or_retry(self):
        from app.services.eval_task_status import reconcile_task_status
        for change in ('new_batch', 'stop', 'retry'):
            with self.subTest(change=change):
                tid, runs = self.seed(['done'])
                with self.sf() as db:
                    db.expire_on_commit = False
                    task = db.get(EvalTask, tid)
                    if change == 'new_batch':
                        db.execute(update(EvalTask).where(EvalTask.id == tid).values(last_batch_id='new')
                                   .execution_options(synchronize_session=False))
                        db.add(EvalRun(project_id=1, eval_task_id=tid, batch_id='new', status=EvalRunStatus.running))
                    elif change == 'stop':
                        db.execute(update(EvalTask).where(EvalTask.id == tid).values(status=EvalTaskStatus.stopped)
                                   .execution_options(synchronize_session=False))
                    else:
                        db.execute(update(EvalRun).where(EvalRun.id == runs[0]).values(status=EvalRunStatus.pending))
                    db.commit()
                    self.assertEqual(task.status, EvalTaskStatus.running)  # stale caller snapshot
                    self.assertFalse(reconcile_task_status(db, task))
                    db.commit()
                self.assertEqual(self.stored_status(tid), 'stopped' if change == 'stop' else 'running')

    def test_retry_then_finish_transitions_back_to_running_and_done(self):
        tid, runs = self.seed(['failed'])
        self.assertEqual(self.read('list', tid)['status'], 'done')
        response = self.client.post(f'/api/eval-tasks/{tid}/runs/{runs[0]}/retry')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.read('/summary-status', tid)['status'], 'running')
        response = self.client.post(f'/api/eval-tasks/{tid}/runs/{runs[0]}/mark-failed')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.stored_status(tid), 'done')


if __name__ == '__main__':
    unittest.main()
