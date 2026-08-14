"""鉴权与权限依赖。

- get_current_user: 解析 JWT，返回 User
- require_platform_admin: 仅平台管理员
- require_project_member: 必须是该项目的成员（含 guest）
- require_project_role(*roles): 必须是该项目指定角色之一；平台管理员自动放行
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import ProjectRole
from app.core.security import decode_token
from app.db.session import get_db
from app.models import User, ProjectMember

bearer_scheme = HTTPBearer(auto_error=True)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_token(creds.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="无效或过期的令牌")
    user_id = payload.get("sub")
    user = db.get(User, int(user_id)) if user_id else None
    if not user or user.status.value != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")
    return user


def require_platform_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限")
    return user


def require_runner(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    """执行机（runner）鉴权：校验固定长期 token（settings.RUNNER_TOKEN）。

    与用户 JWT 体系分离——runner 是无人值守进程，不走登录。token 配在后端 .env，
    runner 端同名配置须填相同值。未配置 RUNNER_TOKEN 时一律拒绝（避免空口令放行）。
    """
    token = creds.credentials
    if not settings.RUNNER_TOKEN or token != settings.RUNNER_TOKEN:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="无效的 runner 令牌")
    return token


class RunnerCtx:
    """runner 请求的鉴权上下文:要么是某成员登记的设备(device),要么是旧共享 token(shared)。

    - 设备 token:device 为该 RunnerDevice,归属到人;接口据此把 runner 锁定为 device.runner_id。
    - 共享 token(settings.RUNNER_TOKEN,兜底):device=None,行为同旧版(靠 query 的 runner 字符串区分)。
    过渡期两种都放行:老 runner 用共享 token 不受影响,新设备用专属 token 归属到人。
    """
    def __init__(self, device=None):
        self.device = device
        self.is_shared = device is None


def require_runner_ctx(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> "RunnerCtx":
    """runner 鉴权(设备 token 优先,共享 token 兜底)。返回 RunnerCtx。"""
    from app.models import RunnerDevice  # 延迟导入,避免 models 与 deps 循环

    token = creds.credentials
    device = db.query(RunnerDevice).filter(RunnerDevice.token == token).first()
    if device is not None:
        return RunnerCtx(device=device)
    if settings.RUNNER_TOKEN and token == settings.RUNNER_TOKEN:
        return RunnerCtx(device=None)   # 共享 token 兜底
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="无效的 runner 令牌")



def get_project_member(
    pid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectMember:
    """要求当前用户是该项目的成员（含 guest）。返回成员关系记录。

    注意：参数名必须与路由路径变量 {pid} 一致，否则会被当成 query 参数。
    """
    if user.is_platform_admin:
        # 平台管理员可访问任意项目；返回一个虚拟 admin 关系对象
        return ProjectMember(user_id=user.id, project_id=pid, role=ProjectRole.admin)
    member = (
        db.query(ProjectMember)
        .filter_by(user_id=user.id, project_id=pid)
        .first()
    )
    if not member:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="你不是该项目成员")
    return member


def require_project_role(*roles: ProjectRole):
    """工厂：要求当前用户在该项目具有指定角色之一。"""

    def _dep(member: ProjectMember = Depends(get_project_member)) -> ProjectMember:
        if ProjectRole(member.role.value) not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="当前角色无此操作权限")
        return member

    return _dep


def assert_project_role(
    db: Session, user: User, project_id: int, roles: tuple[ProjectRole, ...]
) -> ProjectMember:
    """非依赖注入版本：用于 project_id 来自请求体（而非路径）的接口。

    平台管理员视为 admin 放行。返回成员关系（平台管理员返回虚拟关系）。
    """
    if user.is_platform_admin:
        return ProjectMember(user_id=user.id, project_id=project_id, role=ProjectRole.admin)
    member = db.query(ProjectMember).filter_by(user_id=user.id, project_id=project_id).first()
    if not member:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="你不是该项目成员")
    if ProjectRole(member.role.value) not in roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="当前角色无此操作权限")
    return member
