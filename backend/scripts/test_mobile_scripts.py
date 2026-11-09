"""AI cases must contain validated actions with concrete arguments, independently reviewable and dispatchable."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from app.services import mobile_dispatch, test_missions

def step(action, **kwargs):
    return {"action": action, **kwargs}

def discussion():
    steps = [step("new_chat")]
    for n in range(1, 4):
        steps.extend([step("compose", text="{{marker}} " + f"第{n}轮\nHello 😊"), step("send"),
                      step("assert_user_message", turn=n), step("wait_reply")])
    return [*steps, step("open_history"), step("assert_history_restored")]

class MobileScriptTests(unittest.TestCase):
    def test_dispatch_identical_actions_to_android_and_ios(self):
        script = discussion()
        tc = SimpleNamespace(id=1, project_id=1, title="三轮中英文", exec_kind="gui", platform="mobile",
                             steps="三轮后恢复", expected="逐轮返回", precondition="进入 APP", script=json.dumps(script))
        mobile_dispatch.bind_scenario(tc, "script")
        with patch.object(mobile_dispatch, "owned_targets", return_value=["android", "ios"]), patch.object(
                mobile_dispatch, "device_target", side_effect=lambda d: {"platform": d}):
            pair = mobile_dispatch.mobile_payloads(None, tc, 1, {})
            self.assertEqual([p["mobile_script"] for _, p in pair], [script, script])
            tc.script = json.dumps([step("new_chat"), step("assert_send_unavailable")])
            with self.assertRaises(HTTPException):
                mobile_dispatch.mobile_payloads(None, tc, 1, {})

    def test_invalid_states_text_and_actions(self):
        from app.services.mobile_script import validate_script
        draft = [step("new_chat"), step("compose", text=" \n"), step("assert_draft", text=" \n"),
                 step("clear_draft"), step("assert_send_unavailable")]
        self.assertEqual(validate_script(draft), draft)
        for invalid in ([step("wait_reply")], [step("send")], [step("assert_user_message", turn=1)],
                        [step("assert_user_message", turn=True)], [step("open_history")], [step("assert_history_restored")],
                        [step("new_chat")], [step("compose", text="a"*1001)], [step("compose", text=" \t"), step("send")],
                        [step("compose", text="x", url="external")], [step("shell")], [step("assert_reply_contains", text="yes")],
                        [step("compose", text="x")], [step("assert_send_unavailable")]*40):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_script([step("new_chat"), *invalid])

    def test_history_requires_own_marked_thread(self):
        from app.services.mobile_script import validate_script
        script = discussion()
        script[1]["text"] = "hello"
        with self.assertRaisesRegex(ValueError, "marker"):
            validate_script(script)

    def test_generation_supplies_distinct_scripts_and_uncovered_conditions(self):
        from scripts.test_requirement_analysis import FakeEngine, draft
        draft = draft(); draft["rules"][0]["status"] = "confirmed"
        scripts = [discussion(), [step("new_chat"), step("compose", text="   "), step("assert_send_unavailable")],
                   [step("new_chat"), step("compose", text="文\nA😊"), step("assert_draft", text="文\nA😊")]]
        generated = [{"title": str(i), "steps": "核对真实界面", "expected": "能看到指定结果",
                      "precondition": "已登录", "priority": "P1", "criterion_ids": ["R1-C1"], "script": script}
                     for i, script in enumerate(scripts)]
        engine = FakeEngine({"cases": [*generated, {**generated[0], "title": "重复项"}]})
        result = test_missions.generate_mobile_cases(engine, draft)
        cases = result["cases"]
        self.assertEqual([json.loads(c["script"]) for c in cases if c["kind"] == "gui"], scripts)
        self.assertEqual([c["criterion_ids"] for c in cases if c["kind"] == "manual"], [["R1-C2"]])
