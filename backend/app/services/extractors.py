"""需求取文：URL 抓取正文、上传文档解析成纯文本，供 QA Copilot 生成测试点。

安全（关键）：URL 抓取存在 SSRF 风险（云元数据 169.254.169.254、内网服务、file://）。
防护：仅放行 http/https；解析域名后禁止内网/环回/链路本地/保留地址；重定向逐跳
校验（防 3xx 绕过）。DNS rebinding（校验后连接时 IP 变化）属高级攻击，内部工具
不额外防护——已在注释标注权衡。
"""
import html
import io
import ipaddress
import re
import socket
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

_MAX_FETCH_BYTES = 2 * 1024 * 1024  # 网页正文抓取上限 2MB
_UA = "Mozilla/5.0 (compatible; QACopilot/1.0)"


def _assert_safe_url(url: str) -> None:
    """校验 URL 目标地址安全（SSRF 防护）。不安全则抛 ValueError。"""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError("只允许 http/https 链接")
    host = p.hostname
    if not host:
        raise ValueError("无效的 URL")
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("无法解析域名")
    for family, _type, _proto, _canon, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError("禁止访问内网 / 环回 / 保留地址")


class _SafeRedirect(urlrequest.HTTPRedirectHandler):
    """跟随重定向前对目标 URL 再做一次 SSRF 校验，防止 3xx 跳到内网。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _html_to_text(page: str) -> tuple[str, str]:
    """极简 HTML 正文提取：去 script/style、块级标签转换行、剥标签、反转义。"""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    title = html.unescape(m.group(1)).strip() if m else ""
    body = re.sub(r"(?is)<(script|style|noscript|template).*?</\1>", " ", page)
    body = re.sub(r"(?is)<br\s*/?>", "\n", body)
    body = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|section|article)>", "\n", body)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f]+", " ", body)
    body = re.sub(r"\n[ \t]*", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return title, body.strip()


def extract_from_url(url: str) -> tuple[str, str]:
    """抓取 URL 并返回 (标题, 正文文本)。失败抛 ValueError（含安全拒绝）。

    飞书文档（docx/wiki/sheets/base）是鉴权动态页，普通 HTTP 抓不到正文，
    改走飞书 OpenAPI；其余 URL 走通用 HTML 抓取（带 SSRF 防护）。
    """
    from app.services import feishu
    if feishu.is_feishu_url(url):
        return feishu.extract_feishu(url)
    _assert_safe_url(url)
    opener = urlrequest.build_opener(_SafeRedirect())
    req = urlrequest.Request(url, headers={"User-Agent": _UA})
    try:
        with opener.open(req, timeout=10) as resp:
            ctype = resp.headers.get("Content-Type", "") or ""
            raw = resp.read(_MAX_FETCH_BYTES + 1)[:_MAX_FETCH_BYTES]
    except ValueError:
        raise  # 重定向校验拒绝，原样上抛
    except (HTTPError, URLError) as e:
        raise ValueError(f"抓取失败：{getattr(e, 'reason', e)}")

    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        charset = m.group(1)
    text = raw.decode(charset, "ignore")
    if "html" in ctype.lower() or "<html" in text[:2000].lower():
        return _html_to_text(text)
    return "", text.strip()


def extract_from_file(filename: str, data: bytes) -> str:
    """按扩展名把上传文档解析为纯文本。不支持的类型抛 ValueError。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("txt", "md", "markdown"):
        return data.decode("utf-8", "ignore")
    if ext == "docx":
        import docx  # python-docx
        doc = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text)
    if ext == "pdf":
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)
    raise ValueError("不支持的文件类型（支持 txt / md / docx / pdf）")
