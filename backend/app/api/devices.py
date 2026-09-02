"""执行设备(runner_device)管理:成员登记/管理自己的执行机,拿专属 token。

- GET    /api/devices           列出我的设备(token 脱敏,仅注册/重置时返回明文一次)
- POST   /api/devices           注册一台设备(runner_id + name)→ 返回明文 token(仅此一次)
- POST   /api/devices/{id}/reset-token  重置 token → 返回新明文 token
- DELETE /api/devices/{id}      删除我的设备

沿用全项目约定:{code,msg,data} 信封(ok)、手写 _to_out、用户 JWT(get_current_user)。
归属:一切操作只作用于 owner_id==当前用户 的设备(平台管理员不特殊,设备是私人的)。
"""
import json
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_platform_admin
from app.core.enums import EvalRunStatus, normalize_capabilities
from app.db.session import get_db
from app.models import EvalRun, ExecRun, Project, RunnerDevice, TestCase, User
from app.schemas.common import ok

router = APIRouter(prefix="/api/devices", tags=["devices"])

# 设备在线判定阈值:last_seen_at(runner 最近拉取时间)距今在此秒数内即视为在线。
# runner 执行任务期间不轮询队列(不更新 last_seen),故窗口需容忍执行间隔;另叠加
# 「running>0 强制在线」(正在执行必然活着),避免活跃设备被误判离线。
ONLINE_WINDOW_SEC = 180
# 看板 active_runs 每设备最多展示的执行中明细条数(防单设备堆积过多 running 撑爆响应)。
ACTIVE_RUNS_LIMIT = 8


class DeviceIn(BaseModel):
    runner_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    # platform: web(PC端) / android / ios；默认 web 保持向后兼容。
    platform: str = Field("web", pattern="^(web|android|ios)$")
    # capabilities: 逗号分隔能力集(func=功能测试 / eval=对话测评)。默认全能力(与存量口径一致);
    # 落库前经 normalize_capabilities 去重/去非法/排序,空/全非法回落 'func,eval'。
    capabilities: str = Field("func,eval", max_length=64)


class DevicePatchIn(BaseModel):
    """编辑设备:三项均可选,只更新传入的字段(None=不改)。runner_id 不可改(是稳定标识)。"""
    name: str | None = Field(None, min_length=1, max_length=128)
    platform: str | None = Field(None, pattern="^(web|android|ios)$")
    capabilities: str | None = Field(None, max_length=64)


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
        "platform": d.platform,
        "capabilities": d.capabilities or "func,eval",   # 老行空值兜底全能力(与迁移 default 一致)
        "token": d.token if reveal_token else _mask(d.token),  # 仅注册/重置时给明文
        "last_seen_at": (d.last_seen_at.isoformat() + "Z") if d.last_seen_at else None,
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
        platform=body.platform,
        capabilities=normalize_capabilities(body.capabilities),
        token=secrets.token_hex(32),   # 64 位十六进制长随机串
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return ok(_to_out(device, reveal_token=True))


@router.patch("/{device_id}")
def update_device(device_id: int, body: DevicePatchIn,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """编辑我的设备(name/platform/capabilities)。runner_id 是稳定标识,不可改。"""
    device = db.get(RunnerDevice, device_id)
    if not device or device.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="设备不存在或不属于你")
    if body.name is not None:
        device.name = body.name.strip()
    if body.platform is not None:
        device.platform = body.platform
    if body.capabilities is not None:
        device.capabilities = normalize_capabilities(body.capabilities)
    db.commit()
    db.refresh(device)
    return ok(_to_out(device))


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

# eval_run 状态并入看板计数的归一映射:running/pending 原样(设备忙闲口径);done/judged 归 passed、
# failed 归 failed(取"执行成败",判定结论不在看板范畴)。judging 是服务端大模型判定、不占设备,不计。
_EVAL_STATUS_MAP = {"pending": "pending", "running": "running",
                    "done": "passed", "judged": "passed", "failed": "failed"}


