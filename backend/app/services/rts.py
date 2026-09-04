"""RTS 回归智选：确定性风险评分（本文件规则层）+ AI 叙事（Task3）。

风险分由可解释加权信号组成，免 token、可单测。候选池=adopted+非manual 用例。
"""
from datetime import datetime

from sqlalchemy import case, func
from sqlalchemy.orm import Session

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
    now = datetime.utcnow()
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
