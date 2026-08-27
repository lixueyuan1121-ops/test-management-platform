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
    # 路径遍历防护(纵深):拒绝 ..、绝对路径、盘符,内部来源亦视为遍历/IDOR 目标
    if ".." in rel or rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        logger.warning("判定读 trace 拒绝可疑路径:%s", url)
        return fallback
    path = os.path.realpath(os.path.join(_UPLOADS_DIR, rel))
    base = os.path.realpath(_UPLOADS_DIR)
    # realpath 容器校验:解析后必须仍落在 uploads 目录内(挡符号链接/绕过)
    if not (path == base or path.startswith(base + os.sep)):
        logger.warning("判定读 trace 越界拒绝:%s", url)
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else fallback
    except (OSError, json.JSONDecodeError, ValueError):
        logger.warning("判定读 trace 失败:%s", path)
        return fallback


def judge_run(db: Session, run: EvalRun, provider: str | None = None) -> dict:
    """判定一条 eval_run:读 trace+expected+主考维度 → 引擎 → 多维 → 落库。返回判定结果 dict。"""
    expected = ""
    dimension = None
    if run.eval_query_id:
        q = db.get(EvalQuery, run.eval_query_id)
        if q:
            expected = q.expected or ""
            dimension = q.dimension
    trace = _load_trace(run)

    provider_id = generators.normalize_provider(provider)
    # 未回填快速失败:执行机没回写任何东西(无轨迹、无回答、无思考)时没有可判定的素材——
    # 直接标 error 不调引擎,免得空壳 run 白耗几十秒 LLM、拖垮批量判定(前端同步等待会超时)。
    has_material = bool(
        (run.answer or "").strip() or trace.get("ws_captured") or trace.get("tool_calls")
        or str(trace.get("answer") or "").strip() or str(trace.get("thinking") or "").strip()
    )
    if not has_material:
        run.verdict = EvalVerdict.error.value
        run.verdict_reason = "会话未正常回填(无轨迹与回答),无可判定内容;请重跑该用例后再判定"
        run.judged_by = provider_id
        db.commit()
        return {"verdict": "error", "reason": run.verdict_reason}

    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        # 引擎不可用(平台 AI 禁用/claude 缺失):镜像判定失败分支标 verdict=error,
        # 让前端走 error 分支露出真因(而非 verdict=null 的假成功)且可重判;保持 status 不变(done)。
        run.verdict = EvalVerdict.error.value
        run.verdict_reason = f"判定引擎「{provider_id}」不可用"
        run.judged_by = provider_id
        db.commit()
        return {"verdict": "error", "reason": run.verdict_reason}

    run.status = EvalRunStatus.judging
    db.commit()

    raw = ""
    err = None
    try:
        for evt in engine.stream_generate(
            expected or "判定",
            prompt_builder=lambda: claude_runner.build_eval_judge_prompt(trace, expected, dimension),
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

    # 组合判定:核心三维 + 可选主考维(dimension_ok,判定 prompt 注入了主考维度时才有)。
    # 任一明确 false → fail;核心三维全 true(且主考维不为 false)→ pass;
    # 核心维有 None(未判)且无明确 false → error 供复核,不误判 pass。
    passes = [dims[k]["pass"] for k in ("thinking_complete", "tools_ok", "artifact_expected")]
    opt = dims.get("dimension_ok")
    opt_pass = opt.get("pass") if isinstance(opt, dict) else None
    if any(p is False for p in passes) or opt_pass is False:
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
