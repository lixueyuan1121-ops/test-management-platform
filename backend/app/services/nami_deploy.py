"""Nami 静态部署:把综合评价 HTML 上传到 n.cn 网关、换成公网短链(zhaomi.cn/ns.chat.360.cn)。

移植自 skill `nami-static-deploy` 的 deploy-static-zip.py(www.n.cn 上传 + get_short 取短链),
搬进后端服务以便一条龙无人值守时也能出短链。与原脚本的差异:
  · 用 requests(平台已依赖)替代 urllib;
  · **平台名用 platform.system()**——原脚本 os.uname() 在 Windows(线上部署形态)会 AttributeError;
  · cookie / cloud_config 路径可经 .env 配置(NAMI_COOKIE_PATH / NAMI_CLOUD_CONFIG_PATH),
    缺省沿用 skill 的 ~/.openclaw/workspace/config/{.cookie.json,cloud_config.json}。

失败(缺凭据/cookie 过期/网关错)一律抛 NamiDeployError,调用方回落自托管 /r/<code>,不阻断一条龙。
凭据在服务器上不存在或过期时,is_configured() 返回 False,调用方直接跳过(不发无谓请求)。
"""
import hashlib
import io
import json
import logging
import os
import platform
import random
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import requests

from app.core.config import settings

logger = logging.getLogger("test_platform")

_GATEWAY = "https://www.n.cn"
_UPLOAD_API_PATH = "/api/s3/upload/zip"
_UPLOAD_API_URL = f"{_GATEWAY}{_UPLOAD_API_PATH}?appsource=so"
_SHORT_API_URL = f"{_GATEWAY}/api/get_short?appsource=so"
_UA = "Mozilla/5.0 (Nami Static Deploy Backend)"
_TIMEOUT = 30


class NamiDeployError(Exception):
    """部署/取短链失败(缺凭据、cookie 过期、网关错误等)。调用方应回落自托管短链。"""


def _cookie_path() -> Path:
    p = (settings.NAMI_COOKIE_PATH or "~/.openclaw/workspace/config/.cookie.json")
    return Path(p).expanduser()


def _cloud_config_path() -> Path:
    p = (settings.NAMI_CLOUD_CONFIG_PATH or "~/.openclaw/workspace/config/cloud_config.json")
    return Path(p).expanduser()


def is_configured() -> bool:
    """cookie 文件存在即认为"可尝试"(能否成功仍取决于 cookie 是否过期,失败再回落)。"""
    try:
        return _cookie_path().is_file()
    except OSError:
        return False


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise NamiDeployError(f"读取 {path} 失败:{e}")


def _extract_cookie_value(node) -> str:
    """从 cookie 配置(可能是字符串/字典/嵌套/列表)里抠出 "k=v; k2=v2" 形态 cookie。移植自 skill。"""
    if isinstance(node, str):
        c = node.strip()
        if c and "=" in c:
            return c
    if isinstance(node, dict):
        for key in ("cookie", "Cookie"):
            v = node.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        if node and all(isinstance(k, str) and isinstance(v, str) for k, v in node.items()):
            return "; ".join(f"{k}={v}" for k, v in node.items())
        for v in node.values():
            c = _extract_cookie_value(v)
            if c:
                return c
    if isinstance(node, list):
        for item in node:
            c = _extract_cookie_value(item)
            if c:
                return c
    return ""


def _read_cookie() -> str:
    cookie = _extract_cookie_value(_read_json(_cookie_path()))
    if not cookie:
        raise NamiDeployError(f"cookie 未在 {_cookie_path()} 中找到")
    return cookie


def _read_vm_id() -> str:
    """读 vm_id(取短链接口需要);读不到返回空串(调用方退回目录 URL 而非短链)。"""
    try:
        data = _read_json(_cloud_config_path())
    except NamiDeployError:
        return ""
    if not isinstance(data, dict):
        return ""
    raw = data.get("vm_id")
    is_prod = data.get("isProd")
    if not isinstance(raw, str) or not raw.strip() or not isinstance(is_prod, bool):
        return ""
    return f"{'p' if is_prod else 't'}{raw.strip()}"


def _md5(v: str) -> str:
    return hashlib.md5(v.encode("utf-8")).hexdigest()


