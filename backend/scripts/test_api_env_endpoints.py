"""api-env 端点自测(TestClient + 依赖覆盖 + 内存库)。
运行: cd backend && python -m scripts.test_api_env_endpoints

覆盖:parse-curl / import-openapi / contract(成员可读,不泄露 auth 凭据)。
用平台管理员假身份(assert_project_role 对其短路),真实内存 SQLite 供 contract 读取。
"""
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models.api_env import ApiEnv

# 内存库 + 种一条 ApiEnv(含凭据,用于验证 contract 端点不泄露 auth)。
# StaticPool:所有连接共享同一 :memory: 库(否则 TestClient 跨线程会拿到空库)。
_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add(ApiEnv(project_id=1, base_url="https://biz.com", auth_type="fixed",
              auth_json=json.dumps({"headers": {"Authorization": "Bearer SUPER-SECRET"}}),
              contract="GET /api/users 列表\nPOST /api/users 创建"))
_s.commit()


def _override_db():
    yield _s


_FAKE_ADMIN = SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_current_user] = lambda: _FAKE_ADMIN
app.dependency_overrides[get_db] = _override_db

client = TestClient(app)


def main():
    # ---- parse-curl 合法 ----
    curl = "curl -X POST 'https://biz.com/api/users?p=1' -H 'Authorization: Bearer SECRET' -d '{\"n\":1}'"
    r = client.post("/api/api-env/parse-curl", json={"curl": curl})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    data = r.json()["data"]
    assert data["parsed"]["method"] == "POST"
    assert "SECRET" not in r.text, "真实 token 不得出现在响应"
    assert data["contract_line"].startswith("POST /api/users")
    assert len(data["script_seed"]) == 1

    # ---- parse-curl 非法 ----
    assert client.post("/api/api-env/parse-curl", json={"curl": "echo hi"}).json()["code"] != 0

    # ---- import-openapi ----
    spec = {"openapi": "3.0.0", "servers": [{"url": "https://b.com"}],
            "paths": {"/users": {"get": {"summary": "列表"}}}}
    r3 = client.post("/api/api-env/import-openapi", json={"project_id": 1, "spec": spec})
    assert r3.status_code == 200 and r3.json()["code"] == 0, r3.text
    assert r3.json()["data"]["count"] == 1
    assert client.post("/api/api-env/import-openapi", json={"project_id": 1, "spec": _json_str(spec)}).json()["code"] == 0
    assert client.post("/api/api-env/import-openapi", json={"project_id": 1, "spec": "{bad"}).json()["code"] != 0
    assert client.post("/api/api-env/import-openapi", json={"project_id": 1, "spec": {"openapi": "3.0"}}).json()["code"] != 0

    # ---- contract(成员可读,不泄露 auth)----
    rc = client.get("/api/api-env/contract", params={"project_id": 1})
    assert rc.status_code == 200 and rc.json()["code"] == 0, rc.text
    cd = rc.json()["data"]
    assert cd["has_contract"] is True, cd
    assert "POST /api/users" in cd["contract"], cd
    assert cd["base_url"] == "https://biz.com", cd
    assert "SUPER-SECRET" not in rc.text, "contract 端点绝不能泄露 auth 凭据"
    assert "auth" not in cd, "contract 端点不应返回 auth 字段"

    # 无契约项目 → has_contract False(无 ApiEnv 行,get_api_env 返回 None)
    rc2 = client.get("/api/api-env/contract", params={"project_id": 999})
    assert rc2.json()["data"]["has_contract"] is False, rc2.text

    print("OK test_api_env_endpoints")


def _json_str(o):
    return json.dumps(o)


if __name__ == "__main__":
    main()
