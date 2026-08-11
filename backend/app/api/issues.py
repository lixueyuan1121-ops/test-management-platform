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


def _to_out(db: Session, it: RemainingIssue) -> dict:
    return {
        "id": it.id, "report_id": it.report_id, "project_id": it.project_id,
        "task_id": it.task_id, "checklist_item_id": it.checklist_item_id,
        "title": it.title, "description": it.description,
        "severity": it.severity.value, "status": it.status.value,
        "owner": it.owner, "owner_name": _user_name(db, it.owner),
        "external_ref": it.external_ref,
        "task_title": _issue_task_title(db, it),
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
    return ok([_to_out(db, it) for it in rows])


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
