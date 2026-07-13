from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_project_member, require_project_role
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import Project, ProjectMember, User
from app.schemas.common import ok
from app.schemas.project import MemberAdd, MemberUpdate, MemberOut

router = APIRouter(prefix="/api/projects/{pid}/members", tags=["members"])


def _to_out(m: ProjectMember, db: Session) -> dict:
    u = db.get(User, m.user_id)
    return {
        "id": m.id,
        "user_id": m.user_id,
        "username": u.username if u else "",
        "name": u.name if u else "",
        "project_id": m.project_id,
        "role": m.role.value,
        "team_id": m.team_id,
        "created_at": m.created_at,
    }


@router.get("")
def list_members(
    pid: int,
    db: Session = Depends(get_db),
    _=Depends(require_project_role(ProjectRole.admin, ProjectRole.member, ProjectRole.guest)),
):
    """项目所有角色（含 guest）都能看成员列表。"""
    rows = db.query(ProjectMember).filter_by(project_id=pid).all()
    return ok([_to_out(m, db) for m in rows])


@router.post("")
def add_member(
    pid: int,
    body: MemberAdd,
    db: Session = Depends(get_db),
    _=Depends(require_project_role(ProjectRole.admin)),
):
    if not db.get(Project, pid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="项目不存在")
    if not db.get(User, body.user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if db.query(ProjectMember).filter_by(user_id=body.user_id, project_id=pid).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该用户已是项目成员")
    m = ProjectMember(user_id=body.user_id, project_id=pid, role=body.role, team_id=body.team_id)
    db.add(m)
    db.commit()
    db.refresh(m)
    return ok(_to_out(m, db))


@router.patch("/{uid}")
def update_member(
    pid: int,
    uid: int,
    body: MemberUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_project_role(ProjectRole.admin)),
):
    m = db.query(ProjectMember).filter_by(user_id=uid, project_id=pid).first()
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="成员不存在")
    m.role = body.role
    m.team_id = body.team_id
    db.commit()
    return ok(_to_out(m, db))


@router.delete("/{uid}")
def remove_member(
    pid: int,
    uid: int,
    db: Session = Depends(get_db),
    _=Depends(require_project_role(ProjectRole.admin)),
):
    m = db.query(ProjectMember).filter_by(user_id=uid, project_id=pid).first()
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="成员不存在")
    db.delete(m)
    db.commit()
    return ok({"removed": uid})
