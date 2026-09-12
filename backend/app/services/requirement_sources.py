"""Read text, tables and image evidence without publishing private documents.

Extraction is deterministic. Visual interpretation happens later in the AI queue,
with a result per image and a visible failure for every unreadable/limited item.
"""
import base64
import io
import json
import re
import zipfile
from html.parser import HTMLParser
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor

from app.services import extractors, feishu

MAX_IMAGES = 40
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024


class Materials:
    def __init__(self):
        self.items = []
        self.warnings = []
        self.total_bytes = 0

    def failed(self, location, reason, source_url=""):
        self.items.append({"id": f"IMG{len(self.items) + 1}", "location": location,
                           "source_url": source_url, "error": reason})

    def add(self, data, location, source_url=""):
        if len(self.items) >= MAX_IMAGES:
            warning = f"图片超过单次 {MAX_IMAGES} 张上限，后续图片未读取；请拆分资料后补充分析"
            if warning not in self.warnings:
                self.warnings.append(warning)
            return
        if len(data) > MAX_IMAGE_BYTES or self.total_bytes + len(data) > MAX_TOTAL_BYTES:
            self.failed(location, "图片超出大小限制，请压缩或拆分后补充", source_url)
            return
        try:
            from PIL import Image, ImageOps
            with Image.open(io.BytesIO(data)) as original:
                if getattr(original, "n_frames", 1) > 1:
                    self.warnings.append(f"{location} 为多帧图片，仅读取首帧，请补充其余关键帧")
                if original.width * original.height > 40_000_000:
                    raise ValueError("图片像素过大，请拆分")
                img = ImageOps.exif_transpose(original).convert("RGB")
                # Keep small text readable in long screenshots: tile instead of
                # shrinking the entire height into a single thumbnail.
                if img.width > 2000:
                    img = img.resize((2000, max(1, round(img.height * 2000 / img.width))))
                starts = list(range(0, img.height, 1800))
                for index, top in enumerate(starts):
                    if len(self.items) >= MAX_IMAGES:
                        self.warnings.append(f"{location} 未完整读取：分片超出单次图片上限")
                        break
                    tile = img.crop((0, max(0, top - 100), img.width, min(img.height, top + 1800)))
                    output = io.BytesIO()
                    tile.save(output, format="PNG")
                    raw = output.getvalue()
                    if self.total_bytes + len(raw) > MAX_TOTAL_BYTES or len(raw) > MAX_IMAGE_BYTES:
                        self.failed(location, "图片解码后超出大小限制，请拆分后补充", source_url)
                        break
                    self.total_bytes += len(raw)
                    self.items.append({"id": f"IMG{len(self.items) + 1}", "mime_type": "image/png",
                                       "data": base64.b64encode(raw).decode("ascii"), "source_url": source_url,
                                       "location": location + (f"（分片 {index + 1}/{len(starts)}）" if len(starts) > 1 else "")})
        except Exception as exc:
            self.failed(location, f"图片无法解码（{type(exc).__name__}），请转为 PNG/JPG 后补充", source_url)


def public_materials(items):
    return [{k: v for k, v in item.items() if k != "data"} for item in items]


def _download_media(token):
    import requests
    if not re.fullmatch(r"[A-Za-z0-9_-]+", token or ""):
        raise ValueError("无效的图片素材标识")
    with requests.get(f"{feishu._base()}/open-apis/drive/v1/medias/{token}/download",
                      headers={"Authorization": f"Bearer {feishu._get_token()}"},
                      stream=True, timeout=20) as response:
        if response.status_code != 200:
            raise ValueError("图片下载失败，请检查飞书素材下载权限及文档共享权限")
        content = bytearray()
        for chunk in response.iter_content(65536):
            content.extend(chunk)
            if len(content) > MAX_IMAGE_BYTES:
                raise ValueError("图片超过 8MB，请拆分后补充")
        return bytes(content)


