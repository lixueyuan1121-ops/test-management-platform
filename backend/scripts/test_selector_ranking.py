"""selector_ranking 自测（纯函数，免 DB）。
运行: cd backend && .venv/bin/python -m scripts.test_selector_ranking

口径：脆弱 by = text/role（getByText 子串匹配 + role 靠 name，均 copy 依赖）；
order_candidates 把脆弱降到链尾、其余保持相对顺序（稳定排序）。
镜像前端 frontend/src/utils/selector-ranking.js，参照 gui-core.mjs::genCandidates 分梯。
"""
from app.services.selector_ranking import is_fragile, order_candidates, FRAGILE_BYS


def main():
    # is_fragile：text/role 脆弱，其余稳定
    assert FRAGILE_BYS == {"text", "role"}, FRAGILE_BYS
    assert is_fragile({"by": "text", "value": "登录"}) is True
    assert is_fragile({"by": "role", "value": "button", "name": "登录"}) is True
    for by in ("testid", "css", "label", "placeholder"):
        assert is_fragile({"by": by, "value": "x"}) is False, by
    # 缺 by（默认按 css 处理）→ 稳定
    assert is_fragile({"value": "x"}) is False

    # order_candidates：脆弱降尾，稳定保持相对顺序
    cands = [
        {"by": "text", "value": "对话"},
        {"by": "testid", "value": "chat-title"},
        {"by": "label", "value": "标题"},
    ]
    ordered = order_candidates(cands)
    assert [c["by"] for c in ordered] == ["testid", "label", "text"], ordered
    # 原列表不被修改
    assert [c["by"] for c in cands] == ["text", "testid", "label"], cands

    # 多个脆弱之间也保持相对顺序
    cands2 = [
        {"by": "role", "value": "button", "name": "发送"},
        {"by": "css", "value": "#send"},
        {"by": "text", "value": "发送"},
    ]
    assert [c["by"] for c in order_candidates(cands2)] == ["css", "role", "text"], order_candidates(cands2)

    # 全稳定 → 原样；空 → 空
    stable = [{"by": "testid", "value": "a"}, {"by": "css", "value": "#b"}]
    assert order_candidates(stable) == stable
    assert order_candidates([]) == []

    print("OK test_selector_ranking")


if __name__ == "__main__":
    main()
