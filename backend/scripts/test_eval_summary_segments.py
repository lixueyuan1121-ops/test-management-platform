"""综合评价有界分段/合并/续跑回归。可控模型，不发送真实材料、不发送通知。"""
import json
import re
import unittest
from unittest.mock import patch

from sqlalchemy import inspect

from app.api import eval_task
from app.core.config import settings
from app.db import migrate
from app.models import EvalRun, EvalTask
from app.models.ai_eval import EvalBatchSummary, EvalSummaryCheckpoint
from app.services.eval_summary_plan import SummaryPlan, InputBudget, Digest, DIGEST_SYSTEM, compact_item, clip
from app.services.eval_summary_store import invalidate_summary, read_progress
from scripts import test_eval_summary_api_retry as fixture
from scripts.test_eval_summary_api_retry import Engine, SUCCESS, HTML, error_event


def item(i, heavy=False):
    return {"run_id": i, "title": f"用例{i}", "dimension": "文件处理", "engine": "WorkBuddy" if i % 2 else "纳米Work",
            "prompt": "绘制图表" * (150 if heavy else 1), "answer": "交付成功" * (300 if heavy else 1),
            "expected": "有输出", "verdict": "pass", "score": 5, "status": "judged",
            "process": {"source": "trace", "thinking_summary": "思考" * 500,
                        "tool_evidence": [f"步骤{j} 正常返回: " + "正常内容" * 170 for j in range(40)]} if heavy else None}


