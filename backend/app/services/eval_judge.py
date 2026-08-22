"""对话测评判定:读 eval_run 的会话轨迹(trace 文件)+ 期望,调大模型判三维,落库。

复用生成引擎(claude/deepseek)的 stream_generate,累积文本后 parse。判定是平台侧动作。
trace 存磁盘(uploads/eval_traces/{...}.json,子项2),按 run.trace URL 反解路径读。
"""
import json
import logging
import os

from sqlalchemy.orm import Session

from app.core.enums import EvalRunStatus, EvalVerdict
from app.models import EvalQuery, EvalRun
from app.services import claude_runner, generators

logger = logging.getLogger("test_platform")

_UPLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "uploads")
_UPLOADS_DIR = os.path.abspath(_UPLOADS_DIR)


def _load_trace(run: EvalRun) -> dict:
    """按 run.trace(形如 /uploads/eval_traces/xxx.json)反解磁盘路径读 JSON。
    读不到 → 用 run.answer 兜底的空壳(降级判定)。"""
    url = run.trace or ""
    fallback = {"thinking": "", "tool_calls": [], "artifacts": [],
                "answer": run.answer or "", "ws_captured": False}
    if not url.startswith("/uploads/"):
        return fallback
    rel = url[len("/uploads/"):]
    path = os.path.join(_UPLOADS_DIR, rel)
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else fallback
    except (OSError, json.JSONDecodeError, ValueError):
        logger.warning("判定读 trace 失败:%s", path)
        return fallback


def judge_run(db: Session, run: EvalRun, provider: str | None = None) -> dict:
    """判定一条 eval_run:读 trace+expected → 引擎 → 三维 → 落库。返回判定结果 dict。"""
    expected = ""
    if run.eval_query_id:
        q = db.get(EvalQuery, run.eval_query_id)
        if q:
            expected = q.expected or ""
    trace = _load_trace(run)

    provider_id = generators.normalize_provider(provider)
    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        run.verdict_reason = f"判定引擎「{provider_id}」不可用"
        db.commit()
        return {"error": run.verdict_reason}

    run.status = EvalRunStatus.judging
    db.commit()

    raw = ""
    err = None
    try:
        for evt in engine.stream_generate(
            expected or "判定",
            prompt_builder=lambda: claude_runner.build_eval_judge_prompt(trace, expected, None),
            system_prompt=claude_runner.EVAL_JUDGE_SYSTEM_PROMPT,
        ):
            et = evt.get("type")
            if et == "delta":
                raw += evt["text"]
            elif et == "result":
                if evt.get("text"):
                    raw = evt["text"]
            elif et == "error":
                err = evt.get("msg")
    except Exception as e:  # noqa: BLE001
        logger.exception("判定引擎调用异常")
        err = str(e)

    dims = claude_runner.parse_eval_verdict(raw)
    parse_error = dims.get("error")

    if err or parse_error:
        # 判定失败:不进 judged(保持 done 可重判),记原因
        run.status = EvalRunStatus.done
        run.verdict = EvalVerdict.error.value
        run.verdict_reason = (err or "判定输出无法解析")[:2000]
        run.judged_by = provider_id
        db.commit()
        return {"verdict": "error", "reason": run.verdict_reason}

    # 三维任一 fail → fail;有 None(未判维)按合理性:只要有明确 false 即 fail;全 true 才 pass
    passes = [dims[k]["pass"] for k in ("thinking_complete", "tools_ok", "artifact_expected")]
    if any(p is False for p in passes):
        verdict = EvalVerdict.failed.value  # "fail"
    elif all(p is True for p in passes):
        verdict = EvalVerdict.passed.value  # "pass"
    else:
        # 有 None(未给判定)但无明确 false:标 error 供复核,不误判 pass
        verdict = EvalVerdict.error.value

    run.verdict = verdict
    run.verdict_dims = json.dumps(dims, ensure_ascii=False)
    run.verdict_reason = dims.get("summary") or ""
    run.judged_by = provider_id
    run.is_abnormal = (verdict == EvalVerdict.failed.value)
    run.status = EvalRunStatus.judged
    db.commit()
    return {"verdict": verdict, "verdict_dims": dims,
            "is_abnormal": run.is_abnormal, "judged_by": provider_id}
