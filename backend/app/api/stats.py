from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import IssueStatus, ProjectRole, TaskStatus
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
        "total": 0, "pending": 0, "testing": 0, "blocked": 0, "online": 0, "done_rate": 0.0,
    }
    if not pids:
        # 无可见项目：返回全 0 结构 + 空 7 天序列（不报错）
        start = today - timedelta(days=6)
        trend = [{"date": str(start + timedelta(days=i)), "total": 0, "online": 0}
                 for i in range(7)]
        return ok({"date": str(today), "scope": scope, "project_cnt": 0,
                   "today": empty_today, "open_issues": 0, "trend": trend})

    # ---- 今日 KPI：基于派单流转状态(Task.status)，不依赖日报 ----
    status_rows = (
        db.query(Task.status, func.count(Task.id))
        .filter(Task.project_id.in_(pids), Task.assigned_date == today)
        .group_by(Task.status)
        .all()
    )
    counts = {TaskStatus.pending: 0, TaskStatus.testing: 0,
              TaskStatus.blocked: 0, TaskStatus.online: 0}
    for st, c in status_rows:
        counts[st] = c
    total = sum(counts.values())
    online_cnt = counts[TaskStatus.online]
    done_rate = round(online_cnt / total * 100, 1) if total else 0.0

    # ---- 未解决遗留问题（跨项目存量，不限今日）----
    open_issues = (
        db.query(func.count(RemainingIssue.id))
        .filter(RemainingIssue.project_id.in_(pids),
                RemainingIssue.status == IssueStatus.open)
        .scalar() or 0
    )

    # ---- 近 7 天趋势（含今日）：每日任务量 + 上线量 ----
    start = today - timedelta(days=6)
    week_rows = (
        db.query(Task.assigned_date, Task.status, func.count(Task.id))
        .filter(Task.project_id.in_(pids),
                Task.assigned_date >= start,
                Task.assigned_date <= today)
        .group_by(Task.assigned_date, Task.status)
        .all()
    )
    by_day: dict[str, dict] = {}
    for d, st, c in week_rows:
        rec = by_day.setdefault(str(d), {"total": 0, "online": 0})
        rec["total"] += c
        if st == TaskStatus.online:
            rec["online"] += c
    trend = []
    for i in range(7):
        day = str(start + timedelta(days=i))
        v = by_day.get(day)
        trend.append({"date": day,
                      "total": v["total"] if v else 0,
                      "online": v["online"] if v else 0})

    return ok({
        "date": str(today),
        "scope": scope,
        "project_cnt": len(pids),
        "today": {
            "total": total,
            "pending": counts[TaskStatus.pending],
            "testing": counts[TaskStatus.testing],
            "blocked": counts[TaskStatus.blocked],
            "online": online_cnt,
            "done_rate": done_rate,
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


@router.get("/ai")
def ai_stats(
    from_date: date = Query(default=None, alias="from"),
    to_date: date = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 战绩墙聚合：区间内生成/采纳/成本/耗时 + 维度/优先级分布 + 按天趋势。

    生成类指标按 test_case.created_at / ai_task.created_at 落在 [from,to] 筛；
    采纳类指标按 test_case.reviewed_at 落在 [from,to] 筛。现算聚合，不建统计表。

    日期比较用 func.date(col)：SQLite 返回 'YYYY-MM-DD' 字符串、MySQL 返回 date 对象，
    与 Python date 比较两端都正确（SQLite 下 ISO 字符串按字典序比较等价于日期比较，
    且 date() 会截去时间部分，故 [from,to] 含边界无需 <to+1 天的补丁）。
    分组 key 统一 str() 归一（MySQL 下是 date 对象，SQLite 下已是 str）。
    """
    from app.core.enums import AiTaskStatus, ReviewStatus
    from app.models import AiTask, TestCase

    today = date.today()
    to_d = to_date or today
    from_d = from_date or (to_d - timedelta(days=29))   # 默认近 30 天
    scope = "platform" if user.is_platform_admin else "member"
    pids = _visible_project_ids(db, user)

    DIMS = ["功能", "边界", "异常", "兼容", "性能"]
    PRIOS = ["P0", "P1", "P2", "P3"]

    def empty():
        days = (to_d - from_d).days
        trend = [{"date": str(from_d + timedelta(days=i))[5:], "generated": 0, "adopted": 0}
                 for i in range(days + 1)]
        return ok({"scope": scope, "project_cnt": 0, "from": str(from_d), "to": str(to_d),
                   "total_generated": 0, "run_cnt": 0, "total_cost_usd": 0.0, "avg_duration_s": 0.0,
                   "dims": [{"name": n, "count": 0} for n in DIMS],
                   "total_reviewed": 0, "total_adopted": 0, "adopt_rate": 0.0,
                   "prio": [{"p": p, "n": 0} for p in PRIOS], "trend": trend})

    if not pids:
        return empty()

    # ---- 生成类：test_case.created_at ∈ [from, to]（func.date 截时间，含边界）----
    gen_filter = [TestCase.project_id.in_(pids),
                  func.date(TestCase.created_at) >= from_d,
                  func.date(TestCase.created_at) <= to_d]
    total_generated = db.query(func.count(TestCase.id)).filter(*gen_filter).scalar() or 0

    # 维度覆盖计数（固定 5 类，缺补 0）
    dim_rows = (db.query(TestCase.category, func.count(TestCase.id))
                .filter(*gen_filter)
                .group_by(TestCase.category).all())
    dim_map = {k: v for k, v in dim_rows}
    dims = [{"name": n, "count": int(dim_map.get(n, 0))} for n in DIMS]

    # ai_task：done 次数 / 成本 / 平均耗时（按 ai_task.created_at 区间）
    at_filter = [AiTask.project_id.in_(pids),
                 func.date(AiTask.created_at) >= from_d,
                 func.date(AiTask.created_at) <= to_d]
    run_cnt = (db.query(func.count(AiTask.id))
               .filter(*at_filter, AiTask.status == AiTaskStatus.done).scalar() or 0)
    total_cost = (db.query(func.coalesce(func.sum(AiTask.cost_usd), 0))
                  .filter(*at_filter).scalar() or 0)
    avg_ms = (db.query(func.avg(AiTask.duration_ms))
              .filter(*at_filter,
                      AiTask.status == AiTaskStatus.done,
                      AiTask.duration_ms.isnot(None)).scalar())
    avg_duration_s = round(float(avg_ms) / 1000, 1) if avg_ms else 0.0

    # ---- 采纳类：test_case.reviewed_at ∈ [from, to] ----
    rev_filter = [TestCase.project_id.in_(pids),
                  TestCase.reviewed_at.isnot(None),
                  func.date(TestCase.reviewed_at) >= from_d,
                  func.date(TestCase.reviewed_at) <= to_d]
    total_reviewed = (db.query(func.count(TestCase.id))
                      .filter(*rev_filter,
                              TestCase.review_status.in_([ReviewStatus.adopted, ReviewStatus.rejected]))
                      .scalar() or 0)
    total_adopted = (db.query(func.count(TestCase.id))
                     .filter(*rev_filter, TestCase.review_status == ReviewStatus.adopted)
                     .scalar() or 0)
    adopt_rate = round(total_adopted / total_reviewed, 3) if total_reviewed else 0.0

    # 采纳测试点优先级分布（固定 P0-P3，缺补 0）
    prio_rows = (db.query(TestCase.priority, func.count(TestCase.id))
                 .filter(*rev_filter, TestCase.review_status == ReviewStatus.adopted,
                         TestCase.priority.isnot(None))
                 .group_by(TestCase.priority).all())
    prio_map = {k: v for k, v in prio_rows}
    prio = [{"p": p, "n": int(prio_map.get(p, 0))} for p in PRIOS]

    # ---- 趋势：每天 generated（按 created_at）+ adopted（按 reviewed_at）----
    gen_day = dict(db.query(func.date(TestCase.created_at), func.count(TestCase.id))
                   .filter(*gen_filter)
                   .group_by(func.date(TestCase.created_at)).all())
    adopt_day = dict(db.query(func.date(TestCase.reviewed_at), func.count(TestCase.id))
                     .filter(*rev_filter, TestCase.review_status == ReviewStatus.adopted)
                     .group_by(func.date(TestCase.reviewed_at)).all())
    # func.date 在 SQLite 返回 str、MySQL 返回 date；统一转 str 再比对
    gen_day = {str(k): v for k, v in gen_day.items()}
    adopt_day = {str(k): v for k, v in adopt_day.items()}
    days = (to_d - from_d).days
    trend = []
    for i in range(days + 1):
        ds = str(from_d + timedelta(days=i))
        trend.append({"date": ds[5:],  # MM-DD
                      "generated": int(gen_day.get(ds, 0)),
                      "adopted": int(adopt_day.get(ds, 0))})

    return ok({
        "scope": scope, "project_cnt": len(pids), "from": str(from_d), "to": str(to_d),
        "total_generated": int(total_generated), "run_cnt": int(run_cnt),
        "total_cost_usd": round(float(total_cost), 2), "avg_duration_s": avg_duration_s,
        "dims": dims,
        "total_reviewed": int(total_reviewed), "total_adopted": int(total_adopted),
        "adopt_rate": adopt_rate, "prio": prio, "trend": trend,
    })
