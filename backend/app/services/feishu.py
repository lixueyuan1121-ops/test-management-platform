"""飞书 OpenAPI 取文：把 docx / wiki / sheets / base 链接读成纯文本，供生成测试点。

飞书文档是鉴权动态页，普通 HTTP 抓不到正文，必须走开放平台 API：
用企业自建应用的 app_id/secret 换 tenant_access_token（内存缓存+过期刷新），
再按链接类型调对应文档接口。凭据缺失 / 无权限 / 网络错误统一转 ValueError
（中文友好提示），由上层 extractors → 路由转成 400/422 返回前端。

前置：应用需开通 docx/wiki/sheets/bitable 读权限，且目标文档已共享给应用。
"""
import logging
import re
import time
from urllib.parse import urlparse

import requests

from app.core.config import settings

logger = logging.getLogger("test_platform")

_FEISHU_HOSTS = ("feishu.cn", "feishu.net", "larksuite.com", "larkoffice.com")
# 路径里的 /docx/<token>、/wiki/<token> 等；token 为字母数字
_URL_RE = re.compile(r"/(docx|docs|wiki|sheets|sheet|base|bitable)/([A-Za-z0-9]+)")
_KIND_ALIAS = {"docs": "docx", "sheet": "sheets", "bitable": "base"}

_token_cache = {"token": "", "exp": 0.0}


def is_feishu_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _FEISHU_HOSTS)


def is_configured() -> bool:
    return bool(settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET)


def _base() -> str:
    return settings.FEISHU_BASE.rstrip("/")


def _get_token() -> str:
    now = time.monotonic()
    if _token_cache["token"] and _token_cache["exp"] > now + 60:
        return _token_cache["token"]
    try:
        resp = requests.post(
            f"{_base()}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.FEISHU_APP_ID, "app_secret": settings.FEISHU_APP_SECRET},
            timeout=10,
        )
        data = resp.json()
    except requests.RequestException as e:
        raise ValueError(f"飞书网络错误：{e}")
    except ValueError:
        raise ValueError("飞书鉴权返回非 JSON")
    if data.get("code") != 0:
        raise ValueError(f"飞书鉴权失败：{data.get('msg')}")
    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["exp"] = now + float(data.get("expire", 7200))
    return _token_cache["token"]


def _friendly_err(data: dict) -> str:
    msg = data.get("msg", "未知错误")
    low = str(msg).lower()
    if "permission" in low or "forbidden" in low or "access" in low or "no_permission" in low:
        return f"飞书无权限访问该文档（请把文档共享给应用并开通对应读权限）：{msg}"
    if "not found" in low or "not exist" in low:
        return f"飞书文档不存在或链接有误：{msg}"
    return f"飞书接口错误：{msg}"


def _api_get(path: str, params: dict | None = None) -> dict:
    try:
        resp = requests.get(
            f"{_base()}{path}",
            headers={"Authorization": f"Bearer {_get_token()}"},
            params=params,
            timeout=15,
        )
        data = resp.json()
    except requests.RequestException as e:
        raise ValueError(f"飞书网络错误：{e}")
    except ValueError:
        raise ValueError("飞书接口返回非 JSON")
    if data.get("code") != 0:
        raise ValueError(_friendly_err(data))
    return data.get("data", {}) or {}


def _flatten(value) -> str:
    """把 sheets 单元格 / bitable 字段的多态值拍平成文本。"""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "".join(_flatten(v) for v in value)
    if isinstance(value, dict):
        # 富文本段 {"text":...} / 链接 {"link":...} / 人员 {"name":...}
        return value.get("text") or value.get("name") or value.get("link") or ""
    return str(value)


def parse_feishu_url(url: str) -> tuple[str, str]:
    m = _URL_RE.search(url)
    if not m:
        raise ValueError("无法识别飞书文档类型（支持 docx / wiki / sheets / base）")
    kind = _KIND_ALIAS.get(m.group(1), m.group(1))
    return kind, m.group(2)


