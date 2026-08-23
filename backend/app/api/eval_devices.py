"""对话测评客户端设备(vm)快照:CLI 上报 → 平台存 → 前端下发时下拉选目标设备。

区别于 devices.py(物理执行机 runner_device)与 eval_queue.py(执行队列)。
上报走 require_runner_ctx(设备 token 锁 runner_id);查询走用户 JWT。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import RunnerCtx, get_current_user, require_runner_ctx
from app.db.session import get_db
from app.models import EvalClientDevice, User
from app.schemas.common import ok
from app.schemas.eval_device import EvalDeviceReportIn

router = APIRouter(prefix="/api/eval-devices", tags=["eval-devices"])

# 在线状态排序权重(在线优先)
_ONLINE = {"online", "active"}


def _to_out(d: EvalClientDevice) -> dict:
    return {
        "vm_id": d.vm_id,
        "name": d.name,
        "status": d.status,
        "device_type": d.device_type,
        "label": d.label,
        "last_report_at": d.last_report_at.isoformat() if d.last_report_at else None,
    }


@router.post("/report")
def report_devices(body: EvalDeviceReportIn, db: Session = Depends(get_db),
                   ctx: RunnerCtx = Depends(require_runner_ctx)):
    runner = ctx.device.runner_id if ctx.device is not None else body.runner
    now = datetime.utcnow()
    reported = 0
    for item in body.devices:
        if not item.vm_id:
            continue
        row = (db.query(EvalClientDevice)
               .filter(EvalClientDevice.runner == runner, EvalClientDevice.vm_id == item.vm_id)
               .first())
        if row is None:
            row = EvalClientDevice(runner=runner, vm_id=item.vm_id)
            db.add(row)
        row.label = item.label
        row.name = item.name
        row.status = item.status
        row.device_type = item.device_type
        row.last_report_at = now
        reported += 1
    db.commit()
    return ok({"reported": reported})


@router.get("")
def list_devices(runner: str = Query(...), db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    rows = (db.query(EvalClientDevice)
            .filter(EvalClientDevice.runner == runner)
            .all())
    # 在线优先,再按名称;Python 侧排序(数据量小)
    rows.sort(key=lambda d: (0 if (d.status or "") in _ONLINE else 1, d.name or d.vm_id))
    return ok([_to_out(d) for d in rows])
