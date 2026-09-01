"""AI 失败归因（exec 执行失败的根因分类,建议项⑨）。

对标 ReportPortal 的失败自动分类(Product Bug/Automation Bug/System Issue/No Defect),
映射到本仓库口径四类:
  selector    选择器/定位问题(元素找不到/页面结构变化)  → 建议补选择器
  environment 环境问题(网络/超时/登录态/服务不可用/偶发抖动) → 建议重跑/查环境
  assertion   用例问题(断言或预期过时/文案变更/步骤与产品现状不符) → 建议改用例
  bug         产品缺陷(功能行为确实不符合合理预期) → 建议提缺陷

复用生成引擎抽象(claude/deepseek 的 stream_generate,与 eval_judge 同款):AI 读
用例快照+失败原因+逐步执行报告(每步断言的期望 vs 实际),输出结构化 JSON 归因。
人工触发(执行结果页按钮),结果落 exec_run.triage_kind/triage 供筛选与展示;
归因是参考不是裁决——纠偏权仍在人(correct_verdict)。
"""
import json
import logging
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.services import generators

logger = logging.getLogger("test_platform")

TRIAGE_KINDS = ("selector", "environment", "assertion", "bug")

_SYSTEM_PROMPT = (
    "你是资深测试工程师,负责给 UI/接口自动化回归的失败做根因归类。"
    "只输出一个 JSON 对象,不要输出任何其它文字。"
)


def build_triage_prompt(payload: dict, reason: str | None, fail_kind: str | None,
                        report: list | None) -> str:
    """组装归因 prompt:用例快照 + 失败原因 + 逐步报告摘要(控制长度防超 token)。"""
    lines = [
        "对下面这次自动化执行失败做根因归类。",
        "",
        f"【用例】{payload.get('title') or '(无标题)'}",
    ]
    if payload.get("steps"):
        lines.append(f"【步骤】{str(payload['steps'])[:1500]}")
    if payload.get("expected"):
        lines.append(f"【预期】{str(payload['expected'])[:800]}")
    lines.append(f"【失败分类(执行机初判)】{fail_kind or '无'}")
    lines.append(f"【失败原因(执行机回写)】{(reason or '无')[:1200]}")
    if isinstance(report, list) and report:
        lines.append("【逐步执行报告】")
        for s in report[:20]:
            if not isinstance(s, dict):
                continue
            mark = "✓" if s.get("ok") else "✗"
            row = f"{mark} 步{s.get('no', '?')} {s.get('action', '')} {str(s.get('desc') or '')[:80]}"
            if s.get("error"):
                row += f" | 错误:{str(s['error'])[:200]}"
            ck = s.get("check")
            if isinstance(ck, dict):
                row += f" | 期望{'不' if ck.get('negate') else ''}含「{str(ck.get('expected'))[:80]}」实际「{str(ck.get('actual') or '(空)')[:120]}」"
            lines.append(row)
    lines += [
        "",
        "归类到且仅到以下四类之一:",
        "- selector: 选择器/定位问题——元素找不到、页面结构或标识变化导致定位失败",
        "- environment: 环境问题——网络/超时/登录态丢失/服务不可用/偶发抖动,重跑可能就过",
        "- assertion: 用例问题——断言或预期已过时(文案改版/流程变更),产品行为其实合理",
        "- bug: 产品缺陷——功能行为确实不符合合理预期,应提缺陷",
        "",
        '只输出 JSON:{"kind":"selector|environment|assertion|bug","confidence":0到1,'
        '"reason":"一两句归因依据","suggestion":"下一步建议(修用例/补选择器/重跑/提缺陷)"}',
    ]
    return "\n".join(lines)


