"""openapi 精简自测(纯函数,免 DB)。运行: cd backend && python -m scripts.test_openapi_import

覆盖:openapi3(servers) / swagger2(host+basePath+schemes) / 无 paths 报错 / summary 缺省 operationId。
"""
from app.services.openapi_import import openapi_to_contract


def main():
    # ---- openapi 3(servers[0].url)----
    spec3 = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://biz.example.com/api"}],
        "paths": {
            "/users": {
                "get": {"summary": "用户列表"},
                "post": {"summary": "创建用户"},
            },
            "/users/{id}": {
                "delete": {"operationId": "deleteUser"},
                "x-ignored": {"summary": "非法方法应跳过"},
            },
        },
    }
    r = openapi_to_contract(spec3)
    assert r.get("error") is None, r
    assert r["base_url"] == "https://biz.example.com/api", r
    assert r["count"] == 3, r
    lines = r["contract"].splitlines()
    assert "GET /users  用户列表" in lines, lines
    assert "POST /users  创建用户" in lines, lines
    # summary 缺省用 operationId
    assert "DELETE /users/{id}  deleteUser" in lines, lines
    # x-ignored 非标准方法不产出
    assert not any("x-ignored" in l for l in lines)

    # ---- swagger 2(host + basePath + schemes)----
    spec2 = {
        "swagger": "2.0",
        "host": "api.svc.com",
        "basePath": "/v1",
        "schemes": ["https", "http"],
        "paths": {"/login": {"post": {"summary": "登录"}}},
    }
    r2 = openapi_to_contract(spec2)
    assert r2["base_url"] == "https://api.svc.com/v1", r2
    assert r2["contract"] == "POST /login  登录", r2

    # ---- 无 paths / 非对象 → error ----
    assert openapi_to_contract({})["error"]
    assert openapi_to_contract({"paths": {}})["error"]
    assert openapi_to_contract("nope")["error"]
    # paths 存在但无有效操作
    assert openapi_to_contract({"paths": {"/x": {"summary": "无方法"}}})["error"]

    print("OK test_openapi_import")


if __name__ == "__main__":
    main()
