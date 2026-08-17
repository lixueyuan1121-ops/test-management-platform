"""设备探测路由：网页发起探测 → runner 拉取/认领(置 running)/回写 的闭环。

四个接口（对齐 exec_queue 的 runner 契约与全项目约定）：
- POST  /api/probe          网页「发起探测」；用户 JWT + 项目 member/admin（体外 assert_project_role）。
- GET   /api/probe/{id}     查询单次探测状态/结果；用户 JWT + 项目任意角色。
- GET   /api/probe/pending  runner 拉取 pending 并认领(置 running)；runner token（设备锁）。
- PATCH /api/probe/{id}     runner 回写 result/error，状态置 done/failed；runner token（归属校验）。

沿用全项目约定：{code,msg,data} 信封（ok）、手写 _to_out、体外 assert_project_role；
params/result 以 TEXT 存 JSON 字符串（兼容 MySQL 5.6），出参 json.loads 还原。
"""
import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import ProbeRequest, User
from app.schemas.common import ok
from app.schemas.probe import ProbeReportIn, ProbeStartIn

router = APIRouter(prefix="/api/probe", tags=["probe"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)
_ALL_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)


def _loads(raw: str | None) -> dict | None:
    """TEXT JSON 还原成 dict；空/坏数据回落 None，避免一行脏数据让轮询整批 500。"""
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _to_out(r: ProbeRequest) -> dict:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "sub_product": r.sub_product,
        "runner": r.runner,
        "status": r.status,
        "params": _loads(r.params) or {},
        "result": _loads(r.result),
        "error": r.error,
        "screenshot_url": f"/uploads/{r.screenshot_path}" if r.screenshot_path else None,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


# ---- ① 网页「发起探测」：入队一次探测请求 ----
@router.post("")
def start_probe(
    body: ProbeStartIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """发起一次设备探测。项目 member/admin 可操作（project_id 走体外鉴权）。"""
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    r = ProbeRequest(
        project_id=body.project_id,
        sub_product=body.sub_product,
        runner=body.runner,
        status="pending",
        params=json.dumps(body.params, ensure_ascii=False),
        created_by=user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return ok({"id": r.id})


# ---- ② runner 拉取待执行并认领（置 running）----
# 注意：/pending 必须声明在 /{probe_id} 之前，否则会被动态路径吞掉。
@router.get("/pending")
def list_pending(
    runner: str = Query("mac-01"),
    limit: int = Query(5, le=20),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    """runner 拉取该 runner 的 pending 探测，认领即置 running。

    设备 token:runner 锁定为该设备的 runner_id(忽略 query,防拿他人 token 冒充别的设备);
    共享 token(兜底):沿用 query 的 runner。
    """
    if ctx.device is not None:
        runner = ctx.device.runner_id
        ctx.device.last_seen_at = datetime.utcnow()   # 记录设备活跃
    rows = (
        db.query(ProbeRequest)
        .filter(ProbeRequest.status == "pending", ProbeRequest.runner == runner)
        .order_by(ProbeRequest.id)
        .limit(limit)
        .all()
    )
    for r in rows:
        r.status = "running"   # 拉取即认领,避免多次轮询重复下发
    db.commit()
    return ok([_to_out(r) for r in rows])


# ---- ③ 用户查询单次探测状态/结果 ----
@router.get("/{probe_id}")
def get_probe(
    probe_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.get(ProbeRequest, probe_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="探测请求不存在")
    # 按该行所属项目鉴权（任意角色可查）。
    assert_project_role(db, user, r.project_id, _ALL_ROLES)
    return ok(_to_out(r))


# ---- ④ runner 回写结果，状态置 done/failed ----
@router.patch("/{probe_id}")
def report_probe(
    probe_id: int,
    body: ProbeReportIn,
    runner: str = Query("mac-01"),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    if ctx.device is not None:
        runner = ctx.device.runner_id   # 设备 token:以设备身份为准,防冒充
    r = db.get(ProbeRequest, probe_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="探测请求不存在")
    # 归属校验：只能回写派给自己的探测（设备 token 下 runner 已锁定为设备 runner_id;
    # 共享 token 下靠 query runner 区分），避免多台 runner 串扰。
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该探测未派给此执行机")

    if body.result is not None:
        r.result = json.dumps(body.result, ensure_ascii=False)
        r.error = None
        r.status = "done"
    else:
        r.error = body.error
        r.status = "failed"
    db.commit()
    db.refresh(r)
    return ok(_to_out(r))


# ---- ⑤ runner 上传探测整页截图（二进制，独立于 result TEXT 通道）----
# 截图大（几百 KB~几 MB），base64 塞 result TEXT 会撑爆 MySQL 5.6 的 64KB 上限（静默截断）。
# 故走独立 multipart 通道，存服务器文件系统，DB 只记相对路径。
_UPLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads")
_PROBE_SHOT_DIR = os.path.join(_UPLOADS_DIR, "probes")
_MAX_SHOT_BYTES = 10 * 1024 * 1024  # 10MB
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@router.post("/{probe_id}/screenshot")
async def upload_screenshot(
    probe_id: int,
    file: UploadFile = File(...),
    runner: str = Query("mac-01"),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    """runner 上传探测整页截图（PNG）。存 uploads/probes/<id>.png，写 screenshot_path。

    runner token 鉴权 + 归属校验（只能给派给自己的探测传图）；仅 PNG；≤10MB。
    """
    if ctx.device is not None:
        runner = ctx.device.runner_id   # 设备 token：以设备身份为准，防冒充
    r = db.get(ProbeRequest, probe_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="探测请求不存在")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该探测未派给此执行机")
    data = await file.read()
    if len(data) > _MAX_SHOT_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"截图过大（>{_MAX_SHOT_BYTES // 1024 // 1024}MB）")
    if not data.startswith(_PNG_MAGIC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="仅支持 PNG 截图")
    os.makedirs(_PROBE_SHOT_DIR, exist_ok=True)
    rel = f"probes/{probe_id}.png"
    with open(os.path.join(_UPLOADS_DIR, rel), "wb") as f:
        f.write(data)
    r.screenshot_path = rel
    db.commit()
    return ok({"screenshot_url": f"/uploads/{rel}"})
