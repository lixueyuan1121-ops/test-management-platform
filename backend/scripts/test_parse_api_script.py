"""parse_testcases 对 api 用例分流校验自测(project_id=None 免 DB)。
运行: cd backend && python -m scripts.test_parse_api_script

验证:api 合法 script 保留;api 非法(写操作无清理/空)降级 manual;gui 无 script 仍降级(回归)。
"""
import json
from app.services.claude_runner import parse_testcases


def _find(cases, title):
    for c in cases:
        if c["title"] == title:
            return c
    raise AssertionError(f"未找到用例 {title}")


def main():
    valid_api = [
        {"name": "查我", "request": {"method": "GET", "path": "/api/me"},
         "asserts": [{"type": "status", "op": "eq", "value": 200},
                     {"type": "jsonpath", "path": "code", "op": "eq", "value": 0}]},
    ]
    write_no_cleanup = [
        {"name": "建项目", "request": {"method": "POST", "path": "/api/projects", "body": {"name": "n"}},
         "asserts": [{"type": "status", "op": "eq", "value": 200}]},
    ]
    arr = [
        {"title": "api-合法", "kind": "api", "category": "功能", "steps": "s", "expected": "e",
         "priority": "P1", "kind_reason": "调接口", "script": valid_api},
        {"title": "api-写无清理", "kind": "api", "priority": "P1", "script": write_no_cleanup},
        {"title": "api-空script", "kind": "api", "priority": "P2", "script": []},
        {"title": "gui-无script", "kind": "gui", "priority": "P2", "script": []},
    ]
    cases = parse_testcases(json.dumps(arr, ensure_ascii=False), project_id=None)

    ok = _find(cases, "api-合法")
    assert ok["kind"] == "api", f"合法 api 应保持 api,实际 {ok['kind']}"
    assert ok["script"], "合法 api 应保留 script_json"
    parsed = json.loads(ok["script"])
    assert parsed[0]["request"]["method"] == "GET", parsed

    bad = _find(cases, "api-写无清理")
    assert bad["kind"] == "manual", f"写操作无清理应降级 manual,实际 {bad['kind']}"
    assert not bad["script"], "降级后不应保留 script"

    empty = _find(cases, "api-空script")
    assert empty["kind"] == "manual", f"空 script 应降级 manual,实际 {empty['kind']}"

    gui = _find(cases, "gui-无script")
    assert gui["kind"] == "manual", "回归:gui 无 script 仍降级 manual"

    print("OK test_parse_api_script")


if __name__ == "__main__":
    main()
