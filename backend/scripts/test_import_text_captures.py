"""Validate dynamic text assertions through the same guard used by imports."""
import copy
import unittest
from app.services.claude_runner import _validate_script


class TextCaptureTests(unittest.TestCase):
    def setUp(self):
        self.script = [
            {"action": "get_text", "target": {"selector": ".row-title"}, "args": {"save_as": "rank_name"}},
            {"action": "assert_text", "target": {"selector": ".detail-title"}, "args": {"expected_from": "rank_name", "contains": True}},
        ]

    def test_dynamic_assertion_preserved(self):
        original = copy.deepcopy(self.script)
        normalized, error = _validate_script(self.script, set())
        self.assertIsNone(error)
        self.assertEqual(normalized[1]["args"], original[1]["args"])
        self.assertEqual(self.script, original)

    def test_invalid_captures_rejected(self):
        cases = [
            [self.script[1], self.script[0]],
            [self.script[0], self.script[0], self.script[1]],
        ]
        for args in ({}, {"expected_from": "missing"}, {"expected_from": 12},
                     {"expected_from": "rank_name", "expected": "fixed"}):
            step = copy.deepcopy(self.script[1]); step["args"] = args
            cases.append([self.script[0], step])
        for script in cases:
            with self.subTest(script=script):
                self.assertTrue(_validate_script(script)[1])

    def test_static_empty_text_remains_supported(self):
        step = copy.deepcopy(self.script[1]); step["args"] = {"expected": ""}
        self.assertIsNone(_validate_script([step])[1])


if __name__ == "__main__":
    unittest.main()
