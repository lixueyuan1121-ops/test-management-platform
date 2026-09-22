"""Fast durable acceptance. Matching happens exclusively in the background worker."""
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import Project, User, VerifiedImportJob, VerifiedImportItem
from app.schemas.common import ok
from app.schemas.verified_import import DEFAULT_IMPORT_TASK, VerifiedImport, ImportResolution
from app.services.selector_device import owned_device
from app.services.verified_dedup import digest

router = APIRouter(prefix="/api/verified-imports/jobs", tags=["verified-imports"])


def authorize(db, user, project_id, admin=False):
    return assert_project_role(db, user, project_id, (ProjectRole.admin,) if admin else (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))


def get_job(db, user, job_id, admin=False):
    job = db.get(VerifiedImportJob, job_id)
    if not job:
        raise HTTPException(404, "导入任务不存在")
    authorize(db, user, job.project_id, admin)
    return job


def item_summary(item):
    return {"id": item.id, "position": item.position, "title": item.title, "status": item.status,
            "attempts": item.attempts, "error": item.error, "next_attempt_at": item.next_attempt_at,
            "receipt": json.loads(item.receipt) if item.receipt else None}


def summary(job, items):
    counts = {key: sum(i.status == key for i in items) for key in ("pending", "needs_confirmation", "done", "failed")}
    state = next((key for key in ("pending", "needs_confirmation", "failed") if counts[key]), "completed")
    receipts = [json.loads(i.receipt) for i in items if i.receipt]
    return {"job_id": job.id, "project_id": job.project_id, "external_id": job.external_id,
            "requirement": job.requirement, "user_id": job.user_id, "created_at": job.created_at,
            "status": state, "counts": counts, "case_count": len(items),
            "created_cases": sum(r["disposition"] == "created" for r in receipts),
            "reused_cases": sum(r["disposition"] == "reused" for r in receipts),
            "status_url": f"/verified-imports?project_id={job.project_id}&job_id={job.id}"}


@router.post("", status_code=202)
def submit(body: VerifiedImport, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, (ProjectRole.admin, ProjectRole.member))
    if not db.get(Project, body.project_id):
        raise HTTPException(404, "项目不存在")
    device = owned_device(db, user, "", body.runner_device_id)
    if device.platform != "web":
        raise HTTPException(422, "当前导入契约仅支持 web/PC 执行设备")
    if any(c.resolution for c in body.cases):
        raise HTTPException(422, "异步提交无需查重确认；疑似重复由平台导入任务页处理")
    payload = body.model_dump(mode="json")
    # Keep pre-task-name receipts retryable after upgrading.
    hash_payload = dict(payload)
    if hash_payload["task_name"] == DEFAULT_IMPORT_TASK:
        del hash_payload["task_name"]
    sha = digest(hash_payload)
    def existing():
        return db.query(VerifiedImportJob).filter_by(project_id=body.project_id, user_id=user.id, external_id=body.external_id).first()
    job = existing()
    reused = job is not None
    if job is None:
        job = VerifiedImportJob(project_id=body.project_id, user_id=user.id, external_id=body.external_id,
                                digest=sha, requirement=body.requirement, payload=json.dumps(payload, ensure_ascii=False))
        db.add(job)
        try:
            db.flush()
            db.add_all([VerifiedImportItem(job_id=job.id, position=i, title=c.title) for i,c in enumerate(body.cases)])
            db.commit()
        except IntegrityError:
            db.rollback()
            job = existing()
            if job is None:
                raise
            reused = True
    if job.digest != sha:
        raise HTTPException(409, "相同 external_id 已提交不同内容，请保留原始包核对")
    items = db.query(VerifiedImportItem).filter_by(job_id=job.id).all()
    return ok({**summary(job, items), "accepted": True, "reused": reused,
               "message": "已接收，平台后台整理；无需等待，最终结果请在导入任务查看"})


@router.get("")
def listing(project_id: int, page: int = Query(1, ge=1), status: str = "",
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    authorize(db, user, project_id)
    if status and status not in ("pending", "needs_confirmation", "done", "failed"):
        raise HTTPException(422, "未知导入状态")
    query = db.query(VerifiedImportJob).filter_by(project_id=project_id)
    if status:
        query = query.filter(VerifiedImportJob.id.in_(db.query(VerifiedImportItem.job_id).filter_by(status=status)))
    total = query.count()
    jobs = query.order_by(VerifiedImportJob.id.desc()).offset((page-1)*20).limit(20).all()
    items = db.query(VerifiedImportItem).filter(VerifiedImportItem.job_id.in_([j.id for j in jobs])).all() if jobs else []
    return ok({"items": [summary(j, [i for i in items if i.job_id == j.id]) for j in jobs], "total": total, "page": page, "page_size": 20})


@router.get("/{job_id}")
def detail(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = get_job(db, user, job_id)
    role = authorize(db, user, job.project_id)
    items = db.query(VerifiedImportItem).filter_by(job_id=job.id).order_by(VerifiedImportItem.position).all()
    return ok({**summary(job, items), "can_manage": role.role == ProjectRole.admin,
               "items": [item_summary(i) for i in items]})


@router.get("/{job_id}/items/{item_id}")
def item_detail(job_id: int, item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = get_job(db, user, job_id)
    item = db.query(VerifiedImportItem).filter_by(id=item_id, job_id=job.id).first()
    if not item:
        raise HTTPException(404, "导入条目不存在")
    return ok({**item_summary(item), "source": json.loads(job.payload)["cases"][item.position],
               "plan": json.loads(item.plan) if item.plan else None,
               "resolution": json.loads(item.resolution) if item.resolution else None,
               "resolved_by": item.resolved_by, "resolved_at": item.resolved_at})


@router.post("/{job_id}/items/{item_id}/resolve")
def resolve(job_id: int, item_id: int, choice: ImportResolution, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_job(db, user, job_id, admin=True)
    item = db.query(VerifiedImportItem).filter_by(id=item_id, job_id=job_id).first()
    if not item or item.status != "needs_confirmation":
        raise HTTPException(409, "条目已变化，请刷新后再处理")
    plan = json.loads(item.plan)
    if choice.token != plan["confirmation_token"]:
        raise HTTPException(409, "候选已更新，请刷新后确认")
    if choice.action == "reuse" and choice.case_id not in {c["case_id"] for c in plan["candidates"]}:
        raise HTTPException(422, "请选择候选中的用例")
    if choice.action == "create" and any(c["reason"] == "脚本与验收条件一致" for c in plan["candidates"]):
        raise HTTPException(409, "相同场景不能强制新建")
    count = db.query(VerifiedImportItem).filter_by(id=item_id, status="needs_confirmation", plan=item.plan).update(
        {"status": "pending", "resolution": choice.model_dump_json(), "resolved_by": user.id,
         "resolved_at": datetime.utcnow(), "attempts": 0, "next_attempt_at": datetime.utcnow(), "error": None}, synchronize_session=False)
    if not count:
        raise HTTPException(409, "条目已被其他人处理，请刷新")
    db.commit()
    return ok({"accepted": True, "message": "已确认，后台将复核并继续处理"})


@router.post("/{job_id}/items/{item_id}/retry")
def retry(job_id: int, item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_job(db, user, job_id, admin=True)
    count = db.query(VerifiedImportItem).filter_by(id=item_id, job_id=job_id, status="failed").update(
        {"status": "pending", "attempts": 0, "next_attempt_at": datetime.utcnow(), "error": None}, synchronize_session=False)
    if not count:
        raise HTTPException(409, "仅处理失败的条目可以重试")
    db.commit()
    return ok({"accepted": True})
