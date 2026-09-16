"""离线并发回归：真实线程/文件数据库，模型用可控替身，不发送需求或会话资料。
运行：cd backend && .venv/bin/python -m scripts.test_eval_judge_concurrency
"""
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.enums import EvalRunStatus
from app.core.config import settings
from app.db.session import Base
from app.models import AiJob, EvalQuery, EvalRun, EvalTask, Project, User
from app.models.ai_eval import EvalJudgment
from app.api import eval_judge as api
from app.services import ai_jobs, eval_judge, eval_judge_queue as queue, eval_pipeline

API_ERROR = "API Error: API returned an empty or malformed response (HTTP 200) — check for a proxy or gateway intercepting the request"


def verdict():
    return {**{key: {"pass": True, "note": "ok"} for key in eval_judge.claude_runner._JUDGE_DIM_KEYS},
            "score": 5, "summary": "ok"}


class Engine:
    def __init__(self, delay=0.1, gate=None, bad_expected=None, gated_expected=None):
        self.delay, self.gate = delay, gate
        self.bad_expected, self.gated_expected = bad_expected, gated_expected
        self.lock = threading.Condition()
        self.active = self.peak = self.calls = 0

    def is_available(self):
        return True

    def stream_generate(self, expected, **kwargs):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.lock.notify_all()
        try:
            if self.gate and (self.gated_expected is None or expected == self.gated_expected):
                if not self.gate.wait(10):
                    raise TimeoutError("test gate not released")
            time.sleep(self.delay)
            if expected == self.bad_expected:
                yield {"type": "error", "msg": "HTTP 400 invalid input"}
            else:
                yield {"type": "result", "text": json.dumps(verdict())}
        finally:
            with self.lock:
                self.active -= 1
                self.lock.notify_all()

    def wait_active(self, count):
        with self.lock:
            return self.lock.wait_for(lambda: self.active >= count, timeout=5)


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.temp.name}/test.db",
            connect_args={"check_same_thread": False, "timeout": 15})
        Base.metadata.create_all(self.engine)
        self.sf = sessionmaker(bind=self.engine)
        ai_jobs._stop.clear()
        with self.sf() as db:
            db.add_all([Project(id=1, code="p", name="P"),
                        User(id=1, username="admin", name="Admin", password_hash="x", is_platform_admin=True),
                        EvalTask(id=1, project_id=1, name="Task", query_ids="[]",
                                 last_batch_id="auto", auto_pipeline=True, pipeline_status="running")])
            db.commit()

    def tearDown(self):
        ai_jobs.stop_pool(timeout=10)
        self.assertFalse(ai_jobs._threads, "test leaked worker threads")
        self.engine.dispose()
        self.temp.cleanup()

    def seed(self, n, batch="manual", status="done"):
        with self.sf() as db:
            ids = []
            for i in range(n):
                q = EvalQuery(project_id=1, title=f"Q{i}", prompt=f"question-{i}", expected=f"expected-{i}")
                db.add(q); db.flush()
                r = EvalRun(project_id=1, eval_query_id=q.id, eval_task_id=1, batch_id=batch,
                            runner="test", status=status, answer=f"answer-{i}")
                db.add(r); db.flush(); ids.append(r.id)
            db.commit()
            return ids

    def enqueue(self, ids):
        with self.sf() as db:
            return api.judge_batch(api.JudgeBatchIn(project_id=1, run_ids=ids), db, db.get(User, 1))["data"]

    def run_auto(self):
        with self.sf() as db:
            return api._run_batch_judge(db, 1, "auto", task_id=1)

    def test_manual_and_auto_share_four_workers_and_general_jobs_keep_running(self):
        manual, auto = self.seed(6), self.seed(6, "auto")
        gate = threading.Event()
        model = Engine(gate=gate)
        batch = self.enqueue(manual)
        with patch.object(eval_judge.generators, "get_provider", return_value=model), \
             patch.dict(ai_jobs._HANDLERS, {"_probe": lambda db, job: {"ok": True}}):
            ai_jobs.start_pool(1, factory=self.sf, judge_size=4)
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(self.run_auto)
                try:
                    self.assertTrue(model.wait_active(4), "four judgments must really overlap")
                    self.assertFalse(future.done())
                    with self.sf() as db:
                        probe = ai_jobs.enqueue(db, "_probe", input={})
                        probe_id = probe.id
                    result = queue.wait_for_batch(self.sf, [probe_id], timeout=4, poll_interval=0.02)
                    self.assertTrue(result[0]["ok"], "judges must not block the general worker")
                    with self.sf() as db:
                        self.assertEqual(db.query(AiJob).filter_by(kind="eval_judge", status="running").count(), 4)
                finally:
                    gate.set()
                self.assertEqual(future.result(timeout=10), len(auto))
            results = queue.wait_for_batch(self.sf, batch["job_ids"], timeout=10, poll_interval=0.02)
        self.assertEqual(model.peak, 4)
        self.assertEqual(model.calls, len(manual) + len(auto))
        self.assertTrue(all(r["verdict"] == "pass" for r in results))
        with self.sf() as db:
            self.assertEqual(db.query(EvalRun).filter_by(status="judged").count(), 12)

    def test_duplicate_manual_and_auto_submission_reuses_jobs(self):
        ids = self.seed(3, "auto")
        barrier = threading.Barrier(2)
        def submit(auto):
            barrier.wait()
            with self.sf() as db:
                return queue.enqueue_batch(db, 1, run_ids=ids, batch_id="auto",
                                           pipeline_task_id=1 if auto else None)
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(submit, auto) for auto in (True, False)]
            batches = [f.result(timeout=5) for f in futures]
        self.assertEqual(batches[0]["job_ids"], batches[1]["job_ids"])
        with self.sf() as db:
            self.assertEqual(db.query(AiJob).count(), 3)

    def test_running_job_is_reused_and_single_api_returns_same_id(self):
        ids = self.seed(1)
        batch = self.enqueue(ids)
        with self.sf() as db:
            db.get(EvalRun, ids[0]).status = EvalRunStatus.judging
            db.get(AiJob, batch["job_ids"][0]).status = "running"
            db.commit()
            response = api.judge_one(ids[0], api.JudgeIn(), db, db.get(User, 1))["data"]
            self.assertEqual(response["job_id"], batch["job_ids"][0])

    def test_explicit_empty_scope_and_execution_failures_do_not_call_model(self):
        self.seed(1)
        failed = self.seed(1, "auto", "failed")
        self.assertEqual(self.enqueue([])["count"], 0)
        with self.sf() as db, patch.object(eval_judge.generators, "get_provider") as provider:
            result = api._batch_judge_results(db, 1, batch_id="auto")
            self.assertEqual(result[0]["run_id"], failed[0])
            self.assertTrue(result[0]["skipped"])
            provider.assert_not_called()

    def test_failed_judgment_does_not_stop_other_results_or_count_as_success(self):
        self.seed(5, "auto")
        model = Engine(bad_expected="expected-2")
        with patch.object(eval_judge.generators, "get_provider", return_value=model):
            ai_jobs.start_pool(1, factory=self.sf, judge_size=4)
            self.assertEqual(self.run_auto(), 4)
        with self.sf() as db:
            self.assertEqual(db.query(EvalRun).filter_by(status="judged", verdict="pass").count(), 4)
            self.assertEqual(db.query(EvalRun).filter_by(status="done", verdict="error").count(), 1)

    def test_pipeline_waits_for_last_result_before_summary(self):
        self.seed(5, "auto")
        gate = threading.Event()
        model = Engine(gate=gate, gated_expected="expected-4")
        summaries = []
        def summary(*args):
            with self.sf() as db:
                summaries.append(db.query(EvalRun).filter_by(status="judged").count())
            return {"ok": True}
        with patch.object(eval_judge.generators, "get_provider", return_value=model), \
             patch("app.services.notify.notify_eval_pipeline"), \
             patch.object(eval_pipeline, "_summary_with_retry", side_effect=summary), \
             patch.object(eval_pipeline, "_summary_share_url", return_value=None), \
             patch.object(eval_pipeline, "auto_issue_for_eval_failures", return_value=[]):
            ai_jobs.start_pool(1, factory=self.sf, judge_size=4)
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(eval_pipeline.run_pipeline, self.sf, 1, 1, "Task", "auto")
                try:
                    deadline = time.monotonic() + 5
                    completed = 0
                    while time.monotonic() < deadline:
                        with self.sf() as db:
                            completed = db.query(EvalRun).filter_by(status="judged").count()
                        if completed == 4:
                            break
                        time.sleep(0.02)
                    self.assertEqual(completed, 4, "completed results must be saved before batch completion")
                    self.assertFalse(future.done())
                    self.assertEqual(summaries, [])
                finally:
                    gate.set()
                future.result(timeout=10)
        self.assertEqual(summaries, [5])

    def test_incomplete_batch_never_generates_summary(self):
        self.seed(1, "auto")
        with patch("app.services.notify.notify_eval_pipeline"), \
             patch.object(queue, "wait_for_batch", side_effect=TimeoutError("still pending")), \
             patch.object(eval_pipeline, "_summary_with_retry") as summary:
            eval_pipeline.run_pipeline(self.sf, 1, 1, "Task", "auto")
            summary.assert_not_called()
        with self.sf() as db:
            self.assertEqual(db.get(EvalTask, 1).pipeline_status, "failed")

    def test_new_batch_skips_old_automatic_jobs_and_stops_wait(self):
        ids = self.seed(1, "auto")
        with self.sf() as db:
            batch = queue.enqueue_batch(db, 1, run_ids=ids, batch_id="auto", pipeline_task_id=1)
            db.get(EvalTask, 1).last_batch_id = "new"
            db.commit()
        with self.assertRaises(queue.BatchSuperseded):
            queue.wait_for_batch(self.sf, batch["job_ids"], pipeline_task_id=1, batch_id="auto")
        with patch.object(eval_judge.generators, "get_provider") as provider:
            ai_jobs._drain_once(self.sf, judge_only=True)
            result = queue.wait_for_batch(self.sf, batch["job_ids"], timeout=1)
            self.assertTrue(result[0]["skipped"])
            provider.assert_not_called()

    def test_queue_position_ignores_other_pool_and_claims_are_partitioned(self):
        ids = self.seed(2)
        with self.sf() as db:
            general = ai_jobs.enqueue(db, "_probe", input={})
            general_id = general.id
        batch = self.enqueue(ids)
        with self.sf() as db:
            self.assertEqual(ai_jobs.queue_position(db, db.get(AiJob, batch["job_ids"][0])), 0)
            self.assertEqual(ai_jobs.queue_position(db, db.get(AiJob, batch["job_ids"][1])), 1)
            self.assertEqual(ai_jobs.claim_next(db, judge_only=False).id, general_id)
            self.assertIsNone(ai_jobs.claim_next(db, judge_only=False))
            self.assertEqual(ai_jobs.claim_next(db, judge_only=True).id, batch["job_ids"][0])

    def test_manual_historical_rejudge_replaces_obsolete_pending_auto_job(self):
        ids = self.seed(1, "auto")
        with self.sf() as db:
            old = queue.enqueue_batch(db, 1, run_ids=ids, batch_id="auto", pipeline_task_id=1)
            db.get(EvalTask, 1).last_batch_id = "new"
            db.commit()
        manual = self.enqueue(ids)
        self.assertNotEqual(old["job_ids"], manual["job_ids"])
        with patch.object(eval_judge.generators, "get_provider", return_value=Engine()):
            ai_jobs._drain_once(self.sf, judge_only=True)
        result = queue.wait_for_batch(self.sf, manual["job_ids"], timeout=1)
        self.assertEqual(result[0]["verdict"], "pass")
        with self.sf() as db:
            self.assertEqual(db.get(AiJob, old["job_ids"][0]).status, "cancelled")

    def test_controlled_throughput_one_vs_four(self):
        timings = []
        for concurrency in (1, 4):
            ids = self.seed(12)
            batch = self.enqueue(ids)
            model = Engine(delay=0.15)
            with patch.object(eval_judge.generators, "get_provider", return_value=model):
                start = time.monotonic()
                ai_jobs.start_pool(1, factory=self.sf, judge_size=concurrency)
                results = queue.wait_for_batch(self.sf, batch["job_ids"], timeout=15, poll_interval=0.01)
                timings.append(time.monotonic() - start)
                ai_jobs.stop_pool(timeout=5)
            self.assertEqual(model.peak, concurrency)
            self.assertEqual(len(results), 12)
            self.assertTrue(all(r["verdict"] == "pass" for r in results))
            ai_jobs._stop.clear()
        print(f"\nControlled model, 12 judgments: serial={timings[0]:.3f}s four={timings[1]:.3f}s speedup={timings[0]/timings[1]:.2f}x")

    def test_three_votes_retry_only_failed_vote_and_preserve_audit(self):
        ids = self.seed(1)
        with self.sf() as db:
            batch = queue.enqueue_batch(db, 1, run_ids=ids, votes=3)
        with patch.object(eval_judge.generators, "get_provider", return_value=Engine()), \
             patch.object(eval_judge, "_judge_once", side_effect=[(verdict(), None),
                 ({"_raw_output": ""}, API_ERROR), (verdict(), None), (verdict(), None)]) as once, \
             patch.object(eval_judge.time, "sleep"):
            ai_jobs._drain_once(self.sf, judge_only=True)
        self.assertEqual(once.call_count, 4)
        with self.sf() as db:
            row = db.get(EvalRun, ids[0])
            self.assertEqual(row.verdict, "pass")
            self.assertEqual(row.judged_by, "claudex3")
            ballots = json.loads(db.get(EvalJudgment, row.judgment_id).ballots)
            self.assertEqual(len(ballots), 3)
            self.assertEqual([b["transient_failures"] for b in ballots], [[], [API_ERROR], []])

    def test_wait_timeout_keeps_completed_results_and_pending_jobs(self):
        batch = self.enqueue(self.seed(2))
        with self.sf() as db:
            job = db.get(AiJob, batch["job_ids"][0])
            job.status, job.result = "done", '{"verdict":"pass"}'
            db.commit()
        with self.assertRaises(TimeoutError):
            queue.wait_for_batch(self.sf, batch["job_ids"], timeout=0.02, poll_interval=0.01)
        with self.sf() as db:
            self.assertEqual(db.get(AiJob, batch["job_ids"][0]).status, "done")
            self.assertEqual(db.get(AiJob, batch["job_ids"][1]).status, "pending")

    def test_starting_pool_twice_cannot_double_concurrency(self):
        ai_jobs.start_pool(1, factory=self.sf, judge_size=4)
        with self.assertRaises(RuntimeError):
            ai_jobs.start_pool(1, factory=self.sf, judge_size=4)
        self.assertEqual(len(ai_jobs._threads), 5)


