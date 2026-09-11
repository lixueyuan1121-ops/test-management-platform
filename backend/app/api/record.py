"""录制会话路由:建/runner拉取/增量append/查询/停止/保存为用例/删除。

录制是有状态的交互会话(开始→操作→停止),故 events 由 runner 增量 append。
沿用全项目约定:{code,msg,data} 信封、手写 _to_out、体外 assert_project_role;runner 侧 require_runner_ctx + 归属校验。
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import RecordSession, User, Task
from app.schemas.common import ok
from app.schemas.record import RecordStartIn, RecordEventsIn, RecordSaveIn
from app.services.recording import save_recording_as_case
from app.api.ai import _to_case_out  # 用例出参序列化(复用 ai.py)

router = APIRouter(prefix="/api/record", tags=["record"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)
_ALL_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)


def _events(r: RecordSession) -> list:
    try:
        v = json.loads(r.events or "[]")
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _to_out(r: RecordSession) -> dict:
    return {
        "id": r.id, "project_id": r.project_id, "sub_product": r.sub_product,
        "runner": r.runner, "status": r.status, "events": _events(r),
        "error": r.error, "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ---- ① 网页发起录制 ----
@router.post("")
def start_record(body: RecordStartIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """建一个录制会话。项目 member/admin 可操作。"""
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    r = RecordSession(project_id=body.project_id, sub_product=body.sub_product,
                      runner=body.runner, status="pending", events="[]", created_by=user.id)
    db.add(r); db.commit(); db.refresh(r)
    return ok({"id": r.id})


# ---- ② runner 拉本机待录/录制中会话(pending→recording 认领)----
# /pending 必须在 /{sid} 之前声明,否则被动态路径吞掉。
@router.get("/pending")
def list_pending(runner: str = Query("mac-01"), db: Session = Depends(get_db),
                 ctx: RunnerCtx = Depends(require_runner_ctx)):
    """runner 拉该 runner 的 pending/recording 会话。pending 首次拉取即认领置 recording。"""
    if ctx.device is not None:
        runner = ctx.device.runner_id
        ctx.device.last_seen_at = datetime.utcnow()
    rows = (db.query(RecordSession)
            .filter(RecordSession.runner == runner, RecordSession.status.in_(["pending", "recording"]))
            .order_by(RecordSession.id).all())
    for r in rows:
        if r.status == "pending":
            r.status = "recording"
    db.commit()
    return ok([_to_out(r) for r in rows])


# ---- ③ runner 增量上报捕获事件 ----
@router.post("/{sid}/events")
def append_events(sid: int, body: RecordEventsIn, runner: str = Query("mac-01"),
                  db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(RecordSession, sid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="录制会话不存在")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该录制会话未派给此执行机")
    if body.events:
        cur = _events(r)
        cur.extend(body.events)
        r.events = json.dumps(cur, ensure_ascii=False)
        r.updated_at = datetime.utcnow()
        db.commit()
    return ok({"appended": len(body.events or []), "total": len(_events(r))})


# ---- ④ 用户轮询会话(实时步骤)----
@router.get("/{sid}")
def get_record(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(RecordSession, sid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="录制会话不存在")
    assert_project_role(db, user, r.project_id, _ALL_ROLES)
    return ok(_to_out(r))


# ---- ⑤ 用户停止录制 ----
@router.post("/{sid}/stop")
def stop_record(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(RecordSession, sid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="录制会话不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    if r.status in ("pending", "recording"):
        r.status = "stopped"
        db.commit()
    return ok(_to_out(r))


# ---- ⑥ 保存为用例:组装 script + 回填选择器 + 建 e2e 用例 ----
@router.post("/{sid}/save-as-case")
def save_as_case(sid: int, body: RecordSaveIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(RecordSession, sid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="录制会话不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    events = body.events if body.events is not None else _events(r)
    if not events:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="没有可保存的录制步骤")
    t = db.get(Task, body.task_id)
    if not t or t.project_id != r.project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="关联任务不存在或不属于本项目")
    tc = save_recording_as_case(db, r.project_id, r.sub_product, events, title=body.title,
                                task_id=body.task_id, created_by=user.id, precondition=body.precondition)
    r.status = "done"
    db.commit(); db.refresh(tc)
    return ok(_to_case_out(tc))


# ---- ⑦ 丢弃会话 ----
@router.delete("/{sid}")
def delete_record(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(RecordSession, sid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="录制会话不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    db.delete(r); db.commit()
    return ok({"deleted": sid})
