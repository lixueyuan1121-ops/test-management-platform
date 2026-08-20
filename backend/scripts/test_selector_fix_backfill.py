"""「选择器待补」闭环:降级保留 script + 确定性回填 自测(monkeypatch 注册表免 DB)。
运行: cd backend && python -m scripts.test_selector_fix_backfill

覆盖场景(对应设计三改动):
- 改动1 parse_testcases:仅缺 key(补齐即可执行)而降级的用例,保留原始 script 供回填;
  但"补齐后仍有其它问题"(如无断言)的不保留(不留坏脚本)。
- 改动2 revalidate_for_backfill:待补 script 引用的 key 现已全部注册 → 校验通过(可确定性
  回填、无需调 AI);仍缺 key → 校验不过(落 AI 兜底)。
- 改动3 build_script_prompt:gui/e2e 单条重生须约束"复用清单已有 key、勿为同一元素另造新名"。
"""
import json

import app.services.claude_runner as cr


def _one_case_raw():
    """一条 gui 用例:结构完全合法(含断言),仅 submitOrderBtn 未注册。"""
    return json.dumps([{
        "title": "下单按钮可点击",
        "kind": "gui",
        "category": "功能",
        "priority": "P1",
        "steps": "进入任务页→点击下单按钮",
        "expected": "出现下单成功提示",
        "script": [
            {"action": "connect", "desc": "连接客户端"},
            {"action": "click", "target": {"key": "navTasks"}, "desc": "进入任务页"},
            {"action": "click", "target": {"key": "submitOrderBtn"}, "desc": "点下单按钮(可见文案『下单』,右下角主按钮)"},
            {"action": "assert_visible", "target": {"key": "navTasks"}, "desc": "断言页面元素可见"},
        ],
    }], ensure_ascii=False)


def _script_keys(script):
    steps = json.loads(script) if isinstance(script, str) else script
    return [s["target"]["key"] for s in steps if isinstance(s.get("target"), dict) and s["target"].get("key")]


def test_selector_fix_downgrade_preserves_script():
    """改动1:仅缺 key(补齐即可执行)而降级的用例,须保留原始 script 供后续确定性回填。"""
    cr._registered_keys = lambda pid: {"navTasks"}   # submitOrderBtn 未注册
    cr._key_page_map = lambda pid: {}
    cases = cr.parse_testcases(_one_case_raw(), project_id=1)
    assert len(cases) == 1
    c = cases[0]
    assert c["kind"] == "manual", "缺 key 应降级 manual"
    assert c["kind_reason"].startswith("[选择器待补]"), "应带选择器待补标识"
    assert "submitOrderBtn" in c["kind_reason"], "应点名缺失 key"
    assert c["script"] is not None, "改动1:选择器待补用例应保留原始 script(不再丢成 None)"
    assert "submitOrderBtn" in _script_keys(c["script"]), "保留的 script 应仍引用待补 key"


def test_downgrade_with_other_problems_keeps_no_script():
    """回归:补齐 key 后仍有其它问题(如无断言)的用例,不保留 script(不留坏脚本)。"""
    cr._registered_keys = lambda pid: {"navTasks"}
    cr._key_page_map = lambda pid: {}
    raw = json.dumps([{
        "title": "无断言用例", "kind": "gui", "steps": "x", "expected": "y",
        "script": [
            {"action": "connect", "desc": "连接"},
            {"action": "click", "target": {"key": "submitOrderBtn"}, "desc": "点(缺 key 且全脚本无断言)"},
        ],
    }], ensure_ascii=False)
    cases = cr.parse_testcases(raw, project_id=1)
    assert cases[0]["kind"] == "manual"
    assert cases[0]["script"] is None, "补齐后仍有其它问题(无断言)的用例不应保留 script"


def test_backfill_ready_after_key_registered():
    """改动2:待补 script 引用的 key 现已全部注册 → 确定性回填校验通过(err is None,无需调 AI)。"""
    cr._registered_keys = lambda pid: {"navTasks", "submitOrderBtn"}   # 已补齐
    script = json.loads(_one_case_raw())[0]["script"]
    norm, err = cr.revalidate_for_backfill(script, project_id=1)
    assert err is None, f"key 补齐后应可确定性回填,却报: {err}"
    assert "submitOrderBtn" in _script_keys(norm)


def test_backfill_not_ready_when_key_still_missing():
    """改动2:仍缺 key 时回填不成立(err 非空)→ 调用方应落 AI 兜底。"""
    cr._registered_keys = lambda pid: {"navTasks"}   # submitOrderBtn 仍缺
    script = json.loads(_one_case_raw())[0]["script"]
    norm, err = cr.revalidate_for_backfill(script, project_id=1)
    assert err is not None, "仍缺 key 时不应确定性回填(应落 AI 兜底)"


def test_script_prompt_reuse_existing_key():
    """改动3:gui/e2e 单条重生 prompt 须约束『复用清单已有 key、勿为同一元素另造新名』。"""
    p = cr.build_script_prompt("gui", "任务页新建", "打开→新建", "列表出现", project_id=None)
    assert "复用" in p, "缺『复用已有 key』约束(防重生时 key 名漂移)"


def main():
    test_selector_fix_downgrade_preserves_script()
    test_downgrade_with_other_problems_keeps_no_script()
    test_backfill_ready_after_key_registered()
    test_backfill_not_ready_when_key_still_missing()
    test_script_prompt_reuse_existing_key()
    print("OK test_selector_fix_backfill")


if __name__ == "__main__":
    main()
