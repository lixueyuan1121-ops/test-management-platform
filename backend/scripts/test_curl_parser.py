"""curl 解析器自测(纯函数,免 DB)。运行: cd backend && python -m scripts.test_curl_parser

覆盖:标准 curl / 浏览器 copy-as-cURL / 鉴权头剥离 / URL 拆 base_url+path+query /
method 缺省推断 / body JSON 解析 / multipart 告警 / 转 script 种子 / 转契约行。
"""
from app.services.curl_parser import parse_curl, curl_to_script_seed, curl_to_contract_line


def main():
    # ---- 标准多行 curl(含鉴权头,应剥离)----
    c = r"""curl -X POST 'https://biz.example.com/api/users?page=1&size=20' \
      -H 'Content-Type: application/json' \
      -H 'Authorization: Bearer secret-token-123' \
      -d '{"name":"张三","email":"z@x.com"}'"""
    p = parse_curl(c)
    assert p.get("error") is None, p
    assert p["method"] == "POST", p
    assert p["base_url"] == "https://biz.example.com", p
    assert p["path"] == "/api/users", p
    assert p["query"] == {"page": "1", "size": "20"}, p
    assert p["headers"] == {"Content-Type": "application/json"}, "非鉴权头保留"
    assert "Authorization" in p["stripped_auth"], "Authorization 应被剥离"
    assert "secret-token-123" not in str(p), "真实 token 绝不能出现在解析结果"
    assert p["body"] == {"name": "张三", "email": "z@x.com"}, p

    # ---- 浏览器 copy-as-cURL 形态(url 在前、--data-raw、--compressed 无值)----
    c2 = r"""curl 'https://api.svc.com/v1/login' \
      -H 'accept: application/json' \
      -H 'authorization: Bearer abc' \
      --data-raw '{"u":"qa"}' \
      --compressed"""
    p2 = parse_curl(c2)
    assert p2["method"] == "POST", "有 data 缺 -X 应推断 POST"
    assert p2["base_url"] == "https://api.svc.com"
    assert p2["path"] == "/v1/login"
    assert "authorization" not in [h.lower() for h in p2["headers"]], "authorization(小写)也应剥离"
    assert p2["body"] == {"u": "qa"}

    # ---- GET 缺 -X 且无 data → 推断 GET;value-flag 的值不被误当 URL ----
    c3 = "curl 'https://h.com/api/list?q=1' -H 'X-Trace: t' -A 'Mozilla/5.0' --max-time 30"
    p3 = parse_curl(c3)
    assert p3["method"] == "GET", p3
    assert p3["base_url"] == "https://h.com" and p3["path"] == "/api/list", p3
    assert p3["query"] == {"q": "1"}
    assert p3["headers"] == {"X-Trace": "t"}

    # ---- multipart 告警,不强解 ----
    c4 = "curl -X POST 'https://h.com/upload' -F 'file=@a.png' -H 'Authorization: Bearer x'"
    p4 = parse_curl(c4)
    assert any("multipart" in w for w in p4["warnings"]), p4
    assert "Authorization" in p4["stripped_auth"]

    # ---- 非 curl / 空 → error ----
    assert parse_curl("")["error"]
    assert parse_curl("echo hi")["error"]
    # 无 URL → error
    assert parse_curl("curl -X GET -H 'a: b'")["error"], "无 URL 应报错"

    # ---- body 非 JSON(form 串)→ 保留原文 ----
    c5 = "curl -X POST 'https://h.com/f' -d 'a=1&b=2'"
    p5 = parse_curl(c5)
    assert p5["body"] == "a=1&b=2", p5

    # ---- 转变体A 单步 script 种子 ----
    seed = curl_to_script_seed(p)
    assert len(seed) == 1
    st = seed[0]
    assert st["request"]["method"] == "POST"
    assert st["request"]["path"] == "/api/users"
    assert st["request"]["body"] == {"name": "张三", "email": "z@x.com"}
    assert "Authorization" not in str(st), "种子里不得含鉴权头"
    # 默认断言:status 200 + code==0
    ops = [(a["type"], a["op"]) for a in st["asserts"]]
    assert ("status", "eq") in ops and ("jsonpath", "eq") in ops, st["asserts"]

    # ---- 转契约行 ----
    line = curl_to_contract_line(p)
    assert line.startswith("POST /api/users"), line
    assert "body:" in line and "name" in line, line

    print("OK test_curl_parser")


if __name__ == "__main__":
    main()
