"""Human-facing descriptions must remain useful without changing executable evidence."""
import copy
import unittest
from app.services.case_steps import validate_case_steps
from app.schemas.verified_import import CodexImport
from scripts.test_codex_import import codex_source


class ReadableCaseStepsTests(unittest.TestCase):
    def test_rejects_action_dumps_and_empty_numbered_rows(self):
        for steps in ('1. flow_start\n2. connect\n3. click', '1. 点击',
                      '1. 打开文件页\n2.', '第1步：press', '- assert_visible',
                      '1. 点击「文件」\n2. 待补充', ''):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                validate_case_steps(steps)

    def test_business_descriptions_allow_grouping_and_english(self):
        for steps in ('1. 打开「文件」并切换到「AI文件」，检查页签已选中。\n2. 检查列表无多选框。',
                      'Click the Share button and verify the copied link.',
                      '1. 在命令输入框输入 click，检查提示文本。',
                      '1. 复用已完成回答；不存在时创建测试会话。\n\n2. 给该回答点赞并检查选中态。'):
            validate_case_steps(steps)

    def test_codex_contract_rejects_unreadable_steps_before_import(self):
        payload=codex_source(False,'readable-case-0001')
        payload['cases'][0]['steps']='1. flow_start\n2. click\n3.'
        with self.assertRaisesRegex(ValueError,'可读说明'):
            CodexImport.model_validate(payload)

    def test_rewording_steps_preserves_executable_evidence(self):
        payload=codex_source(False,'readable-case-0002')
        before=copy.deepcopy(payload['cases'][0])
        payload['cases'][0]['steps']='1. 打开测试页面并点击目标按钮，检查预期结果。'
        parsed=CodexImport.model_validate(payload)
        for field in ('script','report','codex_evidence'):
            self.assertEqual(parsed.cases[0].model_dump()[field],before[field])

if __name__=='__main__':unittest.main()
