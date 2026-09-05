"""RTS 回归智选：确定性风险评分（本文件规则层）+ AI 叙事（Task3）。

风险分由可解释加权信号组成，免 token、可单测。候选池=adopted+非manual 用例。
"""
from sqlalchemy import case, func
from sqlalchemy.orm import Session, sessionmaker

# 各信号权重（占满分 100 的分配）；具名常量便于校准，理由里透出贡献。
RTS_WEIGHTS = {
    "in_release": 30,   # 用例属本版本需求
    "fail_rate": 25,    # 历史失败率
    "priority": 20,     # 优先级 P0>P3
    "flaky": 10,        # 不稳定需复验
    "had_bug": 10,      # 曾揪真 bug
    "stale": 5,         # 陈旧度（久未跑）
}
_PRIO = {"P0": 1.0, "P1": 0.7, "P2": 0.4, "P3": 0.2}
_STALE_CAP_DAYS = 30


def cases_for_release(db: Session, release_id: int):
    """候选池 = 项目内 adopted + 非 manual 用例；并标出哪些属本版本(用例→需求→版本)。

    返回 (cases:list[{id,title,priority,requirement_id}], in_release:set[case_id])。
    """
    from app.models import TestCase, Requirement
    from app.core.enums import ReviewStatus
    # 该版本的需求 id 集
    req_ids = {r.id for r in db.query(Requirement.id).filter(Requirement.release_id == release_id).all()}
    # 项目 id 经 release 反查
    from app.models.release import ReleaseRecord
    rel = db.get(ReleaseRecord, release_id)
    if rel is None:
        return [], set()
    rows = (db.query(TestCase)
            .filter(TestCase.project_id == rel.project_id,
                    TestCase.review_status == ReviewStatus.adopted,
                    TestCase.exec_kind != "manual").all())
    cases, in_release = [], set()
    for tc in rows:
        cases.append({"id": tc.id, "title": tc.title, "priority": tc.priority,
                      "requirement_id": tc.requirement_id})
        if tc.requirement_id in req_ids:
            in_release.add(tc.id)
    return cases, in_release


def exec_history_signals(db: Session, case_ids: list[int]) -> dict:
    """批量算历史信号(一次聚合，防 N+1)：runs/fails/flaky/had_bug/last_days。"""
    from app.models import ExecRun
    out = {cid: {"runs": 0, "fails": 0, "flaky": False, "had_bug": False, "last_days": None} for cid in case_ids}
    if not case_ids:
        return out
    rows = (db.query(
                ExecRun.test_case_id,
                func.count(ExecRun.id),
                func.sum(case((ExecRun.status.in_(["failed", "blocked"]), 1), else_=0)),
                func.max(case((ExecRun.flaky == True, 1), else_=0)),  # noqa: E712
                func.max(case(((ExecRun.triage_kind == "bug") | (ExecRun.fail_kind == "business"), 1), else_=0)),
                func.max(ExecRun.created_at),
            )
            .filter(ExecRun.test_case_id.in_(case_ids))
            .group_by(ExecRun.test_case_id).all())
    from app.db.clock import db_now
    now = db_now(db)
    for cid, runs, fails, flaky, had_bug, last_at in rows:
        d = out[cid]
        d["runs"] = int(runs or 0)
        d["fails"] = int(fails or 0)
        d["flaky"] = bool(flaky)
        d["had_bug"] = bool(had_bug)
        if last_at is not None:
            d["last_days"] = max(0, (now - last_at).days)
    return out


def score_case(case: dict, sig: dict, in_release: bool):
    """确定性加权风险分 [0,100] + 各信号贡献明细。"""
    detail = {}
    w = RTS_WEIGHTS
    score = 0.0
    if in_release:
        score += w["in_release"]; detail["in_release"] = w["in_release"]
    runs, fails = sig.get("runs", 0), sig.get("fails", 0)
    fr = (fails / runs) if runs else 0.5   # 无历史给中性 0.5，不沉底
    score += w["fail_rate"] * fr; detail["fail_rate"] = round(w["fail_rate"] * fr, 1)
    pr = _PRIO.get((case.get("priority") or "").upper(), 0.4)
    score += w["priority"] * pr; detail["priority"] = round(w["priority"] * pr, 1)
    if sig.get("flaky"):
        score += w["flaky"]; detail["flaky"] = w["flaky"]
    if sig.get("had_bug"):
        score += w["had_bug"]; detail["had_bug"] = w["had_bug"]
    ld = sig.get("last_days")
    if ld is not None:
        st = min(1.0, ld / _STALE_CAP_DAYS)
        score += w["stale"] * st; detail["stale"] = round(w["stale"] * st, 1)
    return round(max(0.0, min(100.0, score)), 1), detail


def rank_candidates(db: Session, release_id: int) -> list[dict]:
    """组装候选风险分并降序。"""
    cases, in_release = cases_for_release(db, release_id)
    sigs = exec_history_signals(db, [c["id"] for c in cases])
    out = []
    for c in cases:
        score, detail = score_case(c, sigs.get(c["id"], {}), c["id"] in in_release)
        out.append({"case_id": c["id"], "title": c["title"], "priority": c["priority"],
                    "risk_score": score, "signals": detail})
    out.sort(key=lambda r: -r["risk_score"])
    return out


import json
import logging

from app.services import generators

logger = logging.getLogger("test_platform")
_RISKS = ("high", "medium", "low")
_SYSTEM_PROMPT = ("你是资深测试负责人，为一次发版的回归范围做整体风险研判并解释推荐理由。"
                  "只输出一个 JSON 对象，不要输出任何其它文字。")


