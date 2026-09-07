"""对话测评判定 prompt 自测(纯函数,无需 DB/引擎)。
运行: cd backend && python -m scripts.test_eval_judge_prompt

覆盖(本次「工具调用一次到位 vs 反复试错」判据):
- build_eval_judge_prompt:工具块含「调用统计」+ 同名工具多次调用标 ×N 与 ⚠、逐次明细带「第 N 次调用」;
  单次调用不误标 ⚠;无工具调用走「(无工具调用)」;prompt 含「工具调用效率」软信号判据。
- parse_eval_verdict:三维恒有;dimension_ok 可选维兼容;score 容错。
- _verdict_of:软信号不改 pass/fail 口径——tools_ok=true 但过程试错仍应 pass(回归护栏)。
"""
from app.services import claude_runner as cr


def _trace(tool_calls):
    return {"thinking": "想一下", "tool_calls": tool_calls, "artifacts": [],
            "answer": "最终答案", "ws_captured": True}


def test_tool_block_aggregates_counts():
    # 同一工具调 3 次(前两次无结果、第三次成功)= 典型「反复试错」
    trace = _trace([
        {"original_tool_name": "web_search", "reached_result": False, "result_text": ""},
        {"original_tool_name": "web_search", "reached_result": False, "result_text": ""},
        {"original_tool_name": "web_search", "reached_result": True, "result_text": "命中"},
    ])
    p = cr.build_eval_judge_prompt(trace, expected="查到资料", dimension="tool_use")
    assert "调用统计：" in p, "工具块应含调用统计"
    assert "web_search ×3" in p, f"应聚合出调用次数 ×3\n{p}"
    assert "（⚠ 多次调用）" in p, "多次调用应标 ⚠"
    assert "第 3 次调用" in p, "逐次明细应标出第几次调用"
    assert "未完成/无结果" in p and "有结果" in p, "应保留每次是否拿到结果"
    print("OK 工具块聚合调用次数 + 反复试错可见")


def test_single_call_not_flagged():
    trace = _trace([{"original_tool_name": "get_weather", "reached_result": True, "result_text": "晴"}])
    p = cr.build_eval_judge_prompt(trace, expected="报天气", dimension="tool_use")
    assert "get_weather ×1" in p, "单次也给出 ×1 统计"
    assert "⚠ 多次调用" not in p, "单次调用不得误标 ⚠"
    assert "第 1 次调用" not in p, "单次调用不加「第 N 次」冗余标注"
    print("OK 单次调用不误标试错")


def test_no_tools():
    p = cr.build_eval_judge_prompt(_trace([]), expected="随便答", dimension=None)
    assert "(无工具调用)" in p, "无工具调用应显式说明"
    assert "调用统计：" not in p, "无工具时不应出现调用统计行"
    print("OK 无工具调用")


def test_efficiency_rule_is_soft_signal():
    p = cr.build_eval_judge_prompt(_trace([]), expected="x", dimension=None)
    assert "工具调用效率" in p, "判定规则应含工具调用效率判据"
    assert "只影响 score" in p, "效率必须是软信号(只影响 score)"
    assert "tools_ok 仍判 true" in p, "试错但达成不得判 fail(软信号护栏)"
    print("OK 工具效率是软信号(不改 pass/fail)")


def test_parse_verdict_dims():
    raw = '{"thinking_complete":{"pass":true,"note":"ok"},"tools_ok":{"pass":true,"note":"调3次才成"},"artifact_expected":{"pass":true,"note":""},"score":3,"summary":"试错但达成"}'
    d = cr.parse_eval_verdict(raw)
    for k in ("thinking_complete", "tools_ok", "artifact_expected"):
        assert d[k]["pass"] is True, f"{k} 应解析为 True"
    assert d["score"] == 3, d.get("score")
    # 软信号口径:三核心维全 true → pass(即便过程试错、score 只有 3)
    assert cr_verdict(d) == "pass", "试错但三维达成应判 pass(软信号不改口径)"
    print("OK 解析 + 软信号口径:试错但达成仍 pass")


def test_parse_verdict_optional_dim():
    raw = '{"thinking_complete":{"pass":true},"tools_ok":{"pass":false},"artifact_expected":{"pass":true},"dimension_ok":{"pass":false,"note":"工具没调对"},"score":2}'
    d = cr.parse_eval_verdict(raw)
    assert d["tools_ok"]["pass"] is False
    assert "dimension_ok" in d and d["dimension_ok"]["pass"] is False, "可选第四维应兼容解析"
    assert cr_verdict(d) == "fail", "tools_ok=false 应判 fail"
    print("OK 可选维 dimension_ok 兼容 + fail 口径")


# _verdict_of 在 eval_judge 里(依赖 EvalVerdict 值);这里薄封装复用它,保证与线上判定同一口径。
def cr_verdict(dims):
    from app.services.eval_judge import _verdict_of
    return _verdict_of(dims)


def main():
    test_tool_block_aggregates_counts()
    test_single_call_not_flagged()
    test_no_tools()
    test_efficiency_rule_is_soft_signal()
    test_parse_verdict_dims()
    test_parse_verdict_optional_dim()
    print("OK test_eval_judge_prompt")


if __name__ == "__main__":
    main()
