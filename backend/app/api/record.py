"""录制会话路由:建/runner拉取/增量append/查询/停止/保存为用例/删除。

录制是有状态的交互会话(开始→操作→停止),故 events 由 runner 增量 append。
沿用全项目约定:{code,msg,data} 信封、手写 _to_out、体外 assert_project_role;runner 侧 require_runner_ctx + 归属校验。
"""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import RecordSession, User, Task, TestCase, RunnerDevice
from app.schemas.common import ok
from app.schemas.record import RecordStartIn, RecordEventsIn, RecordSaveIn
from app.services.recording import save_recording_as_case
from app.services.selector_device import owned_device, lock_device, assert_idle, assert_assigned, active_recording
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
        "runner": r.runner, "runner_device_id": r.runner_device_id, "status": r.status, "events": _events(r),
        "saved_case_id": r.saved_case_id,
        "error": r.error, "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ---- ① 网页发起录制 ----
@router.post("")
def start_record(body: RecordStartIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """建一个录制会话。项目 member/admin 可操作。"""
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    from app.api.selectors import _valid_sub
    device = owned_device(db, user, body.runner, body.runner_device_id)
    assert_idle(db, device.id)
    r = RecordSession(project_id=body.project_id, sub_product=_valid_sub(body.sub_product),
                      runner=device.runner_id, runner_device_id=device.id, status="pending", events="[]", created_by=user.id)
    db.add(r); db.commit(); db.refresh(r)
    return ok({"id": r.id})


# ---- ② runner 拉本机待录/录制中会话(pending→recording 认领)----
# /pending 必须在 /{sid} 之前声明,否则被动态路径吞掉。
@router.get("/pending")
def list_pending(runner: str = Query("mac-01"), consumer_id: str = Query(..., min_length=1, max_length=64),
                 db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is None:
        return ok([])  # 共享 token 可继续旧执行队列，但不能领取设备专属录制。
    device = lock_device(db, ctx)
    device.last_seen_at = datetime.utcnow()
    r = active_recording(db, device.id)
    if r is None:
        db.commit()
        return ok([])
    if r.consumer_id and r.consumer_id != consumer_id:
        if r.updated_at and r.updated_at > datetime.utcnow() - timedelta(seconds=30):
            raise HTTPException(409, detail="该设备的录制已被另一 Runner 进程领取")
        r.consumer_id = consumer_id  # 旧进程失联后恢复，同 event_id 仍由服务端去重。
    if r.status == "pending":
        assert_idle(db, device.id, allow_record=True)
        r.status = "recording"
        r.consumer_id = consumer_id
    r.updated_at = datetime.utcnow()
    db.commit()
    return ok([_to_out(r)])


# ---- ③ runner 增量上报捕获事件 ----
@router.post("/{sid}/events")
def append_events(sid: int, body: RecordEventsIn, runner: str = Query("mac-01"),
                  db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    device = lock_device(db, ctx)
    r = db.query(RecordSession).filter(RecordSession.id == sid).populate_existing().with_for_update().first()
    if not r:
        raise HTTPException(404, detail="录制会话不存在")
    assert_assigned(r, device)
    if r.consumer_id != body.consumer_id:
        raise HTTPException(409, detail="录制领取凭据不匹配")
    cur = _events(r)
    by_id = {e.get("event_id"): e for e in cur if e.get("event_id")}
    added = []
    for event in body.events:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or len(event_id) > 200:
            raise HTTPException(422, detail="录制事件必须带唯一 event_id")
        if event_id in by_id:
            if by_id[event_id] != event:
                raise HTTPException(409, detail="同一事件编号内容不一致")
            continue
        if r.status not in ("recording", "stopping"):
            raise HTTPException(409, detail="会话已结束，不能追加新事件")
        by_id[event_id] = event
        added.append(event)
    if r.status not in ("recording", "stopping", "stopped", "done"):
        raise HTTPException(409, detail="录制会话不可上报")
    if body.final and r.status == "recording":
        raise HTTPException(409, detail="请先请求停止录制")
    cur.extend(added)
    cur.sort(key=lambda e: e.get("ts", 0))
    r.events = json.dumps(cur, ensure_ascii=False)
    if body.final and r.status == "stopping":
        r.status = "stopped"
    r.updated_at = datetime.utcnow()
    db.commit()
    return ok({"appended": len(added), "total": len(cur), "acked": [e["event_id"] for e in body.events], "status": r.status})


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
    # 与领取/上报保持同一加锁顺序，锁后重读，避免 pending 已被领取却直接置 stopped。
    if r.runner_device_id:
        db.query(RunnerDevice).filter(RunnerDevice.id == r.runner_device_id).with_for_update().one()
    r = db.query(RecordSession).filter(RecordSession.id == sid).populate_existing().with_for_update().one()
    if r.status in ("pending", "recording"):
        r.status = "stopped" if r.status == "pending" else "stopping"
        db.commit()
    return ok(_to_out(r))


# ---- ⑥ 保存为用例:组装 script + 回填选择器 + 建 e2e 用例 ----
@router.post("/{sid}/save-as-case")
def save_as_case(sid: int, body: RecordSaveIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.query(RecordSession).filter(RecordSession.id == sid).populate_existing().with_for_update().first()
    if not r:
        raise HTTPException(404, detail="录制会话不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    if r.saved_case_id:
        tc = db.get(TestCase, r.saved_case_id)
        if not tc:
            raise HTTPException(409, detail="该录制已保存，原用例已被删除")
        return ok(_to_case_out(tc))
    if r.status != "stopped":
        raise HTTPException(409, detail="请等待执行机确认停止并上传所有步骤后再保存")
    events = body.events if body.events is not None else _events(r)
    if not events:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="没有可保存的录制步骤")
    t = db.get(Task, body.task_id)
    if not t or t.project_id != r.project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="关联任务不存在或不属于本项目")
    try:
        tc = save_recording_as_case(db, r.project_id, r.sub_product, events, title=body.title,
                                    task_id=body.task_id, created_by=user.id, precondition=body.precondition)
        r.status = "done"
        r.saved_case_id = tc.id
        db.commit()
        db.refresh(tc)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise

    return ok(_to_case_out(tc))


# ---- ⑦ 丢弃会话 ----
@router.delete("/{sid}")
def delete_record(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(RecordSession, sid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="录制会话不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    if r.status in ("recording", "stopping"):
        raise HTTPException(409, detail="请先停止录制并等待上传完成")
    db.delete(r); db.commit()
    return ok({"deleted": sid})
