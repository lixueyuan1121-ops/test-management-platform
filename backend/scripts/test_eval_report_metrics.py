"""回写指标兼容数字和既有字符串；无数据库、无外部服务。"""
import unittest

from pydantic import ValidationError

from app.schemas.eval_queue import EvalReportIn


class EvalReportMetricsTests(unittest.TestCase):
    def test_numeric_metrics_become_strings(self):
        body = EvalReportIn(status="done", reported_duration=141.682, bean_cost=0.0262,
                            tokens=451619, duration_ms=143170)
        self.assertEqual(body.reported_duration, "141.682")
        self.assertEqual(body.bean_cost, "0.0262")
        self.assertEqual(body.tokens, "451619")
        self.assertEqual(body.duration_ms, 143170)

    def test_zero_missing_and_existing_product_strings_are_preserved(self):
        for value, expected in [(0, "0"), (None, None), ("", ""), ("3.00", "3.00"),
                                ("已完成 2分43秒", "已完成 2分43秒"), ("1.2K", "1.2K")]:
            with self.subTest(value=value):
                body = EvalReportIn(status="done", reported_duration=value, bean_cost=value, tokens=value)
                for field in ["reported_duration", "bean_cost", "tokens"]:
                    self.assertEqual(getattr(body, field), expected)

    def test_invalid_metric_types_remain_rejected(self):
        for field in ["reported_duration", "bean_cost", "tokens"]:
            for value in [True, False, [], {}, float("nan"), float("inf"), -float("inf")]:
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    EvalReportIn.model_validate({"status": "done", field: value})
        with self.assertRaises(ValidationError):
            EvalReportIn(status="done", answer=123)


if __name__ == "__main__":
    unittest.main()
