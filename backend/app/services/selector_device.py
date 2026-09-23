"""选择器交互任务的设备身份与占用。所有领取路径锁同一设备行。"""
from datetime import datetime, timedelta

from fastapi import HTTPException
from app.models import RunnerDevice, RecordSession, ProbeRequest, ExecRun

RECORD_LIVE = ("pending", "recording", "stopping")



def owned_device(db, user, runner, device_id=None):
    query = db.query(RunnerDevice).filter(RunnerDevice.owner_id == user.id)
    query = query.filter(RunnerDevice.id == device_id) if device_id is not None else query.filter(RunnerDevice.runner_id == runner)
    device = query.with_for_update().first()
    if not device:
        raise HTTPException(400, detail="请选择已登记且属于你的执行设备")
    return device


def lock_device(db, ctx):
    if ctx.device is None:
        raise HTTPException(403, detail="选择器探测和录制需要使用设备专属 token")
    return db.query(RunnerDevice).filter(RunnerDevice.id == ctx.device.id).with_for_update().one()


def expire_recordings(db, device_id):
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    db.query(RecordSession).filter(RecordSession.runner_device_id == device_id,
        RecordSession.status.in_(RECORD_LIVE), RecordSession.updated_at < cutoff).update(
            {RecordSession.status: "failed", RecordSession.error: "执行机超过 5 分钟未确认录制；已保留收到的步骤，请重新录制"}, synchronize_session="fetch")
    db.query(ProbeRequest).filter(ProbeRequest.runner_device_id == device_id,
        ProbeRequest.status == "running", ProbeRequest.updated_at < cutoff).update(
            {ProbeRequest.status: "failed", ProbeRequest.error: "探测执行机失联，请重新发起探测"}, synchronize_session="fetch")


def active_recording(db, device_id):
    expire_recordings(db, device_id)
    return db.query(RecordSession).filter(RecordSession.runner_device_id == device_id,
        RecordSession.status.in_(RECORD_LIVE)).with_for_update().first()


def device_runs(db, model, device):
    from sqlalchemy import and_, or_
    # A legacy unbound name is safe only when it identifies exactly one device.
    unique = db.query(RunnerDevice).filter(RunnerDevice.runner_id == device.runner_id).count() == 1
    return or_(model.runner_device_id == device.id,
               and_(model.runner_device_id.is_(None), model.runner == device.runner_id, unique))


def reap_stale_exec_locks(db, device_id):
    """Recover interrupted functional AND evaluation work without releasing the row lock."""
    from sqlalchemy import func
    from app.models import EvalRun
    from app.db.clock import db_now
    from app.services.run_activity import expired_run_filter
    device = db.get(RunnerDevice, device_id)
    if device is None:
        return 0
    changed = 0
    for model in (ExecRun, EvalRun):
        values = {model.status: "failed", model.finished_at: func.now(),
                  model.reason: "自动收口:执行机长时间无活动，执行已中断，请重试"}
        stale = db.query(model).filter(device_runs(db, model, device),
            expired_run_filter(model, db_now(db)))
        if model is ExecRun:
            changed += stale.filter(ExecRun.fail_kind == "cancel_requested").update({
                ExecRun.status: "blocked", ExecRun.verdict: "blocked", ExecRun.fail_kind: "cancelled",
                ExecRun.finished_at: func.now(),
                ExecRun.reason: "手动终止：执行机心跳已超时，已释放占用；旧执行机恢复后必须停止原任务",
            }, synchronize_session="fetch")
            values[model.fail_kind] = "timeout"
            values[model.verdict] = "fail"
        changed += stale.update(values, synchronize_session="fetch")
    return changed


def assert_idle(db, device_id, *, allow_record=False):
    from app.models import EvalRun
    reap_stale_exec_locks(db, device_id)
    if not allow_record and active_recording(db, device_id):
        raise HTTPException(409, detail="该设备正在录制，请停止并等待步骤上传完成")
    if db.query(ProbeRequest).filter(ProbeRequest.runner_device_id == device_id,
                                    ProbeRequest.status == "running").with_for_update().first():
        raise HTTPException(409, detail="该设备正在探测，请稍后再试")
    device = db.get(RunnerDevice, device_id)
    for model, label in ((ExecRun, "功能测试"), (EvalRun, "对话测评")):
        if db.query(model).filter(device_runs(db, model, device), model.status == "running").with_for_update().first():
            raise HTTPException(409, detail=f"该设备正在执行{label}，等待当前任务结束后再领取")


def lock_runner_device(db, ctx, runner):
    """Legacy shared tokens must use the same device lock, never bypass it."""
    if ctx.device is not None:
        return lock_device(db, ctx)
    rows = db.query(RunnerDevice).filter(RunnerDevice.runner_id == runner).with_for_update().all()
    if len(rows) > 1:
        raise HTTPException(409, detail="设备标识重名，请使用设备专属 token")
    return rows[0] if rows else None


def assert_assigned(row, device):
    if row.runner_device_id != device.id:
        raise HTTPException(403, detail="任务未分配给此设备")
