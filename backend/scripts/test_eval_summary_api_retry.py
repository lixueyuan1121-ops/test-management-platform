"""综合评价 API Error 离线回归：真实队列 handler/批次存储，可控模型事件。

cd backend && NAMI_DEPLOY_ENABLED=false .venv/bin/python -m scripts.test_eval_summary_api_retry
"""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import eval_task
from app.core.config import settings
from app.core.enums import EvalRunStatus
from app.db.session import Base
from app.models import AiJob, EvalRun, EvalTask, Project
from app.models.ai_eval import EvalBatchSummary
from app.services import ai_jobs, eval_pipeline, notify

API_ERROR = "API Error: API returned an empty or malformed response (HTTP 200) — check for a proxy or gateway intercepting the request"
HTML = "<h2>综合评价</h2><p>成功报告</p>"
SUCCESS = [{"type": "result", "text": HTML}]


def error_event(message=API_ERROR):
    return {"type": "error", "msg": message}


class Engine:
    def __init__(self, *attempts):
        self.attempts = attempts
        self.calls = []
        self.closed = 0

    def is_available(self):
        return True

    def stream_generate(self, requirement, **kwargs):
        events = self.attempts[min(len(self.calls), len(self.attempts) - 1)]
        self.calls.append((requirement, kwargs["prompt_builder"](), kwargs["system_prompt"]))
        try:
            for event in events:
                if isinstance(event, Exception):
                    raise event
                yield event
        finally:
            self.closed += 1


class SummaryApiRetryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.sf = sessionmaker(bind=self.engine)
        with self.sf() as db:
            db.add_all([
                Project(id=1, code="p", name="P"),
                EvalTask(id=1, project_id=1, name="评价任务", last_batch_id="batch"),
                EvalRun(id=1, project_id=1, eval_task_id=1, batch_id="batch", runner="test",
                        status=EvalRunStatus.judged, payload="{}", answer="回答", verdict="pass", score=5),
            ])
            db.commit()
        self.addCleanup(self.engine.dispose)
        self.sleep = self.enterContext(patch.object(eval_task.time, "sleep"))
        self.enterContext(patch.object(eval_task.random, "uniform", return_value=0.25))
        self.enterContext(patch.object(settings, "EVAL_SUMMARY_API_RETRIES", 1))
        self.provider = self.enterContext(patch.object(eval_task.generators, "get_provider"))
        self.notification = self.enterContext(patch.object(notify, "notify_eval_pipeline"))
        self.share_url = self.enterContext(patch.object(eval_pipeline, "_summary_share_url", return_value=None))

    def generate(self, model, *, auto=False):
        self.provider.return_value = model
        if auto:
            return eval_pipeline._summary_with_retry(self.sf, 1, "batch")
        with self.sf() as db:
            return eval_task.generate_task_summary_headless(db, db.get(EvalTask, 1), "batch",
                                                            session_factory=self.sf)

    def assert_status(self, status, html=None):
        with self.sf() as db:
            task = db.get(EvalTask, 1)
            row = db.query(EvalBatchSummary).one()
            self.assertEqual((task.summary_status, row.summary_status), (status, status))
            self.assertEqual((task.summary_html, row.summary_html), (html, html))
            if status == "done":
                self.assertTrue(row.summary_share_code)
                self.assertEqual(task.summary_share_code, row.summary_share_code)
            # 综合评价重试不得重新判定/修改已判结果。
            run = db.get(EvalRun, 1)
            self.assertEqual((run.status, run.verdict, run.score), (EvalRunStatus.judged, "pass", 5))

    def test_error_event_recovers_with_same_input_and_releases_stream_before_sleep(self):
        model = Engine([error_event()], SUCCESS)

        def during_backoff(delay):
            self.assertEqual(delay, 5.25)
            self.assertEqual(model.closed, 1)
            self.assert_status("running")

        self.sleep.side_effect = during_backoff
        result = self.generate(model)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(model.calls[0], model.calls[1])
        self.assertEqual(model.closed, 2)
        self.assert_status("done", HTML)

    def test_error_result_uses_error_field_without_waiting_for_followup_event(self):
        model = Engine([{"type": "result", "is_error": True, "error": API_ERROR, "text": "<p>错误正文</p>"}], SUCCESS)
        self.assertTrue(self.generate(model).get("ok"))
        self.assertEqual(len(model.calls), 2)
        self.assert_status("done", HTML)

    def test_error_result_falls_back_to_text(self):
        model = Engine([{"type": "result", "is_error": True, "text": API_ERROR}], SUCCESS)
        self.assertTrue(self.generate(model).get("ok"))
        self.assertEqual(len(model.calls), 2)

    def test_stream_exception_api_error_retries(self):
        model = Engine([RuntimeError(API_ERROR)], SUCCESS)
        self.assertTrue(self.generate(model).get("ok"))
        self.assertEqual(len(model.calls), 2)

    def test_error_matching_ignores_case_and_spacing(self):
        for message in ("api error: bad", "APIError: bad", "Api  Error: bad", "模型服务返回错误：API Error: timeout"):
            with self.subTest(message=message):
                model = Engine([error_event(message)], SUCCESS)
                self.assertTrue(self.generate(model).get("ok"))
                self.assertEqual(len(model.calls), 2)

    def test_partial_output_is_discarded_before_retry(self):
        model = Engine([{"type": "delta", "text": "<p>未完成的旧正文</p>"}, error_event()],
                       [{"type": "delta", "text": HTML}])
        self.assertTrue(self.generate(model).get("ok"))
        self.assert_status("done", HTML)

    def test_report_mentioning_api_error_is_not_an_error(self):
        html = "<p>本次执行遇到 API Error，已记录失败原因。</p>"
        model = Engine([{"type": "result", "text": html}])
        self.assertTrue(self.generate(model).get("ok"))
        self.assertEqual(len(model.calls), 1)
        self.sleep.assert_not_called()
        self.assert_status("done", html)

    def test_persistent_api_error_including_busy_is_not_retried_by_outer_pipeline(self):
        model = Engine([error_event(API_ERROR + "；服务繁忙，已达并发上限")])
        result = self.generate(model, auto=True)
        self.assertIn(API_ERROR, result["error"])
        self.assertTrue(result["api_error"])
        self.assertEqual(len(model.calls), 2)
        self.sleep.assert_called_once()
        self.assert_status("failed")

    def test_api_error_then_busy_does_not_multiply_retry_budget(self):
        model = Engine([error_event()], [error_event("AI 生成繁忙（已达并发上限）")])
        result = self.generate(model, auto=True)
        self.assertIn("繁忙", result["error"])
        self.assertEqual(len(model.calls), 2)
        self.sleep.assert_called_once()

    def test_retry_configuration_can_disable_or_cap_retries(self):
        for configured, expected in ((0, 1), (-1, 1), (2, 3), (99, 3)):
            with self.subTest(configured=configured), patch.object(settings, "EVAL_SUMMARY_API_RETRIES", configured):
                self.sleep.reset_mock()
                model = Engine([error_event()])
                self.assertIn("error", self.generate(model, auto=True))
                self.assertEqual(len(model.calls), expected)
                self.assertEqual(self.sleep.call_count, expected - 1)
                if expected == 3:
                    self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [5.25, 10.25])
                self.assert_status("failed")

    def test_non_api_errors_do_not_retry(self):
        for message in ("生成超时（>900s）", "HTTP 401 unauthorized", "HTTP 400 invalid input", "HTTP 503 unavailable"):
            with self.subTest(message=message):
                model = Engine([error_event(message)], SUCCESS)
                self.assertEqual(self.generate(model, auto=True)["error"], message)
                self.assertEqual(len(model.calls), 1)
                self.assert_status("failed")
        self.sleep.assert_not_called()

    def test_non_api_exception_does_not_retry(self):
        model = Engine([RuntimeError("stream boom")], SUCCESS)
        self.assertIn("stream boom", self.generate(model)["error"])
        self.assertEqual(len(model.calls), 1)
        self.assert_status("failed")

    def test_non_api_error_result_cannot_be_saved_as_report(self):
        model = Engine([{"type": "result", "is_error": True, "text": "<p>生成超时</p>"}], SUCCESS)
        self.assertIn("error", self.generate(model))
        self.assertEqual(len(model.calls), 1)
        self.assert_status("failed")

    def test_empty_output_does_not_retry(self):
        model = Engine([], SUCCESS)
        self.assertIn("error", self.generate(model))
        self.assertEqual(len(model.calls), 1)
        self.assert_status("failed")

    def enqueue_manual(self, model):
        self.provider.return_value = model
        with self.sf() as db:
            job = ai_jobs.enqueue(db, "eval_summary", project_id=1, ref_kind="eval_task", ref_id=1,
                                  input={"task_id": 1, "batch_id": "batch", "provider": "claude"})
            return job.id

    def test_manual_queue_job_stays_running_then_saves_once_and_notifies_once(self):
        model = Engine([error_event()], SUCCESS)
        job_id = self.enqueue_manual(model)

        def during_backoff(delay):
            with self.sf() as db:
                self.assertEqual(db.get(AiJob, job_id).status, "running")
            self.assert_status("running")
            self.notification.assert_not_called()

        self.sleep.side_effect = during_backoff
        self.assertTrue(ai_jobs._drain_once(self.sf, judge_only=False))
        with self.sf() as db:
            job = db.get(AiJob, job_id)
            self.assertEqual(job.status, "done")
            self.assertTrue(json.loads(job.result)["ok"])
            self.assertEqual(db.query(AiJob).count(), 1)
        self.assert_status("done", HTML)
        self.notification.assert_called_once()
        self.share_url.assert_called_once()

    def test_manual_queue_job_exhaustion_is_failed_without_success_notification(self):
        model = Engine([error_event()])
        job_id = self.enqueue_manual(model)
        self.assertTrue(ai_jobs._drain_once(self.sf, judge_only=False))
        with self.sf() as db:
            job = db.get(AiJob, job_id)
            self.assertEqual(job.status, "failed")
            self.assertIn(API_ERROR, job.error)
        self.assertEqual(len(model.calls), 2)
        self.assert_status("failed")
        self.notification.assert_not_called()

    def test_auto_pipeline_summary_recovers(self):
        model = Engine([error_event()], SUCCESS)
        self.assertTrue(self.generate(model, auto=True).get("ok"))
        self.assertEqual(len(model.calls), 2)
        self.assert_status("done", HTML)

    def test_new_generation_during_backoff_is_not_overwritten(self):
        def replace_generation(delay):
            with self.sf() as db:
                row = db.query(EvalBatchSummary).one()
                row.generation_token = "new-generation"
                row.summary_html = "<p>新请求的评价</p>"
                row.summary_status = "done"
                db.commit()

        self.sleep.side_effect = replace_generation
        model = Engine([error_event()], SUCCESS)
        self.assertTrue(self.generate(model).get("skipped"))
        self.assertEqual(len(model.calls), 1)
        with self.sf() as db:
            row = db.query(EvalBatchSummary).one()
            self.assertEqual((row.generation_token, row.summary_status, row.summary_html),
                             ("new-generation", "done", "<p>新请求的评价</p>"))

    def test_changed_results_during_backoff_stop_old_summary(self):
        def rejudge(delay):
            with self.sf() as db:
                db.get(EvalRun, 1).verdict_reason = "新判定原因"
                db.commit()

        self.sleep.side_effect = rejudge
        model = Engine([error_event()], SUCCESS)
        result = self.generate(model)
        self.assertTrue(result.get("skipped"), result)
        self.assertEqual(len(model.calls), 1)
        self.assert_status("failed")

    def test_historical_batch_retry_does_not_change_current_summary(self):
        with self.sf() as db:
            task = db.get(EvalTask, 1)
            task.last_batch_id = "new-batch"
            task.summary_status, task.summary_html = "done", "<p>当前批次报告</p>"
            db.commit()
        model = Engine([error_event()], SUCCESS)
        self.assertTrue(self.generate(model).get("ok"))
        with self.sf() as db:
            task = db.get(EvalTask, 1)
            self.assertEqual(task.summary_html, "<p>当前批次报告</p>")
            self.assertEqual(task.summary_status, "done")
            self.assertEqual(db.query(EvalBatchSummary).one().summary_html, HTML)

    def test_post_processing_error_never_replays_model(self):
        model = Engine(SUCCESS)
        with patch.object(eval_task, "_sanitize_html", side_effect=RuntimeError(API_ERROR)):
            self.assertIn("error", self.generate(model))
        self.assertEqual(len(model.calls), 1)
        self.sleep.assert_not_called()
        self.assert_status("failed")


if __name__ == "__main__":
    unittest.main()
