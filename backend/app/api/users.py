from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_platform_admin
from app.core.enums import UserStatus
from app.core.security import hash_password
from app.db.session import get_db
from app.models import User
from app.schemas.auth import UserOut
from app.schemas.common import ok
from app.schemas.user import UserCreate, UserUpdate, PasswordReset

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
def list_users(
    keyword: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """供「用户管理」列表与「添加成员」选择器使用。任何登录用户可查（仅基础信息）。"""
    q = db.query(User)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter((User.username.like(like)) | (User.name.like(like)))
    rows = q.order_by(User.id).limit(100).all()
    return ok([UserOut.model_validate(u).model_dump() for u in rows])


def _out(u: User) -> dict:
    return UserOut.model_validate(u).model_dump()


@router.post("")
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_platform_admin),
):
    if db.query(User).filter_by(username=body.username).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="用户名已存在")
    u = User(
        username=body.username,
        password_hash=hash_password(body.password),
        name=body.name,
        email=body.email,
        is_platform_admin=body.is_platform_admin,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return ok(_out(u))


@router.patch("/{uid}")
def update_user(
    uid: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_platform_admin),
):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if body.name is not None:
        u.name = body.name
    if body.email is not None:
        u.email = body.email
    if body.is_platform_admin is not None:
        u.is_platform_admin = body.is_platform_admin
    if body.status is not None:
        try:
            u.status = UserStatus(body.status)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="非法状态")
    db.commit()
    return ok(_out(u))


@router.patch("/{uid}/password")
def reset_password(
    uid: int,
    body: PasswordReset,
    db: Session = Depends(get_db),
    _: User = Depends(require_platform_admin),
):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用户不存在")
    u.password_hash = hash_password(body.password)
    db.commit()
    return ok({"reset": uid})
