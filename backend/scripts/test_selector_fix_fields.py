"""selector_fix_info 解析 + _to_case_out 字段 + list 过滤 的自测。
运行: cd backend && python -m scripts.test_selector_fix_fields
"""
from types import SimpleNamespace
from app.services.claude_runner import selector_fix_info
from app.api.ai import _to_case_out


def main():
    # ---- selector_fix_info:两种格式都能抽出 key + 原意图类型 ----
    ok1, k1, kind1 = selector_fix_info("[选择器待补] 补齐选择器 key:navTasks, submitBtn 后即可执行 gui")
    assert ok1 and k1 == ["navTasks", "submitBtn"] and kind1 == "gui", (ok1, k1, kind1)
    ok2, k2, kind2 = selector_fix_info("[选择器待补] 缺选择器 key:onlyKey(补齐后仍需修其它问题,目标 e2e)")
    assert ok2 and k2 == ["onlyKey"] and kind2 == "e2e", (ok2, k2, kind2)
    # 中文逗号/顿号分隔也支持
    _, k3, _ = selector_fix_info("[选择器待补] 补齐选择器 key:a，b、c 后即可执行 e2e")
    assert k3 == ["a", "b", "c"], k3
    # 非该标识 → (False, [], None)
    assert selector_fix_info("界面验证") == (False, [], None)
    assert selector_fix_info(None) == (False, [], None)

    # ---- _to_case_out:带 selector_fix / selector_fix_keys 字段 ----
    tc = SimpleNamespace(
        id=1, ai_task_id=1, project_id=1, task_id=None, category="功能", title="t",
        steps="s", expected="e", priority="P1", exec_kind="manual", provider="claude",
        kind_reason="[选择器待补] 补齐选择器 key:navTasks 后即可执行 gui",
        adopted=False, review_status="pending", reviewed_at=None, created_at=None, script=None,
    )
    out = _to_case_out(tc)
    assert out["selector_fix"] is True, out
    assert out["selector_fix_keys"] == ["navTasks"], out

    # 普通 manual(非选择器降级)→ selector_fix False
    tc2 = SimpleNamespace(**{**tc.__dict__, "kind_reason": "主观体验,人工"})
    out2 = _to_case_out(tc2)
    assert out2["selector_fix"] is False and out2["selector_fix_keys"] == [], out2

    print("OK test_selector_fix_fields")


if __name__ == "__main__":
    main()