def _signed_headers(api_path: str, cookie: str) -> dict:
    """网关动态签名头(与 skill 一致)。平台名用 platform.system()——Windows 无 os.uname()。"""
    access_token = str(uuid.uuid4())
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    zm_nonce = "".join(str(random.randint(0, 9)) for _ in range(16))
    zm_ua = _md5(_UA)
    zm_token = _md5("".join(["H5", timestamp, "1.3", access_token, zm_ua, api_path, zm_nonce]))
    return {
        "device-platform": "H5", "timestamp": timestamp, "zm-ver": "1.3",
        "access-token": access_token, "zm-token": zm_token, "zm-ua": zm_ua,
        "zm-nonce": zm_nonce, "nami-platform": platform.system().lower(),
        "func-ver": "1", "Request-Id": str(uuid.uuid4()), "Header-Tid": uuid.uuid4().hex,
        "cloud_src": "video", "Accept": "application/json", "Cookie": cookie,
    }


def _html_to_zip_bytes(html: str) -> bytes:
    """把 HTML 文本打成 index.html 在根层的 zip(网关只收 zip;不落地临时文件)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", html)
    return buf.getvalue()


def _extract_uploaded_url(resp: dict) -> str:
    """从上传响应取目录级 URL(短链接口要不带 /index.html 的 base_url)。移植自 skill。"""
    if resp.get("code") != 0:
        raise NamiDeployError(f"上传失败:code={resp.get('code')} msg={resp.get('msg')}")
    data = resp.get("data")
    if not isinstance(data, dict):
        raise NamiDeployError("上传响应缺 data")
    base_url = data.get("base_url")
    if isinstance(base_url, str) and base_url.strip():
        return base_url.strip()
    up_urls = data.get("up_urls")
    if isinstance(up_urls, list):
        entries = [x.strip() for x in up_urls if isinstance(x, str) and x.strip()]
        if entries:
            url = entries[0]
            return url[:-len("/index.html")] if url.endswith("/index.html") else url
    up_url = data.get("up_url")
    if isinstance(up_url, str) and up_url.strip():
        return up_url.strip()
    raise NamiDeployError("上传响应无可用 URL(base_url/up_urls/up_url 皆空)")


def _upload(html: str, cookie: str) -> str:
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    file_bytes = _html_to_zip_bytes(html)
    pre = ("\r\n".join([
        f"--{boundary}", 'Content-Disposition: form-data; name="source_type"', "", "static",
        f"--{boundary}",
        'Content-Disposition: form-data; name="up_file"; filename="report.zip"',
        "Content-Type: application/zip", "",
    ]) + "\r\n").encode("utf-8")
    body = pre + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    headers = _signed_headers(_UPLOAD_API_PATH, cookie)
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    try:
        r = requests.post(_UPLOAD_API_URL, data=body, headers=headers, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise NamiDeployError(f"上传网络异常:{e}")
    if r.status_code != 200:
        raise NamiDeployError(f"上传 HTTP {r.status_code}:{r.text[:200]}")
    try:
        return _extract_uploaded_url(r.json())
    except ValueError:
        raise NamiDeployError(f"上传响应非 JSON:{r.text[:200]}")


def _get_short(vm_id: str, base_url: str, cookie: str) -> str:
    try:
        r = requests.post(_SHORT_API_URL, json={"vm_id": vm_id, "base_url": base_url},
                          headers={"Content-Type": "application/json", "cookie": cookie},
                          timeout=15)
    except requests.RequestException as e:
        raise NamiDeployError(f"取短链网络异常:{e}")
    if r.status_code != 200:
        raise NamiDeployError(f"取短链 HTTP {r.status_code}:{r.text[:200]}")
    try:
        data = r.json()
    except ValueError:
        raise NamiDeployError(f"取短链响应非 JSON:{r.text[:200]}")
    if not isinstance(data, dict) or data.get("code") != 0:
        raise NamiDeployError(f"取短链失败:code={data.get('code') if isinstance(data, dict) else '?'}")
    short = data.get("data", {}).get("shor_url") if isinstance(data.get("data"), dict) else ""
    if not isinstance(short, str) or not short.strip():
        raise NamiDeployError("取短链响应缺 data.shor_url")
    return short.strip()


def deploy_html(html: str) -> str:
    """把 HTML 部署为公网可访问短链,返回短 URL。失败抛 NamiDeployError(调用方回落自托管)。

    无 vm_id(cloud_config 缺失)时退回目录级 URL(仍公网可访问,只是不是最短形态)。
    """
    if not html or not html.strip():
        raise NamiDeployError("HTML 为空,不部署")
    cookie = _read_cookie()
    base_url = _upload(html, cookie)
    vm_id = _read_vm_id()
    if not vm_id:
        return base_url
    try:
        return _get_short(vm_id, base_url, cookie)
    except NamiDeployError:
        return base_url  # 取短链失败退回目录 URL(已公网可访问)
