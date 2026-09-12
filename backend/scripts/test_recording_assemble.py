"""录制组装纯逻辑自测(免 DB)。运行: cd backend && python -m scripts.test_recording_assemble"""
from app.services.recording import assemble_recording_steps, to_camel, _new_key_name, _cand_key


def test_reuse_existing_key():
    # 候选命中已注册 key → 复用 key 名;并把本次候选并入(供 apply_heal_items 合并/补 xpath 等)。
    idx = {_cand_key({"by": "testid", "value": "nav-agents"}): "navAgents"}
    ev = {"action": "click", "tag": "li", "text": "专家",
          "candidates": [{"by": "testid", "value": "nav-agents"}, {"by": "css", "value": ".x"}]}
    steps, new, _exp = assemble_recording_steps([ev], idx)
    assert steps[0]["action"] == "connect", steps
    assert steps[1] == {"action": "click", "target": {"key": "navAgents"}, "args": {}, "desc": "专家"}, steps[1]
    # 命中已有 key 也回填候选(不带 desc/page,不动已有元信息),让复录能补进新捕获的更优候选(如 xpath)
    assert len(new) == 1 and new[0]["key"] == "navAgents", new
    assert "desc" not in new[0] and "page" not in new[0], "复用 key 的回填不应携带 desc/page"


def test_new_key_backfill():
    # 未命中 → 新建 key(testid 转 camel) + 收进 new_keys(带多候选)
    ev = {"action": "click", "tag": "button", "text": "在线预览",
          "candidates": [{"by": "testid", "value": "office-online-preview"}, {"by": "css", "value": ".btn"}]}
    steps, new, _exp = assemble_recording_steps([ev], {})
    assert steps[1]["target"]["key"] == "officeOnlinePreview", steps[1]
    assert len(new) == 1 and new[0]["key"] == "officeOnlinePreview"
    assert new[0]["candidates"][0]["by"] == "testid", new[0]["candidates"]
    print("OK new_key_backfill")


def test_actions_and_assert():
    evs = [
        {"action": "fill", "tag": "input", "value": "hello", "candidates": [{"by": "css", "value": "#q"}]},
        {"action": "assert", "tag": "div", "text": "成功", "candidates": [{"by": "testid", "value": "toast"}],
         "assert": {"kind": "text", "expected": "成功"}},
        {"action": "assert", "tag": "div", "candidates": [{"by": "testid", "value": "panel"}],
         "assert": {"kind": "visible"}},
    ]
    steps, _n, _e = assemble_recording_steps(evs, {})
    assert steps[1]["action"] == "fill" and steps[1]["args"]["text"] == "hello", steps[1]
    assert steps[2]["action"] == "assert_text" and steps[2]["args"]["expected"] == "成功", steps[2]
    assert steps[3]["action"] == "assert_visible", steps[3]
    # 预期结果由断言步汇总(修"录制 e2e 没有预期结果")
    assert "成功" in _e and _e != "", f"expected 应含断言文案,实际 {_e!r}"
    # 无断言 → 兜底预期
    _s2, _n2, e2 = assemble_recording_steps([{"action": "click", "candidates": [{"by": "testid", "value": "x"}]}], {})
    assert e2 == "", "无断言不能伪造预期结果"
    print("OK actions_and_assert")


def test_edge_cases():
    # 无有效候选 → 跳过;重名 key 加序号;非 dict 事件跳过
    assert to_camel("office-open-folder") == "officeOpenFolder"
    assert _new_key_name({"candidates": [{"by": "testid", "value": "x-btn"}]}, {"xBtn"}) == "xBtn2"
    for bad in ({"action": "click", "candidates": [{"by": "css"}]}, None):
        try:
            assemble_recording_steps([bad], {})
        except ValueError:
            pass
        else:
            raise AssertionError("非法录制步骤不得被静默跳过")


def main():
    test_reuse_existing_key(); print("OK reuse_existing_key")
    test_new_key_backfill()
    test_actions_and_assert()
    test_edge_cases()
    print("ALL OK test_recording_assemble")


if __name__ == "__main__":
    main()
