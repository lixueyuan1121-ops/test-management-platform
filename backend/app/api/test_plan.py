"""测试计划 API（TestPlan）——主用例库的可保存集合 + 定时回归。

把 FeedbackRegressionSet 验证过的「命名集合 + cron 定时 + 一键整集跑」模式泛化到主用例库
（test_case 体系），对标主流平台（TestRail Test Plan / MeterSphere 测试计划）的基础形态。

沿用全项目约定：{code,msg,data} 信封（ok/fail）、手写 _to_out、体外 assert_project_role。
下发复用 exec_queue 的 payload 快照/平台校验/批次号；批次元数据记 test_plan_run
（对位 feedback_run），结果按 batch_id 聚合 exec_run 现算（不建独立统计表）。

路由顺序注意：/runs 一族必须注册在 /{pid} 之前，否则 "runs" 会被当成 pid 解析失败。
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ExecKind, ExecStatus, ProjectRole
from app.db.session import get_db
from app.models import ExecRun, TestCase, TestPlan, TestPlanCase, TestPlanRun, User
from app.schemas.common import ok
from app.schemas.test_plan import (
    PlanCasesIn, PlanCreateIn, PlanRunIn, PlanScheduleIn, PlanUpdateIn,
)

router = APIRouter(prefix="/api/test-plans", tags=["test-plans"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)
_READ_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)


def _plan_out(db: Session, p: TestPlan) -> dict:
    case_count = (db.query(func.count(TestPlanCase.id))
                  .filter(TestPlanCase.plan_id == p.id).scalar() or 0)
    return {
        "id": p.id,
        "project_id": p.project_id,
        "name": p.name,
        "description": p.description,
        "runner": p.runner,
        "schedule_cron": p.schedule_cron,
        "schedule_enabled": p.schedule_enabled,
        "case_count": case_count,
        "last_run_at": p.last_run_at.isoformat() if p.last_run_at else None,
        "next_run_at": p.next_run_at.isoformat() if p.next_run_at else None,
        "created_by": p.created_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _aggregate_batch(db: Session, project_id: int, batch_id: str) -> dict:
    """按 batch_id 聚合 exec_run 现算（与 feedback._aggregate_batch 同口径,含重试链聚合）。"""
    from app.api.exec_queue import effective_runs
    rows = effective_runs(
        db.query(ExecRun)
        .filter(ExecRun.project_id == project_id, ExecRun.batch_id == batch_id).all()
    )
    counts: dict = {}
    flaky = 0
    for r in rows:
        key = r.status.value if hasattr(r.status, "value") else r.status
        counts[key] = counts.get(key, 0) + 1
        if getattr(r, "flaky", False):
            flaky += 1
    total = sum(counts.values())
    done = total - counts.get("pending", 0) - counts.get("running", 0)
    return {
        "total": total,
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "blocked": counts.get("blocked", 0),
        "pending": counts.get("pending", 0),
        "running": counts.get("running", 0),
        "flaky": flaky,
        "finished": total > 0 and done == total,
    }


def _auto_case_ids_of_plan(db: Session, plan_id: int) -> list[int]:
    """计划内可自动化用例 id（跳过 manual——无人值守不能因一个 manual 整批失败）。"""
    rows = (db.query(TestPlanCase.case_id, TestCase.exec_kind)
            .join(TestCase, TestCase.id == TestPlanCase.case_id)
            .filter(TestPlanCase.plan_id == plan_id)
            .order_by(TestPlanCase.id).all())
    return [cid for cid, kind in rows if (kind or "gui") != "manual"]


def _dispatch_plan(db: Session, plan: TestPlan, case_ids: list[int], runner: str,
                   trigger: str, started_by: int | None) -> dict:
    """把计划内用例下发成一批 exec_run + 建 test_plan_run 元数据（手动/定时共用）。

    复用 exec_queue 的 payload 快照(_payload_of)/kind 判定/平台校验/批次号——
    保证计划执行与「回归库勾选执行」产出完全同构的 run，runner 侧零改动。
    """
    from app.api.exec_queue import _check_platform, _kind_of, _new_batch_id, _payload_of

    ids = list(dict.fromkeys(case_ids))
    cases = db.query(TestCase).filter(TestCase.id.in_(ids)).all()
    found = {c.id: c for c in cases}
    for cid in ids:
        tc = found.get(cid)
        if tc is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"用例 {cid} 不存在")
        if tc.project_id != plan.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"用例 {cid} 不属于该项目")
        if _kind_of(tc) == ExecKind.manual:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"用例 {cid} 为『人工/不可自动化(manual)』,不能下发到执行机",
            )
        tc_platform = getattr(tc, "platform", "web") or "web"
        _check_platform(runner, tc_platform, db, owner_id=started_by)

    batch_id = _new_batch_id()
    run_ids = []
    for cid in ids:
        tc = found[cid]
        row = ExecRun(
            checklist_item_id=None,          # 计划执行不挂清单项（与回归库执行一致）
            test_case_id=tc.id,
            task_id=getattr(tc, "task_id", None),
            project_id=tc.project_id,
            batch_id=batch_id,
            runner=runner,
            kind=_kind_of(tc),
            status=ExecStatus.pending,
            payload=json.dumps(_payload_of(tc, db), ensure_ascii=False),
            enqueued_by=started_by,
        )
        db.add(row)
        db.flush()
        run_ids.append(row.id)

    pr = TestPlanRun(
        project_id=plan.project_id, plan_id=plan.id, batch_id=batch_id,
        trigger=trigger, case_count=len(ids), started_by=started_by,
    )
    db.add(pr)
    plan.last_run_at = datetime.utcnow()
    db.commit()
    db.refresh(pr)
    return {"batch_id": batch_id, "run_ids": run_ids, "plan_run_id": pr.id}


# ==================== 执行历史（注册在 /{pid} 之前）====================

@router.get("/runs")
def list_runs(
    project_id: int = Query(...),
    plan_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """计划执行批次列表，每条按 batch_id 聚合 exec_run 现算结果。"""
    assert_project_role(db, user, project_id, _READ_ROLES)
    q = db.query(TestPlanRun).filter(TestPlanRun.project_id == project_id)
    if plan_id is not None:
        q = q.filter(TestPlanRun.plan_id == plan_id)
    rows = q.order_by(TestPlanRun.id.desc()).limit(100).all()
    plan_ids = {r.plan_id for r in rows if r.plan_id}
    names = {}
    if plan_ids:
        for pl in db.query(TestPlan).filter(TestPlan.id.in_(plan_ids)).all():
            names[pl.id] = pl.name
    return ok([
        {
            "id": r.id, "plan_id": r.plan_id, "plan_name": names.get(r.plan_id),
            "batch_id": r.batch_id, "trigger": r.trigger, "case_count": r.case_count,
            "started_by": r.started_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "stats": _aggregate_batch(db, r.project_id, r.batch_id),
        }
        for r in rows
    ])


@router.get("/runs/{rid}")
def get_run(
    rid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """单次计划执行详情：逐条 exec_run（含 verdict/report）。"""
    pr = db.get(TestPlanRun, rid)
    if not pr:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行记录不存在")
    assert_project_role(db, user, pr.project_id, _READ_ROLES)
    runs = (db.query(ExecRun)
            .filter(ExecRun.project_id == pr.project_id, ExecRun.batch_id == pr.batch_id)
            .order_by(ExecRun.id).all())
    items = []
    for r in runs:
        try:
            payload = json.loads(r.payload or "{}")
        except (json.JSONDecodeError, ValueError):
            payload = {}
        try:
            report = json.loads(r.report) if r.report else None
        except (json.JSONDecodeError, ValueError):
            report = None
        items.append({
            "run_id": r.id,
            "test_case_id": r.test_case_id,
            "title": payload.get("title"),
            "kind": getattr(r.kind, "value", r.kind),
            "status": getattr(r.status, "value", r.status),
            "verdict": r.verdict, "fail_kind": r.fail_kind, "reason": r.reason,
            "evidence_url": r.evidence_url, "report": report,
            "duration_ms": r.duration_ms,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })
    return ok({
        "id": pr.id, "plan_id": pr.plan_id, "batch_id": pr.batch_id,
        "trigger": pr.trigger, "case_count": pr.case_count,
        "created_at": pr.created_at.isoformat() if pr.created_at else None,
        "stats": _aggregate_batch(db, pr.project_id, pr.batch_id),
        "items": items,
    })


# ==================== 计划 CRUD ====================

@router.get("")
def list_plans(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_project_role(db, user, project_id, _READ_ROLES)
    rows = (db.query(TestPlan).filter(TestPlan.project_id == project_id)
            .order_by(TestPlan.id.desc()).all())
    return ok([_plan_out(db, p) for p in rows])


@router.post("")
def create_plan(
    body: PlanCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    p = TestPlan(project_id=body.project_id, name=body.name,
                 description=body.description, runner=body.runner, created_by=user.id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return ok(_plan_out(db, p))


@router.get("/{pid}")
def get_plan(
    pid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """计划详情（含关联用例列表，供计划内用例管理）。"""
    p = db.get(TestPlan, pid)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="计划不存在")
    assert_project_role(db, user, p.project_id, _READ_ROLES)
    rows = (db.query(TestPlanCase, TestCase)
            .join(TestCase, TestCase.id == TestPlanCase.case_id)
            .filter(TestPlanCase.plan_id == pid)
            .order_by(TestPlanCase.id).all())
    out = _plan_out(db, p)
    out["cases"] = [
        {
            "id": tc.id, "title": tc.title, "category": tc.category,
            "priority": tc.priority, "exec_kind": tc.exec_kind,
            "platform": getattr(tc, "platform", "web") or "web",
            "has_script": bool(tc.script),
            "added_at": link.created_at.isoformat() if link.created_at else None,
        }
        for link, tc in rows
    ]
    return ok(out)


@router.patch("/{pid}")
def update_plan(
    pid: int,
    body: PlanUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = db.get(TestPlan, pid)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="计划不存在")
    assert_project_role(db, user, p.project_id, _WRITE_ROLES)
    if body.name is not None:
        p.name = body.name
    if body.description is not None:
        p.description = body.description
    if body.runner is not None:
        p.runner = body.runner
    db.commit()
    db.refresh(p)
    return ok(_plan_out(db, p))


@router.delete("/{pid}")
def delete_plan(
    pid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删计划：先摘调度 job 再删行（关联 test_plan_case 级联删，test_plan_run 置空保留追溯）。"""
    p = db.get(TestPlan, pid)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="计划不存在")
    assert_project_role(db, user, p.project_id, _WRITE_ROLES)
    try:
        from app.services.scheduler import sync_plan_job
        sync_plan_job(pid, None, False)   # 摘 job（无 job 时静默）
    except Exception:
        pass
    db.delete(p)
    db.commit()
    return ok({"deleted": pid})