def _fetch_docx(token: str, title: str = "") -> tuple[str, str]:
    if not title:
        info = _api_get(f"/open-apis/docx/v1/documents/{token}")
        title = (info.get("document") or {}).get("title", "")
    raw = _api_get(f"/open-apis/docx/v1/documents/{token}/raw_content")
    return title, raw.get("content", "") or ""


def _fetch_sheets(token: str, title: str = "") -> tuple[str, str]:
    if not title:
        meta = _api_get(f"/open-apis/sheets/v3/spreadsheets/{token}")
        title = (meta.get("spreadsheet") or {}).get("title", "")
    sheets = _api_get(f"/open-apis/sheets/v3/spreadsheets/{token}/sheets/query").get("sheets", [])
    parts = []
    for sh in sheets[:5]:  # 限制工作表数量，避免超大表拖垮
        sid = sh.get("sheet_id")
        vr = _api_get(f"/open-apis/sheets/v2/spreadsheets/{token}/values/{sid}!A1:Z200")
        values = (vr.get("valueRange") or {}).get("values", []) or []
        rows = []
        for row in values:
            line = "\t".join(_flatten(c) for c in row).rstrip()
            if line.strip():
                rows.append(line)
        if rows:
            parts.append(f"# {sh.get('title', '')}\n" + "\n".join(rows))
    return title, "\n\n".join(parts)


def _fetch_base(token: str, title: str = "") -> tuple[str, str]:
    if not title:
        app = _api_get(f"/open-apis/bitable/v1/apps/{token}")
        title = (app.get("app") or {}).get("name", "")
    tables = _api_get(f"/open-apis/bitable/v1/apps/{token}/tables").get("items", [])
    parts = []
    for tb in tables[:5]:
        tid = tb.get("table_id")
        recs = _api_get(
            f"/open-apis/bitable/v1/apps/{token}/tables/{tid}/records",
            {"page_size": 100},
        ).get("items", [])
        lines = []
        for r in recs:
            kv = [f"{k}: {_flatten(v)}" for k, v in (r.get("fields") or {}).items()]
            if kv:
                lines.append("; ".join(kv))
        if lines:
            parts.append(f"# {tb.get('name', '')}\n" + "\n".join(lines))
    return title, "\n\n".join(parts)


def _fetch_wiki(token: str) -> tuple[str, str]:
    node = _api_get("/open-apis/wiki/v2/spaces/get_node", {"token": token}).get("node", {})
    obj_type = node.get("obj_type")
    obj_token = node.get("obj_token")
    title = node.get("title", "")
    if not obj_token:
        raise ValueError("该 wiki 节点未关联可读文档")
    if obj_type == "docx":
        return _fetch_docx(obj_token, title)
    if obj_type in ("sheet", "sheets"):
        return _fetch_sheets(obj_token, title)
    if obj_type == "bitable":
        return _fetch_base(obj_token, title)
    if obj_type == "doc":
        raise ValueError("暂不支持旧版文档（doc），请用新版云文档")
    raise ValueError(f"暂不支持的 wiki 节点类型：{obj_type}")


def extract_feishu(url: str) -> tuple[str, str]:
    """飞书链接取文统一入口，返回 (标题, 正文文本)。失败抛 ValueError。"""
    if not is_configured():
        raise ValueError("未配置飞书应用凭据（FEISHU_APP_ID/FEISHU_APP_SECRET），无法读取飞书文档")
    kind, token = parse_feishu_url(url)
    if kind == "docx":
        return _fetch_docx(token)
    if kind == "wiki":
        return _fetch_wiki(token)
    if kind == "sheets":
        return _fetch_sheets(token)
    if kind == "base":
        return _fetch_base(token)
    raise ValueError("不支持的飞书链接类型")


# ---- sheets 写回(导出 eval 结果到飞书表;移植自 ai-eval-cli feishu-sheet.js)----
def _col_to_num(col: str) -> int:
    """列字母→序号(多字母26进制):A=1,Z=26,AA=27。"""
    n = 0
    for ch in str(col).upper():
        c = ord(ch)
        if c < 65 or c > 90:
            continue
        n = n * 26 + (c - 64)
    return n or 1


