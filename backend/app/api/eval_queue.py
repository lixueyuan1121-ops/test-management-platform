"""对话测评执行队列:下发 eval_query → 执行器拉取/认领/回写 eval_run → trace 上传。

独立于 exec_queue(功能测试点执行)。沿用 {code,msg,data} 信封、手写 _to_out、
require_runner_ctx 双通道鉴权(设备 token 锁 runner_id / 共享 token 兜底)。
trace(会话轨迹)大、走独立 multipart 端点存磁盘,eval_run.trace 存 URL(避 MySQL5.6 TEXT 截断)。
"""
import json
import os
import secrets
import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import EvalDeviceKind, EvalRunStatus, ProjectRole
from app.db.session import get_db
from app.models import EvalQuery, EvalRun, User
from app.schemas.common import ok
from app.schemas.eval_queue import EvalEnqueueIn, EvalReportIn

router = APIRouter(prefix="/api/eval-queue", tags=["eval-queue"])
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def _new_batch_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _payload_of(q: EvalQuery) -> dict:
    """下发 payload 快照:执行器驱动对话要用的字段(避免执行时配置漂移)。"""
    return {
        "eval_query_id": q.id,
        "title": q.title,
        "prompt": q.prompt,
        "attachments": json.loads(q.attachments) if q.attachments else [],
        "dialog_options": json.loads(q.dialog_options) if q.dialog_options else {},
        "conversation_group": q.conversation_group,
        "turn_index": q.turn_index,
    }


def _to_out(r: EvalRun) -> dict:
    return {
        "run_id": r.id,
        "eval_query_id": r.eval_query_id,
        "project_id": r.project_id,
        "batch_id": r.batch_id,
        "runner": r.runner,
        "target_engine": r.target_engine,
        "device_kind": getattr(r.device_kind, "value", r.device_kind),
        "status": getattr(r.status, "value", r.status),
        "payload": json.loads(r.payload) if r.payload else {},
        "session_id": r.session_id,
        "share_link": r.share_link,
        "artifact_share_link": r.artifact_share_link,
        "answer": r.answer,
        "trace": r.trace,
        "reported_duration": r.reported_duration,
        "bean_cost": r.bean_cost,
        "tokens": r.tokens,
        "reason": r.reason,
        "duration_ms": r.duration_ms,
        # 判定三维/总判定（历史页需在加载时即展示已判过的结果，故一并序列化；判定由 eval_judge 落库）
        "verdict": r.verdict,
        "verdict_dims": json.loads(r.verdict_dims) if r.verdict_dims else None,
        "verdict_reason": r.verdict_reason,
        "judged_by": r.judged_by,
        "is_abnormal": bool(r.is_abnormal),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.post("/enqueue")
def enqueue(body: EvalEnqueueIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    ids = list(dict.fromkeys(body.eval_query_ids))
    qs = db.query(EvalQuery).filter(EvalQuery.id.in_(ids)).all()
    found = {q.id: q for q in qs}
    for qid in ids:
        q = found.get(qid)
        if q is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测评题 {qid} 不存在")
        if q.project_id != body.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测评题 {qid} 不属于该项目")
    created = []
    batch_id = _new_batch_id()
    for qid in ids:
        q = found[qid]
        row = EvalRun(
            eval_query_id=q.id, project_id=q.project_id, batch_id=batch_id,
            runner=body.runner, target_engine=body.target_engine,
            device_kind=EvalDeviceKind.desktop,
            status=EvalRunStatus.pending, payload=json.dumps(_payload_of(q), ensure_ascii=False),
            enqueued_by=user.id,
        )
        db.add(row); db.flush(); created.append(row.id)
    db.commit()
    return ok({"run_ids": created, "batch_id": batch_id})


@router.get("")
def list_pending(runner: str = Query("mac-01"), limit: int = Query(5, le=20),
                 db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
        ctx.device.last_seen_at = datetime.utcnow(); db.commit()
    rows = (db.query(EvalRun)
            .filter(EvalRun.status == EvalRunStatus.pending, EvalRun.runner == runner)
            .order_by(EvalRun.id).limit(limit).all())
    return ok([_to_out(r) for r in rows])


@router.post("/{run_id}/claim")
def claim(run_id: int, runner: str = Query(...), db: Session = Depends(get_db),
          ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(EvalRun, run_id)
    if not r or r.status != EvalRunStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该执行项不可认领")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    r.status = EvalRunStatus.running; db.commit(); db.refresh(r)
    return ok(_to_out(r))


@router.patch("/{run_id}")
def report(run_id: int, body: EvalReportIn, runner: str = Query(...),
           db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    r.status = EvalRunStatus.done if body.status == "done" else EvalRunStatus.failed
    r.share_link = body.share_link
    r.artifact_share_link = body.artifact_share_link
    r.answer = body.answer
    r.reported_duration = body.reported_duration
    r.bean_cost = body.bean_cost
    r.tokens = body.tokens
    r.session_id = body.session_id
    r.reason = body.reason
    r.duration_ms = body.duration_ms
    db.commit(); db.refresh(r)
    return ok(_to_out(r))


_UPLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads")
_TRACE_ROOT = os.path.join(_UPLOADS_DIR, "eval_traces")
_MAX_TRACE_BYTES = 20 * 1024 * 1024


@router.post("/{run_id}/trace")
async def upload_trace(run_id: int, file: UploadFile = File(...), runner: str = Query("mac-01"),
                       db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    """执行器上传会话轨迹 JSON。存 uploads/eval_traces/{run_id}-<hex>.json,回写 eval_run.trace=URL。

    文件名加随机后缀防枚举(/uploads 无鉴权静态挂载)。同 run 重传(重跑)前先删旧 trace 避堆积。
    """
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    data = await file.read()
    if len(data) > _MAX_TRACE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"轨迹过大(>{_MAX_TRACE_BYTES//1024//1024}MB)")
    try:
        json.loads(data)  # 校验是合法 JSON
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="轨迹须为合法 JSON")
    os.makedirs(_TRACE_ROOT, exist_ok=True)
    # 重传前删该 run 的旧 trace 文件(随机名会产生多份,吞异常)
    try:
        for name in os.listdir(_TRACE_ROOT):
            if name.startswith(f"{run_id}-"):
                try:
                    os.remove(os.path.join(_TRACE_ROOT, name))
                except OSError:
                    pass
    except OSError:
        pass
    rel = f"eval_traces/{run_id}-{secrets.token_hex(8)}.json"
    with open(os.path.join(_UPLOADS_DIR, rel), "wb") as f:
        f.write(data)
    r.trace = f"/uploads/{rel}"; db.commit()
    return ok({"trace_url": f"/uploads/{rel}"})


@router.get("/history")
def list_history(project_id: int = Query(...), limit: int = Query(100, le=500),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (db.query(EvalRun).filter(EvalRun.project_id == project_id)
            .order_by(EvalRun.id.desc()).limit(limit).all())
    return ok([_to_out(r) for r in rows])
