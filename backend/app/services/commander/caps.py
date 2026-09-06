"""Commander 首批 read 能力（薄封装既有服务/查询，只读）。

runner 统一签名 `(db, user, params: dict) -> dict`：
- 需 project 上下文的：从 `params["project_id"]` 取（Task3 保证已注入且校验过）。
- 取具体对象(release_id 等)的：先 `db.get` 反查**真实归属 project**，再
  `assert_project_role`——绝不信客户端传的 project_id 去授权某个具体对象（IDOR 红线）。

read 类统一用**全部角色**(admin/member/guest)做读权限校验。
本文件末尾对每个能力 `register(...)`；`commander/__init__` 的 `from . import caps` 触发。
"""
import json
from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import and_, exists, func

from app.core.deps import assert_project_role
from app.core.enums import IssueStatus, ProjectRole, ReviewStatus, TaskStatus
from app.models import (
    ExecRun,
    FailCluster,
    ReleaseRecord,
    RemainingIssue,
    RtsRecommendation,
    Task,
    TestCase,
)
from app.services import rts
from app.services.commander.registry import Capability, register

# read 能力统一读权限：项目内任意角色（含访客）。
_READ_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)


def _require_project_id(params: dict) -> int:
    pid = params.get("project_id")
    if pid is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="缺少 project_id")
    return int(pid)


def _resolve_release(db, user, release_id) -> ReleaseRecord:
    """IDOR 反查：按 release_id 取真实记录 → 用其真实 project_id 校验读权限。"""
    if release_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="缺少 release_id")
    rel = db.get(ReleaseRecord, int(release_id))
    if rel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="发版记录不存在")
    assert_project_role(db, user, rel.project_id, _READ_ROLES)
    return rel


# ── 能力实现 ──────────────────────────────────────────────────────────────────────

def list_releases(db, user, params: dict) -> dict:
    """列出某项目的发版记录（按发版日期倒序，默认取近 20 条）。"""
    project_id = _require_project_id(params)
    assert_project_role(db, user, project_id, _READ_ROLES)
    try:
        limit = min(max(int(params.get("limit", 20)), 1), 100)
    except (TypeError, ValueError):
        limit = 20
    rows = (
        db.query(ReleaseRecord)
        .filter(ReleaseRecord.project_id == project_id)
        .order_by(ReleaseRecord.release_date.desc(), ReleaseRecord.id.desc())
        .limit(limit)
        .all()
    )
    releases = [
        {
            "id": r.id,
            "version": r.version,
            "sub_product": r.sub_product,
            "release_date": str(r.release_date) if r.release_date else None,
            "req_count": r.req_count,
        }
        for r in rows
    ]
    return {"project_id": project_id, "releases": releases, "count": len(releases)}


def rts_recommendation(db, user, params: dict) -> dict:
    """读某发版最新一条 RTS 回归智选叙事（无则 exists=False）。"""
    rel = _resolve_release(db, user, params.get("release_id"))
    row = (
        db.query(RtsRecommendation)
        .filter(RtsRecommendation.release_id == rel.id)
        .order_by(RtsRecommendation.generated_at.desc(), RtsRecommendation.id.desc())
        .first()
    )
    if row is None:
        return {"release_id": rel.id, "version": rel.version, "exists": False}
    try:
        focus = json.loads(row.focus_points) if row.focus_points else []
    except (json.JSONDecodeError, ValueError, TypeError):
        focus = []
    return {
        "release_id": rel.id,
        "version": rel.version,
        "exists": True,
        "overall_risk": row.overall_risk,
        "summary": row.summary,
        "rationale": row.rationale,
        "focus_points": focus,
        "candidate_count": row.candidate_count,
        "recommended_count": row.recommended_count,
        "provider": row.provider,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }


def rts_candidates(db, user, params: dict) -> dict:
    """某发版的回归候选风险分（现算，降序取 top N，默认 20）。"""
    rel = _resolve_release(db, user, params.get("release_id"))
    try:
        top = min(max(int(params.get("top", 20)), 1), 100)
    except (TypeError, ValueError):
        top = 20
    ranked = rts.rank_candidates(db, rel.id)
    return {
        "release_id": rel.id,
        "version": rel.version,
        "candidate_count": len(ranked),
        "items": ranked[:top],
    }


def fail_cluster_list(db, user, params: dict) -> dict:
    """读某发版的失败聚类结果（按 member_count 降序）。"""
    rel = _resolve_release(db, user, params.get("release_id"))
    rows = (
        db.query(FailCluster)
        .filter(FailCluster.release_id == rel.id)
        .order_by(FailCluster.member_count.desc(), FailCluster.id.desc())
        .all()
    )
    items = [
        {
            "id": c.id,
            "root_cause_title": c.root_cause_title,
            "summary": c.summary,
            "triage_kind": c.triage_kind,
            "member_count": c.member_count,
            "severity": c.severity,
            "confidence": c.confidence,
            "issue_id": c.issue_id,
        }
        for c in rows
    ]
    return {
        "release_id": rel.id,
        "version": rel.version,
        "cluster_count": len(items),
        "fail_count": sum(c.member_count for c in rows),
        "items": items,
    }