def _feishu_document(url):
    kind, token = feishu.parse_feishu_url(url)
    if kind == "wiki":
        node = feishu._api_get("/open-apis/wiki/v2/spaces/get_node", {"token": token}).get("node", {})
        kind, token = node.get("obj_type"), node.get("obj_token")
    if kind != "docx":
        title, text = feishu.extract_feishu(url)
        return title, text, [], ["当前链接按表格记录提取；嵌入图片请另行上传识别，并核对是否存在未读取的工作表或记录"]
    title, text = feishu._fetch_docx(token)
    materials = Materials()
    cursor = None
    seen = set()
    context = "文档"
    pending_images = []
    try:
        for _ in range(200):
            params = {"page_size": 500, "document_revision_id": -1}
            if cursor:
                params["page_token"] = cursor
            page = feishu._api_get(f"/open-apis/docx/v1/documents/{token}/blocks", params)
            for block in page.get("items", []):
                block_url = f"{url.split('#')[0]}#{block.get('block_id', '')}"
                for name, value in block.items():
                    if (name.startswith("heading") or name == "text") and isinstance(value, dict):
                        content = "".join(e.get("text_run", {}).get("content", "") for e in value.get("elements", []))
                        if content.strip():
                            context = content.strip()[:180]
                if block.get("image"):
                    pending_images.append((block["image"].get("token"), f"{context} · 图片", block_url))
                for key, label in (("file", "附件"), ("whiteboard", "画板"), ("sheet", "嵌入表格"), ("bitable", "多维表格")):
                    if block.get(key):
                        materials.warnings.append(f"{context}：{label}需补充读取，可导出为图片、PDF 或文字后上传")
            if not page.get("has_more"):
                break
            cursor = page.get("page_token")
            if not cursor or cursor in seen:
                raise ValueError("文档块分页未完整返回")
            seen.add(cursor)
        else:
            raise ValueError("文档块超过读取上限，请按章节拆分")
    except Exception as exc:
        materials.warnings.append(f"正文已读取，图片清单未完整获取：{str(exc)[:300]}")
    def download(item):
        token, location, block_url = item
        try:
            return _download_media(token), location, block_url, None
        except Exception as exc:
            return None, location, block_url, str(exc)[:300]
    # Bounded parallel I/O, then add in document order so references stay stable.
    with ThreadPoolExecutor(max_workers=6) as pool:
        for data, location, block_url, error in pool.map(download, pending_images[:MAX_IMAGES]):
            if error:
                materials.failed(location, error, block_url)
            else:
                materials.add(data, location, block_url)
    if len(pending_images) > MAX_IMAGES:
        materials.warnings.append(f"文档共 {len(pending_images)} 张图片，仅读取前 {MAX_IMAGES} 张，请拆分后补充")
    return title, text, materials.items, materials.warnings


class _ImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img" and attrs.get("src"):
            self.images.append((attrs["src"], attrs.get("alt") or "网页图片"))


def _public_download(url):
    from urllib import request
    from urllib.error import HTTPError, URLError
    extractors._assert_safe_url(url)
    opener = request.build_opener(extractors._SafeRedirect())
    try:
        with opener.open(request.Request(url, headers={"User-Agent": extractors._UA}), timeout=15) as response:
            raw = response.read(MAX_IMAGE_BYTES + 1)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("资源超过 8MB")
            return raw, response.headers.get("Content-Type", ""), response.geturl()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise ValueError(f"资源下载失败：{getattr(exc, 'reason', exc)}") from exc


def from_url(url):
    if feishu.is_feishu_url(url):
        return _feishu_document(url)
    raw, content_type, final_url = _public_download(url)
    if content_type.startswith("image/"):
        materials = Materials()
        materials.add(raw, "需求图片", final_url)
        return "图片需求", "[需求内容见图片]", materials.items, materials.warnings
    if "application/pdf" in content_type:
        text, images, warnings = from_file("需求.pdf", raw)
        return "PDF 需求", text, images, warnings
    charset = re.search(r"charset=([\w-]+)", content_type, re.I)
    try:
        page = raw.decode(charset.group(1) if charset else "utf-8", "replace")
    except LookupError:
        raise ValueError("网页字符编码不受支持，请导出 UTF-8 文本")
    if "html" not in content_type.lower() and "<html" not in page[:2000].lower():
        return "", page.strip(), [], []
    title, text = extractors._html_to_text(re.sub(r"(?i)</t[dh]>", " | ", page))
    parser = _ImageParser()
    parser.feed(page)
    materials = Materials()
    def download_html(item):
        src, alt = item
        image_url = urljoin(final_url, src)
        try:
            if src.startswith("data:image/") and ";base64," in src:
                data = base64.b64decode(src.split(",", 1)[1], validate=True)
            else:
                data, _, _ = _public_download(image_url)
            return data, alt, image_url if not src.startswith("data:") else final_url, None
        except Exception:
            return None, alt, image_url if not src.startswith("data:") else final_url, "网页图片下载失败，请上传原图补充"
    with ThreadPoolExecutor(max_workers=6) as pool:
        for data, alt, image_url, error in pool.map(download_html, parser.images[:MAX_IMAGES]):
            if error:
                materials.failed(alt, error, image_url)
            else:
                materials.add(data, alt, image_url)
    if len(parser.images) > MAX_IMAGES:
        materials.warnings.append(f"网页超过 {MAX_IMAGES} 张图片，未读取剩余图片")
    materials.warnings.append("已读取网页静态内容；登录态或动态加载内容如未出现，请补充资料")
    return title, text, materials.items, materials.warnings