def _pick_provider(name: str | None):
    """选引擎：显式指定则用；否则选第一个 available 的(不硬 claude)。"""
    pid = generators.normalize_provider(name)
    eng = generators.get_provider(pid)
    if name or eng.is_available():
        return pid, eng
    for cand in generators.PROVIDERS:
        e = generators.get_provider(cand)
        if e.is_available():
            return cand, e
    return pid, eng


def build_rts_prompt(release_info: dict, ranked: list[dict]) -> str:
    top = ranked[:30]
    lines = [
        f"发版：{release_info.get('version')}，候选回归用例 {len(ranked)} 条。",
        "下面是按风险分降序的候选（含命中的风险信号明细），请做整体风险研判与推荐。",
        "",
    ]
    for r in top:
        lines.append(f"- [{r['risk_score']}分] {r['title'][:60]}（P:{r.get('priority') or '?'}）信号:{r['signals']}")
    lines += [
        "",
        '只输出 JSON：{"overall_risk":"high|medium|low","summary":"本次发版风险概述(2-3句)",'
        '"rationale":"为什么建议跑高分这批、可跳过低分那批的整体理由",'
        '"focus_points":["风险点1","风险点2"],"recommended_count":建议跑的条数(整数)}',
    ]
    return "\n".join(lines)


def parse_rts(raw: str) -> dict:
    if not raw or not raw.strip():
        return {"error": "引擎无输出"}
    import re
    text = re.sub(r"```(?:json)?", "", raw).strip("` \n")
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return {"error": "输出无 JSON"}
    try:
        obj = json.loads(text[s:e + 1])
    except (json.JSONDecodeError, ValueError):
        return {"error": "JSON 解析失败"}
    risk = str(obj.get("overall_risk") or "").strip().lower()
    if risk not in _RISKS:
        risk = "medium"
    fp = obj.get("focus_points")
    if not isinstance(fp, list):
        fp = [str(fp)] if fp else []
    try:
        rc = int(obj.get("recommended_count") or 0)
    except (TypeError, ValueError):
        rc = 0
    return {"overall_risk": risk, "summary": str(obj.get("summary") or "")[:1000],
            "rationale": str(obj.get("rationale") or "")[:2000],
            "focus_points": [str(x)[:200] for x in fp][:10], "recommended_count": rc}


def run_rts_job(db: Session, job) -> dict:
    """queue handler：算候选风险分 → AI 叙事 → 落 rts_recommendation(按 release 覆盖)。

    job.input = {release_id, provider?}。

    连接管理(与 fail_cluster 同范式):候选风险分(读)算完即 commit 释放 DB 连接,AI 叙事的
    几十秒里不持有连接(免其空闲被中间层掐断致写库 2013 Lost connection);写库用**全新
    session** 走 _persist_with_retry,首次断连即重连重放。
    """
    from app.models import ReleaseRecord, RtsRecommendation
    from app.services.ai_jobs import _persist_with_retry

    inp = json.loads(job.input or "{}")
    release_id = inp.get("release_id")
    if not release_id:
        raise ValueError("rts job 缺 release_id")
    rel = db.get(ReleaseRecord, release_id)
    if rel is None:
        raise ValueError(f"发版不存在:{release_id}")
    pid, engine = _pick_provider(inp.get("provider"))
    if not engine.is_available():
        raise ValueError(f"叙事引擎「{pid}」不可用")
    ranked = rank_candidates(db, release_id)
    # 读阶段完成:先把后续要用的 rel 字段/归属 project 取成本地量,再 commit 释放连接——
    # commit 后不再回读已 expire 的 rel ORM / 原 db 会话(AI 叙事与写库都不碰 db)。
    rel_version = rel.version
    project_id = job.project_id or rel.project_id
    db.commit()

    # AI 叙事(纯引擎调用,零 DB 连接);结果暂存本地变量,稍后用新 session 落库。
    prompt = build_rts_prompt({"version": rel_version}, ranked)
    raw, err = "", None
    try:
        for evt in engine.stream_generate("回归风险研判", prompt_builder=lambda: prompt, system_prompt=_SYSTEM_PROMPT):
            t = evt.get("type")
            if t == "delta":
                raw += evt.get("text") or ""
            elif t == "result" and evt.get("text"):
                raw = evt["text"]
            elif t == "error":
                err = evt.get("msg")
    except Exception as e:  # noqa: BLE001
        logger.exception("RTS 引擎异常")
        err = str(e)
    if err:
        raise ValueError(err[:500])
    parsed = parse_rts(raw)
    if parsed.get("error"):
        raise ValueError(parsed["error"])

    # ---- 写库(P1:此处才重取连接;P2:短重试兜断连)。按 release 覆盖:delete 旧 + insert 新在
    # 同一新 session 内,幂等重放(断连整段重放)也靠它去重不堆积。delete 必在 parse 成功之后
    # (AI 失败早已 raise,此刻旧数据尚未删——保持"失败不动旧推荐")。 ----
    def _persist(s):
        s.query(RtsRecommendation).filter(RtsRecommendation.release_id == release_id).delete()
        row = RtsRecommendation(
            project_id=project_id, release_id=release_id,
            overall_risk=parsed["overall_risk"], summary=parsed["summary"], rationale=parsed["rationale"],
            focus_points=json.dumps(parsed["focus_points"], ensure_ascii=False),
            candidate_count=len(ranked), recommended_count=parsed["recommended_count"], provider=pid)
        s.add(row)
        s.commit()
        return [row], None

    _persist_with_retry(_persist, sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False))
    return {"release_id": release_id, "candidate_count": len(ranked), **parsed}


from app.services import ai_jobs as _ai_jobs  # noqa: E402
_ai_jobs.register_handler("rts", run_rts_job)
