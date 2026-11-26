"""对话测评判定:读 eval_run 的会话轨迹(trace 文件)+ 期望,调大模型判三维,落库。

复用生成引擎(claude/deepseek)的 stream_generate,累积文本后 parse。判定是平台侧动作。
trace 存磁盘(uploads/eval_traces/{...}.json,子项2),按 run.trace URL 反解路径读。
"""
import json
import copy
import logging
import os
import random
import re
import time

from sqlalchemy.orm import Session

from app.core.enums import EvalRunStatus, EvalVerdict
from app.models import EvalQuery, EvalRun
from app.services import claude_runner, generators
from app.services import eval_judgment_store as judgment_store

logger = logging.getLogger("test_platform")

_UPLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "uploads")
_UPLOADS_DIR = os.path.abspath(_UPLOADS_DIR)
_EMPTY_OUTPUT = "判定服务异常：评委返回空正文，可重判。"


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
        if not isinstance(obj, dict):
            return fallback
        # DOM 回答独立于 WS 回填；空 trace 不能遮蔽已经落库的回答。
        if not str(obj.get("answer") or "").strip():
            obj["answer"] = run.answer or ""
            obj["answer_source"] = "run.answer"
        else:
            obj["answer_source"] = "trace"
        return obj
    except (OSError, json.JSONDecodeError, ValueError):
        logger.warning("判定读 trace 失败:%s", path)
        return fallback


def _judge_once(engine, trace: dict, expected: str, dimension: str | None) -> tuple[dict, str | None]:
    """单次判定:调引擎累积输出并解析。返回 (dims, err);err 非空或 dims 带 error 即本次失败。"""
    raw = ""
    err = None
    completed = False
    diagnostic = {"request_id": None, "stop_reason": None, "text_events": 0}
    try:
        for evt in engine.stream_generate(
            expected or "判定",
            prompt_builder=lambda: trace.get("_judge_prompt") or claude_runner.build_eval_judge_prompt(trace, expected, dimension),
            system_prompt=claude_runner.EVAL_JUDGE_SYSTEM_PROMPT,
            **({"images": trace["_judge_images"]} if trace.get("_judge_images") else {}),
        ):
            et = evt.get("type")
            if et == "delta":
                raw += evt["text"]
                diagnostic["text_events"] += 1
            if et in ("result", "error"):
                for key in diagnostic:
                    if evt.get(key) is not None:
                        diagnostic[key] = evt[key]
                if not diagnostic["stop_reason"]:
                    diagnostic["stop_reason"] = evt.get("finish_reason")
            if et == "result":
                if evt.get("is_error"):
                    # CLI 的错误 result.text 是报错文案，不是模型生成的判定正文。
                    err = evt.get("error") or evt.get("text") or "模型服务返回错误"
                    continue
                completed = True
                if evt.get("text") and (evt["text"].strip() or not raw.strip()):
                    raw = evt["text"]
                if evt.get("complete") is False or diagnostic["stop_reason"] in ("length", "max_tokens", "refusal", "content_filter"):
                    err = f"判定服务未完整返回结果（{diagnostic['stop_reason'] or '连接中断'}），可重判。"
            elif et == "error":
                err = _EMPTY_OUTPUT if evt.get("error_code") == "empty_output" else evt.get("msg") or "模型服务返回错误"
    except Exception as e:  # noqa: BLE001
        logger.exception("判定引擎调用异常")
        err = str(e)
    dims = {}
    if not err:
        if raw.strip():
            dims = claude_runner.parse_eval_verdict(raw)
            if dims.get("error"):
                err = "判定输出无法解析"
        else:
            err = _EMPTY_OUTPUT if completed else "判定服务未返回完整结果，可重判。"
    if not err:
        continuity = (trace.get("evaluation_context") or {}).get("continuity")
        if dimension == "multi_turn" and continuity == "first":
            dims["dimension_ok"] = {"pass": None, "applicable": False, "note": "首轮没有前文，上下文延续不适用。"}
        elif dimension == "multi_turn" and continuity == "follow_up":
            dims.setdefault("dimension_ok", {"pass": None, "note": "判定未给出上下文延续结论。"})
        _guard_missing_evidence(dims, trace)
    dims["_raw_output"] = raw
    dims["_attempt"] = {**diagnostic, "text_chars": len(raw), "error": err}
    return dims, err


