"""综合评价 prompt 深度归因升级自测(python -m scripts.test_eval_summary_deep_prompt)。

验证 build_eval_task_summary_prompt:输出含新章节(失败根因深挖/工具使用质量/借鉴学习)、
每条用例的 process 过程信号被渲染进 prompt、无 process 不渲染多余行。
"""
from app.services.claude_runner import build_eval_task_summary_prompt, _render_process


def _item(**kw):
    base = {"title": "T", "dimension": "thinking", "prompt": "P", "expected": "E",
            "status": "done", "verdict": "pass", "score": 5, "verdict_reason": "R",
            "answer": "A", "reason": "", "process": None, "duration_s": 0}
    base.update(kw)
    return base


def test_prompt_has_new_sections():
    p = build_eval_task_summary_prompt("任务", "说明", [_item()])
    for kw in ("失败根因深挖", "工具使用质量", "借鉴"):
        assert kw in p, f"prompt 应含章节关键词 {kw}"
    print("OK prompt has new sections")


def test_process_rendered():
    proc = {"thinking_summary": "先分析再动手", "tool_calls": ["bash x3（多次调用）"],
            "tool_call_count": 3, "retry_signal": "bash 调用 3 次；含报错重试", "source": "raw_message"}
    p = build_eval_task_summary_prompt("任务", "说明", [_item(process=proc)])
    assert "先分析再动手" in p, "thinking rendered"
    assert "bash x3" in p, "tools rendered"
    assert "含报错重试" in p, "retry rendered"
    print("OK process rendered")


def test_no_process_no_extra_lines():
    assert _render_process(None) == "", "None empty"
    assert _render_process({"source": "none"}) == "", "none empty"
    p = build_eval_task_summary_prompt("任务", "说明", [_item(process=None)])
    assert "思考过程:" not in p, "no process no line"
    print("OK no process no extra lines")


def test_data_source_note():
    p = build_eval_task_summary_prompt("任务", "说明", [_item()])
    assert "WorkBuddy" in p and "过程数据" in p, "prompt notes data source"
    print("OK data source note")


def test_duration_section_and_render():
    # 耗时表现是独立章节;用例块渲染出耗时;结果相当而更快应被点为优势
    p = build_eval_task_summary_prompt("任务", "说明",
                                       [_item(duration_s=120), _item(duration_s=0)])
    assert "耗时表现" in p, "prompt 应含独立「耗时表现」章节"
    assert "耗时:120秒" in p, f"有耗时的用例块应渲染耗时\n{p}"
    assert "更快" in p and "优势" in p, "prompt 应引导:结果相当而更快=优势"
    # duration_s=0(缺耗时)不渲染耗时行
    assert p.count("耗时:120秒") == 1, "缺耗时(0)的用例不应渲染耗时行"
    print("OK duration section + render")


def test_no_tool_not_penalized_and_learn():
    # 综合评价须纠偏:无工具/少工具但结果好不减分,反而是可借鉴点
    p = build_eval_task_summary_prompt("任务", "说明", [_item()])
    assert "不减分" in p or "不扣分" in p, "prompt 应明确无工具而结果好不减分"
    print("OK no-tool not penalized guidance")


def test_concrete_root_cause_and_plain_language():
    # 用户硬要求:分析具体、有问题追根因给建设性建议、口语化
    p = build_eval_task_summary_prompt("任务", "说明", [_item()])
    assert "口语" in p or "大白话" in p or "说人话" in p, "prompt 应要求口语化表述"
    assert "根因" in p or "原因" in p, "prompt 应要求追根因"
    assert "具体" in p, "prompt 应要求分析具体"
    print("OK concrete root-cause + plain language guidance")


def main():
    test_prompt_has_new_sections()
    test_process_rendered()
    test_no_process_no_extra_lines()
    test_data_source_note()
    test_duration_section_and_render()
    test_no_tool_not_penalized_and_learn()
    test_concrete_root_cause_and_plain_language()
    print("\n[PASS] 综合评价深度 prompt 全部通过")


if __name__ == "__main__":
    main()
