"""Offline regression for interrupted judgments; never touches production data."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.session import Base
from app.models import EvalRun
from app.core.enums import EvalRunStatus
from app.services import eval_judge as judge


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def seed(self, status=EvalRunStatus.judging):
        run = EvalRun(project_id=1, status=status, answer="original answer",
                      trace="/uploads/original.json", payload='{"prompt":"original"}')
        self.db.add(run)
        self.db.commit()
        return run

    def test_startup_recovers_only_judging_and_keeps_evidence(self):
        interrupted = self.seed()
        others = [self.seed(s) for s in (EvalRunStatus.running, EvalRunStatus.pending,
                                       EvalRunStatus.done, EvalRunStatus.judged)]
        statuses = [r.status for r in others]
        self.assertEqual(judge.recover_interrupted_judgments(self.db), 1)
        self.db.refresh(interrupted)
        self.assertEqual(interrupted.status, EvalRunStatus.done)
        self.assertEqual(interrupted.verdict, "error")
        self.assertFalse(interrupted.is_abnormal)
        self.assertEqual(interrupted.answer, "original answer")
        self.assertEqual(interrupted.trace, "/uploads/original.json")
        self.assertEqual(interrupted.payload, '{"prompt":"original"}')
        self.assertEqual([r.status for r in others], statuses)
        self.assertEqual(judge.recover_interrupted_judgments(self.db), 0)

    def test_exception_recovers_target_only_and_still_propagates(self):
        run = self.seed(EvalRunStatus.done)
        other = self.seed()
        def crash(db, r, **kwargs):
            r.status = EvalRunStatus.judging
            db.commit()
            raise RuntimeError("write failed")
        with patch.object(judge, "_judge_run", side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                judge.judge_run(self.db, run)
        self.db.expire_all()
        self.assertEqual(run.status, EvalRunStatus.done)
        self.assertEqual(run.verdict, "error")
        self.assertEqual(other.status, EvalRunStatus.judging)

    def test_recovery_does_not_overwrite_committed_success(self):
        run = self.seed(EvalRunStatus.judged)
        run.verdict = "pass"
        run.score = 5
        self.db.commit()
        self.assertEqual(judge.recover_interrupted_judgments(self.db, run.id), 0)
        self.assertEqual(run.verdict, "pass")
        self.assertEqual(run.score, 5)


if __name__ == "__main__":
    unittest.main()
