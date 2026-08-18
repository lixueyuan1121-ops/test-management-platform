"""项目级 api 测试环境 API:读(runner/前端)、存(项目 admin)。
沿用 {code,msg,data} 信封、手写序列化。auth/contract 以 JSON 字符串存 TEXT。"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import ApiEnv, User
from app.schemas.common import ok
from app.services.api_env import get_api_env

router = APIRouter(prefix="/api/api-env", tags=["api-env"])


@router.get("")
def read_env(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """读项目 api 环境(仅项目 admin — auth_json 含被测系统凭据)。无配置返回 null。"""
    assert_project_role(db, user, project_id, (ProjectRole.admin,))
    return ok(get_api_env(db, project_id))


@router.put("")
def upsert_env(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """存项目 api 环境(项目 admin)。auth/contract 前端传对象/字符串,落库转 JSON 文本。"""
    project_id = int(body.get("project_id") or 0)
    assert_project_role(db, user, project_id, (ProjectRole.admin,))
    row = db.query(ApiEnv).filter(ApiEnv.project_id == project_id).first()
    auth = body.get("auth")
    auth_json = json.dumps(auth, ensure_ascii=False) if auth is not None else None
    now = datetime.utcnow()
    if row:
        if body.get("base_url") is not None:
            row.base_url = str(body.get("base_url"))
        if body.get("auth_type") is not None:
            row.auth_type = str(body.get("auth_type"))
        if auth_json is not None:
            row.auth_json = auth_json
        if body.get("contract") is not None:
            row.contract = str(body.get("contract"))
        row.updated_by = user.id
        row.updated_at = now
    else:
        row = ApiEnv(
            project_id=project_id,
            base_url=str(body.get("base_url") or ""),
            auth_type=str(body.get("auth_type") or "fixed"),
            auth_json=auth_json or "{}",
            contract=(str(body.get("contract")) if body.get("contract") is not None else None),
            updated_by=user.id, updated_at=now,
        )
        db.add(row)
    db.commit()
    return ok(get_api_env(db, project_id))
