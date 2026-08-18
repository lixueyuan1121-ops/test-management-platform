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
from app.services.curl_parser import parse_curl, curl_to_script_seed, curl_to_contract_line
from app.services.openapi_import import openapi_to_contract

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


@router.post("/parse-curl")
def parse_curl_endpoint(
    body: dict,
    user: User = Depends(get_current_user),
):
    """解析一段 curl(纯函数,无 DB):返回 {parsed, contract_line, script_seed}。

    鉴权头在 parser 内已剥离(不回真实 token)。前端据此「并入契约」或「转单步 script 种子」。
    任何登录用户可用(不写库、不涉项目数据)。
    """
    parsed = parse_curl(str(body.get("curl") or ""))
    if parsed.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=parsed["error"])
    return ok({
        "parsed": parsed,
        "contract_line": curl_to_contract_line(parsed),
        "script_seed": curl_to_script_seed(parsed),
    })


@router.post("/import-openapi")
def import_openapi_endpoint(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把粘贴的 OpenAPI/Swagger 内容精简成契约清单(项目 admin)。

    仅吃前端粘贴/上传的 spec(不在服务端拉 URL,避免 SSRF)。返回 {base_url, contract, count},
    由前端预览后决定是否写入(走 PUT /api/api-env),本端点不落库。
    """
    project_id = int(body.get("project_id") or 0)
    assert_project_role(db, user, project_id, (ProjectRole.admin,))
    spec = body.get("spec")
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="openapi 内容不是合法 JSON")
    result = openapi_to_contract(spec)
    if result.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return ok(result)