def _overview_device_out(d: RunnerDevice, owner_name: str, utc_now: datetime,
                         counts: dict, today: dict, active: list) -> dict:
    # 在线判定必须用 UTC：last_seen_at 由 runner 拉取时以 datetime.utcnow() 写入
    # （见 exec_queue/perf/eval_queue/probe），判定端若用本地 now() 会凭空多算时区偏移
    # （CST 差 8h → 永远离线）。故此处与写入侧统一用 utcnow。
    # 另：正在执行任务(running>0)的设备必然活着——执行期间不轮询队列 last_seen 会滞后，
    # 故 running>0 直接视为在线，避免活跃设备被误判离线。
    fresh = bool(d.last_seen_at and (utc_now - d.last_seen_at).total_seconds() <= ONLINE_WINDOW_SEC)
    online = fresh or counts.get("running", 0) > 0
    return {
        "id": d.id,
        "runner_id": d.runner_id,
        "name": d.name,
        "platform": d.platform,
        "capabilities": d.capabilities or "func,eval",   # 能力集(func/eval),看板展示标识用
        "owner": {"id": d.owner_id, "name": owner_name},
        # 加 Z 标明 UTC：last_seen_at 是 naive UTC(utcnow 写入)，不带时区前端会当本地时间解析、
        # 凭空差 8h(CST)→ 显示「刚掉线就 8 小时前」。补 Z 让前端 new Date 正确按 UTC 解析。
        "last_seen_at": (d.last_seen_at.isoformat() + "Z") if d.last_seen_at else None,
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
def devices_overview(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """全平台设备只读看板聚合(所有注册用户可查看)。

    一次性返回:全量设备 + owner + 在线状态 + 各状态 run 计数 + 今日终态 + 执行中明细。
    计数与明细合并两类执行:功能测试(exec_run)与对话测评(eval_run,状态经 _EVAL_STATUS_MAP 归一);
    明细每条带 kind 类型标识(func/eval,新执行类型在收集处追加即可扩展)。
    前端定时轮询本端点渲染看板(无任何写操作)。

    关联口径:exec_run/eval_run 均按 `runner`(字符串,= runner_id)归拢——沿用现有下发/拉取的
    runner_id 字符串匹配口径。若两名成员登记了同名 runner_id,其执行计数会合并(既有数据
    模型的固有限制,不在本只读看板内区分)。
    """
    now = datetime.now()
    utc_now = datetime.utcnow()   # 在线判定专用：对齐 last_seen_at 的 utcnow 写入（避免时区偏移误判离线）
    # created_at 由 DB func.now() 生成——生产 MySQL(东八区)非 UTC。凡与 created_at 比较(elapsed 相减、
    # today 当日过滤)都必须用【DB 时钟】为基准,否则用进程 utcnow/now 会整体差 8h(elapsed 恒 -8h、
    # today 跨日错位)。与 scheduler._db_now / reaper 同源治法。online 判定仍用 utcnow(对齐 last_seen)。
    from app.services.scheduler import _db_now
    db_now = _db_now(db)
    db_today = db_now.date()
    devices = db.query(RunnerDevice).order_by(RunnerDevice.id).all()
    # owner 姓名批量取(避免逐设备查 user)
    owner_ids = {d.owner_id for d in devices}
    owner_names = dict(
        db.query(User.id, User.name).filter(User.id.in_(owner_ids)).all()
    ) if owner_ids else {}

    # 全量计数:group by (runner, status)——功能测试 + 对话测评(状态归一后)累加
    counts_by_runner: dict[str, dict] = {}
    for runner, st, cnt in (
        db.query(ExecRun.runner, ExecRun.status, func.count(ExecRun.id))
        .group_by(ExecRun.runner, ExecRun.status).all()
    ):
        counts_by_runner.setdefault(runner, {})[getattr(st, "value", st)] = cnt
    for runner, st, cnt in (
        db.query(EvalRun.runner, EvalRun.status, func.count(EvalRun.id))
        .group_by(EvalRun.runner, EvalRun.status).all()
    ):
        key = _EVAL_STATUS_MAP.get(getattr(st, "value", st))
        if key:
            m = counts_by_runner.setdefault(runner, {})
            m[key] = m.get(key, 0) + cnt

    # 今日计数:同上但限定 created_at 为当天
    today_by_runner: dict[str, dict] = {}
    for runner, st, cnt in (
        db.query(ExecRun.runner, ExecRun.status, func.count(ExecRun.id))
        .filter(func.date(ExecRun.created_at) == db_today)
        .group_by(ExecRun.runner, ExecRun.status).all()
    ):
        today_by_runner.setdefault(runner, {})[getattr(st, "value", st)] = cnt
    for runner, st, cnt in (
        db.query(EvalRun.runner, EvalRun.status, func.count(EvalRun.id))
        .filter(func.date(EvalRun.created_at) == db_today)
        .group_by(EvalRun.runner, EvalRun.status).all()
    ):
        key = _EVAL_STATUS_MAP.get(getattr(st, "value", st))
        if key:
            m = today_by_runner.setdefault(runner, {})
            m[key] = m.get(key, 0) + cnt

    # 执行中明细:功能测试(kind=func) + 对话测评(kind=eval)合并;新执行类型在此追加收集即可扩展。
    # 统一按开始时间排序后再按 runner 截断(ACTIVE_RUNS_LIMIT),并发混跑时两类不偏科。
    all_active: list[tuple[str, dict]] = []   # (runner, item)
    running_rows = (
        db.query(ExecRun, Project.name, TestCase.title)
        .outerjoin(Project, Project.id == ExecRun.project_id)
        .outerjoin(TestCase, TestCase.id == ExecRun.test_case_id)
        .filter(ExecRun.status == "running")
        .order_by(ExecRun.created_at).all()
    )
    for r, proj_name, tc_title in running_rows:
        # 耗时/开始时间必须用 UTC 对齐:created_at 由数据库 func.now() 生成——SQLite 的
        # CURRENT_TIMESTAMP 与 docker MySQL 默认时区都是 UTC;此前用本地 now 相减、且
        # started_at 不带 Z 让前端按本地时区解析,CST 下执行时长凭空多 8 小时(同 last_seen_at 的坑)。
        elapsed = int((db_now - r.created_at).total_seconds() * 1000) if r.created_at else None
        all_active.append((r.runner, {
            "run_id": r.id,
            "kind": "func",
            "title": tc_title or "(无用例快照)",
            "project": r.project_id,          # 项目 id(测试契约)
            "project_name": proj_name,        # 项目名(前端展示用)
            "started_at": (r.created_at.isoformat() + "Z") if r.created_at else None,
            "elapsed_ms": elapsed,
        }))
    eval_running_rows = (
        db.query(EvalRun, Project.name)
        .outerjoin(Project, Project.id == EvalRun.project_id)
        .filter(EvalRun.status == EvalRunStatus.running)
        .order_by(EvalRun.created_at).all()
    )
    for r, proj_name in eval_running_rows:
        try:
            payload = json.loads(r.payload) if r.payload else {}
        except (ValueError, TypeError):
            payload = {}
        elapsed = int((db_now - r.created_at).total_seconds() * 1000) if r.created_at else None
        all_active.append((r.runner, {
            "run_id": r.id,
            "kind": "eval",
            "title": payload.get("title") or payload.get("prompt") or f"测评 run#{r.id}",
            "project": r.project_id,
            "project_name": proj_name,
            "started_at": (r.created_at.isoformat() + "Z") if r.created_at else None,
            "elapsed_ms": elapsed,
        }))
    all_active.sort(key=lambda t: t[1]["started_at"] or "")
    active_by_runner: dict[str, list] = {}
    for runner, item in all_active:
        lst = active_by_runner.setdefault(runner, [])
        if len(lst) < ACTIVE_RUNS_LIMIT:
            lst.append(item)

    out = []
    online_cnt = 0
    running_cnt = 0
    for d in devices:
        counts = counts_by_runner.get(d.runner_id, {})
        today = today_by_runner.get(d.runner_id, {})
        active = active_by_runner.get(d.runner_id, [])
        dev = _overview_device_out(d, owner_names.get(d.owner_id, ""), utc_now, counts, today, active)
        if dev["online"]:
            online_cnt += 1
        if dev["run_counts"]["running"] > 0:
            running_cnt += 1
        out.append(dev)

    # 排序：①在线且执行中 → ②在线空闲 → ③离线；档内按执行中数多→少、再按最近活跃新→旧。
    # 用稳定排序从次要键到主要键分层：先排最次要(活跃时间)，最后排最主要(档位)。
    # last_seen_at 是同格式 ISO 字符串(或 None)，字典序即时间序；None 视作最早("")排档内最后。
    def _tier(dev):
        if dev["online"] and dev["run_counts"]["running"] > 0:
            return 0   # 在线且执行中
        if dev["online"]:
            return 1   # 在线空闲
        return 2       # 离线

    out.sort(key=lambda d: d["last_seen_at"] or "", reverse=True)   # 次要：最近活跃新→旧
    out.sort(key=lambda d: -d["run_counts"]["running"])             # 其次：执行中数多→少
    out.sort(key=_tier)                                             # 主要：档位

    return ok({
        "generated_at": now.isoformat(),
        "total_devices": len(devices),
        "online_devices": online_cnt,
        "running_devices": running_cnt,
        "devices": out,
    })
