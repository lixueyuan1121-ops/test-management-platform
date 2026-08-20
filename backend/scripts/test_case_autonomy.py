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
    # 两段式自治(进入→执行);恢复段已移除(结尾 navHome/回起点是失败源,自治靠进入段自导航)
    assert "用例自治" in p, "缺『用例自治』规则"
    assert "进入" in p and "执行" in p, "缺进入/执行两段式"
    assert "不假设" in p, "缺『不假设当前页』约束"
    assert "已登录" in p, "缺『默认已登录主界面』起点约定"
    # 恢复段应被删除:不再要求结尾导航回起点/收尾恢复
    assert "恢复" not in p, "恢复段应已移除(结尾还原步是执行失败源)"
    assert "导航回起点" not in p, "不应再要求导航回起点页"
    # gui 步数去掉恢复步后回调到 2–5 步(接受 en dash 或 hyphen)
    assert ("2–5 步" in p) or ("2-5 步" in p), "gui 步数应回调到 2–5 步(去恢复步)"
    # 回归:组件4缺 key 话术与既有断言未被破坏
    assert "选择器待补" in p and "描述这个元素" in p
    assert "connect" in p and "assert_visible" in p
    # wait_for 必须带 target(纯等异步用 wait_response),防模型漏 target 撞校验
    assert "wait_response" in p, "缺 wait_response 说明"
    assert "不是纯计时等待" in p, "缺『wait_for 必须带 target』约束"
    # 登录用例保留 homepageTitle(它是登录成功的验证点,非恢复步)
    assert "homepageTitle" in p, "登录正例应保留 homepageTitle 断言"


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


def test_script_prompt_gui_autonomy():
    p = build_script_prompt("gui", "任务页新建校验", "打开任务页→新建", "列表出现新项", project_id=None)
    assert "用例自治" in p, "单条重生 gui 缺自治规则"
    assert ("2-5 步" in p) or ("2–5 步" in p), "gui 步数去恢复步后应为 2-5 步"
    assert "恢复" not in p, "单条重生 gui 恢复段应已移除"
    # 对齐组件4:缺 key 走选择器待补,不再用 selector 兜底
    assert "选择器待补" in p, "应对齐组件4缺 key 话术"
    assert "最接近的语义 key 或 selector" not in p, "旧的『用 selector 兜底』话术应移除"


def test_script_prompt_e2e_autonomy():
    p = build_script_prompt("e2e", "登录后发消息", "登录→发消息→等回复", "有回复", project_id=None)
    assert "用例自治" in p, "单条重生 e2e 缺自治规则"
    assert "≥5 步" in p, "e2e 应保留 ≥5 步要求"
    assert "恢复" not in p, "单条重生 e2e 恢复段应已移除"


def main():
    test_testcase_prompt_has_autonomy_rules()
    test_three_phase_gui_script_not_downgraded()
    test_three_phase_e2e_recognized()
    test_script_prompt_gui_autonomy()
    test_script_prompt_e2e_autonomy()
    print("OK test_case_autonomy")


if __name__ == "__main__":
    main()
