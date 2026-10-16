"""提测台账的日报快照与发版归档。原逐任务日报和验收关系不变。"""
import json
import hashlib
import secrets
import re

from app.models.task_digest import TaskDigestPublication, TaskDigestGeneration, TaskDigestDeployment

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, require_platform_admin
from app.core.enums import ProjectRole, TaskStatus
from app.db.session import get_db
from app.models import Project, ReleaseRecord, Task, TaskDigest, User
from app.schemas.common import ok
from app.schemas.task_ledger import DigestGenerate, DigestSave, TaskReleaseCreate

router = APIRouter(prefix="/api/task-ledger", tags=["task-ledger"])
READ_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)
WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)
STATUS = {"pending": "待跟进", "testing": "测试中", "ready_online": "测试完成",
          "unassigned": "待分配", "waiting_fix": "待开发修复", "regressing": "回归中", "at_risk": "有风险", "paused": "暂停",
          "blocked": "阻塞", "online": "已上线", "closed": "已关闭"}


def selected_tasks(db, project_id, task_ids, lock=False):
    ids = list(dict.fromkeys(task_ids))
    q = db.query(Task).filter(Task.project_id == project_id, Task.id.in_(ids)).order_by(Task.id)
    if lock:
        q = q.with_for_update()
    rows = q.all()
    if len(rows) != len(ids):
        raise HTTPException(400, detail="所选任务已删除或不属于当前项目，请刷新后重试")
    by_id = {t.id: t for t in rows}
    return [by_id[tid] for tid in ids]


def source_hash(row):
    value = json.dumps([row.title, row.content, str(row.report_date)], ensure_ascii=False)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def digest_out(row, db=None):
    publication = (db.query(TaskDigestPublication).filter_by(digest_id=row.id, source_sha256=source_hash(row))
                   .order_by(TaskDigestPublication.id.desc()).first()) if db else None
    deployment = db.get(TaskDigestDeployment, publication.id) if db and publication else None
    return {"id": row.id, "project_id": row.project_id, "report_date": str(row.report_date),
            "title": row.title, "content": row.content, "task_ids": json.loads(row.task_ids),
            "filter_summary": row.filter_summary, "updated_at": row.updated_at,
            "share_path": deployment.url if deployment else f"/d/{publication.code}" if publication else None,
            "share_hosting": deployment.hosting if deployment else "platform"}


@router.post("/digests/generate")
def generate_digest(body: DigestGenerate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, WRITE_ROLES)
    rows = selected_tasks(db, body.project_id, body.task_ids)
    from app.services import ai_jobs, generators
    # Product, version and status are local layout inputs; AI receives titles only.
    payload = {'product_name': db.get(Project, body.project_id).name, 'tasks': [dict(id=task.id, title=task.title,
                             target_version=task.target_version, status=task.status.value) for task in rows]}
    provider = generators.DEFAULT_PROVIDER
    if not generators.get_provider(provider).is_available():
        raise HTTPException(503, detail='平台 AI 服务不可用，暂时无法总结日报')
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded.encode('utf-8')) > 2_000_000:
        raise HTTPException(422, detail='所选任务内容过多，请缩小筛选范围后生成日报')
    generation = TaskDigestGeneration(project_id=body.project_id, author_id=user.id, input=encoded)
    db.add(generation); db.flush()
    job = ai_jobs.enqueue(db, 'task_digest', provider=provider, project_id=body.project_id,
                         user_id=user.id, input={'generation_id': generation.id},
                         ref_kind='task_digest', ref_id=generation.id)
    return ok({'job_id': job.id, 'generation_id': generation.id})


