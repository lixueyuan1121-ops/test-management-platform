"""扫描脚本纯逻辑自测(不联网/不 git):解析 bindings.ts、四段 desc 复用/兜底、驼峰 key。
运行: cd backend && python -m scripts.test_scan_selectors
"""
import os

from scripts.scan_selectors_from_branch import (
    parse_testids, to_camel, auto_desc, load_known_map, build_registry,
)

_REPO = r"D:/git/openclaw360-web/feature-add-testid_20260903"
_KNOWN = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "docs", "testid-selectors-export-full.json"))


def test_parse_and_camel():
    text = '''
    export type X = { testId: string; };
    { kind: "selector", testId: "message-input", selector: ".x" },
    { kind: "selector", testId: "slash-menu-search-clear", selector: ".y" },
    { kind: "selector", testId: "message-input", selector: ".dup" },  // 重复
    '''
    ids = parse_testids(text)
    assert ids == ["message-input", "slash-menu-search-clear"], ids  # 去重保序、忽略类型定义
    assert to_camel("slash-menu-search-clear") == "slashMenuSearchClear"
    assert to_camel("nav-home") == "navHome"
    print("OK parse_and_camel")


def test_auto_desc_four_segments():
    # 未知 testid 也应拼出四段式(3 个 ]-[ 分隔)。
    d = auto_desc("foobar-settings-search-input")
    assert d.count("]-[") == 3, d
    assert d.endswith("[搜索输入框]"), d
    # 前缀命中导航归类
    assert auto_desc("nav-somewhere").startswith("[全局]-[左侧导航栏]"), auto_desc("nav-somewhere")
    # 后缀元素识别:-modal → 弹窗容器
    assert auto_desc("xyz-modal").endswith("[弹窗容器]"), auto_desc("xyz-modal")
    print("OK auto_desc_four_segments")


def test_known_map_reuse():
    known = load_known_map(_KNOWN)
    assert "message-input" in known, "已整理映射应含 message-input"
    reg, auto = build_registry(["message-input", "totally-new-fake-input"], known)
    # 已整理的复用其四段 desc,不进 auto。
    assert reg["messageInput"]["desc"] == known["message-input"]["desc"]
    assert "messageInput" not in auto
    # 新 testid 走兜底,进 auto,desc 仍四段。
    assert "totallyNewFakeInput" in auto
    assert reg["totallyNewFakeInput"]["desc"].count("]-[") == 3
    assert reg["messageInput"]["candidates"] == [{"by": "testid", "value": "message-input"}]
    print("OK known_map_reuse")


def test_scan_real_bindings():
    bindings = os.path.join(_REPO, "src", "test-ids", "bindings.ts")
    if not os.path.exists(bindings):
        print("SKIP scan_real_bindings(未找到本地分支副本)")
        return
    with open(bindings, encoding="utf-8") as f:
        ids = parse_testids(f.read())
    assert len(ids) > 300, f"应扫到数百个 testid,实际 {len(ids)}"
    assert "message-input" in ids and "send-button" in ids
    known = load_known_map(_KNOWN)
    reg, auto = build_registry(ids, known)
    assert len(reg) == len(ids), "每个 testid 一个 key"
    # 每个 desc 都四段
    assert all(v["desc"].count("]-[") == 3 for v in reg.values())
    print(f"OK scan_real_bindings(testid={len(ids)}, 复用={len(reg)-len(auto)}, 自动={len(auto)})")


def main():
    test_parse_and_camel()
    test_auto_desc_four_segments()
    test_known_map_reuse()
    test_scan_real_bindings()
    print("ALL OK test_scan_selectors")


if __name__ == "__main__":
    main()
