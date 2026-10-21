"""Multi-day task filters and checklist summaries must select the same tasks."""
import unittest
from datetime import date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import tasks, checklist
from app.core.deps import get_current_user
from app.core.enums import TaskStatus, ProjectRole
from app.db.session import Base, get_db
from app.models import User, Project, ProjectMember, Task, ChecklistItem


class TaskDatesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as db:
            db.add(User(id=1, username='admin', name='Admin', password_hash='test', is_platform_admin=True))
            db.add_all([Project(id=i, name=f'P{i}', code=f'p{i}') for i in (1, 2)])
            rows = [
                (1, 1, 6, TaskStatus.pending, None),
                (2, 1, 9, TaskStatus.online, 9),
                (3, 1, 10, TaskStatus.closed, 10),
                (4, 1, 11, TaskStatus.pending, None),
                (5, 1, 12, TaskStatus.pending, None),
                (6, 1, 2, TaskStatus.closed, 11),
                (7, 2, 9, TaskStatus.pending, None),
            ]
            for tid, pid, day, state, done in rows:
                task = Task(id=tid, project_id=pid, assigned_by=1, assigned_to=1,
                            title=f'task {tid}', assigned_date=date(2026, 10, day), status=state)
                if done:
                    setattr(task, 'online_at' if state == TaskStatus.online else 'closed_at', datetime(2026, 10, done, 12))
                db.add(task)
                # Summary only joins task IDs, so no test-case data is needed in this isolated SQLite DB.
                db.add(ChecklistItem(task_id=tid, test_case_id=tid, project_id=pid))
            db.commit()
        def session():
            with self.factory() as db:
                yield db
        def current():
            with self.factory() as db:
                return db.get(User, 1)
        app = FastAPI()
        app.include_router(tasks.router)
        app.include_router(checklist.router)
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = current
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def check(self, params, expected):
        rows = self.client.get('/api/tasks', params=[('project_id', '1'), *params])
        summary = self.client.get('/api/tasks/checklist-summary', params=[('project_id', '1'), *params])
        self.assertEqual(rows.status_code, 200, rows.text)
        self.assertEqual(summary.status_code, 200, summary.text)
        data, counts = rows.json()['data'], summary.json()['data']
        self.assertEqual({t['id'] for t in data}, expected)
        self.assertEqual(len(data), len(expected))
        self.assertEqual(set(counts), {str(tid) for tid in expected})
        self.assertTrue(all(v['total'] == 1 for v in counts.values()))
        return {t['id']: t for t in data}

    def test_clear_dates_returns_all_project_tasks_and_summaries(self):
        rows = self.check([], {1, 2, 3, 4, 5, 6})
        self.assertFalse(any(t['is_carried'] for t in rows.values()))

    def test_version_options_include_historical_completed_tasks_and_deduplicate(self):
        with self.factory() as db:
            for tid, version in [(1, '3.28.0'), (2, '3.26.0'), (3, '3.26.0'), (4, ''), (7, 'other-project')]:
                db.get(Task, tid).target_version = version
            db.commit()
        response = self.client.get('/api/tasks/versions', params={'project_id': 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data'], ['3.26.0', '3.28.0'])

    def test_version_options_require_project_access(self):
        with self.factory() as db:
            db.get(User, 1).is_platform_admin = False
            db.add(ProjectMember(project_id=1, user_id=1, role=ProjectRole.guest))
            db.commit()
        self.assertEqual(self.client.get('/api/tasks/versions', params={'project_id': 1}).status_code, 200)
        self.assertEqual(self.client.get('/api/tasks/versions', params={'project_id': 2}).status_code, 403)

    def test_nonconsecutive_dates_union_deduplicates_and_keeps_completion_day(self):
        rows = self.check([('dates', '2026-10-11'), ('dates', '2026-10-09'), ('dates', '2026-10-09')], {1, 2, 4, 6})
        self.assertTrue(rows[1]['is_carried'])
        self.assertTrue(rows[6]['is_carried'])
        self.assertFalse(rows[2]['is_carried'])
        self.assertFalse(rows[4]['is_carried'])

    def test_legacy_single_date_and_multi_date_match(self):
        old = self.check([('date', '2026-10-11')], {1, 4, 6})
        new = self.check([('dates', '2026-10-11')], {1, 4, 6})
        self.assertEqual(old, new)

    def test_unfinished_tasks_keep_carrying_until_online_or_closed(self):
        # No copy or assigned-date update is needed, including after testing completes.
        for status in TaskStatus:
            if status in (TaskStatus.online, TaskStatus.closed):
                continue
            with self.subTest(status=status):
                with self.factory() as db:
                    db.get(Task, 1).status = status
                    db.commit()
                for day in ('2026-10-12', '2026-10-13'):
                    rows = self.check([('date', day)], {1, 4, 5})
                    self.assertTrue(rows[1]['is_carried'])
                    self.assertEqual(rows[1]['assigned_date'], '2026-10-06')
                    self.assertEqual(rows[1]['status'], status.value)

        for status, field in ((TaskStatus.online, 'online_at'), (TaskStatus.closed, 'closed_at')):
            with self.subTest(status=status):
                with self.factory() as db:
                    task = db.get(Task, 1)
                    task.status = status
                    task.online_at = task.closed_at = None
                    setattr(task, field, datetime(2026, 10, 12, 12))
                    db.commit()
                rows = self.check([('date', '2026-10-12')], {1, 4, 5})
                self.assertTrue(rows[1]['is_carried'])
                self.assertEqual(rows[1]['assigned_date'], '2026-10-06')
                self.check([('date', '2026-10-13')], {4, 5})

    def test_invalid_dates_rejected_by_both_endpoints(self):
        for path in ('/api/tasks', '/api/tasks/checklist-summary'):
            for invalid in ('invalid', '2026-02-30'):
                response = self.client.get(path, params={'project_id': 1, 'dates': invalid})
                self.assertEqual(response.status_code, 422)

    def test_range_includes_endpoints_and_middle_days(self):
        rows = self.check([('start_date', '2026-10-09'), ('end_date', '2026-10-11')], {2, 3, 4})
        self.assertFalse(any(t['is_carried'] for t in rows.values()))

    def test_same_day_range_excludes_carryover_and_completion_only_matches(self):
        rows = self.check([('start_date', '2026-10-11'), ('end_date', '2026-10-11')], {4})
        self.assertFalse(rows[4]['is_carried'])

    def test_future_range_does_not_include_old_pending_tasks(self):
        self.check([('start_date', '2026-11-23'), ('end_date', '2026-11-24')], set())

    def test_invalid_or_mixed_range_rejected(self):
        for path in ('/api/tasks', '/api/tasks/checklist-summary'):
            for params in [
                {'start_date': '2026-10-09'}, {'end_date': '2026-10-11'},
                {'start_date': '2026-10-11', 'end_date': '2026-10-09'},
                {'start_date': 'invalid', 'end_date': '2026-10-11'},
                {'start_date': '2026-10-09', 'end_date': '2026-10-11', 'date': '2026-10-10'},
            ]:
                self.assertEqual(self.client.get(path, params={'project_id': 1, **params}).status_code, 422)

    def test_range_crosses_month_and_year(self):
        self.check([('start_date', '2025-12-31'), ('end_date', '2026-11-01')], {1, 2, 3, 4, 5, 6})


if __name__ == '__main__':
    unittest.main()
