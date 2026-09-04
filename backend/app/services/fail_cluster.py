"""失败聚类去噪：规则粗聚（本文件纯函数部分）+ AI 命名（Task3）。

规则粗聚 = 按失败指纹分组，同指纹一簇。指纹靠归一化失败特征，
免 token、确定性、可单测。AI 只负责给簇起根因标题/判严重度（Task3）。
"""
import hashlib
import json
import re

_HEX = re.compile(r"0x[0-9a-fA-F]+")
_NUM = re.compile(r"\d+")
_WS = re.compile(r"\s+")
_URLQ = re.compile(r"\?[^\s]*")


def normalize_reason(reason: str | None) -> str:
    """去掉易变量（行号/时间/数字 id/十六进制/url query），保留失败骨架文本。"""
    if not reason:
        return ""
    t = str(reason)
    t = _URLQ.sub("", t)
    t = _HEX.sub("#", t)
    t = _NUM.sub("#", t)          # 所有数字→占位符（毫秒/行号/id 归一）
    t = _WS.sub(" ", t).strip()
    return t[:300]


def _first_fail_step(report) -> str:
    """从逐步报告取首个失败步的区分特征 = action + 归一化判据文本（UI 失败最稳的指纹源）。

    runner 落库的步 `check` 结构是 {actual, expected, mode, negate}——**没有 key**（参照
    exec_triage 只读 expected/actual/negate）。故用真实存在的字段构造区分特征：优先 check.expected，
    其次 step.error，再次 step.desc，均经 normalize_reason 抹掉易变量。
    """
    if not isinstance(report, list):
        return ""
    for s in report:
        if isinstance(s, dict) and not s.get("ok"):
            ck = s.get("check")
            detail = ""
            if isinstance(ck, dict):
                detail = str(ck.get("expected") or "")
            detail = detail or str(s.get("error") or "") or str(s.get("desc") or "")
            return f"{s.get('action', '')}:{normalize_reason(detail)}".strip(":")
    return ""


def build_fingerprint(triage_kind, reason, fail_kind, report) -> str:
    """指纹 = triage_kind + (首失败步 或 归一reason 或 fail_kind) 的短哈希。"""
    feat = _first_fail_step(report) or normalize_reason(reason) or (fail_kind or "unknown")
    raw = f"{triage_kind or 'none'}|{feat}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{triage_kind or 'none'}-{h}"


def rule_cluster(runs: list[dict]) -> list[dict]:
    """按指纹粗聚。runs 每项须含 id/triage_kind/reason/fail_kind/report/requirement_id。

    返回每簇 {fingerprint, triage_kind, run_ids, requirement_ids, member_count, sample}，
    按 member_count 降序。sample 取簇内第一条（供 AI 命名）。
    """
    buckets: dict[str, dict] = {}
    for r in runs:
        fp = build_fingerprint(r.get("triage_kind"), r.get("reason"),
                               r.get("fail_kind"), r.get("report"))
        b = buckets.get(fp)
        if b is None:
            b = buckets[fp] = {
                "fingerprint": fp,
                "triage_kind": r.get("triage_kind"),
                "run_ids": [],
                "requirement_ids": [],
                "sample": r,
            }
        b["run_ids"].append(r["id"])
        rid = r.get("requirement_id")
        if rid is not None and rid not in b["requirement_ids"]:
            b["requirement_ids"].append(rid)
    out = []
    for b in buckets.values():
        b["member_count"] = len(b["run_ids"])
        out.append(b)
    out.sort(key=lambda c: -c["member_count"])
    return out


import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.services import generators

logger = logging.getLogger("test_platform")

_SEVERITIES = ("blocker", "critical", "major", "minor", "trivial")
_SYSTEM_PROMPT = (
    "你是资深测试工程师，为一组同根因的自动化失败起一个精炼的根因标题并判严重度。"
    "只输出一个 JSON 对象，不要输出任何其它文字。"
)


