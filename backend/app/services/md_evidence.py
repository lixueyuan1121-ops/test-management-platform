"""Authenticated task evidence and independently retryable Geelib attachment receipts."""
import hashlib
import re
import time
import uuid
from pathlib import Path

import requests
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.models.md_run import MdRunEvidence, MdEvidenceDelivery
from app.services import md_bug_skill, md_attachment_skill
from app.services.geelib import GeelibError
from app.services.md_result import linked_cases, case_keys

MAX_FILE = 3 * 1024 * 1024
MAX_FILES = 32
MAX_TOTAL = 32 * 1024 * 1024
MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.log': 'text/plain', '.txt': 'text/plain'}


def save(db, run, name, label, content):
    ext = Path(name).suffix.lower()
    if (not re.fullmatch(r'[\w .()（）\-]{1,140}', name) or name.startswith('.') or ext not in MIME
            or len(label) > 200 or any(ord(c) < 32 for c in label)):
        raise HTTPException(422, '无效的附件文件名或关联用例')
    if not 0 < len(content) <= MAX_FILE:
        raise HTTPException(413, '单份执行附件须在 1 字节至 3 MiB 之间')
    kind = 'screenshot' if MIME[ext].startswith('image/') else 'log'
    if ext == '.png' and not content.startswith(b'\x89PNG\r\n\x1a\n'):
        raise HTTPException(422, '截图不是 PNG 文件')
    if ext in ('.jpg', '.jpeg') and not content.startswith(b'\xff\xd8\xff'):
        raise HTTPException(422, '截图不是 JPEG 文件')
    if kind == 'log' and b'\x00' in content:
        raise HTTPException(422, '客户端日志须为文本')
    key = hashlib.sha256(label.encode() + b'\0' + name.encode() + b'\0' + content).hexdigest()
    query = db.query(MdRunEvidence).filter_by(run_id=run.id)
    existing = query.filter_by(fingerprint=key).first()
    if existing:
        return existing
    count, size = query.with_entities(func.count(MdRunEvidence.id), func.sum(MdRunEvidence.size)).one()
    if count >= MAX_FILES or (size or 0) + len(content) > MAX_TOTAL:
        raise HTTPException(413, '任务附件超过 32 份或 32 MiB')
    row = MdRunEvidence(run_id=run.id, fingerprint=key, name=name, kind=kind, size=len(content),
                        case_label=label, content=content)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return query.filter_by(fingerprint=key).one()
    return row


def metadata(row):
    return {'id': row.id, 'name': row.name, 'kind': row.kind, 'case': row.case_label, 'size': row.size}


def related(rows, finding, count):
    keys = linked_cases(finding) | case_keys(finding['title'])
    specific = [row for row in rows if row.case_label and keys.intersection(case_keys(row.case_label))]
    # The final screenshot is context only for a single finding. A shared task log is explicitly named.
    kinds = {row.kind for row in specific}
    return specific + [row for row in rows if not row.case_label and row.kind not in kinds
                       and (count == 1 or (row.kind == 'log' and row.name == '任务时段客户端.log'))]


def _remote_file(rows, name):
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get('file_name') == name and row.get('id'):
                return str(row['id'])
    return None


def _upload(row, sub_id, matter_id, name, token):
    try:
        rows = md_attachment_skill.upload(sub_id, matter_id, name, row.content, MIME[Path(name).suffix.lower()], token)
    except OSError as exc:
        raise GeelibError('服务端暂时无法启动附件技能，请稍后补传') from exc
    remote_id = _remote_file(rows, name)
    if not remote_id:
        raise ValueError('missing attachment id')
    return remote_id


def _reconcile(sub_id, matter_id, name, token):
    detail = md_bug_skill._read_tracker('GET', 'getDetail', {'sub_id': sub_id, 'id': matter_id}, token)
    if isinstance(detail, dict):
        for info in (detail, detail.get('matterInfo'), detail.get('matter')):
            if isinstance(info, dict) and isinstance(info.get('attachs'), list):
                return _remote_file(info['attachs'], name)
    raise ValueError('attachment list unavailable')


def deliver(db, row, sub_id, matter_id, token):
    key = (row.id, sub_id, matter_id)
    claim = uuid.uuid4().hex
    now = int(time.time())
    entry = db.get(MdEvidenceDelivery, key)
    prior = entry.state if entry else None
    previous_start = entry.started if entry else now
    if entry:
        if prior == 'sent' or (prior == 'sending' and now - entry.started < 120):
            return prior
        changed = db.query(MdEvidenceDelivery).filter_by(evidence_id=row.id, sub_id=sub_id, matter_id=matter_id,
                    attempt=entry.attempt, state=prior).update({'attempt': claim, 'started': now, 'state': 'sending'})
        db.commit()
        if not changed:
            return 'sending'
    else:
        db.add(MdEvidenceDelivery(evidence_id=row.id, sub_id=sub_id, matter_id=matter_id, state='sending',
                                  attempt=claim, started=now))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return 'sending'
    name = f'QALab-{row.run_id}-{row.id}-{row.fingerprint[:10]}-{row.name}'
    remote_id, message = None, None
    last_upload = previous_start
    try:
        if prior in ('sending', 'uncertain'):
            remote_id = _reconcile(sub_id, matter_id, name, token)
            if not remote_id and now - previous_start < 120:
                raise ValueError('等待极库云回执同步')
        if not remote_id:
            last_upload = now
            remote_id = _upload(row, sub_id, matter_id, name, token)
        state = 'sent'
    except GeelibError as exc:
        state = 'uncertain' if prior in ('sending', 'uncertain') else 'failed'
        message = str(exc)[:200]
    except (requests.RequestException, ValueError, TypeError):
        state, message = 'uncertain', '附件回执未确认，重试时会先核对工单附件列表'
    db.query(MdEvidenceDelivery).filter_by(evidence_id=row.id, sub_id=sub_id, matter_id=matter_id,
            attempt=claim).update({'state': state, 'remote_id': remote_id, 'message': message, 'started': last_upload})
    db.commit()
    return state