def from_file(filename, data):
    ext = filename.rsplit(".", 1)[-1].lower()
    materials = Materials()
    if ext in {"png", "jpg", "jpeg", "webp", "gif", "bmp"}:
        materials.add(data, filename)
        return "[需求内容见图片]", materials.items, materials.warnings
    if ext == "docx":
        # Reject zip bombs before python-docx expands entries.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 80 * 1024 * 1024:
                raise ValueError("文档解压后超过 80MB，请按章节拆分")
        import docx
        from docx.oxml.ns import qn
        doc = docx.Document(io.BytesIO(data))
        parts = []
        if any("header" in str(rel.reltype) or "footer" in str(rel.reltype) for rel in doc.part.rels.values()):
            materials.warnings.append("Word 页眉页脚未纳入正文解析；如含验收信息，请补充截图或导出 PDF")
        for index, element in enumerate(doc.element.body):
            paragraphs = element.iter(qn("w:p"))
            lines = []
            if element.tag == qn("w:tbl"):
                from docx.table import Table
                table = Table(element, doc._body)
                lines = [" | ".join(cell.text.replace("\n", " / ") for cell in row.cells) for row in table.rows]
            else:
                for p in paragraphs:
                    line = "".join(n.text or "" for n in p.iter(qn("w:t")))
                    if line:
                        lines.append(line)
            if lines:
                parts.append("\n".join(lines))
            for image in element.iter(qn("a:blip")):
                rid = image.get(qn("r:embed"))
                try:
                    materials.add(doc.part.related_parts[rid].blob, f"第 {index + 1} 内容块 · {' / '.join(lines)[:100]}")
                except Exception:
                    materials.failed(f"第 {index + 1} 内容块", "嵌入图片未读取，请补充原图")
            if any(n.tag.rsplit("}", 1)[-1] in {"pict", "object", "diagram"} for n in element.iter()):
                materials.warnings.append(f"第 {index + 1} 内容块含旧式图形或嵌入对象，请对照原文并补充截图")
        return "\n\n".join(parts), materials.items, materials.warnings
    if ext == "pdf":
        import pypdfium2 as pdfium
        parts = []
        pdf = pdfium.PdfDocument(data)
        try:
            if len(pdf) > MAX_IMAGES:
                materials.warnings.append(f"PDF 共 {len(pdf)} 页，仅读取前 {MAX_IMAGES} 页，请拆分剩余页面")
            for index in range(min(len(pdf), MAX_IMAGES)):
                page = pdf[index]
                text_page = page.get_textpage()
                try:
                    parts.append(f"[第 {index + 1} 页]\n{text_page.get_text_range()}")
                    # Rendering the whole page preserves scanned text, vectors,
                    # table headers and arrows that image-only extraction misses.
                    bitmap = page.render(scale=min(2, 2000 / max(page.get_width(), 1)))
                    try:
                        output = io.BytesIO()
                        bitmap.to_pil().save(output, format="PNG")
                        materials.add(output.getvalue(), f"PDF 第 {index + 1} 页")
                    finally:
                        bitmap.close()
                finally:
                    text_page.close()
                    page.close()
        finally:
            pdf.close()
        return "\n\n".join(parts), materials.items, materials.warnings
    return extractors.extract_from_file(filename, data), [], []


def save_source(db, user_id, title, url, text, materials, warnings):
    from app.models import RequirementSource
    from app.schemas.ai import REQUIREMENT_MAX_LEN
    if len(text) > REQUIREMENT_MAX_LEN:
        warnings.append(f"正文共 {len(text)} 字，编辑区只载入前 {REQUIREMENT_MAX_LEN} 字；完整原文已保存，请拆分后分析")
    source = RequirementSource(created_by=user_id, title=title[:512], url=url, text=text,
                               materials=json.dumps(materials, ensure_ascii=False), warnings=json.dumps(warnings, ensure_ascii=False))
    db.add(source)
    db.commit()
    return {"source_id": source.id, "title": title, "filename": title, "chars": len(text),
            "text": text[:REQUIREMENT_MAX_LEN], "materials": public_materials(materials), "warnings": warnings,
            "truncated": len(text) > REQUIREMENT_MAX_LEN}
