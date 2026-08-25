"""执行设备(runner_device)管理:成员登记/管理自己的执行机,拿专属 token。

- GET    /api/devices           列出我的设备(token 脱敏,仅注册/重置时返回明文一次)
- POST   /api/devices           注册一台设备(runner_id + name)→ 返回明文 token(仅此一次)
- POST   /api/devices/{id}/reset-token  重置 token → 返回新明文 token
- DELETE /api/devices/{id}      删除我的设备

沿用全项目约定:{code,msg,data} 信封(ok)、手写 _to_out、用户 JWT(get_current_user)。
归属:一切操作只作用于 owner_id==当前用户 的设备(平台管理员不特殊,设备是私人的)。
"""
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_platform_admin
from app.db.session import get_db
from app.models import ExecRun, Project, RunnerDevice, TestCase, User
from app.schemas.common import ok

router = APIRouter(prefix="/api/devices", tags=["devices"])

# 设备在线判定阈值:last_seen_at(runner 最近拉取时间)距今在此秒数内即视为在线。
# runner 轮询间隔通常几秒,60s 可容忍偶发抖动又能较快反映掉线。
ONLINE_WINDOW_SEC = 60
# 看板 active_runs 每设备最多展示的执行中明细条数(防单设备堆积过多 running 撑爆响应)。
ACTIVE_RUNS_LIMIT = 8


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


# ---- 设备看板(只读聚合;平台管理员) ----

_COUNT_STATUSES = ("running", "pending", "passed", "failed", "blocked")


def _overview_device_out(d: RunnerDevice, owner_name: str, now: datetime,
                         counts: dict, today: dict, active: list) -> dict:
    online = bool(d.last_seen_at and (now - d.last_seen_at).total_seconds() <= ONLINE_WINDOW_SEC)
    return {
        "id": d.id,
        "runner_id": d.runner_id,
        "name": d.name,
        "owner": {"id": d.owner_id, "name": owner_name},
        "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
        "online": online,
        # 各状态全量计数(缺省补 0),供卡片四格 + 汇总
        "run_counts": {s: counts.get(s, 0) for s in _COUNT_STATUSES},
        # 今日终态计数(passed/failed/blocked),供"今日战果"
        "today": {"passed": today.get("passed", 0), "failed": today.get("failed", 0),
                  "blocked": today.get("blocked", 0)},
        # 当前执行中明细,动效数据源
        "active_runs": active,
    }


@router.get("/overview")
def devices_overview(db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    """全平台设备只读看板聚合(仅平台管理员)。

    一次性返回:全量设备 + owner + 在线状态 + 各状态 exec_run 计数 + 今日终态 + 执行中明细。
    前端定时轮询本端点渲染看板(无任何写操作)。

    关联口径:exec_run 按 `runner`(字符串,= runner_id)归拢——沿用现有下发/拉取的
    runner_id 字符串匹配口径。若两名成员登记了同名 runner_id,其执行计数会合并(既有数据
    模型的固有限制,不在本只读看板内区分)。
    """
    now = datetime.now()
    devices = db.query(RunnerDevice).order_by(RunnerDevice.id).all()
    # owner 姓名批量取(避免逐设备查 user)
    owner_ids = {d.owner_id for d in devices}
    owner_names = dict(
        db.query(User.id, User.name).filter(User.id.in_(owner_ids)).all()
    ) if owner_ids else {}

    # 全量计数:group by (runner, status)
    counts_by_runner: dict[str, dict] = {}
    for runner, st, cnt in (
        db.query(ExecRun.runner, ExecRun.status, func.count(ExecRun.id))
        .group_by(ExecRun.runner, ExecRun.status).all()
    ):
        counts_by_runner.setdefault(runner, {})[getattr(st, "value", st)] = cnt

    # 今日计数:同上但限定 created_at 为当天
    today_by_runner: dict[str, dict] = {}
    for runner, st, cnt in (
        db.query(ExecRun.runner, ExecRun.status, func.count(ExecRun.id))
        .filter(func.date(ExecRun.created_at) == now.date())
        .group_by(ExecRun.runner, ExecRun.status).all()
    ):
        today_by_runner.setdefault(runner, {})[getattr(st, "value", st)] = cnt

    # 执行中明细:join Project/TestCase 取名字;按 runner 归拢(每设备截断 ACTIVE_RUNS_LIMIT)
    active_by_runner: dict[str, list] = {}
    running_rows = (
        db.query(ExecRun, Project.name, TestCase.title)
        .outerjoin(Project, Project.id == ExecRun.project_id)
        .outerjoin(TestCase, TestCase.id == ExecRun.test_case_id)
        .filter(ExecRun.status == "running")
        .order_by(ExecRun.created_at).all()
    )
    for r, proj_name, tc_title in running_rows:
        lst = active_by_runner.setdefault(r.runner, [])
        if len(lst) >= ACTIVE_RUNS_LIMIT:
            continue
        elapsed = int((now - r.created_at).total_seconds() * 1000) if r.created_at else None
        lst.append({
            "run_id": r.id,
            "title": tc_title or "(无用例快照)",
            "project": r.project_id,          # 项目 id(测试契约)
            "project_name": proj_name,        # 项目名(前端展示用)
            "started_at": r.created_at.isoformat() if r.created_at else None,
            "elapsed_ms": elapsed,
        })

    out = []
    online_cnt = 0
    running_cnt = 0
    for d in devices:
        counts = counts_by_runner.get(d.runner_id, {})
        today = today_by_runner.get(d.runner_id, {})
        active = active_by_runner.get(d.runner_id, [])
        dev = _overview_device_out(d, owner_names.get(d.owner_id, ""), now, counts, today, active)
        if dev["online"]:
            online_cnt += 1
        if dev["run_counts"]["running"] > 0:
            running_cnt += 1
        out.append(dev)

    return ok({
        "generated_at": now.isoformat(),
        "total_devices": len(devices),
        "online_devices": online_cnt,
        "running_devices": running_cnt,
        "devices": out,
    })
