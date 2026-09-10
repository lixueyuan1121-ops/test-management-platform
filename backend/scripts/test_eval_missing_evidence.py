"""离线回归：空轨迹不遮蔽回答，证据不足不误判失败。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

from app.services import eval_judge as judge, claude_runner as cr


def dims(value=True):
    return {**{k: {"pass": value, "note": "test"} for k in cr._JUDGE_DIM_KEYS},
            "summary": "test", "score": 1 if value is False else 5}


class MissingEvidenceTests(unittest.TestCase):
    def run_record(self, answer=""):
        return SimpleNamespace(id=589, eval_query_id=None, trace="/uploads/eval_traces/test.json",
                               answer=answer, status="judged", score=1, verdict="fail",
                               verdict_dims="old", is_abnormal=True)

    def test_existing_empty_trace_uses_answer(self):
        run = self.run_record("视频压缩完成，954077 字节，下载链接")
        with patch("builtins.open", mock_open(read_data='{"answer":" ","tool_calls":[]}')):
            trace = judge._load_trace(run)
        self.assertEqual(trace["answer"], run.answer)
        self.assertEqual(trace["answer_source"], "run.answer")
        self.assertIn(run.answer, cr.build_eval_judge_prompt(trace, "压缩视频"))

    def test_real_trace_answer_preserved(self):
        with patch("builtins.open", mock_open(read_data='{"answer":"full trace answer"}')):
            self.assertEqual(judge._load_trace(self.run_record("short"))["answer"], "full trace answer")

    def test_missing_invalid_and_unsafe_files_fall_back(self):
        run = self.run_record("answer")
        with patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(judge._load_trace(run)["answer"], "answer")
        for raw in ("not json", "[]", "null"):
            with patch("builtins.open", mock_open(read_data=raw)):
                self.assertEqual(judge._load_trace(run)["answer"], "answer")
        run.trace = "/uploads/../secret"
        with patch("builtins.open") as opened:
            self.assertEqual(judge._load_trace(run)["answer"], "answer")
            opened.assert_not_called()

    def test_empty_material_clears_old_failure_without_llm(self):
        run = self.run_record()
        with patch.object(judge, "_load_trace", return_value={"ws_captured": True}), \
                patch.object(judge.generators, "get_provider") as provider:
            result = judge.judge_run(Mock(), run)
        provider.assert_not_called()
        self.assertEqual(result["verdict"], "error")
        self.assertIsNone(run.score)
        self.assertIsNone(run.verdict_dims)
        self.assertFalse(run.is_abnormal)

    def test_missing_evidence_failure_is_unknown(self):
        d = dims(False)
        judge._guard_missing_evidence(d, {"answer": "已完成，有产物链接"})
        self.assertTrue(all(d[k]["pass"] is None for k in cr._JUDGE_DIM_KEYS))
        self.assertEqual(judge._verdict_of(d), "error")

    def test_direct_answer_failure_remains_failure(self):
        d = dims()
        d["artifact_expected"] = {"pass": False, "note": "明确未完成", "evidence_source": "answer",
                                  "evidence_quote": "无法压缩视频"}
        judge._guard_missing_evidence(d, {"answer": "抱歉，无法压缩视频"})
        self.assertEqual(judge._verdict_of(d), "fail")
        d["artifact_expected"]["evidence_quote"] = "invented quote"
        judge._guard_missing_evidence(d, {"answer": "抱歉，无法压缩视频"})
        self.assertIsNone(d["artifact_expected"]["pass"])

    def test_correct_answer_without_tools_can_pass(self):
        d = dims()
        judge._guard_missing_evidence(d, {"answer": "2"})
        self.assertEqual(judge._verdict_of(d), "pass")

    def test_model_receives_fallback_and_unsupported_failure_is_guarded(self):
        run = self.run_record("视频压缩完成，下载链接")
        engine = Mock()

        def generate(_expected, *, prompt_builder, system_prompt):
            self.assertIn(run.answer, prompt_builder())
            return iter([{"type": "result", "text": json.dumps(dims(False))}])

        engine.stream_generate.side_effect = generate
        with patch("builtins.open", mock_open(read_data='{"answer":"","ws_captured":true}')), \
                patch.object(judge.generators, "get_provider", return_value=engine):
            result = judge.judge_run(Mock(), run)
        self.assertEqual(result["verdict"], "error")
        self.assertIsNone(run.score)
        self.assertFalse(run.is_abnormal)

    def test_unknown_ballot_does_not_lower_majority_score(self):
        run = self.run_record("answer")
        uncertain = dims(None)
        uncertain["score"] = 1
        with patch.object(judge, "_load_trace", return_value={"answer": "answer"}), \
                patch.object(judge.generators, "get_provider", return_value=Mock()), \
                patch.object(judge, "_judge_once", side_effect=[(dims(), None), (dims(), None), (uncertain, None)]):
            result = judge.judge_run(Mock(), run, votes=3)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(run.score, 5)

    def test_unknown_focus_not_pass(self):
        d = dims()
        d["dimension_ok"] = {"pass": None, "note": "缺工具记录"}
        self.assertEqual(judge._verdict_of(d), "error")

    def test_unknown_score_not_saved_and_votes_require_majority(self):
        run = self.run_record("answer")
        uncertain = dims(None)
        with patch.object(judge, "_load_trace", return_value={"answer": "answer"}), \
                patch.object(judge.generators, "get_provider", return_value=Mock()), \
                patch.object(judge, "_judge_once", side_effect=[(dims(), None), (uncertain, None), (uncertain, None)]):
            result = judge.judge_run(Mock(), run, votes=3)
        self.assertEqual(result["verdict"], "error")
        self.assertIsNone(run.score)
        self.assertIsNone(json.loads(run.verdict_dims)["score"])
        self.assertFalse(run.is_abnormal)

    def test_prompt_and_parser_preserve_unknown_and_evidence(self):
        prompt = cr.build_eval_judge_prompt({"answer": "answer"}, "expected", "tool_use")
        self.assertNotIn("(无工具调用)", prompt)
        self.assertIn("true/false/null", prompt)
        self.assertIn("仅自称完成", prompt)
        d = dims(None)
        d["tools_ok"]["evidence_source"] = "answer"
        d["tools_ok"]["evidence_quote"] = "quote"
        parsed = cr.parse_eval_verdict(json.dumps(d))
        self.assertIsNone(parsed["tools_ok"]["pass"])
        self.assertEqual(parsed["tools_ok"]["evidence_quote"], "quote")

    def test_summary_does_not_turn_missing_evidence_into_product_failure(self):
        prompt = cr.build_eval_task_summary_prompt("task", "", [])
        self.assertIn("证据不足不是产品失败", prompt)
        self.assertIn("明确标注证据冲突", prompt)


if __name__ == "__main__":
    unittest.main()
