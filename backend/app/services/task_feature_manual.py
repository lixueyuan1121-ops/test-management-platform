"""Merge the selected tasks' HTML attachments and publish a verified manual."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from sqlalchemy import func

from app.core.config import settings
from app.models.task import Task
from app.models.task_digest import TaskDigestManual
from app.services import nami_deploy

BACKEND = Path(__file__).resolve().parents[2]
MAX_TOTAL = 40 * 1024 * 1024


class ManualError(Exception):
    pass


@dataclass
class ManualJob:
    publication_id: int
    source_hash: str
    sources: list
    upload_url: str | None
    html_hash: str | None


def prepare_manual(db, publication, digest):
    ids = json.loads(digest.task_ids)
    query = db.query(Task).filter(Task.project_id == digest.project_id, Task.id.in_(ids), Task.feature_html_name.isnot(None))
    metadata = {row.id: row for row in query.with_entities(
        Task.id, Task.feature_html_name, Task.feature_html_hash, func.length(Task.feature_html).label("size")).all()}
    ordered = [metadata[i] for i in ids if i in metadata]
    record = db.get(TaskDigestManual, publication.id, with_for_update=True)
    if not ordered:
        if record:
            db.delete(record)
        return None
    signature = hashlib.sha256(json.dumps([list(row) for row in ordered], ensure_ascii=False).encode()).hexdigest()
    if record is None:
        record = TaskDigestManual(publication_id=publication.id)
        db.add(record)
    if record.source_hash != signature:
        record.source_hash = signature
        record.short_url = record.upload_url = record.html_hash = None
    if record.short_url:
        return None
    if sum(row.size for row in ordered) > MAX_TOTAL:
        record.error = "所选功能说明附件合计超过 40 MiB，请缩小日报范围或压缩内嵌图片"
        return None
    sources = []
    if not record.upload_url:
        files = dict(query.with_entities(Task.id, Task.feature_html).all())
        for row in ordered:
            data = files.get(row.id)
            if data is None or hashlib.sha256(data).hexdigest() != row.feature_html_hash:
                record.error = "附件刚被修改，请重新发布"
                return None
            sources.append((row.feature_html_name, data))
    record.error = None
    return ManualJob(publication.id, signature, sources, record.upload_url, record.html_hash)


def merge_manual(sources):
    skill = Path(os.environ.get("TASK_REPORT_SUITE_DIR") or BACKEND / ".skills/tuitui-report-suite").resolve()
    if not all((skill / f).is_file() for f in ("scripts/merge_reports.py", "scripts/clean_merged_html.py", "assets/manual.css")):
        raise ManualError("服务端尚未完整安装 tuitui-report-suite")
    with tempfile.TemporaryDirectory() as staging:
        root = Path(staging)
        paths = []
        for i, (name, data) in enumerate(sources):
            directory = root / str(i)
            directory.mkdir()
            # A Unicode upload name can exceed the server filesystem byte limit.
            stem = Path(name).stem.encode("utf-8")[:220].decode("utf-8", errors="ignore")
            path = directory / ((stem or "功能说明") + ".html")
            path.write_bytes(data)
            paths.append(str(path))
        manifest = root / "sources.json"
        manifest.write_text(json.dumps(paths), encoding="utf-8")
        env = {"PATH": os.defpath, "PYTHONUTF8": "1"}
        if os.name == "nt":
            env["SystemRoot"] = os.environ["SystemRoot"]
        result = subprocess.run([sys.executable, "-I", "-X", "utf8", str(BACKEND / "skill_adapters/task_feature_manual.py"),
                                 str(skill), str(manifest)], cwd=root, env=env, capture_output=True,
                                encoding="utf-8", timeout=60)
        if result.returncode == 2:
            raise ManualError(result.stderr.strip()[:240] or "功能说明格式无效")
        if result.returncode:
            raise ManualError("功能说明合并失败，请检查附件是否为完整的 HTML 文档")
        return (root / "manual.html").read_text(encoding="utf-8")


def verify_manual(url, expected_hash):
    if not nami_deploy.is_short_url(url):
        raise ManualError("短链服务未返回有效的手册短链")
    target = url
    for _ in range(4):
        with requests.get(target, timeout=(5, 20), stream=True, allow_redirects=False) as response:
            if response.is_redirect:
                target = urljoin(target, response.headers["Location"])
                parts = urlsplit(target)
                if parts.scheme != "https" or not (nami_deploy.is_short_url(target) or parts.netloc == "ns.chat.360.cn"):
                    break
                continue
            response.raise_for_status()
            digest, size = hashlib.sha256(), 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_TOTAL + 5 * 1024 * 1024:
                    raise ManualError("短链返回的页面超过手册大小限制")
                digest.update(chunk)
            if digest.hexdigest() == expected_hash:
                return
            break
    raise ManualError("短链返回的页面与本次合并的手册不一致，请检查服务端短链配置后重试")


def publish_manual(db, job):
    direct, checksum, short_url, error = job.upload_url, job.html_hash, None, None
    try:
        if not direct:
            html = merge_manual(job.sources)
            checksum = hashlib.sha256(html.encode("utf-8")).hexdigest()
        if not settings.NAMI_DEPLOY_ENABLED or not nami_deploy.is_configured():
            raise ManualError("服务端 HTML 发布服务未配置，请联系管理员后重试")
        if not direct:
            direct = nami_deploy.deploy_html(html, prefer_shortlink=False)
        short = nami_deploy.shorten_uploaded_url(direct)
        verify_manual(short, checksum)
        short_url = short
    except ManualError as exc:
        error = str(exc)
    except subprocess.TimeoutExpired:
        error = "功能说明合并超时，请缩小附件后重试"
    except (OSError, ValueError, nami_deploy.NamiDeployError, requests.RequestException):
        error = "手册短链发布或访问校验失败，请稍后重试"
    record = db.get(TaskDigestManual, job.publication_id, populate_existing=True, with_for_update=True)
    if record and record.source_hash == job.source_hash and not record.short_url:
        if direct:
            record.upload_url, record.html_hash = direct, checksum
        record.short_url, record.error = short_url, error
    db.commit()
