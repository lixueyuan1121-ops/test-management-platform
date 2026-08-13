"""验收清单路由：任务清单查询、手动补挂、勾执行结果、失败转遗留。

清单项由采纳测试点自动挂载（见 ai.py review_testcase 副作用）或手动补挂。
权限：清单项所属项目 member/admin 可写（不限 assigned_to，本版放开协作），guest 只读。
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ChecklistStatus, IssueStatus, ProjectRole, ReviewStatus, TaskStatus
from app.db.session import get_db
from app.models import ChecklistItem, ExecRun, RemainingIssue, Task, TestCase, User
from app.schemas.checklist import AttachChecklistIn, ChecklistToIssueIn, ChecklistTickIn
from app.schemas.common import ok

router = APIRouter(prefix="/api", tags=["checklist"])

_ALL_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def _user_name(db: Session, uid: int | None) -> str:
    if not uid:
        return ""
    u = db.get(User, uid)
    return u.name if u else ""


def _to_out(db: Session, item: ChecklistItem) -> dict:
    tc = db.get(TestCase, item.test_case_id)
    # 最近一次自动执行(exec_run)的回写结果——供前端在清单项上展示失败/通过原因。
    # 按 id 倒序取最新一条(同一清单项可被多次下发执行,只关心最近结果)。
    last_run = (
        db.query(ExecRun)
        .filter(ExecRun.checklist_item_id == item.id)
        .order_by(ExecRun.id.desc())
        .first()
    )
    return {
        "id": item.id,
        "task_id": item.task_id,
        "test_case_id": item.test_case_id,
        "project_id": item.project_id,
        "exec_status": item.exec_status.value,
        "executed_by": item.executed_by,
        "executed_by_name": _user_name(db, item.executed_by),
        "executed_at": item.executed_at.isoformat() if item.executed_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        # 关联 test_case 展示字段（补挂/采纳的来源测试点）
        "title": tc.title if tc else "",
        "category": tc.category if tc else None,
        "exec_kind": getattr(tc, "exec_kind", "gui") if tc else "gui",  # 前端据此禁止下发 manual 用例
        "steps": tc.steps if tc else None,
        "expected": tc.expected if tc else None,
        "priority": tc.priority if tc else None,
        # 最近一次自动执行的结果（无则为 None，前端据此显示"查看原因"）
        "exec_run_id": last_run.id if last_run else None,
        "exec_verdict": last_run.verdict if last_run else None,
        "exec_reason": last_run.reason if last_run else None,
        "exec_runner": last_run.runner if last_run else None,
        "exec_run_status": last_run.status.value if last_run else None,
        "exec_run_at": last_run.updated_at.isoformat() if last_run and last_run.updated_at else None,
    }


@router.get("/tasks/checklist-summary")
def list_checklist_summary(
    project_id: int = Query(...),
    on_date: date = Query(..., alias="date"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """管理员任务表用：某项目某日各任务的验收进度汇总（只读，SQL 现算聚合）。

    返回 map：{ "<task_id>": {total, passed, failed, blocked, pending} }，
    只含有清单项的任务；无清单项的任务不出现（前端据此显示 —）。

    顺延可见：与 list_tasks 口径一致——除当天派单的任务外，还带出更早日期但仍未完成
    （status ∉ {online, closed}）的任务，否则顺延到次日的任务其清单/下发入口会消失。
    """
    assert_project_role(db, user, project_id, _ALL_ROLES)
    task_ids = [
        tid for (tid,) in
        db.query(Task.id)
        .filter(
            Task.project_id == project_id,
            or_(
                Task.assigned_date == on_date,
                (Task.assigned_date < on_date) & Task.status.notin_(
                    [TaskStatus.online, TaskStatus.closed]
                ),
            ),
        )
        .all()
    ]
    if not task_ids:
        return ok({})
    rows = (
        db.query(ChecklistItem.task_id, ChecklistItem.exec_status, func.count(ChecklistItem.id))
        .filter(ChecklistItem.task_id.in_(task_ids))
        .group_by(ChecklistItem.task_id, ChecklistItem.exec_status)
        .all()
    )
    summary: dict[str, dict] = {}
    for tid, st, cnt in rows:
        key = str(tid)
        rec = summary.setdefault(key, {"total": 0, "passed": 0, "failed": 0, "blocked": 0, "pending": 0})
        # st 是 ChecklistStatus 枚举；取 .value 作为 key（pending/passed/failed/blocked）
        # GROUP BY 保证每个 (task_id, status) 只有一行，直接赋值即可
        rec[st.value] = int(cnt)
        rec["total"] += int(cnt)
    return ok(summary)


@router.get("/tasks/{tid}/checklist")
def get_task_checklist(
    tid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """取任务的验收清单（清单项 + 关联 test_case 展示字段）。"""
    task = db.get(Task, tid)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    assert_project_role(db, user, task.project_id, _ALL_ROLES)
    rows = (
        db.query(ChecklistItem)
        .filter(ChecklistItem.task_id == tid)
        .order_by(ChecklistItem.id)
        .all()
    )
    return ok([_to_out(db, it) for it in rows])


@router.post("/tasks/{tid}/checklist")
def attach_checklist(
    tid: int,
    body: AttachChecklistIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """手动补挂：把已采纳、同项目的 test_case 批量加入任务清单。

    整体校验：任一 test_case 未采纳或跨项目 → 400，整批拒绝。
    幂等：已存在的 (task_id, test_case_id) 跳过，不报错。
    """
    task = db.get(Task, tid)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)

    ids = list(dict.fromkeys(body.test_case_ids))  # 去重保序
    cases = db.query(TestCase).filter(TestCase.id.in_(ids)).all()
    found = {c.id: c for c in cases}
    for cid in ids:
        c = found.get(cid)
        if c is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测试点 {cid} 不存在")
        if c.project_id != task.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测试点 {cid} 不属于该任务的项目")
        if c.review_status != ReviewStatus.adopted:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测试点 {cid} 未采纳，不能加入清单")

    existing = {
        it.test_case_id
        for it in db.query(ChecklistItem.test_case_id)
        .filter(ChecklistItem.task_id == tid, ChecklistItem.test_case_id.in_(ids))
        .all()
    }
    for cid in ids:
        if cid in existing:
            continue
        db.add(ChecklistItem(task_id=tid, test_case_id=cid, project_id=task.project_id))
    db.commit()

    rows = (
        db.query(ChecklistItem)
        .filter(ChecklistItem.task_id == tid, ChecklistItem.test_case_id.in_(ids))
        .order_by(ChecklistItem.id)
        .all()
    )
    return ok([_to_out(db, it) for it in rows])


@router.patch("/checklist/{item_id}")
def tick_checklist(
    item_id: int,
    body: ChecklistTickIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """勾执行结果 passed/failed/blocked/pending。回 pending 时清空 executed_by/at。"""
    item = db.get(ChecklistItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="清单项不存在")
    assert_project_role(db, user, item.project_id, _WRITE_ROLES)
    item.exec_status = body.exec_status
    if body.exec_status == ChecklistStatus.pending:
        item.executed_by = None
        item.executed_at = None
    else:
        item.executed_by = user.id
        item.executed_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return ok(_to_out(db, item))


@router.post("/checklist/{item_id}/to-issue")
def checklist_to_issue(
    item_id: int,
    body: ChecklistToIssueIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """失败清单项一键转遗留问题。前置：exec_status==failed。

    创建 RemainingIssue：report_id=None（走任务直挂新路径），task_id/checklist_item_id
    指向来源，status=open。title 缺省用来源 test_case.title。
    """
    item = db.get(ChecklistItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="清单项不存在")
    assert_project_role(db, user, item.project_id, _WRITE_ROLES)
    if item.exec_status != ChecklistStatus.failed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="仅失败的清单项可转遗留问题")

    tc = db.get(TestCase, item.test_case_id)
    title = (body.title or "").strip() or (tc.title if tc else "未命名遗留问题")
    issue = RemainingIssue(
        report_id=None,
        task_id=item.task_id,
        checklist_item_id=item.id,
        project_id=item.project_id,
        title=title[:255],
        severity=body.severity,
        status=IssueStatus.open,
        owner=body.owner,
        external_ref=body.external_ref,
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)
    return ok({
        "id": issue.id,
        "title": issue.title,
        "severity": issue.severity.value,
        "status": issue.status.value,
        "project_id": issue.project_id,
        "task_id": issue.task_id,
        "checklist_item_id": issue.checklist_item_id,
        "report_id": issue.report_id,
        "owner": issue.owner,
        "external_ref": issue.external_ref,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
    })


@router.get("/tasks/{tid}/adoptable-cases")
def list_adoptable_cases(
    tid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出该任务所在项目、已采纳、且尚未进本任务清单的 test_case（供手动补挂弹窗选择）。"""
    task = db.get(Task, tid)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    assert_project_role(db, user, task.project_id, _ALL_ROLES)
    attached = {
        cid for (cid,) in
        db.query(ChecklistItem.test_case_id).filter(ChecklistItem.task_id == tid).all()
    }
    rows = (
        db.query(TestCase)
        .filter(TestCase.project_id == task.project_id,
                TestCase.review_status == ReviewStatus.adopted)
        .order_by(TestCase.id.desc())
        .all()
    )
    out = [
        {"id": tc.id, "title": tc.title, "category": tc.category, "priority": tc.priority}
        for tc in rows if tc.id not in attached
    ]
    return ok(out)
