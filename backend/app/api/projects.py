from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_platform_admin
from app.core.enums import ProjectStatus
from app.db.session import get_db
from app.models import Project, User
from app.schemas.common import ok
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectOut

router = APIRouter(prefix="/api/projects", tags=["projects"])

_PLATFORM_TYPES = ("pc", "app")


def _norm_platform(v: str | None) -> str | None:
    """校验项目平台类型：空→None(未分类)；非 pc/app→400。"""
    if v is None:
        return None
    v = v.strip().lower()
    if not v:
        return None
    if v not in _PLATFORM_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="非法平台类型")
    return v


@router.get("")
def list_projects(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """平台管理员看全部；普通用户只看自己是成员的项目。"""
    if user.is_platform_admin:
        rows = db.query(Project).order_by(Project.id.desc()).all()
    else:
        rows = _member_projects(db, user.id)
    return ok([ProjectOut.model_validate(p).model_dump() for p in rows])


def _member_projects(db: Session, user_id: int):
    from app.models import ProjectMember
    ids = [r.project_id for r in db.query(ProjectMember).filter_by(user_id=user_id).all()]
    if not ids:
        return []
    return db.query(Project).filter(Project.id.in_(ids)).order_by(Project.id.desc()).all()


@router.post("")
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_platform_admin),
):
    if db.query(Project).filter_by(code=body.code).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="项目编码已存在")
    p = Project(name=body.name, code=body.code, description=body.description,
                platform_type=_norm_platform(body.platform_type))
    db.add(p)
    db.commit()
    db.refresh(p)
    return ok(ProjectOut.model_validate(p).model_dump())


@router.patch("/{pid}")
def update_project(
    pid: int,
    body: ProjectUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_platform_admin),
):
    p = db.get(Project, pid)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="项目不存在")
    if body.name is not None:
        p.name = body.name
    if body.description is not None:
        p.description = body.description
    if body.status is not None:
        try:
            p.status = ProjectStatus(body.status)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="非法状态")
    if "platform_type" in body.model_fields_set:
        p.platform_type = _norm_platform(body.platform_type)
    db.commit()
    return ok(ProjectOut.model_validate(p).model_dump())