class RetryTests(unittest.TestCase):
    def test_cli_error_result_retries_without_treating_error_text_as_judgment(self):
        event = eval_judge.claude_runner._parse_line(json.dumps({
            "type": "result", "subtype": "error_during_execution", "is_error": True, "result": API_ERROR}))
        engine = Mock()
        engine.stream_generate.side_effect = [iter([event]),
            iter([{"type": "result", "text": json.dumps(verdict())}])]
        with patch.object(eval_judge.time, "sleep") as sleep:
            dims, err = eval_judge._judge_with_retry(engine, {}, "expected", None)
        self.assertIsNone(err)
        self.assertEqual(dims["score"], 5)
        self.assertEqual(dims["_transient_failures"], [API_ERROR])
        self.assertNotIn(API_ERROR, dims["_raw_output"])
        self.assertEqual(engine.stream_generate.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_cli_synthetic_api_error_is_retried(self):
        event = eval_judge.claude_runner._parse_line(json.dumps({
            "type": "assistant", "isApiErrorMessage": True,
            "message": {"content": [{"type": "text", "text": API_ERROR}]}}))
        engine = Mock()
        engine.stream_generate.side_effect = [iter([event]),
            iter([{"type": "result", "text": json.dumps(verdict())}])]
        with patch.object(eval_judge.time, "sleep"):
            self.assertIsNone(eval_judge._judge_with_retry(engine, {}, "expected", None)[1])
        self.assertEqual(engine.stream_generate.call_count, 2)

    def test_api_error_after_partial_output_retries_with_fresh_buffer(self):
        engine = Mock()
        engine.stream_generate.side_effect = [iter([
            {"type": "delta", "text": '{"incomplete":'},
            {"type": "result", "is_error": True, "text": API_ERROR},
            {"type": "error", "msg": "模型服务返回错误：" + API_ERROR},
        ]), iter([{"type": "result", "text": json.dumps(verdict())}])]
        with patch.object(eval_judge.time, "sleep"):
            dims, err = eval_judge._judge_with_retry(engine, {}, "expected", None)
        self.assertIsNone(err)
        self.assertNotIn("incomplete", dims["_raw_output"])
        self.assertEqual(dims["_transient_failures"], ["模型服务返回错误：" + API_ERROR])
        self.assertEqual(engine.stream_generate.call_count, 2)

    def test_api_error_matching_ignores_case_and_spacing(self):
        for msg in ("api error: HTTP 200", "Api Error: timeout", "APIError: HTTP 401", "API  ERROR: HTTP 400"):
            with self.subTest(msg=msg), patch.object(eval_judge, "_judge_once",
                    side_effect=[({"_raw_output": "partial"}, msg), (verdict(), None)]) as once, \
                 patch.object(eval_judge.time, "sleep"):
                self.assertIsNone(eval_judge._judge_with_retry(None, {}, "", None)[1])
                self.assertEqual(once.call_count, 2)

    def test_api_error_retry_budget_and_disable_are_respected(self):
        for budget in (0, 1, 2):
            with self.subTest(budget=budget), patch.object(settings, "EVAL_JUDGE_TRANSIENT_RETRIES", budget), \
                 patch.object(eval_judge, "_judge_once", side_effect=lambda *_: ({"_raw_output": ""}, API_ERROR)) as once, \
                 patch.object(eval_judge.time, "sleep") as sleep:
                dims, err = eval_judge._judge_with_retry(None, {}, "", None)
                self.assertEqual(err, API_ERROR)
                self.assertEqual(once.call_count, budget + 1)
                self.assertEqual(sleep.call_count, budget)
                self.assertEqual(dims.get("_transient_failures", []), [API_ERROR] * budget)

    def test_api_error_in_successful_judgment_text_does_not_retry(self):
        result = verdict()
        result["summary"] = "正确展示 API Error 提示"
        engine = Mock()
        engine.stream_generate.return_value = iter([{"type": "result", "text": json.dumps(result)}])
        with patch.object(eval_judge.time, "sleep") as sleep:
            dims, err = eval_judge._judge_with_retry(engine, {}, "expected", None)
        self.assertIsNone(err)
        self.assertEqual(dims["score"], 5)
        self.assertEqual(engine.stream_generate.call_count, 1)
        sleep.assert_not_called()

    def test_transient_without_output_retries_only_current_ballot(self):
        with patch.object(eval_judge, "_judge_once", side_effect=[
            ({"_raw_output": ""}, "HTTP 429 rate limit"), (verdict(), None)]
        ) as once, patch.object(eval_judge.time, "sleep") as sleep:
            dims, err = eval_judge._judge_with_retry(None, {}, "", None)
        self.assertIsNone(err)
        self.assertEqual(once.call_count, 2)
        self.assertEqual(dims["_transient_failures"], ["HTTP 429 rate limit"])
        self.assertGreaterEqual(sleep.call_args.args[0], 5)

    def test_nonretryable_and_partial_output_are_not_replayed(self):
        for raw, error in [("", "生成超时 >900s"), ("", "HTTP 400"),
                           ("", "HTTP 401"), ("{half", "HTTP 503"),
                           ("", "DeepSeek HTTP 429 已重试多次"), ("bad", "判定输出无法解析")]:
            with self.subTest(error=error), patch.object(eval_judge, "_judge_once",
                    return_value=({"_raw_output": raw}, error)) as once:
                self.assertEqual(eval_judge._judge_with_retry(None, {}, "", None)[1], error)
                self.assertEqual(once.call_count, 1)

    def test_retry_budget_is_bounded(self):
        with patch.object(eval_judge, "_judge_once", return_value=({"_raw_output": ""}, "HTTP 503")) as once, \
             patch.object(eval_judge.time, "sleep"):
            self.assertEqual(eval_judge._judge_with_retry(None, {}, "", None)[1], "HTTP 503")
        self.assertEqual(once.call_count, 2)


if __name__ == "__main__":
    unittest.main()