def parse_triage(raw: str) -> dict:
    """从引擎输出提取归因 JSON(容忍代码围栏/前后杂文)。失败返回 {'error': ...}。"""
    if not raw or not raw.strip():
        return {"error": "引擎无输出"}
    text = re.sub(r"```(?:json)?", "", raw).strip("` \n")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {"error": f"输出中无 JSON(前 120 字:{text[:120]})"}
    try:
        obj = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return {"error": f"JSON 解析失败(前 120 字:{text[start:start + 120]})"}
    kind = str(obj.get("kind") or "").strip().lower()
    if kind not in TRIAGE_KINDS:
        return {"error": f"kind 非法:{kind or '(空)'}"}
    try:
        conf = max(0.0, min(1.0, float(obj.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    return {
        "kind": kind,
        "confidence": round(conf, 2),
        "reason": str(obj.get("reason") or "")[:1000],
        "suggestion": str(obj.get("suggestion") or "")[:500],
    }


def triage_run(db: Session, run, provider: str | None = None) -> dict:
    """归因一条失败/阻塞的 exec_run:组 prompt → 引擎 → 解析 → 落库。

    成功返回归因 dict(已落 run.triage_kind/run.triage);失败返回 {'error': ...}
    且**不覆盖**已有归因(可重试)。
    """
    provider_id = generators.normalize_provider(provider)
    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        return {"error": f"归因引擎「{provider_id}」未启用或不可用"}
    try:
        payload = json.loads(run.payload or "{}")
    except (json.JSONDecodeError, ValueError):
        payload = {}
    try:
        report = json.loads(run.report) if run.report else None
    except (json.JSONDecodeError, ValueError):
        report = None

    raw = ""
    err = None
    try:
        for evt in engine.stream_generate(
            payload.get("title") or "失败归因",
            prompt_builder=lambda: build_triage_prompt(payload, run.reason, run.fail_kind, report),
            system_prompt=_SYSTEM_PROMPT,
        ):
            et = evt.get("type")
            if et == "delta":
                raw += evt.get("text") or ""
            elif et == "result":
                if evt.get("text"):
                    raw = evt["text"]
            elif et == "error":
                err = evt.get("msg")
    except Exception as e:  # noqa: BLE001
        logger.exception("归因引擎调用异常")
        err = str(e)
    if err:
        return {"error": err[:500]}
    parsed = parse_triage(raw)
    if parsed.get("error"):
        return parsed
    parsed["provider"] = provider_id
    parsed["at"] = datetime.utcnow().isoformat()
    run.triage_kind = parsed["kind"]
    run.triage = json.dumps(parsed, ensure_ascii=False)
    db.commit()
    return parsed


def run_triage_job(db: Session, job) -> dict:
    """AI 任务队列的归因 handler(方案2):读 job 入参 → 引擎 → 解析 → 写域表 → 返回 result。

    job.input = {"run_id": int, "provider"?: str}。解析失败抛异常(由 run_job 置 job failed,
    不覆盖已有归因);域写入只在解析成功、引擎无 error 时发生——失败天然不覆盖。
    """
    from app.core.enums import ExecStatus
    from app.models import ExecRun

    inp = json.loads(job.input or "{}")
    run_id = inp.get("run_id")
    if not run_id:
        raise ValueError("归因 job 缺少 run_id")
    run = db.get(ExecRun, run_id)
    if run is None:
        raise ValueError(f"执行项不存在:{run_id}")
    # 域写入前校验(与端点一致):失败/阻塞才可归因
    if run.status not in (ExecStatus.failed, ExecStatus.blocked):
        raise ValueError(f"只能归因失败/阻塞的执行(当前 {getattr(run.status, 'value', run.status)})")
    provider_id = generators.normalize_provider(inp.get("provider"))
    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        raise ValueError(f"归因引擎「{provider_id}」未启用或不可用")

    try:
        payload = json.loads(run.payload or "{}")
    except (json.JSONDecodeError, ValueError):
        payload = {}
    try:
        report = json.loads(run.report) if run.report else None
    except (json.JSONDecodeError, ValueError):
        report = None
    prompt = build_triage_prompt(payload, run.reason, run.fail_kind, report)
    system = _SYSTEM_PROMPT
    title = payload.get("title") or "失败归因"

    raw = ""
    err = None
    try:
        for evt in engine.stream_generate(title, prompt_builder=lambda: prompt, system_prompt=system):
            et = evt.get("type")
            if et == "delta":
                raw += evt.get("text") or ""
            elif et == "result":
                if evt.get("text"):
                    raw = evt["text"]
            elif et == "error":
                err = evt.get("msg")
    except Exception as e:  # noqa: BLE001
        logger.exception("归因引擎调用异常 run=%s", run_id)
        err = err or str(e)
    if err:
        raise ValueError(err[:500])
    parsed = parse_triage(raw)
    if parsed.get("error"):
        raise ValueError(parsed["error"])
    parsed["provider"] = provider_id
    parsed["at"] = datetime.utcnow().isoformat()
    run.triage_kind = parsed["kind"]
    run.triage = json.dumps(parsed, ensure_ascii=False)
    db.commit()
    return {"run_id": run_id, **parsed}


# 注册为队列 handler(ai_jobs 惰性 import 本模块时触发)
from app.services import ai_jobs as _ai_jobs  # noqa: E402
_ai_jobs.register_handler("triage", run_triage_job)
