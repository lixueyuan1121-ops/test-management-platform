"""api-env 的 parse-curl / import-openapi 端点自测(TestClient + 依赖覆盖,免真实鉴权/DB)。
运行: cd backend && python -m scripts.test_api_env_endpoints

用平台管理员假身份(assert_project_role 对其短路),验证路由注册 + 信封 + 错误码 + admin 门禁。
"""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import get_db

# 平台管理员假身份:assert_project_role 只读 .id/.is_platform_admin,短路放行,不查库。
_FAKE_ADMIN = SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_current_user] = lambda: _FAKE_ADMIN
app.dependency_overrides[get_db] = lambda: (yield SimpleNamespace())  # parse-curl 不用;import 走 admin 短路不查库

client = TestClient(app)


def main():
    # ---- parse-curl 合法 ----
    curl = "curl -X POST 'https://biz.com/api/users?p=1' -H 'Authorization: Bearer SECRET' -d '{\"n\":1}'"
    r = client.post("/api/api-env/parse-curl", json={"curl": curl})
    assert r.status_code == 200, r.text
    env = r.json()
    assert env["code"] == 0, env
    data = env["data"]
    assert data["parsed"]["method"] == "POST"
    assert data["parsed"]["base_url"] == "https://biz.com"
    assert "SECRET" not in r.text, "真实 token 不得出现在响应"
    assert data["contract_line"].startswith("POST /api/users")
    assert len(data["script_seed"]) == 1

    # ---- parse-curl 非法 → 非 0 ----
    r2 = client.post("/api/api-env/parse-curl", json={"curl": "echo hi"})
    assert r2.json()["code"] != 0, r2.text

    # ---- import-openapi 合法(project_id 任意,平台 admin 短路)----
    spec = {"openapi": "3.0.0", "servers": [{"url": "https://b.com"}],
            "paths": {"/users": {"get": {"summary": "列表"}}}}
    r3 = client.post("/api/api-env/import-openapi", json={"project_id": 1, "spec": spec})
    assert r3.status_code == 200, r3.text
    d3 = r3.json()
    assert d3["code"] == 0, d3
    assert d3["data"]["count"] == 1
    assert d3["data"]["base_url"] == "https://b.com"

    # ---- import-openapi:spec 作为 JSON 字符串也接受 ----
    import json as _json
    r4 = client.post("/api/api-env/import-openapi", json={"project_id": 1, "spec": _json.dumps(spec)})
    assert r4.json()["code"] == 0, r4.text

    # ---- import-openapi:坏 JSON 字符串 → 非 0 ----
    r5 = client.post("/api/api-env/import-openapi", json={"project_id": 1, "spec": "{bad json"})
    assert r5.json()["code"] != 0, r5.text

    # ---- import-openapi:无 paths → 非 0 ----
    r6 = client.post("/api/api-env/import-openapi", json={"project_id": 1, "spec": {"openapi": "3.0.0"}})
    assert r6.json()["code"] != 0, r6.text

    print("OK test_api_env_endpoints")


if __name__ == "__main__":
    main()
