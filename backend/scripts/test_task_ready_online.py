"""待上线：可保存、仍未完成，MySQL 迁移不丢失该状态。"""
import unittest
from datetime import date,timedelta
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import Task
from app.core.enums import TaskStatus
from app.schemas.task import TaskUpdate
from app.api.tasks import update_task,list_tasks
from app.db import migrate

class ReadyOnlineTests(unittest.TestCase):
 def test_save_carryover_and_online_transition(self):
  engine=create_engine('sqlite://')
  from app.db.session import Base
  Base.metadata.create_all(engine)
  with Session(engine) as db:
   t=Task(project_id=1,assigned_by=1,assigned_to=1,title='待上线验证',assigned_date=date.today()-timedelta(days=1),status=TaskStatus.testing)
   db.add(t);db.commit();tid=t.id
   user=SimpleNamespace(id=1,is_platform_admin=True)
   with patch('app.api.tasks.assert_project_role'):
    update_task(tid,TaskUpdate(status='ready_online'),db,user)
    db.expire_all();saved=db.get(Task,tid)
    self.assertEqual(saved.status,TaskStatus.ready_online)
    self.assertIsNone(saved.online_at);self.assertIsNone(saved.closed_at)
    self.assertTrue(saved.status_locked)
    self.assertTrue(any(row['id']==tid for row in list_tasks(1,date.today(),False,db,user)['data']))
    update_task(tid,TaskUpdate(status='online'),db,user)
    self.assertIsNotNone(db.get(Task,tid).online_at)
  engine.dispose()
 def test_mysql_migration_preserves_waiting_status_in_both_enum_definitions(self):
  engine=MagicMock();engine.dialect.name='mysql';connection=engine.begin.return_value.__enter__.return_value
  with patch.object(migrate,'engine',engine),patch.object(migrate,'_columns',return_value={'status'}):
   migrate.migrate_task_status()
  statements=[str(call.args[0]) for call in connection.execute.call_args_list]
  enums=[s for s in statements if s.startswith('ALTER TABLE task')]
  self.assertEqual(len(enums),2)
  self.assertTrue(all("'ready_online'" in s for s in enums))
if __name__=='__main__':unittest.main()
