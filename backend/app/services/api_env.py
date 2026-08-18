"""api_env 服务:DB 单源读项目 api 环境。生成侧/下发侧/API 都经此层,口径一致。"""
import json
from sqlalchemy.orm import Session
from app.models import ApiEnv


def get_api_env(db: Session, project_id: int) -> dict | None:
    """读项目 api 环境。无配置返回 None。auth_json 解析失败按空 dict 兜底。"""
    row = db.query(ApiEnv).filter(ApiEnv.project_id == project_id).first()
    if not row:
        return None
    try:
        auth = json.loads(row.auth_json or "{}")
        if not isinstance(auth, dict):
            auth = {}
    except (json.JSONDecodeError, ValueError):
        auth = {}
    return {
        "base_url": row.base_url or "",
        "auth_type": row.auth_type or "fixed",
        "auth": auth,
        "contract": row.contract,
    }
