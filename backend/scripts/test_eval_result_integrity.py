"""结果可信度回归：快照、重试归档、批次报告和计划派发。无外部服务调用。

cd backend && .venv/bin/python -m scripts.test_eval_result_integrity
"""
import asyncio
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import eval_queue, eval_task, test_plan
from app.api.eval_report import render_report_page, view_shared_report
from app.core.deps import RunnerCtx
from app.core.enums import EvalRunStatus, EvalTaskStatus
from app.db.session import Base
from app.models import EvalQuery, EvalRun, ExecRun, Project, RunnerDevice, TestCase, TestPlan, User
from app.models.ai_eval import EvalBatchSummary, EvalRunHistory, EvalTask
from app.schemas.eval_queue import EvalRetryFailedIn
from app.services.eval_snapshot import rubric_of
from app.services.eval_summary_store import batch_summary, summary_view
from app.services import eval_pipeline, eval_judge


class ResultIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.db = self.sessions()
        self.user = User(id=1, username="review", name="Review", password_hash="x", is_platform_admin=True)
        self.task = EvalTask(id=1, project_id=1, name="结果回归", last_batch_id="new", auto_pipeline=True,
                             status=EvalTaskStatus.done, pipeline_status="done")
        self.query = EvalQuery(id=1, project_id=1, title="原题目", prompt="原提问", expected="原基准", dimension="thinking")
        self.db.add_all([self.user, Project(id=1, name="P", code="P"), self.task, self.query])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def run_row(self, batch="new", status=EvalRunStatus.failed, **kwargs):
        payload = kwargs.pop("payload", json.dumps(eval_queue._payload_of(self.query), ensure_ascii=False))
        row = EvalRun(project_id=1, eval_task_id=1, eval_query_id=1, batch_id=batch,
                      runner="r1", target_engine="namiwork", status=status, payload=payload, **kwargs)
        self.db.add(row)
        self.db.commit()
        return row

    def generate(self, bid="new", callback=None):
        class Engine:
            def is_available(self):
                return True

            def stream_generate(self, *args, **kwargs):
                if callback:
                    callback()
                yield {"type": "result", "text": f"<p>{bid} 的评价</p>"}

        with patch.object(eval_task.generators, "get_provider", return_value=Engine()):
            return eval_task.generate_task_summary_headless(self.db, self.task, bid, session_factory=self.sessions)

    def test_expected_and_dimension_survive_query_edit_and_delete(self):
        row = self.run_row(status=EvalRunStatus.done, answer="回答")
        self.query.expected, self.query.dimension = "新基准", "tool_use"
        self.db.commit()
        self.assertEqual(rubric_of(self.db, row), ("原基准", "thinking"))
        items = eval_task._summary_items(self.db, [row])
        self.assertEqual((items[0]["expected"], items[0]["dimension"]), ("原基准", "thinking"))
        self.db.delete(self.query)
        self.db.commit()
        self.assertEqual(rubric_of(self.db, row), ("原基准", "thinking"))

    def test_snapshot_empty_is_intentional_and_legacy_falls_back(self):
        row = self.run_row(payload='{"expected": null, "dimension": null}')
        self.assertEqual(rubric_of(self.db, row), ("", None))
        self.assertEqual(eval_task._summary_items(self.db, [row])[0]["expected"], "")
        row.payload = "{}"
        self.assertEqual(rubric_of(self.db, row), ("原基准", "thinking"))

    def test_judge_passes_snapshot_to_engine(self):
        row = self.run_row(status=EvalRunStatus.done, answer="回答")
        self.query.expected = "修改后的题库"
        self.db.commit()
        with patch.object(eval_judge.generators, "get_provider") as engine, \
                patch.object(eval_judge, "_judge_once", return_value=(None, "测试终止")) as judge:
            engine.return_value.is_available.return_value = True
            eval_judge.judge_run(self.db, row)
            self.assertEqual(judge.call_args.args[2:], ("原基准", "thinking"))

    def test_judged_first_turn_and_failed_second_retry_together_with_evidence(self):
        payload = json.dumps({"conversation_group": "g"})
        first = self.run_row(status=EvalRunStatus.judged, payload=payload, answer="首轮", verdict="pass", score=5)
        second = self.run_row(payload=payload, answer="失败回答", raw_message='{"original":1}',
                              review_mark="false_positive", review_note="人工备注", trace="/uploads/eval_traces/old.json")
        self.task.summary_status, self.task.summary_html = "done", "<p>过期评价</p>"
        self.db.commit()
        self.assertEqual(len(eval_queue.reset_conversation_for_retry(self.db, second)), 2)
        self.db.commit()
        self.assertTrue(all(r.status == EvalRunStatus.pending for r in (first, second)))
        self.assertIsNone(first.score)
        self.assertIsNone(second.raw_message)
        history = self.db.query(EvalRunHistory).order_by(EvalRunHistory.eval_run_id).all()
        self.assertEqual([(h.attempt, h.status, h.answer) for h in history], [(1, "judged", "首轮"), (1, "failed", "失败回答")])
        self.assertEqual((history[1].raw_message, history[1].review_note), ('{"original":1}', "人工备注"))
        self.assertEqual(self.task.status, EvalTaskStatus.running)
        self.assertIsNone(self.task.pipeline_status)
        self.assertIsNone(eval_task._to_out(self.task, self.db)["summary_html"])
        first.status, second.status = EvalRunStatus.done, EvalRunStatus.done
        self.db.commit()
        with patch.object(eval_pipeline, "_spawn_pipeline") as spawn:
            self.assertTrue(eval_pipeline.on_batch_maybe_done(self.db, "new"))
            self.assertFalse(eval_pipeline.on_batch_maybe_done(self.db, "new"))
            spawn.assert_called_once()

    def test_batch_retry_does_not_archive_same_group_twice(self):
        first = self.run_row(payload='{"conversation_group":"g"}')
        second = self.run_row(payload='{"conversation_group":"g"}')
        result = eval_queue.retry_failed_batch(EvalRetryFailedIn(project_id=1, batch_id="new"), self.db, self.user)
        self.assertEqual(result["data"]["retried"], 2)
        self.assertEqual(self.db.query(EvalRunHistory).count(), 2)
        second.status = EvalRunStatus.failed
        self.db.commit()
        eval_queue.retry_run_any(second.id, self.db, self.user)
        self.assertEqual([h.attempt for h in self.db.query(EvalRunHistory).filter_by(eval_run_id=second.id).all()], [1, 2])
        self.assertEqual(self.db.query(EvalRunHistory).filter_by(eval_run_id=first.id).count(), 1)

    def test_historical_retry_does_not_reset_current_pipeline(self):
        row = self.run_row(batch="old")
        self.task.pipeline_status = "running"
        self.task.summary_html, self.task.summary_status = "当前评价", "done"
        self.db.commit()
        eval_queue.retry_run_any(row.id, self.db, self.user)
        self.assertEqual(self.task.pipeline_status, "running")
        self.assertEqual(self.task.summary_html, "当前评价")

    def test_queued_judge_cannot_finish_a_retried_run(self):
        row = self.run_row(answer="旧回答")
        eval_queue.retry_run_any(row.id, self.db, self.user)
        with patch.object(eval_judge.generators, "get_provider") as provider:
            self.assertTrue(eval_judge.judge_run(self.db, row).get("skipped"))
            provider.assert_not_called()
        self.assertEqual(row.status, EvalRunStatus.pending)
        self.assertIsNone(row.verdict)

    def test_pipeline_judge_preserves_execution_failure_for_retry(self):
        from app.api.eval_judge import _batch_judge_results
        row = self.run_row(answer="部分回答", reason="执行中断")
        with patch.object(eval_judge.generators, "get_provider") as provider:
            result = _batch_judge_results(self.db, 1, batch_id="new")
            self.assertTrue(result[0].get("skipped"))
            provider.assert_not_called()
        self.assertEqual(row.status, EvalRunStatus.failed)
        self.assertEqual(row.reason, "执行中断")

    def test_dispatch_preserves_previous_report_and_clears_current_cache(self):
        self.task.query_ids = "[1]"
        self.task.summary_status, self.task.summary_html, self.task.summary_share_code = "done", "旧评价", "legacy"
        self.run_row(status=EvalRunStatus.done)
        ids, bid = eval_task.dispatch_task_runs(self.db, self.task, ["r1"], ["namiwork"], None, {}, None, 1)
        self.db.commit()
        self.assertTrue(ids)
        self.assertEqual(self.task.last_batch_id, bid)
        self.assertIsNone(self.task.summary_html)
        self.assertIsNone(self.task.summary_share_code)
        self.assertEqual(summary_view(self.db, self.task, "new").summary_html, "旧评价")

    def test_running_pipeline_or_judge_rejects_retry_without_erasing_results(self):
        row = self.run_row(answer="保留")
        self.task.pipeline_status = "running"
        self.db.commit()
        with self.assertRaises(HTTPException):
            eval_queue.reset_conversation_for_retry(self.db, row)
        self.assertEqual(row.answer, "保留")
        self.assertEqual(self.db.query(EvalRunHistory).count(), 0)
        self.task.pipeline_status = "done"
        row.payload = '{"conversation_group":"g"}'
        self.run_row(status=EvalRunStatus.judging, payload=row.payload)
        with self.assertRaises(HTTPException):
            eval_queue.reset_conversation_for_retry(self.db, row)

    def test_trace_upload_preserves_archived_file(self):
        with tempfile.TemporaryDirectory() as directory:
            trace_root = Path(directory) / "eval_traces"
            trace_root.mkdir()
            row = self.run_row()
            old = trace_root / f"{row.id}-old.json"
            old.write_text('{"answer":"旧证据"}')
            row.trace = f"/uploads/eval_traces/{old.name}"
            self.db.commit()
            eval_queue.retry_run_any(row.id, self.db, self.user)
            row.status, row.claim_token = EvalRunStatus.running, "new-token"
            self.db.commit()
            unreferenced = trace_root / f"{row.id}-orphan.json"
            unreferenced.write_text('{}')
            with patch.object(eval_queue, "_TRACE_ROOT", str(trace_root)), patch.object(eval_queue, "_UPLOADS_DIR", directory):
                result = asyncio.run(eval_queue.upload_trace(row.id, UploadFile(file=io.BytesIO(b'{"answer":"new"}')),
                                     runner="r1", claim_token="new-token", db=self.db, ctx=RunnerCtx(device=None)))
            self.assertTrue(old.exists())
            self.assertFalse(unreferenced.exists())
            self.assertNotEqual(result["data"]["trace_url"], f"/uploads/eval_traces/{old.name}")
            attempts = eval_queue.run_attempts(row.id, self.db, self.user)["data"]
            self.assertEqual(attempts[0]["trace"], f"/uploads/eval_traces/{old.name}")

    def test_reading_old_batch_keeps_current_running(self):
        self.run_row(batch="old", status=EvalRunStatus.done)
        self.run_row(status=EvalRunStatus.running)
        self.task.status = EvalTaskStatus.running
        self.db.commit()
        eval_task.task_runs(1, "old", self.db, self.user)
        self.assertEqual(self.task.status, EvalTaskStatus.running)

    def test_old_summary_and_share_link_only_include_old_details(self):
        self.run_row(batch="old", status=EvalRunStatus.judged, payload='{"title":"旧批次用例"}', score=2)
        self.run_row(status=EvalRunStatus.judged, payload='{"title":"新批次用例"}', score=5)
        current = self.generate()
        old = self.generate("old")
        self.assertTrue(current.get("ok"), current)
        self.assertTrue(old.get("ok"), old)
        self.assertNotEqual(current["share_code"], old["share_code"])
        self.db.expire_all()
        self.assertEqual(self.task.summary_html, "<p>new 的评价</p>")
        old_out = eval_task.task_runs(1, "old", self.db, self.user)["data"]
        self.assertEqual(old_out["task"]["summary_html"], "<p>old 的评价</p>")
        with patch("app.db.session.SessionLocal", self.sessions):
            html = view_shared_report(old["share_code"]).body.decode()
        self.assertIn("旧批次用例", html)
        self.assertNotIn("新批次用例", html)

    def test_delayed_old_summary_cannot_overwrite_new_batch(self):
        self.run_row(status=EvalRunStatus.done)
        def dispatch_new():
            with self.sessions() as db:
                task = db.get(EvalTask, 1)
                task.last_batch_id, task.summary_html, task.summary_status = "newer", "更新批次评价", "done"
                db.commit()
        result = self.generate(callback=dispatch_new)
        self.assertTrue(result.get("ok"), result)
        self.db.expire_all()
        self.assertEqual(self.task.summary_html, "更新批次评价")
        self.assertEqual(summary_view(self.db, self.task, "new").summary_html, "<p>new 的评价</p>")

    def test_retry_invalidates_inflight_summary(self):
        row = self.run_row(answer="失败内容")
        def retry():
            with self.sessions() as db:
                eval_queue.retry_run_any(row.id, db, db.get(User, 1))
        result = self.generate(callback=retry)
        self.assertTrue(result.get("skipped"), result)
        self.db.expire_all()
        self.assertIsNone(self.task.summary_html)
        self.assertEqual(row.status, EvalRunStatus.pending)

    def test_verdict_change_during_summary_rejects_stale_report(self):
        row = self.run_row(status=EvalRunStatus.done)
        def rejudge():
            with self.sessions() as db:
                db.get(EvalRun, row.id).score = 5
                db.commit()
        self.assertTrue(self.generate(callback=rejudge).get("skipped"))
        self.db.expire_all()
        self.assertEqual(summary_view(self.db, self.task).summary_status, "failed")

    def test_old_pipeline_cleanup_does_not_fail_new_pipeline(self):
        self.task.pipeline_status, self.task.summary_status = "running", "running"
        self.db.commit()
        eval_pipeline._reconcile_stuck_status(self.sessions, 1, "old")
        self.db.expire_all()
        self.assertEqual((self.task.pipeline_status, self.task.summary_status), ("running", "running"))

    def test_pipeline_claim_rechecks_completion_after_concurrent_retry(self):
        row = self.run_row(status=EvalRunStatus.done)
        self.task.pipeline_status = None
        self.db.commit()
        def completion_check(*args):
            row.status = EvalRunStatus.pending
            self.db.commit()
            return True  # 模拟检查完成后、抢占前发生重试
        with patch.object(eval_pipeline, "_batch_all_settled", side_effect=completion_check), \
                patch.object(eval_pipeline, "_spawn_pipeline") as spawn:
            self.assertFalse(eval_pipeline.on_batch_maybe_done(self.db, "new"))
            spawn.assert_not_called()

    def test_legacy_summary_and_startup_recovery(self):
        self.task.summary_status, self.task.summary_html, self.task.summary_share_code = "done", "旧系统评价", "legacy"
        self.db.commit()
        row = batch_summary(self.db, self.task, "new", create=True)
        self.assertEqual((row.summary_html, row.summary_share_code), ("旧系统评价", "legacy"))
        row.summary_status = "running"
        self.db.commit()
        eval_pipeline.reap_stale_running_on_startup(self.db)
        self.db.refresh(row)
        self.assertEqual(row.summary_status, "failed")

    def test_history_migration_adds_raw_message_idempotently(self):
        from app.db.migrate import ensure_eval_run_history_table
        with self.engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_run_history DROP COLUMN raw_message"))
        ensure_eval_run_history_table(self.engine)
        ensure_eval_run_history_table(self.engine)
        self.assertIn("raw_message", {c["name"] for c in inspect(self.engine).get_columns("eval_run_history")})

    def test_plan_checks_live_eval_capability_and_binds_auto_device(self):
        device = RunnerDevice(owner_id=1, runner_id="r1", name="设备", token="test-only",
                              last_seen_at=datetime.now(), last_eval_at=datetime.now())
        case = TestCase(ai_task_id=1, project_id=1, title="功能测试", exec_kind="gui", platform="web")
        plan = TestPlan(project_id=1, name="计划", created_by=1)
        self.db.add_all([device, case, plan])
        self.db.commit()
        with self.assertRaises(HTTPException) as failure:
            test_plan._dispatch_plan(self.db, plan, [case.id], "r1", "manual", 1)
        self.assertEqual(failure.exception.status_code, 400)
        self.assertEqual(self.db.query(ExecRun).count(), 0)
        device.last_eval_at, device.last_exec_at = None, datetime.now()
        self.db.commit()
        result = test_plan._dispatch_plan(self.db, plan, [case.id], "auto", "schedule", None)
        run = self.db.get(ExecRun, result["run_ids"][0])
        self.assertEqual((run.runner, run.runner_device_id, run.auto_reassign), ("r1", device.id, True))


if __name__ == "__main__":
    unittest.main()