def _num_to_col(n: int) -> str:
    """序号→列字母(_col_to_num 逆)。"""
    s = ""
    while n > 0:
        r = (n - 1) % 26
        s = chr(65 + r) + s
        n = (n - 1) // 26
    return s


_SHEET_TOKEN_ERR = {99991663, 99991661, 99991668}  # token 失效码(刷新重试)


def _api_put(path: str, body: dict) -> dict:
    """PUT 封装(仿 _api_get);token 失效刷新重试一次。code!=0 抛 ValueError。"""
    def _do():
        resp = requests.put(
            f"{_base()}{path}",
            headers={"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json"},
            json=body, timeout=15,
        )
        return resp.json()
    try:
        data = _do()
    except requests.RequestException as e:
        raise ValueError(f"飞书网络错误：{e}")
    except ValueError:
        raise ValueError("飞书接口返回非 JSON")
    if data.get("code") in _SHEET_TOKEN_ERR:
        _token_cache["token"] = ""  # 清缓存强制刷新
        try:
            data = _do()
        except requests.RequestException as e:
            raise ValueError(f"飞书网络错误：{e}")
    if data.get("code") != 0:
        raise ValueError(_friendly_err(data))
    return data.get("data", {}) or {}


def parse_sheet_url(url: str) -> tuple[str, str]:
    """从飞书 sheets/wiki 链接解析 (spreadsheet_token, sheet_id)。wiki 经 get_node 换 obj_token。"""
    import re as _re
    m_sheet = _re.search(r"[?&]sheet=([A-Za-z0-9]+)", url or "")
    sheet_id = m_sheet.group(1) if m_sheet else "0"
    m = _re.search(r"/sheets/([A-Za-z0-9]+)", url or "")
    if m:
        return m.group(1), sheet_id
    m = _re.search(r"/wiki/([A-Za-z0-9]+)", url or "")
    if m:
        node = _api_get("/open-apis/wiki/v2/spaces/get_node", {"token": m.group(1)}).get("node", {})
        obj = node.get("obj_token")
        if not obj:
            raise ValueError("wiki 节点未关联电子表格")
        return obj, sheet_id
    raise ValueError("无法识别飞书表格链接(支持 /sheets/ 或 /wiki/)")


_MAX_CELL = 45000  # 飞书单元格约 5 万字符上限


def _column_groups(col_map: dict) -> list:
    """把 {字段:列字母} 按列号排序,相邻列合并成组(非连续列分区间,不碰中间列)。
    返回 [{'start':n,'end':n,'fields':[...]}]。"""
    entries = sorted(({"field": f, "num": _col_to_num(c)} for f, c in col_map.items()), key=lambda e: e["num"])
    groups = []
    for e in entries:
        if groups and e["num"] == groups[-1]["end"] + 1:
            groups[-1]["end"] = e["num"]; groups[-1]["fields"].append(e["field"])
        else:
            groups.append({"start": e["num"], "end": e["num"], "fields": [e["field"]]})
    return groups


def write_sheet_rows(sheet_url: str, rows: list[dict], col_map: dict, start_row: int = 2) -> int:
    """把 rows(每个是 {字段:值} dict)按 col_map(字段→列字母)从 start_row 起逐行写飞书表。
    非连续列分区间写(不碰中间列);answer 等长文本截断。返回写入行数。"""
    if not is_configured():
        raise ValueError("未配置飞书应用凭据(FEISHU_APP_ID/FEISHU_APP_SECRET)")
    token, sheet_id = parse_sheet_url(sheet_url)
    groups = _column_groups(col_map)
    for i, row in enumerate(rows):
        real_row = start_row + i
        for g in groups:
            vals = []
            for f in g["fields"]:
                v = row.get(f, "")
                v = "" if v is None else str(v)
                if len(v) > _MAX_CELL:
                    v = v[:_MAX_CELL]
                vals.append(v)
            rng = f"{sheet_id}!{_num_to_col(g['start'])}{real_row}:{_num_to_col(g['end'])}{real_row}"
            _api_put(f"/open-apis/sheets/v2/spreadsheets/{token}/values",
                     {"valueRange": {"range": rng, "values": [vals]}})
    return len(rows)