# ==================== 计划内用例增删 ====================

@router.get("/{pid}/candidate-cases")
def candidate_cases(
    pid: int,
    keyword: str | None = Query(None),
    only_regression: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """可加入计划的候选用例（同项目、已采纳、未入本计划；manual 也可入计划但执行时跳过）。"""
    from app.core.enums import ReviewStatus

    p = db.get(TestPlan, pid)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="计划不存在")
    assert_project_role(db, user, p.project_id, _READ_ROLES)
    in_plan = {r.case_id for r in db.query(TestPlanCase).filter(TestPlanCase.plan_id == pid).all()}
    q = (db.query(TestCase)
         .filter(TestCase.project_id == p.project_id,
                 TestCase.review_status == ReviewStatus.adopted))
    if only_regression:
        q = q.filter(TestCase.is_regression.is_(True))
    if keyword:
        q = q.filter(TestCase.title.like(f"%{keyword}%"))
    rows = q.order_by(TestCase.id.desc()).limit(300).all()
    return ok([
        {
            "id": tc.id, "title": tc.title, "category": tc.category,
            "priority": tc.priority, "exec_kind": tc.exec_kind,
            "platform": getattr(tc, "platform", "web") or "web",
            "is_regression": tc.is_regression, "has_script": bool(tc.script),
            "in_plan": tc.id in in_plan,
        }
        for tc in rows
    ])