def _guard_missing_evidence(dims: dict, trace: dict) -> None:
    """缺过程记录时，失败结论须有输入中的直接引文；缺记录本身不是失败证据。"""
    if trace.get("thinking") and trace.get("tool_calls"):
        return
    sources = {
        "answer": str(trace.get("answer") or ""),
        "thinking": str(trace.get("thinking") or ""),
        "tool_calls": json.dumps(trace.get("tool_calls") or [], ensure_ascii=False),
        "artifacts": json.dumps(trace.get("artifacts") or [], ensure_ascii=False),
    }
    changed = False
    for key in (*claude_runner._JUDGE_DIM_KEYS, "dimension_ok"):
        dim = dims.get(key)
        if not isinstance(dim, dict) or dim.get("pass") is not False:
            continue
        source = dim.get("evidence_source")
        quote = str(dim.get("evidence_quote") or "").strip()
        if not quote or source not in sources or not trace.get(source) or quote not in sources[source]:
            dim["pass"] = None
            dim["note"] = "证据不足：过程记录缺失，且失败结论未引用可核对的实际证据；需补齐轨迹或人工复核。"
            changed = True
    if changed:
        dims["summary"] = "部分维度证据不足，不能将未捕获思考、工具或产物记录认定为任务未执行；请结合已有回答补充核验。"


def _judge_with_retry(engine, trace, expected, dimension):
    """同一票的空正文可重试一次；其他临时错误继续使用配置的重试预算。"""
    from app.core.config import settings
    failures = []
    attempts = []
    retried_empty = False
    retries = max(0, min(2, settings.EVAL_JUDGE_TRANSIENT_RETRIES))
    while True:
        dims, error = _judge_once(engine, trace, expected, dimension)
        dims = dims or {}
        attempts.append(dims.pop("_attempt", {"error": error}))
        api_error = error and re.search(r"\bapi\s*error\b", error, re.I)
        transient = error and re.search(r"\b(?:429|502|503|529)\b|rate.?limit|overloaded|限流", error, re.I)
        retryable = api_error or (transient and not dims.get("_raw_output")
            and "已重试多次" not in error and "超时" not in error and "timeout" not in error.lower())
        if error == _EMPTY_OUTPUT and not retried_empty:
            retried_empty = True
        elif retryable and retries > 0:
            retries -= 1
        else:
            dims["_attempts"] = attempts
            if failures:
                dims["_transient_failures"] = failures
            return dims, error
        failures.append(error)
        delay = 5 * (2 ** (len(failures) - 1)) + random.uniform(0, 1)
        logger.warning("判定模型调用失败，%.1fs 后重试当前票（第 %d 次调用）: %s", delay, len(attempts) + 1, error)
        time.sleep(delay)


def _verdict_of(dims: dict) -> str:
    """由多维结论推导单次总判定(pass/fail/error),与原单票口径一致:
    任一明确 false → fail;核心三维及已提供主考维全 true → pass;有 None 未判 → error。"""
    passes = [dims[k]["pass"] for k in ("thinking_complete", "tools_ok", "artifact_expected")]
    opt = dims.get("dimension_ok")
    if isinstance(opt, dict) and opt.get("applicable") is not False:
        passes.append(opt.get("pass"))
    if any(p is False for p in passes):
        return EvalVerdict.failed.value
    if all(p is True for p in passes):
        return EvalVerdict.passed.value
    return EvalVerdict.error.value


def recover_interrupted_judgments(db: Session, run_id: int | None = None, reason: str = "服务重启中断判定，请重新判定") -> int:
    """Startup-only sweep, or targeted recovery after a failed invocation."""
    q = db.query(EvalRun).filter(EvalRun.status == EvalRunStatus.judging)
    if run_id is not None:
        q = q.filter(EvalRun.id == run_id)
    rows = q.with_for_update().all()
    for run in rows:
        run.status = EvalRunStatus.done
        run.verdict = EvalVerdict.error.value
        run.verdict_reason = reason[:2000]
        run.score = run.verdict_dims = None
        run.review_mark = run.review_note = None
        run.is_abnormal = False
        judgment_store.complete(db, run)
    db.commit()
    return len(rows)