def stats_overview(db, user, params: dict) -> dict:
    """某项目今日任务 KPI（现算，不建统计表）。

    简化版：仅取传入 project_id 单项目的今日派单流转 + 未解决遗留问题（口径同
    /stats/overview 的今日部分，但限定单项目、不返回 7 天趋势——Commander 只需一句能答的摘要）。
    """
    project_id = _require_project_id(params)
    assert_project_role(db, user, project_id, _READ_ROLES)
    today = date.today()
    d = params.get("date")
    if isinstance(d, str) and d:
        try:
            today = date.fromisoformat(d)
        except ValueError:
            pass

    status_rows = (
        db.query(Task.status, func.count(Task.id))
        .filter(
            Task.project_id == project_id,
            (
                (Task.assigned_date == today)
                | (
                    (Task.assigned_date < today)
                    & (Task.status.in_([TaskStatus.testing, TaskStatus.blocked]))
                )
            ),
        )
        .group_by(Task.status)
        .all()
    )
    counts = {
        TaskStatus.pending: 0,
        TaskStatus.testing: 0,
        TaskStatus.blocked: 0,
        TaskStatus.online: 0,
        TaskStatus.closed: 0,
    }
    for st, c in status_rows:
        counts[st] = c
    total = sum(counts.values())
    done_cnt = counts[TaskStatus.online] + counts[TaskStatus.closed]
    done_rate = round(done_cnt / total * 100, 1) if total else 0.0

    open_issues = (
        db.query(func.count(RemainingIssue.id))
        .filter(
            RemainingIssue.project_id == project_id,
            RemainingIssue.status == IssueStatus.open,
        )
        .scalar()
        or 0
    )

    return {
        "project_id": project_id,
        "date": str(today),
        "today": {
            "total": total,
            "pending": counts[TaskStatus.pending],
            "testing": counts[TaskStatus.testing],
            "blocked": counts[TaskStatus.blocked],
            "online": counts[TaskStatus.online],
            "closed": counts[TaskStatus.closed],
            "done_cnt": done_cnt,
            "done_rate": done_rate,
        },
        "open_issues": int(open_issues),
    }


def stats_ai_funnel(db, user, params: dict) -> dict:
    """某项目 AI 全链路价值漏斗的阶段计数（现算，不建统计表）。

    简化版：限定单项目、时间窗 [today-days+1, today]（默认 30 天），返回
    生成→采纳→可自动化→已执行→通过 五级计数（用例维度去重，严格单调）。
    """
    project_id = _require_project_id(params)
    assert_project_role(db, user, project_id, _READ_ROLES)
    try:
        days = int(params.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    if days <= 0 or days > 365:
        days = 30
    today = date.today()
    d_from = today - timedelta(days=days - 1)

    def _win(q, col):
        return q.filter(func.date(col) >= d_from, func.date(col) <= today)

    def _tc_q(*flt):
        return db.query(func.count(TestCase.id)).filter(TestCase.project_id == project_id, *flt)

    generated = _win(_tc_q(), TestCase.created_at).scalar() or 0
    adopted = _win(
        _tc_q(TestCase.review_status == ReviewStatus.adopted), TestCase.created_at
    ).scalar() or 0
    automatable = _win(
        _tc_q(TestCase.review_status == ReviewStatus.adopted, TestCase.exec_kind != "manual"),
        TestCase.created_at,
    ).scalar() or 0

    def _executed_case(*run_flt):
        return _win(
            _tc_q(
                TestCase.review_status == ReviewStatus.adopted,
                TestCase.exec_kind != "manual",
                exists().where(and_(ExecRun.test_case_id == TestCase.id, *run_flt)),
            ),
            TestCase.created_at,
        ).scalar() or 0

    executed = _executed_case(ExecRun.status.in_(["passed", "failed", "blocked"]))
    passed = _executed_case(ExecRun.status == "passed")

    return {
        "project_id": project_id,
        "from": str(d_from),
        "to": str(today),
        "days": days,
        "funnel": [
            {"stage": "generated", "label": "AI 生成", "count": int(generated)},
            {"stage": "adopted", "label": "已采纳", "count": int(adopted)},
            {"stage": "automatable", "label": "可自动化", "count": int(automatable)},
            {"stage": "executed", "label": "已执行", "count": int(executed)},
            {"stage": "passed", "label": "执行通过", "count": int(passed)},
        ],
        "adopt_rate": round(adopted / generated * 100, 1) if generated else 0.0,
    }


# ── 注册 ────────────────────────────────────────────────────────────────────────

register(Capability(
    name="list_releases",
    desc="列出某项目的发版记录（版本号/子产品/发版日期/需求数），按发版日期倒序。",
    params={"project_id": "项目 ID（服务端注入）", "limit": "可选，最多返回条数，默认 20"},
    kind="read",
    runner=list_releases,
))

register(Capability(
    name="rts_recommendation",
    desc="读某发版最新一条 RTS 回归智选 AI 叙事（整体风险等级/概述/推荐理由/关注点）。",
    params={"release_id": "发版 ID"},
    kind="read",
    runner=rts_recommendation,
))

register(Capability(
    name="rts_candidates",
    desc="某发版的回归候选用例风险分（现算，按风险降序，含命中信号明细），取 top N。",
    params={"release_id": "发版 ID", "top": "可选，取前 N 条，默认 20"},
    kind="read",
    runner=rts_candidates,
))

register(Capability(
    name="fail_cluster_list",
    desc="读某发版的失败聚类结果（根因簇标题/摘要/归因类别/影响条数/严重度），按影响条数降序。",
    params={"release_id": "发版 ID"},
    kind="read",
    runner=fail_cluster_list,
))

register(Capability(
    name="stats_overview",
    desc="某项目今日任务 KPI 概览（总数/各状态/完成率/未解决遗留问题数）。",
    params={"project_id": "项目 ID（服务端注入）", "date": "可选，YYYY-MM-DD，默认今天"},
    kind="read",
    runner=stats_overview,
))

register(Capability(
    name="stats_ai_funnel",
    desc="某项目 AI 全链路价值漏斗阶段计数：生成→采纳→可自动化→已执行→通过。",
    params={"project_id": "项目 ID（服务端注入）", "days": "可选，时间窗天数，默认 30"},
    kind="read",
    runner=stats_ai_funnel,
))