class PlanningTests(unittest.TestCase):
    def test_small_input_uses_one_call_and_preserves_report_requirements(self):
        plan = SummaryPlan("小任务", "说明", [item(1)], {"metrics": {"total": 1}}, InputBudget())
        self.assertIsNotNone(plan.direct)
        self.assertIn("失败根因深挖", plan.direct)
        self.assertIn("[样本1]", plan.direct)
        plan.budget.require(plan.direct, plan.final_system)

    def test_84_heavy_cases_all_appear_once_with_bounded_requests(self):
        items = [item(i, True) for i in range(1, 85)]
        plan = SummaryPlan("大型办公测评", "说明", items, {"metrics": {"total": 84}}, InputBudget())
        self.assertIsNone(plan.direct)
        self.assertGreater(len(plan.chunks), 1)
        prompts = [plan.digest_prompt("\n\n".join(group)) for group in plan.chunks]
        for prompt in prompts:
            plan.budget.require(prompt, DIGEST_SYSTEM)
        ids = re.findall(r"\[样本(\d+)\]", "\n".join(prompts))
        self.assertEqual(sorted(map(int, ids)), list(range(1, 85)))
        print(f"84 heavy cases: {len(prompts)} chunks; largest request {max(map(len, prompts))} chars")

    def test_utf8_budget_is_enforced_independently_of_character_budget(self):
        budget = InputBudget(12000)
        self.assertFalse(budget.fits("😀" * 7000, "系统"))
        value = clip("头" + "😀" * 30000 + "尾", 3000, 4000)
        self.assertLessEqual(len(value), 3000)
        self.assertLessEqual(len(value.encode()), 4000)
        self.assertTrue(value.startswith("头") and value.endswith("尾"))

    def test_single_pathological_item_and_metadata_are_bounded(self):
        sample = item(1, True)
        for key in ("title", "dimension", "engine", "answer", "reason", "prompt", "expected", "verdict_reason"):
            sample[key] = "😀中文" * 15000
        sample["attachments"] = [{"name": "附件", "base64": "x" * 200000}]
        sample["process"]["tool_calls"] = ["工具" * 2000] * 40
        sample["process"]["retry_signal"] = "重试" * 50000
        plan = SummaryPlan("任务" * 50000, "说明" * 50000, [sample], {"metrics": {"total": 1}}, InputBudget(12000))
        if plan.direct:
            plan.budget.require(plan.direct, plan.final_system)
        else:
            for group in plan.chunks:
                plan.budget.require(plan.digest_prompt("\n".join(group)), DIGEST_SYSTEM)

    def test_middle_error_and_recovery_are_kept_and_original_is_unchanged(self):
        sample = item(7, True)
        rows = sample["process"]["tool_evidence"]
        rows[20] = "步骤21 FileNotFoundError /中文目录/input.xlsx"
        rows[21] = "步骤22 修复目录后读取成功 /中文目录/input.xlsx"
        original = json.dumps(sample, ensure_ascii=False)
        result = json.dumps(compact_item(sample, 7), ensure_ascii=False)
        self.assertIn("FileNotFoundError", result)
        self.assertIn("修复目录后读取成功", result)
        self.assertIn("评测系统截断", result)
        self.assertEqual(json.dumps(sample, ensure_ascii=False), original)

    def test_statistics_keep_complete_denominator_and_drop_per_trial_payload(self):
        aggregate = {"metrics": {"total": 10000, "passed": 9998, "judge_errors": 2, "avg_score": 4.3},
                     "trial_metrics": {"tasks": [{"run_ids": list(range(10000)), "prompt": "不应进汇总"}],
                                       "by_engine_variant": [{"engine": "WorkBuddy", "success_rate": 50.0}]},
                     "by_dimension": [{"group": "组" + str(i), "total": 2} for i in range(500)]}
        plan = SummaryPlan("任务", "", [item(1)], aggregate, InputBudget(12000))
        self.assertIn('"total":10000', plan.statistics)
        self.assertIn('"avg_score":4.3', plan.statistics)
        self.assertIn('"success_rate":50.0', plan.statistics)
        self.assertIn("分组明细省略数量", plan.statistics)
        self.assertNotIn("不应进汇总", plan.statistics)
        self.assertNotIn("run_ids", plan.statistics)

    def test_merge_tree_converges_and_keeps_coverage_for_1000_chunks(self):
        plan = SummaryPlan("任务", "", [item(1)], {"metrics": {"total": 1000}}, InputBudget(12000))
        nodes = [Digest(plan.budget.digest("中间证据" * 2000), 1) for _ in range(1000)]
        levels = 0
        while not plan.budget.fits(plan.final_prompt(nodes), plan.final_system):
            groups = plan.merge_groups(nodes)
            self.assertLess(len(groups), len(nodes))
            for group in groups:
                plan.budget.require(plan.digest_prompt(plan.render_digests(group), merge=True), DIGEST_SYSTEM)
            nodes = [Digest(plan.budget.digest("归并证据" * 2000), sum(n.count for n in group)) for group in groups]
            levels += 1
            self.assertLess(levels, 15)
        self.assertEqual(sum(n.count for n in nodes), 1000)
        self.assertGreater(levels, 1)