def judge_run(db: Session, run: EvalRun, provider: str | None = None, votes: int = 1) -> dict:
    run_id = run.id
    started = time.monotonic()
    logger.info("判定开始 run_id=%s provider=%s", run_id, provider)
    try:
        if isinstance(run, EvalRun):
            # 排队期间可能已重试；先锁定终态结果，避免旧判定把 pending 覆盖回 done。
            run = db.query(EvalRun).filter_by(id=run_id).with_for_update().populate_existing().first()
            if run is None or run.status not in (EvalRunStatus.done, EvalRunStatus.judged):
                db.rollback()
                return {"skipped": True, "reason": "执行状态已变化或正在判定，请完成后再判定"}
            judgment_store.begin(db, run, generators.normalize_provider(provider))
            run.status = EvalRunStatus.judging
            db.commit()
        result = _judge_run(db, run, provider=provider, votes=votes)
        logger.info("判定结束 run_id=%s verdict=%s elapsed=%.1fs", run_id, result.get("verdict"), time.monotonic() - started)
        return result
    except Exception:
        logger.exception("判定异常 run_id=%s elapsed=%.1fs", run_id, time.monotonic() - started)
        # Roll back before opening a fresh session: the original connection may be broken.
        bind = db.get_bind()
        try:
            db.rollback()
        except Exception:
            logger.warning("判定旧连接回滚失败 run_id=%s", run_id, exc_info=True)
        try:
            with Session(bind=bind) as recovery:
                recover_interrupted_judgments(recovery, run_id, "平台判定异常，未得出新结论，请重新判定；详情见服务日志")
        except Exception:
            logger.exception("判定状态恢复失败 run_id=%s", run_id)
        raise


