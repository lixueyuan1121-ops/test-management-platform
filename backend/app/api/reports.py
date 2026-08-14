from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import DailyReport, Project, ProjectMember, RemainingIssue, Task, User
from app.schemas.common import ok
from app.schemas.report import IssueItem, ReportOut, ReportUpsert

router = APIRouter(prefix="/api/daily-reports", tags=["reports"])


def _user_name(db: Session, uid: int) -> str:
    u = db.get(User, uid)
    return u.name if u else ""


def _issue_out(r: RemainingIssue) -> dict:
    return {
        "id": r.id, "title": r.title, "description": r.description,
        "severity": r.severity.value, "status": r.status.value,
        "owner": r.owner, "external_ref": r.external_ref,
    }


def _issues_for(db: Session, report_id: int) -> list[dict]:
    rows = db.query(RemainingIssue).filter_by(report_id=report_id).all()
    return [_issue_out(r) for r in rows]


def _to_out(db: Session, r: DailyReport, user_name: str | None = None,
            issues: list[dict] | None = None) -> dict:
    # 预取参数(批量场景)优先;缺省则逐个查(单条 upsert 返回兜底)。消除列表 N+1。
    name = user_name if user_name is not None else _user_name(db, r.user_id)
    iss = issues if issues is not None else _issues_for(db, r.id)
    return {
        "id": r.id, "task_id": r.task_id, "user_id": r.user_id,
        "user_name": name,
        "project_id": r.project_id, "report_date": str(r.report_date),
        "progress_pct": r.progress_pct, "is_online": r.is_online,
        "online_time": r.online_time.isoformat() if r.online_time else None,
        "workload_hours": float(r.workload_hours or 0), "summary": r.summary,
        "issues": iss,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _to_out_list(db: Session, rows: list[DailyReport]) -> list[dict]:
    """批量序列化日报:一次预取 user 名 + 一次取全部 issue 按 report_id 分组,消除逐行 N+1。"""
    if not rows:
        return []
    report_ids = [r.id for r in rows]
    uids = {r.user_id for r in rows if r.user_id}
    name_map = dict(db.query(User.id, User.name).filter(User.id.in_(uids)).all()) if uids else {}
    issues_map: dict[int, list[dict]] = {}
    for iss in db.query(RemainingIssue).filter(RemainingIssue.report_id.in_(report_ids)).all():
        issues_map.setdefault(iss.report_id, []).append(_issue_out(iss))
    return [
        _to_out(db, r, user_name=name_map.get(r.user_id, ""), issues=issues_map.get(r.id, []))
        for r in rows
    ]


@router.post("")
def upsert_report(
    body: ReportUpsert,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """成员提交/更新日报。只能为自己被指派的任务提交。"""
    task = db.get(Task, body.task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    # 平台管理员或项目 admin/member 可提交（一般是任务指派人本人）
    if not user.is_platform_admin:
        m = db.query(ProjectMember).filter_by(
            user_id=user.id, project_id=task.project_id
        ).first()
        if not m or ProjectRole(m.role.value) not in (ProjectRole.admin, ProjectRole.member):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无日报提交权限")
        if task.assigned_to != user.id and m.role.value != ProjectRole.admin.value:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只能为指派给自己的任务提交日报")

    rep = db.query(DailyReport).filter_by(
        task_id=body.task_id, report_date=body.report_date
    ).first()
    if rep:
        rep.progress_pct = body.progress_pct
        rep.is_online = body.is_online
        rep.online_time = body.online_time
        rep.workload_hours = body.workload_hours
        rep.summary = body.summary
    else:
        rep = DailyReport(
            task_id=body.task_id, user_id=user.id, project_id=task.project_id,
            report_date=body.report_date, progress_pct=body.progress_pct,
            is_online=body.is_online, online_time=body.online_time,
            workload_hours=body.workload_hours, summary=body.summary,
        )
        db.add(rep)
        db.flush()
    # 遗留问题整体替换
    db.query(RemainingIssue).filter_by(report_id=rep.id).delete()
    for it in body.issues:
        db.add(RemainingIssue(
            report_id=rep.id, project_id=task.project_id,
            title=it.title, description=it.description,
            severity=it.severity, status=it.status,
            owner=it.owner, external_ref=it.external_ref,
        ))
    db.commit()
    db.refresh(rep)
    return ok(_to_out(db, rep))


@router.get("")
def list_reports(
    project_id: int = Query(...),
    date: date = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """某项目某日的全部日报（admin/member/guest 可看）。"""
    from app.core.deps import assert_project_role
    assert_project_role(db, user, project_id,
                        (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (
        db.query(DailyReport)
        .filter(DailyReport.project_id == project_id, DailyReport.report_date == date)
        .all()
    )
    return ok(_to_out_list(db, rows))
