"""综合评价 prompt 深度归因升级自测(python -m scripts.test_eval_summary_deep_prompt)。

验证 build_eval_task_summary_prompt:输出含新章节(失败根因深挖/工具使用质量/借鉴学习)、
每条用例的 process 过程信号被渲染进 prompt、无 process 不渲染多余行。
"""
from app.services.claude_runner import build_eval_task_summary_prompt, _render_process


def _item(**kw):
    base = {"title": "T", "dimension": "thinking", "prompt": "P", "expected": "E",
            "status": "done", "verdict": "pass", "score": 5, "verdict_reason": "R",
            "answer": "A", "reason": "", "process": None}
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


def main():
    test_prompt_has_new_sections()
    test_process_rendered()
    test_no_process_no_extra_lines()
    test_data_source_note()
    print("\n[PASS] 综合评价深度 prompt 全部通过")


if __name__ == "__main__":
    main()