def _judge_run(db: Session, run: EvalRun, provider: str | None = None, votes: int = 1) -> dict:
    """判定一条 eval_run:读 trace+expected+主考维度 → 引擎 → 多维 → 落库。返回判定结果 dict。

    votes>1 = 稳健判定(主流 multi-judge 多数决):独立判 N 次,pass/fail 按多数票定结论
    (平票/全 error → error 供复核,不猜);score 取有效均值。代价是 N 倍引擎调用时长,默认 1。
    """
    votes = max(1, min(5, int(votes or 1)))
    from app.services.eval_snapshot import JUDGE_RULES_VERSION, rubric_of, context_of
    expected, dimension = rubric_of(db, run)
    trace = _load_trace(run)
    trace.pop('_judge_images', None)  # visual input must come from verified uploads, never trace-supplied base64
    trace["evaluation_context"] = context_of(db, run)
    missing_history = trace["evaluation_context"].get("continuity") == "missing_history"
    from app.services.eval_artifacts import verify_run
    verification = verify_run(db, run) if isinstance(run, EvalRun) else None
    if verification:
        trace["artifact_verification"] = verification
        trace["artifacts"] = [*(trace.get("artifacts") or []), {"verification": verification}]

    provider_id = generators.normalize_provider(provider)
    engine = None
    if isinstance(run, EvalRun):
        from app.services.eval_artifacts import image_evidence
        images, visual = image_evidence(db, run)
        if visual and not missing_history:
            engine = generators.get_provider(provider_id)
            can_see = provider_id == 'claude' or getattr(engine, 'supports_images', lambda: False)()
            if images and can_see:
                trace['_judge_images'] = images
            for item in visual:
                item['sent_to_model'] = bool(item.get('status') == 'ready' and can_see)
                if item.get('status') == 'ready' and not can_see:
                    item['reason'] = '当前判定引擎未配置视觉能力；只有尺寸证据，不能判断画面内容'
            trace['artifacts'] = [*(trace.get('artifacts') or []), {'image_evidence': visual}]
    from app.core.config import settings
    requested_model = settings.DEEPSEEK_MODEL if provider_id == 'deepseek' else settings.AI_MODEL
    if provider_id == 'deepseek' and trace.get('_judge_images'):
        requested_model = settings.DEEPSEEK_VISION_MODEL
    elif provider_id == 'anthropic_http':
        from app.services.generators.anthropic_http_runner import _model
        requested_model = _model()
    trace["_judge_prompt"] = claude_runner.build_eval_judge_prompt(trace, expected, dimension)
    judgment_store.set_input(db, run, trace["_judge_prompt"],
        claude_runner.EVAL_JUDGE_SYSTEM_PROMPT, {"provider": provider_id, "votes": votes,
        "trace_url": getattr(run, "trace", None), "rules_version": JUDGE_RULES_VERSION,
        "requested_model": requested_model or None, "observed_model": None,
        "execution_config": trace.get("execution_config"), "runtime": trace.get("runtime"),
        "input_files": trace.get("input_files")})
    # 未回填快速失败:执行机没回写任何东西(无轨迹、无回答、无思考)时没有可判定的素材——
    # 直接标 error 不调引擎,免得空壳 run 白耗几十秒 LLM、拖垮批量判定(前端同步等待会超时)。
    has_material = bool(
        trace.get("tool_calls") or trace.get("artifacts")
        or str(trace.get("answer") or "").strip() or str(trace.get("thinking") or "").strip()
    )
    if missing_history or not has_material:
        run.verdict = EvalVerdict.error.value
        run.verdict_reason = ("判定输入缺失：无法取得本次多轮会话完整且唯一的前序问答，请核对分组、轮次和执行记录后重判。"
                              if missing_history else "证据不足：无可用回答、思考、工具或产物记录；不能据此判任务失败，请补齐证据后重判。")
        run.score = None
        details = {"judge_error": "missing_context" if missing_history else "insufficient_evidence"}
        if verification:
            details["artifact_verification"] = verification
        run.verdict_dims = json.dumps(details, ensure_ascii=False)
        run.is_abnormal = False
        run.status = EvalRunStatus.done
        run.judged_by = provider_id
        judgment_store.complete(db, run, [])
        return {"verdict": "error", "reason": run.verdict_reason}

    engine = engine or generators.get_provider(provider_id)
    if not engine.is_available():
        # 引擎不可用(平台 AI 禁用/claude 缺失):镜像判定失败分支标 verdict=error,
        # 让前端走 error 分支露出真因(而非 verdict=null 的假成功)且可重判;保持 status 不变(done)。
        run.verdict = EvalVerdict.error.value
        run.verdict_reason = f"判定引擎「{provider_id}」不可用"
        run.score = None
        run.verdict_dims = json.dumps({"judge_error": "provider_error"})
        run.is_abnormal = False
        run.status = EvalRunStatus.done
        run.judged_by = provider_id
        judgment_store.complete(db, run, [])
        return {"verdict": "error", "reason": run.verdict_reason}

    run.status = EvalRunStatus.judging
    db.commit()

    # N 次独立判定收集票(单次失败计 error 票不断批)
    ballots: list[tuple[str, dict]] = []   # (verdict, dims);判定失败的票 dims 为 None
    audit_ballots = []
    fail_reasons: list[str] = []
    for _ in range(votes):
        dims_i, err_i = _judge_with_retry(engine, trace, expected, dimension)
        if not err_i and verification and verification["status"] != "pass":
            failed = verification["status"] == "fail"
            evidence = next(c for c in verification["checks"] if c["status"] == ("fail" if failed else "unknown"))
            dims_i["artifact_expected"] = {"pass": False if failed else None,
                "note": evidence["reason"], "evidence_source": "artifacts", "evidence_quote": evidence["reason"]}
            dims_i["artifact_verification"] = verification
            if failed:
                dims_i["score"] = min(dims_i.get("score") or 2, 2)
            dims_i["summary"] = f"产物核验{'未通过' if failed else '证据不足'}：{evidence['reason']}；" + (dims_i.get("summary") or "")
        elif not err_i and verification:
            dims_i["artifact_verification"] = verification
        raw_i = dims_i.pop("_raw_output", None) if isinstance(dims_i, dict) else None
        transient_failures = dims_i.pop("_transient_failures", [])
        attempts = dims_i.pop("_attempts", [])
        audit_ballots.append({"dims": copy.deepcopy(dims_i), "error": err_i, "raw_output": raw_i,
                              "transient_failures": transient_failures, "attempts": attempts})
        if err_i:
            ballots.append((EvalVerdict.error.value, None))
            fail_reasons.append(err_i)
        else:
            ballots.append((_verdict_of(dims_i), dims_i))

    valid = [(v, d) for v, d in ballots if d is not None]
    if not valid:
        # 全部失败:不进 judged(保持 done 可重判),记原因
        run.status = EvalRunStatus.done
        run.verdict = EvalVerdict.error.value
        run.verdict_reason = (fail_reasons[0] if fail_reasons else "判定失败")[:2000]
        run.score = None
        code = "empty_output" if all(e == _EMPTY_OUTPUT for e in fail_reasons) else "provider_error"
        run.verdict_dims = json.dumps({"judge_error": code})
        run.is_abnormal = False
        run.judged_by = provider_id
        judgment_store.complete(db, run, audit_ballots)
        return {"verdict": "error", "reason": run.verdict_reason}

    n_pass = sum(1 for v, _ in valid if v == EvalVerdict.passed.value)
    n_fail = sum(1 for v, _ in valid if v == EvalVerdict.failed.value)
    if n_pass > len(ballots) / 2:
        verdict = EvalVerdict.passed.value
    elif n_fail > len(ballots) / 2:
        verdict = EvalVerdict.failed.value
    else:
        # 平票(含全 error 票):标 error 供复核,不猜
        verdict = EvalVerdict.error.value

    # dims 取与最终结论一致的最后一票(error 结论时取最后一张有效票);score 取有效均值
    dims = next((d for v, d in reversed(valid) if v == verdict), valid[-1][1])
    scores = [d.get("score") for v, d in valid
              if v != EvalVerdict.error.value and isinstance(d.get("score"), int)]
    score = round(sum(scores) / len(scores)) if scores else dims.get("score")
    if verdict == EvalVerdict.error.value:
        score = None
        dims["score"] = None
        if len(valid) == len(ballots) and not n_pass and not n_fail:
            dims["judge_error"] = "insufficient_evidence"
    reason = dims.get("summary") or ""
    if verdict == EvalVerdict.error.value:
        reason = "无法定论（证据不足或有效票未过半）：" + reason
    if votes > 1:
        reason = f"[{len(valid)}票:{n_pass}过/{n_fail}不过] {reason}"

    run.verdict = verdict
    run.score = score
    run.verdict_dims = json.dumps(dims, ensure_ascii=False)
    run.verdict_reason = reason
    run.judged_by = provider_id if votes == 1 else f"{provider_id}x{votes}"
    run.is_abnormal = (verdict == EvalVerdict.failed.value)
    run.status = EvalRunStatus.judged
    judgment_store.complete(db, run, audit_ballots)
    return {"verdict": verdict, "verdict_dims": dims, "score": run.score,
            "is_abnormal": run.is_abnormal, "judged_by": run.judged_by}


