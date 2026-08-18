"""curl 解析器(纯函数,无 DB/无网络)。

用途(设计稿 §9):吃一段 curl 文本 → 吐 {method, base_url, path, query, headers, body},
并**剥离鉴权头**(Authorization/Cookie/X-Api-Key 等)——curl 里的是会过期的真实 token,
明文进库/进 script 都不安全,统一抽走归 stripped_auth,鉴权由项目 api_env 配置注入。

两个下游用途共用同一 parser:
  - curl_to_contract_line:并入项目契约(注入生成 prompt)。
  - curl_to_script_seed:转成变体A 单步 script 种子(request + 默认断言),AI 再补断言/边界/清理。

支持范围:最常见形态(-X/--request、-H/--header、-d/--data*/--data-raw、GET 带 query、
浏览器 copy-as-cURL)。multipart(-F)/cookie(-b)先告警不强解。
"""
import json
import re
import shlex
from urllib.parse import parse_qsl, urlsplit

# 鉴权类头名(小写比对):命中即从 headers 抽走归 stripped_auth,不落进契约/种子。
_AUTH_HEADER_NAMES = {
    "authorization", "cookie", "x-api-key", "x-auth-token", "x-token",
    "api-key", "token", "x-access-token", "proxy-authorization",
}

# 带值的 curl flag(需跳过其后一个 token,否则值会被误当 URL)。
_VALUE_FLAGS = {
    "-A", "--user-agent", "-e", "--referer", "-o", "--output", "-m", "--max-time",
    "--connect-timeout", "-x", "--proxy", "--retry", "-w", "--write-out",
    "-c", "--cookie-jar", "--cacert", "--cert", "--key", "-T", "--upload-file",
    "--resolve", "--limit-rate",
}


def parse_curl(text: str) -> dict:
    """解析 curl 文本。成功返回 dict,失败返回 {"error": 原因}。"""
    if not text or "curl" not in text:
        return {"error": "不是有效的 curl 命令(未见 curl)"}
    # 合并 shell 行连接符 \<newline> 成一行,再按 shell 词法分词(处理引号/转义)。
    cleaned = re.sub(r"\\\s*\n", " ", text.strip())
    try:
        tokens = shlex.split(cleaned)
    except ValueError as e:
        return {"error": f"curl 解析失败(引号可能不匹配):{e}"}
    if not tokens:
        return {"error": "curl 命令为空"}

    method = None
    headers: dict[str, str] = {}
    stripped_auth: list[str] = []
    warnings: list[str] = []
    data = None
    url = None

    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t == "curl":
            i += 1
            continue
        if t in ("-X", "--request"):
            if i + 1 < n:
                method = tokens[i + 1].upper()
            i += 2
            continue
        if t in ("-H", "--header"):
            h = tokens[i + 1] if i + 1 < n else ""
            i += 2
            if ":" in h:
                name, _, val = h.partition(":")
                name, val = name.strip(), val.strip()
                if name.lower() in _AUTH_HEADER_NAMES:
                    stripped_auth.append(name)
                elif name:
                    headers[name] = val
            continue
        if t in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"):
            data = tokens[i + 1] if i + 1 < n else ""
            i += 2
            continue
        if t in ("-F", "--form"):
            warnings.append("multipart/form-data(-F)未解析,请手动补 body")
            i += 2
            continue
        if t in ("-b", "--cookie"):
            stripped_auth.append("Cookie")
            i += 2
            continue
        if t in ("-u", "--user"):
            stripped_auth.append("BasicAuth(-u)")
            i += 2
            continue
        if t == "--url":
            if i + 1 < n:
                url = tokens[i + 1]
            i += 2
            continue
        if t in _VALUE_FLAGS:
            i += 2   # 跳过 flag 及其值
            continue
        if t.startswith("-"):
            i += 1   # 其它无值 flag(-s/-k/-i/-L/--compressed 等)
            continue
        # 非 flag → URL(取第一个)
        if url is None:
            url = t
        i += 1

    if not url:
        return {"error": "未找到 URL"}

    parts = urlsplit(url)
    base_url = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
    path = parts.path or "/"
    query = dict(parse_qsl(parts.query)) if parts.query else {}
    if not base_url:
        warnings.append("URL 无 scheme/host,base_url 为空(执行时用项目 base_url)")

    if not method:
        method = "POST" if data is not None else "GET"

    body = None
    if data is not None:
        try:
            body = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            body = data   # 非 JSON(如 form 串 a=1&b=2)保留原文

    return {
        "method": method,
        "base_url": base_url,
        "path": path,
        "query": query,
        "headers": headers,
        "body": body,
        "stripped_auth": stripped_auth,
        "warnings": warnings,
    }


def curl_to_script_seed(parsed: dict) -> list:
    """curl 解析结果 → 变体A 单步 script 种子(request + 默认断言)。鉴权头已剥离,AI 再补断言/边界/清理。"""
    if not parsed or parsed.get("error"):
        return []
    req = {"method": parsed["method"], "path": parsed["path"]}
    if parsed.get("query"):
        req["query"] = parsed["query"]
    if parsed.get("headers"):
        req["headers"] = parsed["headers"]
    if parsed.get("body") is not None:
        req["body"] = parsed["body"]
    return [{
        "name": f"{parsed['method']} {parsed['path']}",
        "request": req,
        "asserts": [
            {"type": "status", "op": "eq", "value": 200},
            {"type": "jsonpath", "path": "code", "op": "eq", "value": 0},
        ],
    }]


def curl_to_contract_line(parsed: dict) -> str:
    """curl 解析结果 → 契约清单一行:'METHOD /path  query: ...  body: ...'(仅列字段名,不含值)。"""
    if not parsed or parsed.get("error"):
        return ""
    line = f"{parsed['method']} {parsed['path']}"
    extra = []
    if parsed.get("query"):
        extra.append(f"query: {','.join(parsed['query'].keys())}")
    if isinstance(parsed.get("body"), dict) and parsed["body"]:
        extra.append(f"body: {','.join(str(k) for k in parsed['body'].keys())}")
    return line + ("  " + "  ".join(extra) if extra else "")
