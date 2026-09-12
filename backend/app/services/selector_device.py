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
        RecordSession.status.in_(RECORD_LIVE)).first()


def assert_idle(db, device_id, *, allow_record=False):
    if not allow_record and active_recording(db, device_id):
        raise HTTPException(409, detail="该设备正在录制，请停止并等待步骤上传完成")
    if db.query(ProbeRequest).filter(ProbeRequest.runner_device_id == device_id,
                                    ProbeRequest.status == "running").first():
        raise HTTPException(409, detail="该设备正在探测，请稍后再试")
    from sqlalchemy import or_, and_
    device = db.get(RunnerDevice, device_id)
    if db.query(ExecRun).filter(or_(ExecRun.runner_device_id == device_id,
                              and_(ExecRun.runner_device_id.is_(None), ExecRun.runner == device.runner_id)),
                              ExecRun.status == "running").first():
        raise HTTPException(409, detail="该设备正在执行用例，请执行结束后再试")


def assert_assigned(row, device):
    if row.runner_device_id != device.id:
        raise HTTPException(403, detail="任务未分配给此设备")
