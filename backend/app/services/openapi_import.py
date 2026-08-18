"""OpenAPI/Swagger → 精简接口契约(纯函数,无网络)。

设计稿 §9:导入 Swagger 得到"精简接口清单",注入生成 prompt。**不在服务端拉取 URL**
(避免 SSRF——服务器去请求用户给的任意/内网地址);只吃已粘贴/上传的 spec dict。

把 paths 压成每行 'METHOD /path  summary' 的清单;base_url 取 openapi3 servers[0].url,
或 swagger2 的 schemes+host+basePath。产出直接写进 api_env.contract。
"""
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def openapi_to_contract(spec) -> dict:
    """OpenAPI/Swagger dict → {base_url, contract, count}。失败返回 {"error": 原因}。"""
    if not isinstance(spec, dict):
        return {"error": "openapi 内容不是对象(应为 JSON 对象)"}
    paths = spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        return {"error": "openapi 无 paths(不是有效的 OpenAPI/Swagger 文档?)"}

    # base_url:openapi3 servers[0].url;否则 swagger2 host+basePath(scheme 取 schemes[0] 或 https)。
    base_url = ""
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        base_url = str(servers[0].get("url") or "")
    elif spec.get("host"):
        schemes = spec.get("schemes")
        scheme = schemes[0] if isinstance(schemes, list) and schemes else "https"
        base_url = f"{scheme}://{spec['host']}{spec.get('basePath', '') or ''}"

    lines = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for m in _HTTP_METHODS:
            op = item.get(m)
            if not isinstance(op, dict):
                continue
            summary = str(op.get("summary") or op.get("operationId") or "").strip()
            line = f"{m.upper()} {path}"
            if summary:
                line += f"  {summary}"
            lines.append(line)

    if not lines:
        return {"error": "openapi paths 无有效操作(get/post/put/patch/delete)"}
    return {"base_url": base_url, "contract": "\n".join(lines), "count": len(lines)}
