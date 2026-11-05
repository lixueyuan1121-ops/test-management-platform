"""A stale editor must not silently undo a completed testing status."""
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.tasks import list_tasks, sync_task_status, update_task
from app.core.enums import TaskStatus
from app.db.session import Base
from app.models import Task
from app.schemas.task import TaskUpdate


class TaskStatusConflictTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.task = Task(project_id=1, assigned_by=1, assigned_to=1,
                         title='首次访问推荐弹窗', assigned_date=date(2026, 11, 4),
                         status=TaskStatus.pending)
        self.db.add(self.task)
        self.db.commit()
        self.user = SimpleNamespace(id=1, is_platform_admin=True)
        self.roles = patch('app.api.tasks.assert_project_role')
        self.roles.start()
        self.addCleanup(self.roles.stop)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

    def update(self, **payload):
        return update_task(self.task.id, TaskUpdate(**payload), self.db, self.user)

    def test_stale_status_edit_is_rejected_without_changing_notes(self):
        self.update(status='ready_online')
        with self.assertRaises(HTTPException) as error:
            self.update(status='pending', expected_status='pending', description='stale')
        self.assertEqual(error.exception.status_code, 409)
        self.db.expire_all()
        self.assertEqual(self.task.status, TaskStatus.ready_online)
        self.assertIsNone(self.task.description)

    def test_notes_and_next_day_listing_preserve_manual_status(self):
        self.update(status='ready_online')
        self.update(description='已上预发，等待素材')
        sync_task_status(self.task.id, TaskUpdate(status='pending'), self.db, self.user)
        rows = list_tasks(1, date(2026, 11, 5), False, self.db, self.user)['data']
        self.assertEqual(rows[0]['status'], 'ready_online')
        self.assertEqual(rows[0]['description'], '已上预发，等待素材')

    def test_fresh_intentional_reopen_is_allowed(self):
        self.update(status='ready_online')
        self.update(status='testing', expected_status='ready_online')
        self.db.expire_all()
        self.assertEqual(self.task.status, TaskStatus.testing)


if __name__ == '__main__':
    unittest.main()
