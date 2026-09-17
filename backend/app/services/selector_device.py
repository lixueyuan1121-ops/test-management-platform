"""选择器交互任务的设备身份与占用。所有领取路径锁同一设备行。"""
from datetime import datetime, timedelta

from fastapi import HTTPException
from app.models import RunnerDevice, RecordSession, ProbeRequest, ExecRun

RECORD_LIVE = ("pending", "recording", "stopping")

# 心跳超过该时长的 running exec_run 视为“执行机猝死残留锁”（runner 每 60s 发一次心跳，
# 强关终端/断网即不再回写）。领取路径遇到即就地收口，使设备下次拉活自愈，
# 不必干等 scheduler 的 2 小时全局兜底。
EXEC_HEARTBEAT_STALE_MINUTES = 5


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


def reap_stale_exec_locks(db, device_id):
    """就地收口该设备名下“心跳超时”的 running exec_run（runner 猝死残留锁）。

    判定用 heartbeat_at，退化到 started_at/updated_at（从未发心跳的旧记录）。
    收口为 failed + fail_kind=timeout，留痕原因。返回收口条数。领取路径判定占用前调用，
    与 scheduler.reap_stale_exec_runs（2 小时全局兜底）同款归因、更早触发。
    """
    from sqlalchemy import or_, and_, func
    device = db.get(RunnerDevice, device_id)
    if device is None:
        return 0
    cutoff = datetime.utcnow() - timedelta(minutes=EXEC_HEARTBEAT_STALE_MINUTES)
    changed = db.query(ExecRun).filter(
        or_(ExecRun.runner_device_id == device_id,
            and_(ExecRun.runner_device_id.is_(None), ExecRun.runner == device.runner_id)),
        ExecRun.status == "running",
        func.coalesce(ExecRun.heartbeat_at, ExecRun.started_at, ExecRun.updated_at) < cutoff,
    ).update({ExecRun.status: "failed", ExecRun.fail_kind: "timeout",
              ExecRun.finished_at: func.now(),
              ExecRun.reason: "自动收口:执行机心跳超时(runner 猝死/强关未回写),标记失败"},
             synchronize_session="fetch")
    if changed:
        db.commit()
    return changed


def assert_idle(db, device_id, *, allow_record=False):
    if not allow_record and active_recording(db, device_id):
        raise HTTPException(409, detail="该设备正在录制，请停止并等待步骤上传完成")
    if db.query(ProbeRequest).filter(ProbeRequest.runner_device_id == device_id,
                                    ProbeRequest.status == "running").first():
        raise HTTPException(409, detail="该设备正在探测，请稍后再试")
    from sqlalchemy import or_, and_
    reap_stale_exec_locks(db, device_id)   # 先清心跳超时的残留锁，再判占用（设备拉活即自愈）
    device = db.get(RunnerDevice, device_id)
    if db.query(ExecRun).filter(or_(ExecRun.runner_device_id == device_id,
                              and_(ExecRun.runner_device_id.is_(None), ExecRun.runner == device.runner_id)),
                              ExecRun.status == "running").first():
        raise HTTPException(409, detail="该设备正在执行用例，请执行结束后再试")


def assert_assigned(row, device):
    if row.runner_device_id != device.id:
        raise HTTPException(403, detail="任务未分配给此设备")