@router.get('/digests/generations/{generation_id}')
def generated_digest(generation_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(TaskDigestGeneration, generation_id)
    if row is None or row.author_id != user.id:
        raise HTTPException(404, detail='日报生成记录不存在')
    assert_project_role(db, user, row.project_id, WRITE_ROLES)
    if not row.result:
        raise HTTPException(409, detail='日报仍在总结中')
    return ok(json.loads(row.result))


@router.get("/digests")
def list_digests(project_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, WRITE_ROLES)
    rows = db.query(TaskDigest).filter_by(project_id=project_id, author_id=user.id).order_by(TaskDigest.id.desc()).all()
    return ok([digest_out(r, db) for r in rows])


def _save_digest(body, db, user):
    assert_project_role(db, user, body.project_id, WRITE_ROLES)
    row = db.query(TaskDigest).filter_by(id=body.id).with_for_update().first() if body.id else None
    if body.id and (not row or row.project_id != body.project_id or row.author_id != user.id):
        raise HTTPException(404, detail="日报不存在或无编辑权限")
    if row is None:
        selected_tasks(db, body.project_id, body.task_ids)
        row = TaskDigest(project_id=body.project_id, author_id=user.id,
                         task_ids=json.dumps(list(dict.fromkeys(body.task_ids))), filter_summary=body.filter_summary)
        db.add(row)
    # 保存已有快照不重新读取任务，任务后续删除不影响历史日报。
    row.report_date, row.title, row.content = body.report_date, body.title, body.content
    db.flush()
    return row


@router.post("/digests")
def save_digest(body: DigestSave, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = _save_digest(body, db, user)
    db.commit()
    db.refresh(row)
    return ok(digest_out(row, db))


@router.post("/digests/publish")
def publish_digest(body: DigestSave, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.services.task_digest_skill import render_task_digest_html
    row = _save_digest(body, db, user)
    html = render_task_digest_html({'title': row.title, 'content': row.content, 'report_date': str(row.report_date)})
    html_hash = hashlib.sha256(html.encode('utf-8')).hexdigest()
    publication = db.query(TaskDigestPublication).filter_by(
        digest_id=row.id, source_sha256=source_hash(row), html_sha256=html_hash).first()
    if publication is None:
        publication = TaskDigestPublication(digest_id=row.id, code=secrets.token_urlsafe(12),
            source_sha256=source_hash(row), html_sha256=html_hash, html=html)
        db.add(publication)
    db.flush()
    publication_id, digest_id, fallback = publication.id, row.id, f'/d/{publication.code}'
    deployed = db.get(TaskDigestDeployment, publication_id)
    has_cloud_url = deployed is not None and deployed.hosting == 'nami'
    db.commit()  # Do not hold a DB connection during the shared service's upload.
    if not has_cloud_url:
        from app.services.report_publishing import publish_html
        published = publish_html(html, fallback, prefer_shortlink=True)
        deployed = db.get(TaskDigestDeployment, publication_id)
        if deployed is None:
            deployed = TaskDigestDeployment(publication_id=publication_id, **published)
            db.add(deployed)
        else:
            deployed.url, deployed.hosting = published['url'], published['hosting']
        db.commit()
    return ok(digest_out(db.get(TaskDigest, digest_id), db))


@router.get("/digests/shared/{code}")
def shared_digest(code: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16}', code):
        raise HTTPException(404, detail="日报链接不存在")
    publication = db.query(TaskDigestPublication).filter_by(code=code).first()
    if publication is None:
        raise HTTPException(404, detail="日报链接不存在")
    row = db.get(TaskDigest, publication.digest_id)
    assert_project_role(db, user, row.project_id, READ_ROLES)
    return ok({'html': publication.html})


@router.post("/releases")
def fill_release(body: TaskReleaseCreate, db: Session = Depends(get_db), user: User = Depends(require_platform_admin)):
    from app.api.release import _norm_channel, _norm_sub_product, _to_out
    project = db.get(Project, body.project_id)
    if not project:
        raise HTTPException(404, detail="项目不存在")
    # 同项目归档串行；同时锁任务和目标记录，重复点击不重复累加。
    db.query(Project).filter_by(id=body.project_id).with_for_update().one()
    tasks = selected_tasks(db, body.project_id, body.task_ids, lock=True)
    version = body.version.strip()
    if not version or any(t.status != TaskStatus.online or (t.target_version or '').strip() != version for t in tasks):
        raise HTTPException(400, detail="仅可填写已上线且归属版本一致、非空的任务")
    if not body.content.strip():
        raise HTTPException(400, detail="上线内容不能为空")
    rel = None
    if body.release_id:
        rel = db.query(ReleaseRecord).filter_by(id=body.release_id).with_for_update().first()
        if not rel or rel.project_id != body.project_id or rel.version != version:
            raise HTTPException(400, detail="发版记录必须属于同一项目和归属版本")
    linked_ids = {t.release_record_id for t in tasks if t.release_record_id}
    if linked_ids:
        if len(linked_ids) == 1 and all(t.release_record_id for t in tasks) and (not rel or rel.id in linked_ids):
            existing = db.get(ReleaseRecord, next(iter(linked_ids)))
            if existing:
                return ok({"release": _to_out(db, existing), "added": 0})
        # 不允许混入已归档任务，否则手工编写的内容可能重复。
        raise HTTPException(409, detail="所选任务含已填写发版记录的内容，请刷新后仅选择未填写任务")
    if not rel:
        sub = _norm_sub_product(body.sub_product, project.platform_type, db=db, project_id=project.id)
        duplicate = db.query(ReleaseRecord).filter_by(project_id=body.project_id, version=version,
            sub_product=sub, release_date=body.release_date).first()
        if duplicate:
            raise HTTPException(409, detail="该版本和日期已有发版记录，请选择追加到已有记录")
        rel = ReleaseRecord(project_id=body.project_id, version=version, release_date=body.release_date,
            sub_product=sub, channel=_norm_channel(body.channel, project.platform_type),
            req_count=0, created_by=user.id, memo=body.memo)
        db.add(rel)
        db.flush()
    content = "\n\n".join(filter(None, [rel.content, body.content.strip()]))
    if len(content.encode('utf-8')) > 65000:
        raise HTTPException(400, detail="追加后上线内容超出容量，请精简本次内容后重试")
    rel.content = content
    rel.req_count = (rel.req_count or 0) + len(tasks)
    for task in tasks:
        task.release_record_id = rel.id
    db.commit()
    return ok({"release": _to_out(db, rel), "added": len(tasks)})
