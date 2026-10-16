"""Isolated transaction fault injection; no live database or runner."""
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from app.db.session import Base
from app.models import Project, RunnerDevice, ExecRun
from app.api.exec_queue import report
from app.schemas.exec_queue import ExecReportIn


class ReportDeadlockTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add(Project(id=1, name='test', code='report-test', status='active'))
        self.db.add(RunnerDevice(id=1, owner_id=1, name='test', runner_id='test', token='test-only-token'))
        self.db.add(ExecRun(id=1, project_id=1, runner='test', runner_device_id=1,
                            kind='gui', status='running', started_at=datetime.utcnow(), payload='{}'))
        self.db.commit()
        self.ctx = SimpleNamespace(device=self.db.get(RunnerDevice,1))
        self.body = ExecReportIn(verdict='fail',fail_kind='business',reason='original assertion',report=[{'no':5,'ok':False}])

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def test_deadlock_retries_whole_transaction_and_preserves_verdict(self):
        real_commit = self.db.commit
        attempts = []
        def commit():
            self.db.flush()  # partial changes must disappear on rollback
            attempts.append(1)
            if len(attempts)<3: raise OperationalError('UPDATE',{},Exception(1213,'Deadlock found'))
            real_commit()
        with patch.object(self.db,'commit',side_effect=commit), patch('app.api.exec_queue.time.sleep'):
            result=report(1,self.body,'test',self.db,self.ctx)
        self.assertEqual(len(attempts),3)
        self.assertEqual(result['data']['status'],'failed')
        self.assertEqual(result['data']['reason'],'original assertion')
        self.assertEqual(result['data']['fail_kind'],'business')

    def test_persistent_deadlock_rolls_back_and_stops_after_three_attempts(self):
        with patch.object(self.db,'commit',side_effect=OperationalError('UPDATE',{},Exception(1213,'deadlock'))) as commit, patch('app.api.exec_queue.time.sleep'):
            with self.assertRaises(OperationalError):report(1,self.body,'test',self.db,self.ctx)
        self.assertEqual(commit.call_count,3)
        self.assertEqual(self.db.get(ExecRun,1).status,'running')

    def test_other_database_errors_are_not_retried(self):
        with patch.object(self.db,'commit',side_effect=OperationalError('UPDATE',{},Exception(1045,'denied'))) as commit:
            with self.assertRaises(OperationalError):report(1,self.body,'test',self.db,self.ctx)
        self.assertEqual(commit.call_count,1)
        self.assertEqual(self.db.get(ExecRun,1).status,'running')

if __name__=='__main__':unittest.main()
