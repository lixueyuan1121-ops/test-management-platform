from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import create_access_token, create_refresh_token, decode_token, verify_password
from app.db.session import get_db
from app.models import User, ProjectMember, Project
from app.schemas.auth import LoginIn, RefreshIn, TokenOut, UserOut, MeOut
from app.schemas.common import ok

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if user.status.value != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="账号已禁用")
    return ok({
        "access_token": create_access_token(str(user.id)),
        "refresh_token": create_refresh_token(str(user.id)),
        "token_type": "bearer",
    })


@router.post("/refresh")
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="刷新令牌无效")
    user = db.get(User, int(payload["sub"]))
    if not user or user.status.value != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="用户不可用")
    return ok({
        "access_token": create_access_token(str(user.id)),
        "token_type": "bearer",
    })


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    memberships = []
    if not user.is_platform_admin:
        rows = (
            db.query(ProjectMember, Project)
            .join(Project, Project.id == ProjectMember.project_id)
            .filter(ProjectMember.user_id == user.id)
            .all()
        )
        for m, p in rows:
            memberships.append({
                "project_id": p.id,
                "project_code": p.code,
                "project_name": p.name,
                "role": m.role.value,
            })
    return ok({
        "user": UserOut.model_validate(user).model_dump(),
        "is_platform_admin": user.is_platform_admin,
        "memberships": memberships,
    })
