"""_validate_api_script 自测(纯函数,免 DB)。运行: cd backend && python -m scripts.test_validate_api_script

覆盖设计稿 §7.2 全部规则:结构/method/path/asserts/op/jsonpath-path/需值 op/
变量引用闭环(先 extract 后引用)/写操作必带 cleanup。
"""
from app.services.claude_runner import _validate_api_script, _collect_var_refs


def _ok(script, **kw):
    norm, err = _validate_api_script(script, **kw)
    assert err is None, f"本应合法却报错: {err}"
    return norm


def _bad(script, frag, **kw):
    norm, err = _validate_api_script(script, **kw)
    assert err is not None, f"本应非法却通过: {script}"
    assert frag in err, f"错误信息应含「{frag}」,实际: {err}"


def main():
    # ---- 合法:最小只读单步 ----
    norm = _ok([
        {"name": "查我", "request": {"method": "GET", "path": "/api/me"},
         "asserts": [{"type": "status", "op": "eq", "value": 200},
                     {"type": "jsonpath", "path": "code", "op": "eq", "value": 0}]},
    ])
    assert norm[0]["request"]["method"] == "GET"
    assert len(norm[0]["asserts"]) == 2

    # ---- 合法:登录→创建→清理链式,变量闭环 + 写操作带 cleanup ----
    chain = [
        {"name": "登录", "request": {"method": "POST", "path": "/api/auth/login", "body": {"u": "qa"}},
         "asserts": [{"type": "jsonpath", "path": "code", "op": "eq", "value": 0}],
         "extract": {"token": "data.token"}},
        {"name": "创建", "request": {"method": "POST", "path": "/api/projects",
                                     "headers": {"Authorization": "Bearer {{token}}"}, "body": {"name": "n"}},
         "asserts": [{"type": "jsonpath", "path": "data.id", "op": "exists"}],
         "extract": {"pid": "data.id"}},
        {"name": "清理", "cleanup": True,
         "request": {"method": "DELETE", "path": "/api/projects/{{pid}}",
                     "headers": {"Authorization": "Bearer {{token}}"}},
         "asserts": [{"type": "status", "op": "eq", "value": 200}]},
    ]
    norm = _ok(chain)
    assert norm[2]["cleanup"] is True, "cleanup 标记应保留"
    assert norm[1]["request"]["headers"]["Authorization"] == "Bearer {{token}}", "可选 request 字段应保留"
    assert norm[0]["extract"] == {"token": "data.token"}, "extract 应保留"

    # ---- 结构类 ----
    _bad([], "非数组")
    _bad("nope", "非数组")
    _bad([123], "非对象")
    _bad([{"asserts": [{"type": "status", "op": "eq", "value": 200}]}], "request")  # 缺 request
    _bad([{"request": {"method": "FOO", "path": "/a"}, "asserts": [{"type": "status", "op": "eq", "value": 200}]}], "method")
    _bad([{"request": {"method": "GET", "path": ""}, "asserts": [{"type": "status", "op": "eq", "value": 200}]}], "path")

    # ---- asserts 类 ----
    _bad([{"request": {"method": "GET", "path": "/a"}, "asserts": []}], "asserts")  # 空断言
    _bad([{"request": {"method": "GET", "path": "/a"}, "asserts": [{"type": "xx", "op": "eq", "value": 1}]}], "type")
    _bad([{"request": {"method": "GET", "path": "/a"}, "asserts": [{"type": "status", "op": "zz", "value": 1}]}], "op")
    _bad([{"request": {"method": "GET", "path": "/a"}, "asserts": [{"type": "jsonpath", "op": "eq", "value": 1}]}], "path")  # jsonpath 缺 path
    _bad([{"request": {"method": "GET", "path": "/a"}, "asserts": [{"type": "status", "op": "eq"}]}], "value")  # eq 缺 value

    # exists 不需要 value(合法)
    _ok([{"request": {"method": "GET", "path": "/a"},
          "asserts": [{"type": "jsonpath", "path": "data.id", "op": "exists"}]}])
    # value=0 合法(0 不等于"缺失")
    _ok([{"request": {"method": "GET", "path": "/a"},
          "asserts": [{"type": "jsonpath", "path": "code", "op": "eq", "value": 0}]}])

    # ---- 变量引用闭环 ----
    _bad([{"name": "引用未定义", "request": {"method": "GET", "path": "/api/p/{{pid}}"},
           "asserts": [{"type": "status", "op": "eq", "value": 200}]}], "未定义变量")
    # auth_vars 预置(固定注入)后引用合法
    _ok([{"request": {"method": "GET", "path": "/api/me", "headers": {"Authorization": "Bearer {{token}}"}},
          "asserts": [{"type": "status", "op": "eq", "value": 200}]}], auth_vars={"token"})

    # ---- 写操作必带 cleanup ----
    _bad([{"name": "创建无清理", "request": {"method": "POST", "path": "/api/projects", "body": {"name": "n"}},
           "asserts": [{"type": "status", "op": "eq", "value": 200}], "extract": {"pid": "data.id"}}],
         "cleanup")
    # 纯只读无需 cleanup(合法)
    _ok([{"request": {"method": "GET", "path": "/a"}, "asserts": [{"type": "status", "op": "eq", "value": 200}]}])

    # ---- _collect_var_refs 辅助 ----
    assert _collect_var_refs({"h": "Bearer {{token}}", "p": "/x/{{pid}}", "n": 1}) == {"token", "pid"}
    assert _collect_var_refs("no vars here") == set()

    print("OK test_validate_api_script")


if __name__ == "__main__":
    main()
