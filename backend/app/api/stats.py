from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import IssueStatus, ProjectRole
from app.db.session import get_db
from app.models import DailyReport, Project, ProjectMember, RemainingIssue, Task, User
from app.schemas.common import ok

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _visible_project_ids(db: Session, user: User) -> list[int]:
    """当前用户可统计的项目集合：平台管理员=全部项目；普通用户=其参与的项目。"""
    if user.is_platform_admin:
        return [pid for (pid,) in db.query(Project.id).all()]
    return [
        pid for (pid,) in
        db.query(ProjectMember.project_id).filter(ProjectMember.user_id == user.id).all()
    ]


@router.get("/overview")
def overview_stats(
    date_: date = Query(default=None, alias="date"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """工作台首页跨项目汇总：今日 KPI + 近 7 天趋势。

    权限：平台管理员统计全部项目，普通成员统计其参与的项目。
    口径与 /stats/daily 一致，对 task/daily_report/remaining_issue 现算聚合，不建统计表。
    """
    today = date_ or date.today()
    scope = "platform" if user.is_platform_admin else "member"
    pids = _visible_project_ids(db, user)

    empty_today = {
        "should_submit": 0, "submitted": 0, "not_submitted": 0, "submit_rate": 0.0,
        "online_cnt": 0, "avg_progress": 0.0, "workload_hours": 0.0,
    }
    if not pids:
        # 无可见项目：返回全 0 结构 + 空 7 天序列（不报错）
        start = today - timedelta(days=6)
        trend = [{"date": str(start + timedelta(days=i)), "hours": 0.0, "submitted": 0}
                 for i in range(7)]
        return ok({"date": str(today), "scope": scope, "project_cnt": 0,
                   "today": empty_today, "open_issues": 0, "trend": trend})

    # ---- 今日 KPI ----
    today_tasks = (
        db.query(Task.id, Task.assigned_to)
        .filter(Task.project_id.in_(pids), Task.assigned_date == today)
        .all()
    )
    task_ids = [t.id for t in today_tasks]
    should_submit = len({t.assigned_to for t in today_tasks})

    today_reports = (
        db.query(DailyReport)
        .filter(DailyReport.task_id.in_(task_ids), DailyReport.report_date == today)
        .all() if task_ids else []
    )
    submitted = len({r.user_id for r in today_reports})
    online_cnt = sum(1 for r in today_reports if r.is_online)
    avg_progress = round(sum(r.progress_pct for r in today_reports) / len(today_reports), 1) \
        if today_reports else 0.0
    workload_hours = round(sum(float(r.workload_hours or 0) for r in today_reports), 1)
    submit_rate = round(submitted / should_submit * 100, 1) if should_submit else 0.0

    # ---- 未解决遗留问题（跨项目存量，不限今日）----
    open_issues = (
        db.query(func.count(RemainingIssue.id))
        .filter(RemainingIssue.project_id.in_(pids),
                RemainingIssue.status == IssueStatus.open)
        .scalar() or 0
    )

    # ---- 近 7 天趋势（含今日）----
    start = today - timedelta(days=6)
    week_reports = (
        db.query(DailyReport)
        .filter(DailyReport.project_id.in_(pids),
                DailyReport.report_date >= start,
                DailyReport.report_date <= today)
        .all()
    )
    by_day: dict[str, dict] = {}
    for r in week_reports:
        d = by_day.setdefault(str(r.report_date), {"hours": 0.0, "users": set()})
        d["hours"] += float(r.workload_hours or 0)
        d["users"].add(r.user_id)
    trend = []
    for i in range(7):
        day = str(start + timedelta(days=i))
        v = by_day.get(day)
        trend.append({"date": day,
                      "hours": round(v["hours"], 1) if v else 0.0,
                      "submitted": len(v["users"]) if v else 0})

    return ok({
        "date": str(today),
        "scope": scope,
        "project_cnt": len(pids),
        "today": {
            "should_submit": should_submit,
            "submitted": submitted,
            "not_submitted": max(should_submit - submitted, 0),
            "submit_rate": submit_rate,
            "online_cnt": online_cnt,
            "avg_progress": avg_progress,
            "workload_hours": workload_hours,
        },
        "open_issues": open_issues,
        "trend": trend,
    })


@router.get("/daily")
def daily_stats(
    project_id: int = Query(...),
    date: date = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """日报统计：应交/已交/未交名单/平均进度/上线数/遗留问题数。"""
    assert_project_role(db, user, project_id,
                        (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    tasks = (
        db.query(Task)
        .filter(Task.project_id == project_id, Task.assigned_date == date)
        .all()
    )
    task_ids = [t.id for t in tasks]
    should_submit_ids = sorted({t.assigned_to for t in tasks})

    submitted_rows = (
        db.query(DailyReport)
        .filter(DailyReport.task_id.in_(task_ids), DailyReport.report_date == date)
        .all() if task_ids else []
    )
    submitted_ids = sorted({r.user_id for r in submitted_rows})
    not_submitted_ids = [u for u in should_submit_ids if u not in submitted_ids]

    def name(uid):
        u = db.get(User, uid)
        return u.name if u else ""

    avg_progress = round(sum(r.progress_pct for r in submitted_rows) / len(submitted_rows), 1) if submitted_rows else 0
    online_cnt = sum(1 for r in submitted_rows if r.is_online)
    workload_total = round(sum(float(r.workload_hours or 0) for r in submitted_rows), 1)

    report_ids = [r.id for r in submitted_rows]
    open_issues = (
        db.query(func.count(RemainingIssue.id))
        .filter(RemainingIssue.report_id.in_(report_ids),
                RemainingIssue.status == IssueStatus.open)
        .scalar() if report_ids else 0
    )
    new_issues = (
        db.query(func.count(RemainingIssue.id))
        .filter(RemainingIssue.report_id.in_(report_ids))
        .scalar() if report_ids else 0
    )

    return ok({
        "project_id": project_id,
        "date": str(date),
        "should_submit": len(should_submit_ids),
        "submitted": len(submitted_ids),
        "not_submitted": [{"user_id": u, "name": name(u)} for u in not_submitted_ids],
        "avg_progress": avg_progress,
        "online_cnt": online_cnt,
        "workload_total": workload_total,
        "open_issues": open_issues,
        "new_issues": new_issues,
        "reports": [
            {"task_id": r.task_id, "user_id": r.user_id, "name": name(r.user_id),
             "progress_pct": r.progress_pct, "is_online": r.is_online,
             "workload_hours": float(r.workload_hours or 0), "summary": r.summary}
            for r in submitted_rows
        ],
    })


@router.get("/workload")
def workload_stats(
    project_id: int = Query(...),
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """工作量统计：按成员聚合人时/任务数/上线数 + 每日趋势序列。"""
    assert_project_role(db, user, project_id,
                        (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (
        db.query(DailyReport)
        .filter(DailyReport.project_id == project_id,
                DailyReport.report_date >= from_date,
                DailyReport.report_date <= to_date)
        .all()
    )
    by_member: dict[int, dict] = {}
    daily: dict[str, dict] = {}
    for r in rows:
        m = by_member.setdefault(r.user_id, {"hours": 0.0, "tasks": set(), "online": 0})
        m["hours"] += float(r.workload_hours or 0)
        m["tasks"].add(r.task_id)
        m["online"] += 1 if r.is_online else 0
        d = daily.setdefault(str(r.report_date), {"hours": 0.0, "online": 0})
        d["hours"] += float(r.workload_hours or 0)
        d["online"] += 1 if r.is_online else 0

    def _name(uid):
        u = db.get(User, uid)
        return u.name if u else ""

    members = [{
        "user_id": uid, "name": _name(uid),
        "hours": round(v["hours"], 1), "task_cnt": len(v["tasks"]), "online_cnt": v["online"],
    } for uid, v in by_member.items()]
    members.sort(key=lambda x: x["hours"], reverse=True)

    daily_series = [{"date": d, "hours": round(v["hours"], 1), "online_cnt": v["online"]}
                     for d, v in sorted(daily.items())]
    total_hours = round(sum(v["hours"] for v in by_member.values()), 1)
    return ok({
        "project_id": project_id,
        "from": str(from_date), "to": str(to_date),
        "total_hours": total_hours,
        "total_online": sum(v["online"] for v in by_member.values()),
        "members": members,
        "daily": daily_series,
    })
