"""Import dialect guard: real malformed input, both Python copies and offline CLI."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.test_qalab_skill_macos import qalab, payload, FakeApi, Store
from app.services.script_targets import validate_targets, VALID_ACTIONS, TARGET_ACTIONS
from app.services.claude_runner import _VALID_ACTIONS

class ContractTests(unittest.TestCase):
    def test_action_contract_matches_server(self):
        self.assertEqual(VALID_ACTIONS, _VALID_ACTIONS)

    def test_real_bad_payload_and_malformed_targets_never_contact_platform(self):
        for step in [
            {"action": "click", "selector": {"text": "自动化"}},
            {"action": "wait_for_element", "selector": {"key": "automation-main-view"}},
            {"action": "assert_visible", "selector": {"check": {"actual": "visible", "expected": "visible"}, "key": "automation-main-view"}},
            {"action": "click", "target": "text=自动化"},
            {"action": "click", "target": {"selector": "  "}},
            {"action": "click", "target": {"key": 12}},
            {"action": "click", "target": {"selector": ".nav", "within": {}}},
        ]:
            api = FakeApi(); body = payload()
            body['cases'][0]['script'].insert(0, step)
            body['cases'][0]['report'].insert(0, {"action": step['action'], "ok": True})
            with self.subTest(step=step):
                with self.assertRaisesRegex(ValueError, '第 1 步'):
                    validate_targets(body['cases'][0]['script'], 'Logo')
                with self.assertRaisesRegex(RuntimeError, '第 1 步'):
                    qalab.Client(api, Store()).import_cases(body, 'unused')
                self.assertEqual(api.calls, [])

    def test_supported_locators_and_targetless_steps_unchanged(self):
        for target in [{'key': 'registered-key'}, {'selector': 'text=自动化'}, {'selector': 'xpath=//nav//button'}, {'selector': '.nav', 'frame': 'shell', 'within': {'selector': 'aside'}}]:
            script = [{'action': 'connect'}, {'action': 'press', 'args': {'key_name': 'Escape'}}]
            script += [{'action': action, 'target': target, 'args': {}} for action in TARGET_ACTIONS]
            original = copy.deepcopy(script)
            validate_targets(script, 'flow')
            qalab.validate_targets(script, 'flow')
            self.assertEqual(script, original)

    def test_validate_cli_without_login_or_network(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'payload.json'; path.write_text(json.dumps(payload()), encoding='utf-8')
            with patch.object(qalab, 'Api', side_effect=AssertionError('no network')), patch.object(qalab, 'MacKeychain', side_effect=AssertionError('no credentials')):
                qalab.main(['validate', '--payload', str(path)])

    def test_windows_validate_accepts_and_rejects_without_login(self):
        if sys.platform != 'win32': self.skipTest('Windows PowerShell required')
        helper = Path(__file__).resolve().parents[2] / 'tools/skills/qalab-requirement-test/scripts/qalab.ps1'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'payload.json'
            for step, success in [(None, True), ({'action': 'click', 'selector': {'text': '自动化'}}, False), ({'action': 'wait_for_element', 'target': {'selector': '.home'}}, False), ({'action': 'click', 'target': {'selector': ' '}}, False)]:
                body = payload()
                if step:
                    body['cases'][0]['script'].insert(0,step)
                    body['cases'][0]['report'].insert(0,{'action':step['action'],'ok':True})
                path.write_text(json.dumps(body,ensure_ascii=False),encoding='utf-8-sig')
                run=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(helper),'-Action','validate','-PayloadPath',str(path)],capture_output=True)
                self.assertEqual(run.returncode==0,success,run.stdout+run.stderr)

if __name__ == '__main__': unittest.main()
