"""综合评价状态：入队、刷新恢复、重试、异常、历史批次及增量迁移（离线）。"""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import eval_task
from app.core.config import settings
from app.db import migrate
from app.db.session import Base
from app.models import AiJob, EvalRun, EvalTask, Project, User
from app.models.ai_eval import EvalBatchSummary
from app.services import ai_jobs, eval_pipeline, notify
from app.services.eval_summary_store import invalidate_summary


class SummaryProgressTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.sf = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.sf() as db:
            db.add_all([Project(id=1, code="p", name="P"),
                        User(id=1, username="admin", name="Admin", password_hash="x", is_platform_admin=True),
                        EvalTask(id=1, name="T", project_id=1, last_batch_id="new"),
                        EvalRun(id=1, project_id=1, eval_task_id=1, batch_id="new", runner="r", status="judged", answer="回答"),
                        EvalRun(id=2, project_id=1, eval_task_id=1, batch_id="old", runner="r", status="judged", answer="旧回答")])
            db.commit()
        self.provider = self.enterContext(patch.object(eval_task.generators, "get_provider"))
        self.provider.return_value.is_available.return_value = True
        self.sleep = self.enterContext(patch.object(eval_task.time, "sleep"))
        self.enterContext(patch.object(settings, "EVAL_SUMMARY_API_RETRIES", 1))
        self.enterContext(patch.object(notify, "notify_eval_pipeline"))
        self.enterContext(patch.object(eval_pipeline, "_summary_share_url", return_value=None))

    def submit(self, bid="new"):
        with self.sf() as db:
            return eval_task.summarize_task(1, eval_task.EvalTaskSummarizeIn(batch_id=bid), db, db.get(User, 1))["data"]

    def status(self, bid="new"):
        with self.sf() as db:
            return eval_task.get_summary_status(1, bid, db, db.get(User, 1))["data"]

    def test_enqueue_status_survives_new_session_and_duplicate_submit_reuses_job(self):
        first = self.submit()
        status = self.status()
        self.assertEqual(status["summary_status"], "queued")
        self.assertEqual(status["summary_progress"]["job_id"], first["job_id"])
        self.assertEqual(status["summary_progress"]["stage"], "queued")
        self.assertGreaterEqual(status["summary_progress"]["elapsed_seconds"], 0)
        self.assertNotIn("summary_html", status)
        self.assertTrue(self.submit()["reused"])
        with self.sf() as db:
            self.assertEqual(db.query(AiJob).count(), 1)
            self.assertEqual(eval_task._to_out(db.get(EvalTask, 1), db)["summary_status"], "queued")

    def test_manual_lifecycle_reports_model_wait_content_retry_and_done(self):
        snapshots = []
        calls = 0

        def stream(*args, **kwargs):
            nonlocal calls
            calls += 1
            snapshots.append(self.status())
            yield {"type": "delta", "text": "<p>综合评价</p>"}
            snapshots.append(self.status())
            if calls == 1:
                yield {"type": "error", "msg": "API Error: malformed response"}

        self.provider.return_value.stream_generate.side_effect = stream
        self.sleep.side_effect = lambda _: snapshots.append(self.status())
        job = self.submit()
        ai_jobs._drain_once(self.sf, judge_only=False)
        self.assertEqual([s["summary_progress"]["stage"] for s in snapshots],
                         ["waiting_model", "generating", "retry_wait", "waiting_model", "generating"])
        self.assertTrue(all(s["summary_status"] == "running" for s in snapshots))
        self.assertGreater(snapshots[1]["summary_progress"]["output_chars"], 0)
        self.assertIn("API Error", snapshots[2]["summary_progress"]["last_error"])
        status = self.status()
        self.assertEqual(status["summary_status"], "done")
        self.assertEqual(status["summary_progress"]["retry_count"], 1)
        self.assertTrue(status["summary_share_code"])
        self.assertTrue(status["summary_progress"]["finished_at"])
        with self.sf() as db:
            self.assertEqual(db.get(AiJob, job["job_id"]).status, "done")

    def test_model_failure_is_persistent_with_original_error(self):
        self.provider.return_value.stream_generate.side_effect = lambda *a, **k: iter([
            {"type": "error", "msg": "API Error: malformed response (HTTP 200)"}])
        self.submit()
        ai_jobs._drain_once(self.sf, judge_only=False)
        status = self.status()
        self.assertEqual(status["summary_status"], "failed")
        self.assertIn("HTTP 200", status["summary_progress"]["error"])
        self.assertEqual(status["summary_progress"]["retry_count"], 1)

    def test_failed_or_cancelled_queue_job_does_not_stay_queued(self):
        for terminal in ("failed", "cancelled"):
            with self.subTest(status=terminal):
                job = self.submit()
                with self.sf() as db:
                    row = db.get(AiJob, job["job_id"])
                    row.status, row.error = terminal, "服务已中断本次任务"
                    db.commit()
                status = self.status()
                self.assertEqual(status["summary_status"], "failed")
                self.assertEqual(status["summary_progress"]["error"], "服务已中断本次任务")

    def test_skipped_queued_job_displays_reason(self):
        job = self.submit()
        with self.sf() as db:
            row = db.get(AiJob, job["job_id"])
            row.status, row.result = "done", json.dumps({"skipped": True, "reason": "该批次没有可评结果"})
            db.commit()
        self.assertEqual(self.status()["summary_progress"]["error"], "该批次没有可评结果")

    def test_history_queue_status_is_isolated_from_current_batch(self):
        self.submit("old")
        self.assertEqual(self.status("old")["summary_status"], "queued")
        self.assertIsNone(self.status("new")["summary_status"])
        self.assertEqual(self.status("old")["summary_batch_id"], "old")

    def test_invalidated_queued_job_does_not_generate_or_restore_old_status(self):
        self.submit()
        with self.sf() as db:
            invalidate_summary(db, db.get(EvalTask, 1), "new")
            db.commit()
        ai_jobs._drain_once(self.sf, judge_only=False)
        self.provider.return_value.stream_generate.assert_not_called()
        self.assertIsNone(self.status()["summary_status"])

    def test_restart_preserves_failure_reason_and_pending_job(self):
        self.submit("old")
        with self.sf() as db:
            db.add(EvalBatchSummary(eval_task_id=1, batch_id="new", summary_status="running",
                                   summary_progress='{"stage":"retry_wait","retry_count":1}'))
            db.get(EvalTask, 1).summary_status = "running"
            db.commit()
            eval_pipeline.reap_stale_running_on_startup(db)
        status = self.status()
        self.assertEqual(status["summary_status"], "failed")
        self.assertIn("服务重启", status["summary_progress"]["error"])
        self.assertEqual(self.status("old")["summary_status"], "queued")

    def test_legacy_progress_column_migration_is_idempotent_and_preserves_report(self):
        with self.sf() as db:
            db.add(EvalBatchSummary(eval_task_id=1, batch_id="new", summary_status="done", summary_html="<p>旧报告</p>"))
            db.commit()
        with self.engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_batch_summary DROP COLUMN summary_progress"))
        with patch.object(migrate, "engine", self.engine):
            migrate.ensure_eval_task_tables()
            migrate.ensure_eval_task_tables()
        self.assertIn("summary_progress", {c["name"] for c in inspect(self.engine).get_columns("eval_batch_summary")})
        with self.sf() as db:
            self.assertEqual(db.query(EvalBatchSummary).one().summary_html, "<p>旧报告</p>")
        self.assertEqual(self.status()["summary_status"], "done")


if __name__ == "__main__":
    unittest.main()
