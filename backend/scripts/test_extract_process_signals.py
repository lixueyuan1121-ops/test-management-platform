"""_extract_process_signals 纯函数自测(python -m scripts.test_extract_process_signals)。

从 WorkBuddy raw_message(JSON,含 reasoning 思维链 + 工具调用)/ 纳米 trace(tool_calls+thinking)
提取过程信号:思维链摘要、工具调用序列(按名聚合次数)、试错信号、数据来源。纯函数,可单测。
"""
import json
from app.services.claude_runner import _extract_process_signals


def test_raw_message_reasoning_and_tools():
    raw = json.dumps({
        "requestId": "req-1", "toolCallCount": 4,
        "messages": [{
            "messageType": "assistant",
            "content": [
                {"type": "reasoning", "text": "先读文件,再跑测试。"},
                {"type": "tool_use", "name": "read_file"},
                {"type": "tool_use", "name": "bash"},
                {"type": "tool_use", "name": "bash"},
                {"type": "tool_use", "name": "bash"},
            ],
        }],
    }, ensure_ascii=False)
    out = _extract_process_signals(raw, None)
    assert out["source"] == "raw_message", out
    assert "先读文件" in out["thinking_summary"], out
    joined = " ".join(out["tool_calls"])
    assert "read_file" in joined and "bash" in joined, out
    assert "3" in joined, "bash 3 次应体现次数"
    assert out["tool_call_count"] == 4, out
    assert "bash" in out["retry_signal"], "bash 多次应进试错"
    print("OK raw_message reasoning+tools")


def test_raw_message_non_json_degrades():
    out = _extract_process_signals("这不是JSON只是文本" * 50, None)
    assert out["source"] == "raw_message", out
    assert out["thinking_summary"], "退化应保留摘要"
    assert out["tool_calls"] == [], out
    print("OK raw_message non-json degrade")


def test_trace_tool_calls_aggregated():
    trace = {
        "thinking": "分析需求后调用接口。",
        "tool_calls": [
            {"original_tool_name": "http_get", "reached_result": True, "result_text": "200 OK"},
            {"name": "run_python", "reached_result": False, "result_text": "Traceback: SyntaxError"},
            {"name": "run_python", "reached_result": True, "result_text": "done"},
        ],
    }
    out = _extract_process_signals(None, trace)
    assert out["source"] == "trace", out
    assert "分析需求" in out["thinking_summary"], out
    joined = " ".join(out["tool_calls"])
    assert "http_get" in joined and "run_python" in joined, out
    assert "run_python" in out["retry_signal"], "run_python 2 次应进试错"
    assert "报错" in out["retry_signal"], "Traceback 应识别报错"
    print("OK trace tool_calls aggregated")


def test_both_empty():
    out = _extract_process_signals(None, None)
    assert out["source"] == "none", out
    assert out["thinking_summary"] == "" and out["tool_calls"] == [] and out["retry_signal"] == "", out
    assert out["tool_call_count"] == 0, out
    print("OK both empty")


def test_raw_message_preferred_over_trace():
    raw = json.dumps({"messages": [{"content": [{"type": "reasoning", "text": "来自raw"}]}]}, ensure_ascii=False)
    trace = {"thinking": "来自trace"}
    out = _extract_process_signals(raw, trace)
    assert out["source"] == "raw_message" and "来自raw" in out["thinking_summary"], out
    print("OK raw_message preferred")


def test_dirty_data_tolerant():
    trace = {"tool_calls": [None, "x", {"foo": "bar"}, {"name": "ok"}]}
    out = _extract_process_signals(None, trace)
    assert out["source"] == "trace", out
    assert "ok" in " ".join(out["tool_calls"]), out
    print("OK dirty data tolerant")


def main():
    test_raw_message_reasoning_and_tools()
    test_raw_message_non_json_degrades()
    test_trace_tool_calls_aggregated()
    test_both_empty()
    test_raw_message_preferred_over_trace()
    test_dirty_data_tolerant()
    print("\n[PASS] _extract_process_signals 全部通过")


if __name__ == "__main__":
    main()
