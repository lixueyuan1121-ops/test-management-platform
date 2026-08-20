"""gui/e2e 用例自治生成规则自测(project_id=None 免 DB)。
运行: cd backend && python -m scripts.test_case_autonomy
"""
from app.services.claude_runner import (
    build_testcase_prompt,
    build_script_prompt,
    _validate_script,
    _looks_like_e2e,
)


def test_testcase_prompt_has_autonomy_rules():
    p = build_testcase_prompt("测试任务页新建与校验", project_id=None)
    # 三段式自治关键字
    assert "用例自治" in p, "缺『用例自治』规则"
    assert "进入" in p and "恢复" in p, "缺进入/恢复三段式"
    assert "不假设" in p, "缺『不假设当前页』约束"
    assert "已登录" in p, "缺『默认已登录主界面』起点约定"
    # gui 步数放宽到 3–6 步(接受 en dash 或 hyphen)
    assert ("3–6 步" in p) or ("3-6 步" in p), "gui 步数应放宽到 3–6 步"
    # 回归:组件4缺 key 话术与既有断言未被破坏
    assert "选择器待补" in p and "描述这个元素" in p
    assert "connect" in p and "assert_visible" in p


def test_three_phase_gui_script_not_downgraded():
    # 进入(导航)→ 等待 → 断言 → 恢复(回起点),全用已注册 key
    script = [
        {"action": "connect", "desc": "连接"},
        {"action": "click", "target": {"key": "navTasks"}, "desc": "进入任务页"},
        {"action": "wait_for", "target": {"key": "taskList"}, "args": {"timeout_ms": 6000}, "desc": "等任务页"},
        {"action": "assert_visible", "target": {"key": "taskList"}, "desc": "断言任务列表可见"},
        {"action": "click", "target": {"key": "navHome"}, "desc": "恢复:回首页"},
    ]
    valid = {"navTasks", "taskList", "navHome"}
    norm, err = _validate_script(script, valid)
    assert err is None, f"三段式 gui 不应被判非法: {err}"
    assert len(norm) == 5


def test_three_phase_e2e_recognized():
    script = [
        {"action": "connect", "desc": "连接"},
        {"action": "click", "target": {"key": "navTasks"}, "desc": "进入"},
        {"action": "click", "target": {"key": "newTaskBtn"}, "desc": "新建"},
        {"action": "fill", "target": {"key": "taskTitleInput"}, "args": {"text": "自动化任务"}, "desc": "填标题"},
        {"action": "click", "target": {"key": "submitBtn"}, "desc": "提交"},
        {"action": "wait_for", "target": {"key": "taskList"}, "args": {"timeout_ms": 8000}, "desc": "等列表刷新"},
        {"action": "assert_text", "target": {"key": "taskList"}, "args": {"expected": "自动化任务", "contains": True}, "desc": "断言出现"},
        {"action": "click", "target": {"key": "navHome"}, "desc": "恢复:回首页"},
    ]
    assert _looks_like_e2e(script) is True, "多步三段式应识别为 e2e"


def main():
    test_testcase_prompt_has_autonomy_rules()
    test_three_phase_gui_script_not_downgraded()
    test_three_phase_e2e_recognized()
    print("OK test_case_autonomy")


if __name__ == "__main__":
    main()
