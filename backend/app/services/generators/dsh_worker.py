#!/usr/bin/env python3
"""dsh 子进程 worker —— 在【独立 venv】里运行,由后端 deepseek_runner 以子进程方式调用。

隔离要点(B-1 方案):
- 本脚本**只依赖 deepseek_harness**(dsh SDK),**不 import 任何平台代码**,
  因此可在一个只装了 deepseek-harness-sdk 的独立 venv 里独立运行,
  与后端主环境(fastapi/sqlalchemy/pydantic 等)彻底隔离——互不影响版本。
- 通信协议:stdin 读一行 JSON 入参;stdout 按**行分隔 JSON**输出事件流:
    {"type":"delta","text":...}      每个已提交的 assistant 文本块(块级,非 token 级)
    {"type":"result","text":...,"output_tokens":N,"finish_reason":...}  最终结果
    {"type":"error","msg":...}       失败
  stderr 仅用于本进程日志(后端会读取以便排查),不参与协议。

入参(stdin 一行 JSON):
  {prompt, model, max_tokens, api_key?, base_url?, session_root?, session_id}

注意:prompt 由后端(主环境)用平台统一的 build_testcase_prompt 构造好后传入,
本 worker 不做 prompt 拼装,保证两引擎用同一 prompt。
"""
import json
import sys


def _emit(obj: dict) -> None:
    """输出一行 JSON 事件到 stdout 并立即 flush(保证后端实时读到)。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _extract_text(payload) -> str:
    """从 notification payload 里尽力抽出 assistant 文本(兼容多种结构)。"""
    if not payload:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("text"), str):
            return payload["text"]
        if isinstance(payload.get("delta"), str):
            return payload["delta"]
        content = payload.get("content")
        if isinstance(content, list):
            return "".join(b.get("text", "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text")
        chunk = payload.get("chunk")
        if isinstance(chunk, dict):
            return _extract_text(chunk)
    return ""


def _extract_output_tokens(result) -> int | None:
    """从 RunResult.events 里找 usage.outputTokens(拿不到返回 None)。"""
    events = getattr(result, "events", None) or []
    total = None
    for ev in events:
        if isinstance(ev, dict):
            data = ev.get("data") or {}
            chunk = data.get("chunk") if isinstance(data, dict) else None
            if isinstance(chunk, dict) and chunk.get("type") == "usage":
                u = chunk.get("usage")
                if isinstance(u, dict) and isinstance(u.get("outputTokens"), int):
                    total = (total or 0) + u["outputTokens"]
    return total


def main() -> int:
    raw = sys.stdin.readline()
    try:
        args = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        _emit({"type": "error", "msg": f"worker 入参解析失败:{e}"})
        return 1

    prompt = args.get("prompt") or ""
    if not prompt.strip():
        _emit({"type": "error", "msg": "空 prompt"})
        return 1

    try:
        from deepseek_harness import DeepSeekHarness
    except Exception as e:  # noqa: BLE001
        _emit({"type": "error", "msg": f"dsh SDK 不可用(独立 venv 未装 deepseek-harness-sdk?):{e}"})
        return 1

    chunk_texts: list[str] = []

    def on_notification(note):
        try:
            payload = getattr(note, "payload", None)
            if payload is None and isinstance(note, dict):
                payload = note.get("payload")
        except Exception:  # noqa: BLE001
            payload = None
        text = _extract_text(payload)
        if text:
            chunk_texts.append(text)
            _emit({"type": "delta", "text": text})

    kwargs = dict(
        provider="deepseek-official",
        model=args.get("model") or "deepseek-v4-flash",
        max_tokens=int(args.get("max_tokens") or 49152),
    )
    if args.get("api_key"):
        kwargs["api_key"] = args["api_key"]
    if args.get("base_url"):
        kwargs["base_url"] = args["base_url"]
    if args.get("session_root"):
        kwargs["session_root"] = args["session_root"]

    try:
        with DeepSeekHarness(**kwargs) as h:
            result = h.run(prompt, session_id=args.get("session_id"), on_notification=on_notification)
        raw_text = getattr(result, "final_response", "") or "".join(chunk_texts)
        finish = getattr(result, "finish_reason", None)
        if finish == "error":
            _emit({"type": "error", "msg": "dsh 引擎返回 error(详见 worker stderr / dsh session 日志)"})
            return 1
        if finish == "max-tokens" and not raw_text.strip():
            _emit({"type": "error", "msg": "dsh 生成被 max-tokens 截断且无正文,请调大 DEEPSEEK_MAX_TOKENS"})
            return 1
        _emit({
            "type": "result",
            "text": raw_text,
            "output_tokens": _extract_output_tokens(result),
            "finish_reason": finish,
        })
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        sys.stderr.write(traceback.format_exc())
        _emit({"type": "error", "msg": f"dsh 生成异常:{e}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