def build_naming_prompt(sample: dict, member_count: int) -> str:
    lines = [
        f"下面是一组共 {member_count} 条、被判定为同一根因的自动化失败。请为该根因起标题、判严重度。",
        "",
        f"【归因类别】{sample.get('triage_kind') or '无'}",
        f"【代表失败原因】{str(sample.get('reason') or '无')[:800]}",
    ]
    rep = sample.get("report")
    if isinstance(rep, list):
        for s in rep[:8]:
            if isinstance(s, dict) and not s.get("ok"):
                lines.append(f"✗ 步{s.get('no','?')} {s.get('action','')} 错误:{str(s.get('error') or '')[:150]}")
    lines += [
        "",
        '只输出 JSON：{"root_cause_title":"一句话根因(≤40字)","summary":"根因说明",'
        '"severity":"blocker|critical|major|minor|trivial","confidence":0到1}',
    ]
    return "\n".join(lines)


def parse_naming(raw: str) -> dict:
    if not raw or not raw.strip():
        return {"error": "引擎无输出"}
    text = re.sub(r"```(?:json)?", "", raw).strip("` \n")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {"error": f"输出无 JSON(前120:{text[:120]})"}
    try:
        obj = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return {"error": "JSON 解析失败"}
    title = str(obj.get("root_cause_title") or "").strip()[:255]
    if not title:
        return {"error": "缺 root_cause_title"}
    sev = str(obj.get("severity") or "").strip().lower()
    if sev not in _SEVERITIES:
        sev = "major"
    try:
        conf = max(0.0, min(1.0, float(obj.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    return {"root_cause_title": title, "summary": str(obj.get("summary") or "")[:1000],
            "severity": sev, "confidence": round(conf, 2)}


def collect_failed_runs(db: Session, release_id: int,
                        requirement_ids: list[int] | None = None,
                        task_ids: list[int] | None = None) -> list[dict]:
    """回溯该版本内 failed/blocked 执行。双路径并集：
    ①链路径：用例→需求.release_id==release_id（受 requirement_ids/task_ids 勾选收窄）。
    ②直路径（兜底）：ExecRun.release_id==release_id 且**回溯不到需求**的 orphan 失败——
      无条件纳入（requirement_id=None），不受勾选门控（spec「兜底不丢数据」）。
    直路径**只塞 orphan**：有需求归属的 run 一律走链路径，否则被过滤掉的需求 run 会经直路径泄漏回来。
    返回带 requirement_id 的 dict 列表。
    """
    from sqlalchemy import or_

    from app.core.enums import ExecStatus
    from app.models import ExecRun, TestCase, Requirement

    fail_status = [ExecStatus.failed, ExecStatus.blocked]
    # 链路径：用例挂需求，需求属该版本
    q_chain = (db.query(ExecRun, TestCase.requirement_id)
               .join(TestCase, ExecRun.test_case_id == TestCase.id)
               .join(Requirement, TestCase.requirement_id == Requirement.id)
               .filter(Requirement.release_id == release_id,
                       ExecRun.status.in_(fail_status)))
    if requirement_ids:
        q_chain = q_chain.filter(TestCase.requirement_id.in_(requirement_ids))
    if task_ids:
        q_chain = q_chain.filter(TestCase.task_id.in_(task_ids))
    seen: dict[int, dict] = {}
    for run, req_id in q_chain.all():
        seen[run.id] = _run_to_dict(run, req_id)
    # 直路径：执行直接挂 release_id 但无需求归属（test_case_id 为空，或其用例无 requirement）。
    # 这些是真正「回溯不到需求」的 orphan——永远兜底纳入，不随 requirement_ids/task_ids 门控。
    orphan_q = (db.query(ExecRun)
                .outerjoin(TestCase, ExecRun.test_case_id == TestCase.id)
                .filter(ExecRun.release_id == release_id,
                        ExecRun.status.in_(fail_status),
                        or_(ExecRun.test_case_id.is_(None),
                            TestCase.requirement_id.is_(None))))
    for run in orphan_q.all():
        seen.setdefault(run.id, _run_to_dict(run, None))
    return list(seen.values())


def _run_to_dict(run, requirement_id) -> dict:
    try:
        report = json.loads(run.report) if run.report else None
    except (json.JSONDecodeError, ValueError):
        report = None
    return {"id": run.id, "triage_kind": run.triage_kind, "reason": run.reason,
            "fail_kind": run.fail_kind, "report": report, "requirement_id": requirement_id}


def _pick_provider(explicit: str | None) -> str:
    """选命名引擎：显式传了就用显式的（规整成合法 id）；否则挑一个**当前 available** 的
    provider（内网常以 deepseek 为零依赖路径，claude CLI 未必可用）；都不可用才回落默认。"""
    if explicit:
        return generators.normalize_provider(explicit)
    for p in generators.available_providers():
        if p.get("available"):
            return p["id"]
    return generators.DEFAULT_PROVIDER


def run_fail_cluster_job(db: Session, job) -> dict:
    """queue handler：拉失败→粗聚→逐簇 AI 命名→落 fail_cluster 表。

    job.input = {release_id, requirement_ids?, task_ids?, batch_key, provider?}。
    重跑：同 batch_key 先删旧簇；按 fingerprint 从上一批迁移已建缺陷的 issue_id。
    """
    from app.models import FailCluster

    inp = json.loads(job.input or "{}")
    release_id = inp.get("release_id")
    if not release_id:
        raise ValueError("fail_cluster job 缺 release_id")
    batch_key = inp.get("batch_key") or f"rel{release_id}-{job.id}"
    provider_id = _pick_provider(inp.get("provider"))
    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        raise ValueError(f"命名引擎「{provider_id}」未启用或不可用")

    runs = collect_failed_runs(db, release_id, inp.get("requirement_ids"), inp.get("task_ids"))
    clusters = rule_cluster(runs)

    # 迁移旧批次已建缺陷（按 fingerprint）
    prev = {c.fingerprint: c.issue_id for c in
            db.query(FailCluster).filter(FailCluster.release_id == release_id,
                                         FailCluster.issue_id.isnot(None)).all()}
    # 清本 batch_key 旧行（幂等重跑）
    db.query(FailCluster).filter(FailCluster.batch_key == batch_key).delete()

    out = []
    for c in clusters:
        naming = _name_one(engine, c)
        fcrow = FailCluster(
            project_id=job.project_id, release_id=release_id,
            root_cause_title=naming.get("root_cause_title") or f"未命名根因（{c['triage_kind'] or '未知'}）",
            summary=naming.get("summary"), triage_kind=c["triage_kind"],
            fingerprint=c["fingerprint"], run_ids=json.dumps(c["run_ids"]),
            requirement_ids=json.dumps(c["requirement_ids"]), member_count=c["member_count"],
            severity=naming.get("severity"), confidence=naming.get("confidence"),
            issue_id=prev.get(c["fingerprint"]), batch_key=batch_key,
        )
        db.add(fcrow)
        out.append({"fingerprint": c["fingerprint"], "member_count": c["member_count"]})
    db.commit()
    return {"release_id": release_id, "batch_key": batch_key,
            "fail_count": len(runs), "cluster_count": len(clusters), "clusters": out}


def _name_one(engine, cluster: dict) -> dict:
    prompt = build_naming_prompt(cluster["sample"], cluster["member_count"])
    raw, err = "", None
    try:
        for evt in engine.stream_generate("失败根因命名",
                                          prompt_builder=lambda: prompt, system_prompt=_SYSTEM_PROMPT):
            et = evt.get("type")
            if et == "delta":
                raw += evt.get("text") or ""
            elif et == "result" and evt.get("text"):
                raw = evt["text"]
            elif et == "error":
                err = evt.get("msg")
    except Exception as e:  # noqa: BLE001
        logger.exception("命名引擎异常")
        err = str(e)
    if err:
        return {}
    parsed = parse_naming(raw)
    return {} if parsed.get("error") else parsed


from app.services import ai_jobs as _ai_jobs  # noqa: E402
_ai_jobs.register_handler("fail_cluster", run_fail_cluster_job)
