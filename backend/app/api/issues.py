from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import IssueStatus, ProjectRole
from app.db.session import get_db
from app.models import DailyReport, RemainingIssue, Task, User
from app.schemas.common import ok
from app.schemas.issue import IssueUpdate

router = APIRouter(prefix="/api/issues", tags=["issues"])


def _user_name(db: Session, uid: int | None) -> str:
    if not uid:
        return ""
    u = db.get(User, uid)
    return u.name if u else ""


def _issue_task_title(db: Session, it: RemainingIssue) -> str:
    """取遗留问题所属任务名，兼容两条来源：report 路径(report→task) 与 task 直挂路径(task_id)。"""
    if it.report_id is not None:
        r = db.get(DailyReport, it.report_id)
        if r:
            t = db.get(Task, r.task_id)
            return t.title if t else ""
    if it.task_id is not None:
        t = db.get(Task, it.task_id)
        return t.title if t else ""
    return ""


def _name_map(db: Session, uids) -> dict:
    ids = {u for u in uids if u}
    return dict(db.query(User.id, User.name).filter(User.id.in_(ids)).all()) if ids else {}


def _to_out(db: Session, it: RemainingIssue, names: dict | None = None, titles: dict | None = None) -> dict:
    owner_name = names.get(it.owner, "") if names is not None else _user_name(db, it.owner)
    task_title = titles.get(it.id, "") if titles is not None else _issue_task_title(db, it)
    return {
        "id": it.id, "report_id": it.report_id, "project_id": it.project_id,
        "task_id": it.task_id, "checklist_item_id": it.checklist_item_id,
        "title": it.title, "description": it.description,
        "severity": it.severity.value, "status": it.status.value,
        "owner": it.owner, "owner_name": owner_name,
        "external_ref": it.external_ref,
        "task_title": task_title,
        "created_at": it.created_at.isoformat() if it.created_at else None,
        "resolved_at": it.resolved_at.isoformat() if it.resolved_at else None,
    }


@router.get("")
def list_issues(
    project_id: int = Query(...),
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """遗留问题列表（admin/member/guest 可看）。status=open/resolved。"""
    assert_project_role(db, user, project_id,
                        (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    q = db.query(RemainingIssue).filter(RemainingIssue.project_id == project_id)
    if status_filter:
        try:
            q = q.filter(RemainingIssue.status == IssueStatus(status_filter))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="非法状态")
    rows = q.order_by(RemainingIssue.created_at.desc()).all()
    # 批量预取 owner 名 + 任务标题(经 report→task 或直挂 task),消除逐行 N+1
    names = _name_map(db, [it.owner for it in rows])
    report_ids = {it.report_id for it in rows if it.report_id}
    rep_task = dict(db.query(DailyReport.id, DailyReport.task_id).filter(DailyReport.id.in_(report_ids))) if report_ids else {}
    tid_of = {it.id: (rep_task.get(it.report_id) if it.report_id else it.task_id) for it in rows}
    task_ids = {t for t in tid_of.values() if t}
    task_title = dict(db.query(Task.id, Task.title).filter(Task.id.in_(task_ids))) if task_ids else {}
    titles = {iid: task_title.get(tid, "") for iid, tid in tid_of.items()}
    return ok([_to_out(db, it, names=names, titles=titles) for it in rows])


@router.patch("/{iid}")
def update_issue(
    iid: int,
    body: IssueUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """状态流转：open↔resolved、改 owner / external_ref。仅项目 admin。"""
    it = db.get(RemainingIssue, iid)
    if not it:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="遗留问题不存在")
    assert_project_role(db, user, it.project_id, (ProjectRole.admin,))
    if body.status is not None:
        it.status = body.status
        if body.status == IssueStatus.resolved:
            from datetime import datetime
            it.resolved_at = datetime.utcnow()
    if body.owner is not None:
        it.owner = body.owner
    if body.external_ref is not None:
        it.external_ref = body.external_ref
    db.commit()
    db.refresh(it)
    return ok(_to_out(db, it))
