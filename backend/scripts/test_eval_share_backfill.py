"""Share backfill regression tests using an isolated in-memory database."""
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.eval_queue import report, _backfill_conversation_share, _backfill_qwork_conversation_share
from app.core.deps import RunnerCtx
from app.core.enums import EvalRunStatus
from app.db.session import Base
from app.models import EvalRun, Project
from app.schemas.eval_queue import EvalReportIn

LINK = "https://work.n.cn/share/test-session"


class ShareBackfillTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(Project(id=1, name="test", code="test"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def run_row(self, **overrides):
        values = dict(project_id=1, batch_id="b1", target_engine="namiwork", runner="r1",
                      claim_token="claim1", status=EvalRunStatus.done,
                      payload=json.dumps({"conversation_group": "g1", "compare_group": "A"}))
        values.update(overrides)
        row = EvalRun(**values)
        self.db.add(row)
        self.db.commit()
        return row

    def test_last_report_fills_completed_turns_before_return(self):
        first = self.run_row(answer="first answer", score=4, verdict="pass", status=EvalRunStatus.judged)
        last = self.run_row(status=EvalRunStatus.running)
        report(last.id, EvalReportIn(status="done", answer="last answer", share_link=LINK),
               runner="r1", claim_token="claim1", db=self.db, ctx=RunnerCtx())
        self.db.refresh(first)
        self.assertEqual(first.share_link, LINK)
        self.assertEqual((first.answer, first.score, first.verdict, first.status),
                         ("first answer", 4, "pass", EvalRunStatus.judged))

    def test_never_crosses_execution_boundaries_or_overwrites(self):
        source = self.run_row(share_link=LINK)
        variants = [dict(batch_id="b2"), dict(project_id=2), dict(eval_task_id=22), dict(runner="r2"),
                    dict(claim_token="new-claim"), dict(target_engine="workbuddy"),
                    dict(status=EvalRunStatus.running), dict(status=EvalRunStatus.cancelled),
                    dict(payload='{"conversation_group":"other","compare_group":"A"}'),
                    dict(payload='{"conversation_group":"g1","compare_group":"B"}'),
                    dict(payload='[]'), dict(share_link="https://work.n.cn/share/existing")]
        targets = [self.run_row(**variant) for variant in variants]
        self.assertEqual(_backfill_conversation_share(self.db, source), 0)
        self.assertTrue(all(r.share_link is None for r in targets[:-1]))
        self.assertTrue(targets[-1].share_link.endswith('/existing'))

    def test_missing_claim_group_or_valid_share_never_backfills(self):
        target = self.run_row()
        for overrides in [dict(claim_token=None), dict(payload='{}'),
                          dict(share_link="https://ns.chat.360.cn/file.mp4"), dict(share_link=None)]:
            values = dict(share_link=LINK)
            values.update(overrides)
            source = self.run_row(**values)
            self.assertEqual(_backfill_conversation_share(self.db, source), 0)
        self.assertIsNone(target.share_link)

    def test_qwork_latest_full_share_updates_previous_snapshot_without_changing_judgment(self):
        first = self.run_row(target_engine="qwork", session_id="qs1", score=4, verdict="pass",
                             answer="first", status=EvalRunStatus.judged,
                             share_link="https://qwork.360.cn/share/first")
        last = self.run_row(target_engine="qwork", status=EvalRunStatus.running,
                            payload='{"conversation_group":"g1","compare_group":"A","turn_index":1}')
        link = "https://qwork.360.cn/share/all"
        report(last.id, EvalReportIn(status="done", answer="last", session_id="qs1", share_link=link),
               runner="r1", claim_token="claim1", db=self.db, ctx=RunnerCtx())
        self.db.refresh(first)
        self.assertEqual(first.share_link, link)
        self.assertEqual((first.answer, first.score, first.verdict, first.status),
                         ("first", 4, "pass", EvalRunStatus.judged))

    def test_qwork_never_crosses_session_product_execution_or_turn_boundary(self):
        source = self.run_row(target_engine="qwork", session_id="qs1", share_link="https://qwork.360.cn/share/all",
                              payload='{"conversation_group":"g1","compare_group":"A","turn_index":1}')
        for variant in [dict(session_id="qs2"), dict(session_id=None), dict(target_engine="namiwork"),
                        dict(target_engine="workbuddy"), dict(project_id=2), dict(batch_id="b2"),
                        dict(eval_task_id=22), dict(runner="r2"), dict(claim_token="claim2"),
                        dict(status=EvalRunStatus.cancelled), dict(status=EvalRunStatus.running),
                        dict(payload='{"conversation_group":"other","compare_group":"A"}'),
                        dict(payload='{"conversation_group":"g1","compare_group":"B"}'),
                        dict(payload='{"conversation_group":"g1","compare_group":"A","turn_index":2}'),
                        dict(payload='[]')]:
            values = dict(target_engine="qwork", session_id="qs1")
            values.update(variant)
            target = self.run_row(**values)
            self.assertEqual(_backfill_qwork_conversation_share(self.db, source), 0)
            self.assertIsNone(target.share_link)

    def test_qwork_missing_or_invalid_link_does_not_erase_existing_share(self):
        target = self.run_row(target_engine="qwork", session_id="qs1", share_link="https://qwork.360.cn/share/old")
        for link in [None, "https://work.n.cn/share/other", "https://qwork.360.cn.evil/share/other", "https://qwork.360.cn/share/"]:
            source = self.run_row(target_engine="qwork", session_id="qs1", share_link=link,
                                  payload='{"conversation_group":"g1","compare_group":"A","turn_index":1}')
            self.assertEqual(_backfill_qwork_conversation_share(self.db, source), 0)
        self.assertEqual(target.share_link, "https://qwork.360.cn/share/old")


if __name__ == "__main__":
    unittest.main()