class SegmentIntegrationTests(unittest.TestCase):
    setUp = fixture.SummaryApiRetryTests.setUp
    generate = fixture.SummaryApiRetryTests.generate

    def seed(self, count=5):
        self.enterContext(patch.object(settings, "EVAL_SUMMARY_CHUNK_MAX_ITEMS", 2))
        self.enterContext(patch.object(settings, "EVAL_SUMMARY_MAX_INPUT_CHARS", 12000))
        with self.sf() as db:
            for i in range(2, count + 1):
                db.add(EvalRun(id=i, project_id=1, eval_task_id=1, batch_id="batch", runner="test",
                    status="judged", payload=json.dumps({"title": f"用例{i}"}), answer="回答", verdict="pass", score=5))
            db.commit()

    def progress(self):
        with self.sf() as db:
            return read_progress(db.query(EvalBatchSummary).one())

    def test_only_failing_segment_retries_then_final_is_saved(self):
        self.seed()
        digest = [{"type": "result", "text": "样本证据：成功交付"}]
        model = Engine(digest, [error_event()], digest, digest, SUCCESS)
        self.assertTrue(self.generate(model).get("ok"))
        self.assertEqual(len(model.calls), 5)
        self.assertEqual(model.calls[1], model.calls[2])
        self.assertNotEqual(model.calls[0][1], model.calls[1][1])
        self.assertEqual(self.progress()["chunk_completed"], 3)
        self.assertEqual(self.progress()["retry_count"], 1)
        with self.sf() as db:
            self.assertEqual(db.query(EvalSummaryCheckpoint).count(), 3)
            self.assertEqual(db.query(EvalBatchSummary).one().summary_html, HTML)
            self.assertTrue(all(r.verdict == "pass" and r.score == 5 for r in db.query(EvalRun).all()))

    def test_failure_then_new_invocation_resumes_completed_segments(self):
        self.seed()
        digest = [{"type": "result", "text": "已完成第一段"}]
        model = Engine(digest, [error_event()])
        self.assertIn("分段分析 2/3", self.generate(model)["error"])
        self.assertEqual(self.progress()["chunk_completed"], 1)
        resumed = Engine(digest, digest, SUCCESS)
        self.assertTrue(self.generate(resumed).get("ok"))
        self.assertEqual(len(resumed.calls), 3)
        self.assertNotIn("[样本1]", resumed.calls[0][1])
        self.assertEqual(self.progress()["reused_chunks"], 1)

    def test_final_error_resume_only_calls_final_model(self):
        self.seed()
        digest = [{"type": "result", "text": "证据摘要"}]
        self.assertIn("生成最终综合评价", self.generate(Engine(digest, digest, digest, [error_event()]))["error"])
        model = Engine(SUCCESS)
        self.assertTrue(self.generate(model).get("ok"))
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(self.progress()["reused_chunks"], 3)

    def test_changed_results_or_model_configuration_invalidate_checkpoints(self):
        self.seed()
        digest = [{"type": "result", "text": "旧证据摘要"}]
        self.assertIn("error", self.generate(Engine(digest, [error_event()])))
        with self.sf() as db:
            db.get(EvalRun, 1).answer = "修改后的证据"
            db.commit()
        model = Engine(digest, [error_event()])
        self.generate(model)
        self.assertIn("修改后的证据", model.calls[0][1])
        self.assertEqual(self.progress()["reused_chunks"], 0)
        with patch.object(settings, "AI_MODEL", "different-model"):
            model = Engine(digest, [error_event()])
            self.generate(model)
            self.assertIn("[样本1]", model.calls[0][1])
            self.assertEqual(self.progress()["reused_chunks"], 0)

    def test_rejudge_during_segment_stops_before_next_call_or_checkpoint(self):
        self.seed()
        parent = self
        class Rejudge(Engine):
            def stream_generate(self, *args, **kwargs):
                yield from super().stream_generate(*args, **kwargs)
                with parent.sf() as db:
                    db.get(EvalRun, 1).verdict_reason = "重新判定结果"
                    invalidate_summary(db, db.get(EvalTask, 1), "batch")
                    db.commit()
        model = Rejudge([{"type": "result", "text": "摘要"}])
        self.assertTrue(self.generate(model).get("skipped"))
        self.assertEqual(len(model.calls), 1)
        with self.sf() as db:
            self.assertEqual(db.query(EvalSummaryCheckpoint).count(), 0)
            self.assertIsNone(db.query(EvalBatchSummary).one().summary_html)

    def test_84_long_cases_hierarchical_flow_all_requests_bounded(self):
        self.seed(84)
        parent = self
        class Hierarchical(Engine):
            def stream_generate(self, requirement, **kwargs):
                prompt, system = kwargs["prompt_builder"](), kwargs["system_prompt"]
                self.calls.append((requirement, prompt, system))
                InputBudget(12000).require(prompt, system)
                parent.assertLessEqual(parent.progress()["input_chars"], 12000)
                yield {"type": "result", "text": "中间证据" * 1500 if system == DIGEST_SYSTEM else HTML}
        model = Hierarchical()
        with patch.object(eval_task, "_summary_items", return_value=[item(i, True) for i in range(1, 85)]):
            self.assertTrue(self.generate(model, auto=True).get("ok"))
        self.assertEqual(self.progress()["chunk_completed"], self.progress()["chunk_total"])
        self.assertGreater(self.progress()["merge_level"], 1)
        self.assertEqual(self.progress()["phase"], "final")
        self.assertIn('"total":84', model.calls[-1][1])
        self.assertIn('"passed":84', model.calls[-1][1])
        self.assertIn('"group":"文件处理"', model.calls[-1][1])
        self.assertIn("用例数:84", model.calls[-1][1])

    def test_empty_segment_never_produces_partial_report(self):
        self.seed()
        model = Engine([])
        self.assertIn("未返回有效证据摘要", self.generate(model)["error"])
        self.assertEqual(len(model.calls), 1)
        with self.sf() as db:
            self.assertEqual(db.query(EvalSummaryCheckpoint).count(), 0)
            self.assertEqual(db.query(EvalBatchSummary).one().summary_status, "failed")

    def test_history_cache_and_current_report_stay_isolated(self):
        self.seed()
        with self.sf() as db:
            task = db.get(EvalTask, 1)
            task.last_batch_id, task.summary_status, task.summary_html = "new", "done", "<p>新批次报告</p>"
            db.commit()
        digest = [{"type": "result", "text": "证据"}]
        self.assertTrue(self.generate(Engine(digest, digest, digest, SUCCESS)).get("ok"))
        with self.sf() as db:
            self.assertEqual(db.get(EvalTask, 1).summary_html, "<p>新批次报告</p>")
            self.assertTrue(all(row.batch_id == "batch" for row in db.query(EvalSummaryCheckpoint).all()))
            invalidate_summary(db, db.get(EvalTask, 1), "batch")
            db.commit()
            self.assertEqual(db.query(EvalSummaryCheckpoint).count(), 0)

    def test_checkpoint_table_migration_is_idempotent(self):
        EvalSummaryCheckpoint.__table__.drop(self.engine)
        with patch.object(migrate, "engine", self.engine):
            migrate.ensure_eval_task_tables()
            migrate.ensure_eval_task_tables()
        self.assertIn("eval_summary_checkpoint", inspect(self.engine).get_table_names())

    def test_duration_uses_legacy_reported_value_without_treating_missing_as_zero(self):
        with self.sf() as db:
            db.get(EvalRun, 1).reported_duration = "2分30秒"
            db.commit()
        model = Engine(SUCCESS)
        self.assertTrue(self.generate(model).get("ok"))
        self.assertIn('"duration_samples":1', model.calls[0][1])
        self.assertIn('"avg_duration_ms":150000', model.calls[0][1])

    def test_changed_unversioned_input_is_rejected_before_final_write(self):
        self.seed()
        parent = self
        class ChangedOutsideApi(Engine):
            def stream_generate(self, *args, **kwargs):
                yield from super().stream_generate(*args, **kwargs)
                with parent.sf() as db:
                    db.get(EvalRun, 1).answer = "变化后的回答"
                    db.commit()
        model = ChangedOutsideApi(SUCCESS)
        self.assertTrue(self.generate(model).get("skipped"))
        with self.sf() as db:
            self.assertEqual(db.query(EvalBatchSummary).one().summary_status, "failed")
            self.assertIsNone(db.query(EvalBatchSummary).one().summary_html)

    def test_intermediate_request_cannot_bypass_budget_when_builder_changes(self):
        self.seed()
        model = Engine(SUCCESS)
        # 即使规划器错误地返回超大分段，调用边界的最后检查也不能把请求发给模型。
        with patch.object(SummaryPlan, "_pack", return_value=[["超长" * 20000]]):
            self.assertIn("超过安全预算", self.generate(model)["error"])
        self.assertEqual(len(model.calls), 0)


if __name__ == "__main__":
    unittest.main()
