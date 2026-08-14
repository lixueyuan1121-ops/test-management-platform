"""执行设备(runner_device)管理:成员登记/管理自己的执行机,拿专属 token。

- GET    /api/devices           列出我的设备(token 脱敏,仅注册/重置时返回明文一次)
- POST   /api/devices           注册一台设备(runner_id + name)→ 返回明文 token(仅此一次)
- POST   /api/devices/{id}/reset-token  重置 token → 返回新明文 token
- DELETE /api/devices/{id}      删除我的设备

沿用全项目约定:{code,msg,data} 信封(ok)、手写 _to_out、用户 JWT(get_current_user)。
归属:一切操作只作用于 owner_id==当前用户 的设备(平台管理员不特殊,设备是私人的)。
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import RunnerDevice, User
from app.schemas.common import ok

router = APIRouter(prefix="/api/devices", tags=["devices"])


class DeviceIn(BaseModel):
    runner_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)


def _mask(token: str) -> str:
    """token 脱敏:只留前 6 后 4，中间打码(列表展示用,避免泄露完整 token)。"""
    if not token or len(token) <= 12:
        return "****"
    return f"{token[:6]}…{token[-4:]}"


def _to_out(d: RunnerDevice, *, reveal_token: bool = False) -> dict:
    return {
        "id": d.id,
        "runner_id": d.runner_id,
        "name": d.name,
        "token": d.token if reveal_token else _mask(d.token),  # 仅注册/重置时给明文
        "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@router.get("")
def list_my_devices(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(RunnerDevice)
        .filter(RunnerDevice.owner_id == user.id)
        .order_by(RunnerDevice.id)
        .all()
    )
    return ok([_to_out(d) for d in rows])


@router.post("")
def register_device(body: DeviceIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """注册一台我的设备,生成专属 token(明文仅此次返回)。"""
    dup = (
        db.query(RunnerDevice)
        .filter(RunnerDevice.owner_id == user.id, RunnerDevice.runner_id == body.runner_id)
        .first()
    )
    if dup:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"你已登记过 runner_id={body.runner_id} 的设备")
    device = RunnerDevice(
        owner_id=user.id,
        runner_id=body.runner_id.strip(),
        name=body.name.strip(),
        token=secrets.token_hex(32),   # 64 位十六进制长随机串
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return ok(_to_out(device, reveal_token=True))


@router.post("/{device_id}/reset-token")
def reset_token(device_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    device = db.get(RunnerDevice, device_id)
    if not device or device.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="设备不存在或不属于你")
    device.token = secrets.token_hex(32)
    db.commit()
    db.refresh(device)
    return ok(_to_out(device, reveal_token=True))


@router.delete("/{device_id}")
def delete_device(device_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    device = db.get(RunnerDevice, device_id)
    if not device or device.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="设备不存在或不属于你")
    db.delete(device)
    db.commit()
    return ok({"deleted": device_id})