@router.post("/{pid}/cases")
def add_cases(
    pid: int,
    body: PlanCasesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把用例加入计划（幂等：已在计划内的跳过）。校验同项目。"""
    p = db.get(TestPlan, pid)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="计划不存在")
    assert_project_role(db, user, p.project_id, _WRITE_ROLES)
    ids = list(dict.fromkeys(body.case_ids))
    cases = {c.id: c for c in db.query(TestCase).filter(TestCase.id.in_(ids)).all()}
    for cid in ids:
        tc = cases.get(cid)
        if tc is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"用例 {cid} 不存在")
        if tc.project_id != p.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"用例 {cid} 不属于该项目")
    existing = {r.case_id for r in db.query(TestPlanCase)
                .filter(TestPlanCase.plan_id == pid, TestPlanCase.case_id.in_(ids)).all()}
    added = 0
    for cid in ids:
        if cid in existing:
            continue
        db.add(TestPlanCase(plan_id=pid, case_id=cid))
        added += 1
    db.commit()
    return ok({"added": added, "skipped": len(ids) - added})


@router.delete("/{pid}/cases")
def remove_cases(
    pid: int,
    body: PlanCasesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = db.get(TestPlan, pid)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="计划不存在")
    assert_project_role(db, user, p.project_id, _WRITE_ROLES)
    removed = (db.query(TestPlanCase)
               .filter(TestPlanCase.plan_id == pid, TestPlanCase.case_id.in_(body.case_ids))
               .delete(synchronize_session=False))
    db.commit()
    return ok({"removed": removed})


# ==================== 立即执行 / 定时 ====================

@router.post("/{pid}/run")
def run_plan(
    pid: int,
    body: PlanRunIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """立即整计划执行（下发计划内所有可自动化用例，自动跳过 manual，trigger=manual）。"""
    p = db.get(TestPlan, pid)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="计划不存在")
    assert_project_role(db, user, p.project_id, _WRITE_ROLES)
    case_ids = _auto_case_ids_of_plan(db, pid)
    if not case_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="计划内无可自动化用例（manual 用例不下发）")
    runner = (body.runner if body and body.runner else None) or p.runner
    res = _dispatch_plan(db, p, case_ids, runner, trigger="manual", started_by=user.id)
    return ok(res)


@router.patch("/{pid}/schedule")
def set_schedule(
    pid: int,
    body: PlanScheduleIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """设置计划的定时执行。cron 存库 + 联动调度器 add/remove job + 回填 next_run_at。"""
    from app.services.scheduler import sync_plan_job

    p = db.get(TestPlan, pid)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="计划不存在")
    assert_project_role(db, user, p.project_id, _WRITE_ROLES)
    if body.enabled:
        if not body.cron:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="启用定时需提供 cron 表达式")
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(body.cron)
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"cron 表达式非法：{body.cron}")
    p.schedule_cron = body.cron
    p.schedule_enabled = body.enabled
    next_run = sync_plan_job(pid, body.cron, body.enabled)
    p.next_run_at = next_run.replace(tzinfo=None) if next_run else None
    db.commit()
    db.refresh(p)
    return ok(_plan_out(db, p))
