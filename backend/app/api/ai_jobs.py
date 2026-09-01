"""AI 任务队列统一轮询端点(方案2):前端 POST 特性端点拿 job_id 后,轮询这里取状态/结果。

- GET /api/ai-jobs/{id}     状态 + 排队位次 + 结果(done)/错误(failed)
- POST /api/ai-jobs/{id}/cancel  仅 pending 可取消(running 无法远程中断 → 409)

鉴权:job 所有者(user_id)或该 project 成员(admin/member/guest)可读/取消;越权 403。
"""
import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import AiJob, User
from app.schemas.common import ok
from app.services import ai_jobs

router = APIRouter(prefix="/api/ai-jobs", tags=["ai-jobs"])

_READ_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)


def _loads(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _authorize(db: Session, user: User, job: AiJob) -> None:
    """owner 直接放行;否则要求是 job.project_id 的成员(平台管理员在 assert 内放行)。"""
    if job.user_id is not None and job.user_id == user.id:
        return
    if job.project_id is not None:
        assert_project_role(db, user, job.project_id, _READ_ROLES)  # 非成员 → 403
        return
    # 无 project 归属且非 owner:仅平台管理员可看
    if not user.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="无权查看该任务")


def _to_out(db: Session, job: AiJob) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "provider": job.provider,
        "status": job.status,
        "queue_position": ai_jobs.queue_position(db, job),
        "result": _loads(job.result),
        "output_raw": job.output_raw,
        "error": job.error,
        "ref_kind": job.ref_kind,
        "ref_id": job.ref_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.get("/{job_id}")
def get_ai_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = db.get(AiJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="AI 任务不存在")
    _authorize(db, user, job)
    return ok(_to_out(db, job))


@router.post("/{job_id}/cancel")
def cancel_ai_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """仅 pending 可取消(条件 UPDATE 防与 worker 抢占竞态);running/已终态 → 409。"""
    job = db.get(AiJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="AI 任务不存在")
    _authorize(db, user, job)
    res = db.execute(
        update(AiJob).where(AiJob.id == job_id, AiJob.status == "pending")
        .values(status="cancelled")
    )
    db.commit()
    if res.rowcount != 1:
        db.refresh(job)
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"任务当前状态「{job.status}」不可取消(仅排队中可取消)")
    db.refresh(job)
    return ok(_to_out(db, job))
