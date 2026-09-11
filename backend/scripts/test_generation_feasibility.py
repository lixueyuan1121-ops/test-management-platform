"""Regression for executable GUI/E2E generation and automatic mock exclusion."""
import json
import unittest
from unittest.mock import patch

from app.services import claude_runner as c


class GenerationFeasibilityTests(unittest.TestCase):
    def test_all_prompt_paths_share_policy(self):
        with patch.object(c, "_load_selector_keys", return_value=[]), \
             patch.object(c, "_load_api_contract", return_value=None):
            prompts = [c.build_testcase_prompt("创建并查询记录", shard=shard, no_script=short)
                       for shard in [None, *c.TESTCASE_SHARDS] for short in (False, True)]
            prompts += [c.build_script_prompt(kind, "创建记录", "点击保存", "列表出现记录")
                        for kind in ("gui", "e2e")]
        for prompt in prompts:
            self.assertIn("当前自动生成禁止接口 mock", prompt)
            self.assertNotIn("优先用 mock_route", prompt)
            self.assertNotIn("主动生成这类用例", prompt)
            self.assertNotIn("script.target.key 只能取这里", prompt)
            self.assertNotIn("若无法用 key 表达，请改判 manual", prompt)
        self.assertNotIn("mock_route", c._GUI_SCRIPT_SPEC)

    def test_mock_excluded_without_removing_real_gui(self):
        real = [{"action": "connect"}, {"action": "assert_visible", "target": {"key": "list"}}]
        mock = [real[0], {"action": "mock_route", "args": {"url": "**/api/list"}}, real[1]]
        cases = [{"title": "真实界面", "kind": "gui", "script": real},
                 {"title": "伪造返回", "kind": "gui", "script": mock}]
        with patch.object(c, "_registered_keys", return_value={"list"}), \
             patch.object(c, "_key_page_map", return_value={}):
            rows = c.parse_testcases(json.dumps(cases))
        self.assertEqual([r["title"] for r in rows], ["真实界面"])
        self.assertEqual(rows[0]["kind"], "gui")
        self.assertIsNotNone(c._validate_generated_gui_script(mock, {"list"})[1])
        # Existing manually maintained mock scripts remain editable.
        self.assertIsNone(c._validate_script(mock, {"list"})[1])

    def test_missing_selector_preserves_repairable_script(self):
        case = {"title": "新界面", "kind": "gui", "script": [
            {"action": "connect"}, {"action": "assert_visible", "target": {"key": "newList"}}]}
        with patch.object(c, "_registered_keys", return_value={"known"}), \
             patch.object(c, "_key_page_map", return_value={}):
            row = c.parse_testcases(json.dumps([case]))[0]
        self.assertIn("选择器", row["kind_reason"])
        self.assertIsNotNone(row["script"])
        case["script"] = [{"action": "connect"}]
        with patch.object(c, "_registered_keys", return_value={"known"}), \
             patch.object(c, "_key_page_map", return_value={}):
            row = c.parse_testcases(json.dumps([case]))[0]
        self.assertIn("无任何断言", row["kind_reason"])


if __name__ == "__main__":
    unittest.main()