def run_judge_job(db: Session, job) -> dict:
    """AI 任务队列的判定 handler(方案2 P2):判定单条 eval_run。

    job.input = {"run_id": int, "provider"?: str, "votes"?: int}。复用 judge_run(它自身落库
    verdict/score/status)。返回 run_out 形状,供前端轮询后原地更新行(status/verdict/dims/reason)。
    """
    inp = json.loads(job.input or "{}")
    run_id = inp.get("run_id")
    if not run_id:
        raise ValueError("判定 job 缺少 run_id")
    run = db.get(EvalRun, run_id)
    if run is None:
        raise ValueError(f"执行项不存在:{run_id}")
    if inp.get("pipeline_task_id") is not None:
        from app.models import EvalTask
        task = db.get(EvalTask, inp["pipeline_task_id"])
        if not task or task.last_batch_id != inp.get("pipeline_batch_id"):
            return {"run_id": run_id, "skipped": True, "reason": "测评任务已切换批次",
                    "error": "测评任务已切换批次，跳过旧批判定"}
    result = judge_run(db, run, provider=inp.get("provider"), votes=inp.get("votes") or 1)
    if result.get("skipped"):
        return {"run_id": run_id, **result, "error": result.get("reason") or "判定已跳过"}
    db.refresh(run)
    return {
        "run_id": run.id,
        "status": getattr(run.status, "value", run.status),
        "verdict": run.verdict,
        "score": run.score,
        "verdict_dims": json.loads(run.verdict_dims) if run.verdict_dims else None,
        "verdict_reason": run.verdict_reason,
        "is_abnormal": bool(run.is_abnormal),
        "judged_by": run.judged_by, "judgment_id": run.judgment_id,
    }


# 注册为队列 handler(ai_jobs 惰性 import 本模块时触发)
from app.services import ai_jobs as _ai_jobs  # noqa: E402
_ai_jobs.register_handler("eval_judge", run_judge_job)
