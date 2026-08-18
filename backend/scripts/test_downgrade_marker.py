"""降级标识自测:因选择器未注册而降级的用例,kind_reason 打「选择器待补」标识并列出缺的 key。
运行: cd backend && python -m scripts.test_downgrade_marker
"""
import json
import app.services.claude_runner as cr
from app.services.claude_runner import parse_testcases, _unregistered_keys, _SELECTOR_FIX_MARK

# 免 DB:桩掉注册表读取,固定 valid_keys = {navTasks}
cr._registered_keys = lambda pid=None: {"navTasks"}


def _find(cases, title):
    for c in cases:
        if c["title"] == title:
            return c
    raise AssertionError(f"未找到 {title}")


def _gui(title, key, with_assert=True):
    """造一条 gui 用例:connect + assert_visible(key)。with_assert=False 则去掉断言(制造非选择器问题)。"""
    steps = [{"action": "connect", "desc": "连接"}]
    if with_assert:
        steps.append({"action": "assert_visible", "target": {"key": key}, "desc": "看见"})
    else:
        steps.append({"action": "click", "target": {"key": key}, "desc": "点"})  # 无断言 → 另有问题
    return {"title": title, "kind": "gui", "priority": "P1", "kind_reason": "界面验证", "script": steps}


def main():
    arr = [
        # ① 只差一个未注册 key,补齐即可执行 → 标「选择器待补 ... 后即可执行 gui」
        _gui("只缺选择器", "missingKey", with_assert=True),
        # ② 未注册 key + 无断言(既缺 key 又缺断言)→ 标「补齐后仍需修其它问题」
        _gui("缺key且无断言", "missingKey2", with_assert=False),
        # ③ 用已注册 key,合法 → 保持 gui,不打标识
        _gui("正常gui", "navTasks", with_assert=True),
        # ④ 非选择器原因降级(全用已注册 key 但无断言)→ 降级但不打「选择器待补」
        {"title": "无断言降级", "kind": "gui", "priority": "P2", "kind_reason": "界面",
         "script": [{"action": "connect"}, {"action": "click", "target": {"key": "navTasks"}}]},
    ]
    cases = parse_testcases(json.dumps(arr, ensure_ascii=False), project_id=1)

    c1 = _find(cases, "只缺选择器")
    assert c1["kind"] == "manual", c1["kind"]
    assert c1["kind_reason"].startswith(_SELECTOR_FIX_MARK), c1["kind_reason"]
    assert "missingKey" in c1["kind_reason"], c1["kind_reason"]
    assert "即可执行 gui" in c1["kind_reason"], c1["kind_reason"]

    c2 = _find(cases, "缺key且无断言")
    assert c2["kind"] == "manual"
    assert c2["kind_reason"].startswith(_SELECTOR_FIX_MARK), c2["kind_reason"]
    assert "missingKey2" in c2["kind_reason"]
    assert "仍需修其它问题" in c2["kind_reason"], c2["kind_reason"]

    c3 = _find(cases, "正常gui")
    assert c3["kind"] == "gui", c3["kind"]
    assert _SELECTOR_FIX_MARK not in c3["kind_reason"]
    assert c3["script"], "合法 gui 应保留 script"

    c4 = _find(cases, "无断言降级")
    assert c4["kind"] == "manual"
    assert _SELECTOR_FIX_MARK not in c4["kind_reason"], "非选择器原因不应打此标识"

    # _unregistered_keys 单元
    sc = [{"action": "click", "target": {"key": "a"}}, {"action": "assert_visible", "target": {"key": "b"}}]
    assert _unregistered_keys(sc, {"a"}) == ["b"]
    assert _unregistered_keys(sc, set()) == [], "空注册表不构成缺失"

    print("OK test_downgrade_marker")


if __name__ == "__main__":
    main()
