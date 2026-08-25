from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import IssueStatus, ProjectRole, ReviewStatus, TaskStatus
from app.db.session import get_db
from app.models import DailyReport, ExecRun, Project, ProjectMember, RemainingIssue, Task, TestCase, User
from app.schemas.common import ok
from app.services.claude_runner import _SELECTOR_FIX_MARK

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
        "total": 0, "pending": 0, "testing": 0, "blocked": 0, "online": 0,
        "closed": 0, "done_cnt": 0, "done_rate": 0.0,
    }
    if not pids:
        # 无可见项目：返回全 0 结构 + 空 7 天序列（不报错）
        start = today - timedelta(days=6)
        trend = [{"date": str(start + timedelta(days=i)), "total": 0, "online": 0}
                 for i in range(7)]
        return ok({"date": str(today), "scope": scope, "project_cnt": 0,
                   "today": empty_today, "open_issues": 0, "trend": trend})

    # ---- 今日 KPI：基于派单流转状态(Task.status)，不依赖日报 ----
    # 口径：今日派发的全部任务 + 历史派发但仍处于 testing/blocked 的延期任务。
    status_rows = (
        db.query(Task.status, func.count(Task.id))
        .filter(
            Task.project_id.in_(pids),
            or_(
                Task.assigned_date == today,
                and_(Task.assigned_date < today,
                     Task.status.in_([TaskStatus.testing, TaskStatus.blocked])),
            ),
        )
        .group_by(Task.status)
        .all()
    )
    counts = {TaskStatus.pending: 0, TaskStatus.testing: 0,
              TaskStatus.blocked: 0, TaskStatus.online: 0, TaskStatus.closed: 0}
    for st, c in status_rows:
        counts[st] = c
    total = sum(counts.values())
    done_cnt = counts[TaskStatus.online] + counts[TaskStatus.closed]
    done_rate = round(done_cnt / total * 100, 1) if total else 0.0

    # ---- 未解决遗留问题（跨项目存量，不限今日）----
    # 两条来源：report 路径（project_id 命中）与 task 直挂路径（task_id 指向可见项目的任务）。
    # RemainingIssue.project_id 两路径都会带上，故 project_id.in_(pids) 已覆盖大部分；
    # 但为兼容历史 task 直挂 issue 的 project_id 与其 task 项目一致的约定，按 id 取并集去重防双算。
    open_ids = {
        iid for (iid,) in
        db.query(RemainingIssue.id)
        .filter(RemainingIssue.project_id.in_(pids),
                RemainingIssue.status == IssueStatus.open)
        .all()
    }
    task_open_ids = {
        iid for (iid,) in
        db.query(RemainingIssue.id)
        .join(Task, Task.id == RemainingIssue.task_id)
        .filter(Task.project_id.in_(pids),
                RemainingIssue.status == IssueStatus.open)
        .all()
    }
    open_issues = len(open_ids | task_open_ids)

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
        if st in (TaskStatus.online, TaskStatus.closed):
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
            "online": counts[TaskStatus.online],
            "closed": counts[TaskStatus.closed],
            "done_cnt": done_cnt,
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

    # 一次预取本次涉及的所有用户名(应交 + 已交),消除 name() 的逐用户 N+1
    all_uids = {u for u in should_submit_ids if u} | {r.user_id for r in submitted_rows if r.user_id}
    name_map = dict(db.query(User.id, User.name).filter(User.id.in_(all_uids)).all()) if all_uids else {}

    def name(uid):
        return name_map.get(uid, "")

    avg_progress = round(sum(r.progress_pct for r in submitted_rows) / len(submitted_rows), 1) if submitted_rows else 0
    online_cnt = sum(1 for r in submitted_rows if r.is_online)
    workload_total = round(sum(float(r.workload_hours or 0) for r in submitted_rows), 1)

    report_ids = [r.id for r in submitted_rows]
    # open_issues 两条来源，按 id 去重：
    # (1) report 路径：该日已交日报下挂的 open issue；
    # (2) task 路径：task_id 指向"本项目、assigned_date==该日"的任务的 open issue。
    report_open_ids = {
        iid for (iid,) in
        db.query(RemainingIssue.id)
        .filter(RemainingIssue.report_id.in_(report_ids),
                RemainingIssue.status == IssueStatus.open)
        .all()
    } if report_ids else set()
    task_open_ids = {
        iid for (iid,) in
        db.query(RemainingIssue.id)
        .join(Task, Task.id == RemainingIssue.task_id)
        .filter(Task.project_id == project_id,
                Task.assigned_date == date,
                RemainingIssue.status == IssueStatus.open)
        .all()
    }
    open_issues = len(report_open_ids | task_open_ids)
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
    """工作量统计：按成员聚合任务数/上线数 + 每日趋势序列。

    口径基于 task 派单（非日报）：工作量 = 任务数量（条），成员按 assigned_to 分组，
    上线数 = status==online 的任务数。对 task 现算聚合，不建统计表。
    """
    assert_project_role(db, user, project_id,
                        (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    # 聚合下推 SQL:不再全量拉 Task 到内存。online 用条件求和(status==online 计 1)。
    online_sum = func.sum(case((Task.status.in_([TaskStatus.online, TaskStatus.closed]), 1), else_=0))
    base = [Task.project_id == project_id,
            Task.assigned_date >= from_date,
            Task.assigned_date <= to_date]

    # 按成员聚合:任务数 + 上线数
    member_rows = (
        db.query(Task.assigned_to, func.count(Task.id), online_sum)
        .filter(*base)
        .group_by(Task.assigned_to)
        .all()
    )
    # 按天聚合:任务数 + 上线数
    day_rows = (
        db.query(Task.assigned_date, func.count(Task.id), online_sum)
        .filter(*base)
        .group_by(Task.assigned_date)
        .all()
    )

    # 一次预取成员名,消除逐用户 N+1
    uids = {uid for uid, _, _ in member_rows if uid is not None}
    name_map = dict(db.query(User.id, User.name).filter(User.id.in_(uids)).all()) if uids else {}

    members = [{
        "user_id": uid, "name": name_map.get(uid, ""),
        "task_cnt": int(cnt), "online_cnt": int(online or 0),
    } for uid, cnt, online in member_rows]
    members.sort(key=lambda x: x["task_cnt"], reverse=True)

    daily_series = [{"date": str(d), "task_cnt": int(cnt), "online_cnt": int(online or 0)}
                    for d, cnt, online in day_rows]
    daily_series.sort(key=lambda x: x["date"])

    total_tasks = sum(m["task_cnt"] for m in members)
    total_online = sum(m["online_cnt"] for m in members)
    return ok({
        "project_id": project_id,
        "from": str(from_date), "to": str(to_date),
        "total_tasks": total_tasks,
        "total_online": total_online,
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
                   "prio": [{"p": p, "n": 0} for p in PRIOS], "trend": trend,
                   "by_provider": []})

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
    # 只统计测试点生成(kind=testcase_gen)；对话测评 query 生成(eval_query_gen)不污染测试点战绩墙。
    at_filter = [AiTask.project_id.in_(pids),
                 AiTask.kind == "testcase_gen",
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

    # ---- 按引擎(provider)对比:各引擎的 生成/采纳/成本/耗时 ----
    # 生成数、采纳数按 test_case.provider 分组;成本/耗时/生成次数按 ai_task.provider 分组。
    gen_by_prov = dict(db.query(TestCase.provider, func.count(TestCase.id))
                       .filter(*gen_filter).group_by(TestCase.provider).all())
    rev_by_prov = dict(db.query(TestCase.provider, func.count(TestCase.id))
                       .filter(*rev_filter,
                               TestCase.review_status.in_([ReviewStatus.adopted, ReviewStatus.rejected]))
                       .group_by(TestCase.provider).all())
    adopt_by_prov = dict(db.query(TestCase.provider, func.count(TestCase.id))
                         .filter(*rev_filter, TestCase.review_status == ReviewStatus.adopted)
                         .group_by(TestCase.provider).all())
    cost_by_prov = dict(db.query(AiTask.provider, func.coalesce(func.sum(AiTask.cost_usd), 0))
                        .filter(*at_filter).group_by(AiTask.provider).all())
    run_by_prov = dict(db.query(AiTask.provider, func.count(AiTask.id))
                       .filter(*at_filter, AiTask.status == AiTaskStatus.done)
                       .group_by(AiTask.provider).all())
    dur_by_prov = dict(db.query(AiTask.provider, func.avg(AiTask.duration_ms))
                       .filter(*at_filter, AiTask.status == AiTaskStatus.done,
                               AiTask.duration_ms.isnot(None))
                       .group_by(AiTask.provider).all())
    # 汇总所有出现过的 provider(生成侧或任务侧任一有记录即列出)
    prov_ids = set(gen_by_prov) | set(rev_by_prov) | set(cost_by_prov) | set(run_by_prov)
    by_provider = []
    for p in sorted(prov_ids):
        reviewed = int(rev_by_prov.get(p, 0))
        adopted = int(adopt_by_prov.get(p, 0))
        avg_ms_p = dur_by_prov.get(p)
        by_provider.append({
            "provider": p,
            "generated": int(gen_by_prov.get(p, 0)),
            "reviewed": reviewed,
            "adopted": adopted,
            "adopt_rate": round(adopted / reviewed, 3) if reviewed else 0.0,
            "run_cnt": int(run_by_prov.get(p, 0)),
            "cost_usd": round(float(cost_by_prov.get(p, 0) or 0), 2),
            "avg_duration_s": round(float(avg_ms_p) / 1000, 1) if avg_ms_p else 0.0,
        })

    return ok({
        "scope": scope, "project_cnt": len(pids), "from": str(from_d), "to": str(to_d),
        "total_generated": int(total_generated), "run_cnt": int(run_cnt),
        "total_cost_usd": round(float(total_cost), 2), "avg_duration_s": avg_duration_s,
        "dims": dims,
        "total_reviewed": int(total_reviewed), "total_adopted": int(total_adopted),
        "adopt_rate": adopt_rate, "prio": prio, "trend": trend,
        "by_provider": by_provider,
    })


@router.get("/ai-funnel")
def ai_funnel(
    days: int = 30,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 全链路价值漏斗:生成→采纳→可自动化→已执行→通过,附真bug数/选择器卡点/省时。

    时间窗 [today-days+1, today];项目范围 = 当前用户可见项目。全部现算不建表。
    selector_pending 是当前存量卡点(不限时间窗)。saved_hours 按每条执行折算 5 分钟人工。
    """
    if days <= 0 or days > 365:
        days = 30
    pids = _visible_project_ids(db, user)
    today = date.today()
    d_from = today - timedelta(days=days - 1)

    def _win(q, col):
        return q.filter(func.date(col) >= d_from, func.date(col) <= today)

    def _tc_q(*flt):
        return db.query(func.count(TestCase.id)).filter(TestCase.project_id.in_(pids), *flt)

    generated = _win(_tc_q(), TestCase.created_at).scalar() or 0
    adopted = _win(_tc_q(TestCase.review_status == ReviewStatus.adopted),
                   TestCase.created_at).scalar() or 0
    automatable = _win(_tc_q(TestCase.review_status == ReviewStatus.adopted,
                             TestCase.exec_kind != "manual"),
                       TestCase.created_at).scalar() or 0

    def _run_q(*flt):
        return db.query(func.count(ExecRun.id)).filter(ExecRun.project_id.in_(pids), *flt)

    executed = _win(_run_q(ExecRun.status.in_(["passed", "failed", "blocked"])),
                    ExecRun.created_at).scalar() or 0
    passed = _win(_run_q(ExecRun.status == "passed"), ExecRun.created_at).scalar() or 0
    bugs_found = _win(_run_q(ExecRun.fail_kind == "business"), ExecRun.created_at).scalar() or 0

    selector_pending = _tc_q(
        TestCase.review_status == ReviewStatus.adopted,
        TestCase.kind_reason.like(f"{_SELECTOR_FIX_MARK}%"),
    ).scalar() or 0

    return ok({
        "from": str(d_from), "to": str(today), "days": days,
        "funnel": [
            {"stage": "generated", "label": "AI 生成", "count": generated},
            {"stage": "adopted", "label": "已采纳", "count": adopted},
            {"stage": "automatable", "label": "可自动化", "count": automatable},
            {"stage": "executed", "label": "已执行", "count": executed},
            {"stage": "passed", "label": "执行通过", "count": passed},
        ],
        "adopt_rate": round(adopted / generated * 100, 1) if generated else 0.0,
        "bugs_found": bugs_found,
        "selector_pending": selector_pending,
        "saved_hours": round(executed * 5 / 60, 1),
    })
