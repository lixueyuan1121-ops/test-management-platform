"""文件传输/路径/编码证据从 trace 到判定与综合评价的回归测试。"""
from app.services.claude_runner import (
    _extract_process_signals, build_eval_task_summary_prompt, build_eval_judge_prompt,
)


def main():
    trace = {"tool_calls": [
        {"name": "read_file", "args": {"path": "/uploads/报告.csv"}, "result_text": "已读取", "reached_result": True},
        {"name": "upload_file", "is_mcp": True, "mcp_server": "remote", "args": {"path": "/uploads/报告.csv"}, "result_text": "file_id=f123", "reached_result": True},
        {"name": "bash", "args": {"command": "cat /tmp/报告.csv"}, "result_text": "No such file or directory", "reached_result": True},
        {"name": "bash", "args": {"command": "cat /uploads/报告.csv"}, "result_text": "UnicodeDecodeError: invalid start byte", "reached_result": True},
        {"name": "read_csv", "args": {"path": "/uploads/报告.csv", "encoding": "gbk"}, "result_text": "读取成功:20行", "reached_result": True},
    ]}
    proc = _extract_process_signals(None, trace)
    report = build_eval_task_summary_prompt("文件任务", "", [{
        "title": "读取本地报告", "engine": "纳米Work", "verdict": "pass", "score": 5,
        "answer": "完成", "process": proc, "attachments": [{"name": "报告.csv"}],
    }])
    for evidence in ("/uploads/报告.csv", "file_id=f123", "No such file or directory",
                     "UnicodeDecodeError", "gbk", "读取成功:20行", "步骤5", "MCP:remote"):
        assert evidence in report, evidence
    assert report.index("步骤3") < report.index("步骤4") < report.index("步骤5")
    assert "请求附件(仅证明任务配置,不证明已上传成功)" in report
    assert "最终通过的用例也要记录过程问题" in report
    judge = build_eval_judge_prompt(trace, "读取报告")
    assert "/tmp/报告.csv" in judge and "gbk" in judge
    # 工具返回很长时,中间的错误仍有证据,正常重复调用不被提取器写成根因。
    long_trace = {"tool_calls": [{"name": "read", "result_text": "a" * 2000 + "ENOENT missing path" + "z" * 2000}]}
    assert "ENOENT missing path" in str(_extract_process_signals(None, long_trace)["tool_evidence"])
    assert _extract_process_signals(None, None).get("tool_evidence", []) == []
    assert _extract_process_signals('not json', trace)["tool_evidence"]
    print("PASS 文件流转/路径/编码证据保留,判定与综合评价全链路")


if __name__ == "__main__":
    main()
