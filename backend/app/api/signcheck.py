"""Windows 签名校验路由：平台上传文件 → Windows 校验机 agent 执行 360Base_test.exe → 回传结果。

三类调用方、三种鉴权：
- 平台用户（JWT）       /api/signcheck            上传、查结果、历史、删除
- 校验机 agent（Bearer） /api/signcheck/agent/*    认领、下载文件、回传；token = SIGNCHECK_AGENT_TOKEN
- 流水线/脚本（X-CI-Token）/api/hooks/signcheck    上传并（可选）等待结果；token = CI_HOOK_TOKEN

上传走原始字节流（Content-Type: application/octet-stream，文件名放 query 的 filename），
不用 multipart：大文件（上限默认 2 GB）可以边收边写盘、边算 sha256、超限即断，
不必先整体缓冲成临时文件再拷一遍。

沿用全项目约定：{code,msg,data} 信封、手写 _to_out、状态用 VARCHAR。
"""
import base64
import binascii
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import unquote

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from app.api.hooks import require_hook_auth
from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import SessionLocal, get_db
from app.models import SignCheck, User
from app.schemas.common import ok
from app.schemas.signcheck import SignCheckResultIn
from app.services import signcheck as svc

logger = logging.getLogger("test_platform")
router = APIRouter(prefix="/api/signcheck", tags=["signcheck"])
hook_router = APIRouter(prefix="/api/hooks/signcheck", tags=["ci-hooks"])

_bearer = HTTPBearer(auto_error=True)

# 校验机心跳：agent 每次轮询刷新。只存进程内存——平台是 uvicorn 单进程部署，
# 重启后 agent 下一次轮询（几秒内）就会重新出现，不值得为此建表。
_agents: dict[str, datetime] = {}
_ONLINE_WINDOW = timedelta(seconds=30)
# 认领后迟迟不回传（agent 被杀/断网）→ 判 failed，避免页面永久「校验中」。原程序硬超时 120s，这里留足余量。
_RUNNING_TTL = timedelta(minutes=10)
_FINAL = ("completed", "failed")


# ==================== 公共辅助 ====================

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None   # naive UTC → 带 Z，前端按 UTC 解析


def _user_name(db: Session, uid: int | None) -> str | None:
    if not uid:
        return None
    u = db.get(User, uid)
    return (u.name or u.username) if u else None


def _to_out(db: Session, r: SignCheck, full: bool = False) -> dict:
    try:
        signatures = json.loads(r.signatures_json) if r.signatures_json else []
    except (json.JSONDecodeError, ValueError):
        signatures = []
    out = {
        "id": r.id,
        "filename": r.filename,
        "file_size": r.file_size,
        "sha256": r.sha256,
        "source": r.source,
        "status": r.status,
        "verdict": r.verdict,
        "signatures": signatures,
        "error": r.error,
        "agent": r.agent,
        "duration_ms": r.duration_ms,
        "created_by": r.created_by,
        "created_by_name": _user_name(db, r.created_by),
        "created_at": _iso(r.created_at),
        "started_at": _iso(r.started_at),
        "finished_at": _iso(r.finished_at),
    }
    if full:
        out["exit_code"] = r.exit_code
        out["stdout"] = r.stdout
        out["stderr"] = r.stderr
    return out


def _online_agents() -> list[str]:
    now = datetime.utcnow()
    return sorted(a for a, seen in _agents.items() if now - seen <= _ONLINE_WINDOW)


def _fail(r: SignCheck, reason: str) -> None:
    r.status = "failed"
    r.verdict = "error"
    r.error = reason
    r.finished_at = datetime.utcnow()
    svc.remove_file(r.id)


def _reap(db: Session) -> None:
    """惰性收口卡住的记录（读接口顺带调用，不另起定时任务）。"""
    now = datetime.utcnow()
    stale = (
        db.query(SignCheck)
        .filter(SignCheck.status == "running", SignCheck.started_at < now - _RUNNING_TTL)
        .all()
    )
    for r in stale:
        _fail(r, "校验机认领后超时未回传结果，请重新上传校验。")
    waiting = (
        db.query(SignCheck)
        .filter(SignCheck.status == "pending",
                SignCheck.created_at < now - timedelta(minutes=settings.SIGNCHECK_PENDING_TTL_MINUTES))
        .all()
    )
    for r in waiting:
        _fail(r, "长时间没有校验机认领（Windows 校验机 agent 未在线），请启动校验机后重新上传。")
    if stale or waiting:
        db.commit()


def _clean_filename(raw: str | None) -> str:
    """只保留展示/回传用的文件名本体：去路径、去控制字符、限长。落盘用的是记录 id，不用它。"""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    if not name or name in (".", ".."):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="缺少文件名（query 参数 filename）")
    if len(name) > 200:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文件名过长，请缩短文件名后重试")
    return name


async def _receive(request: Request, filename: str | None, source: str, user_id: int | None) -> int:
    """把请求体流式写盘并建一条 pending 记录，返回记录 id。"""
    if not settings.SIGNCHECK_AGENT_TOKEN:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="签名校验未启用（服务端未配置 SIGNCHECK_AGENT_TOKEN）")
    # query 参数已由框架解码；请求头只能放 ASCII，所以约定为 URL 编码，这里解一次
    name = _clean_filename(filename or unquote(request.headers.get("x-file-name") or ""))
    max_bytes = settings.SIGNCHECK_MAX_MB * 1024 * 1024
    too_large = HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                              detail=f"单个文件不能超过 {settings.SIGNCHECK_MAX_MB} MB")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise too_large

    def _queue_full() -> bool:
        db = SessionLocal()
        try:
            n = db.query(SignCheck).filter(SignCheck.status.in_(("pending", "running"))).count()
            return n >= settings.SIGNCHECK_MAX_QUEUE
        finally:
            db.close()

    if await run_in_threadpool(_queue_full):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="排队中的校验过多，请稍后再试")

    svc.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    part = svc.STORAGE_DIR / f"upload-{uuid.uuid4().hex}.part"
    digest, size = hashlib.sha256(), 0
    try:
        async with await anyio.open_file(part, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    raise too_large
                digest.update(chunk)
                await f.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文件内容为空")

        def _create() -> int:
            db = SessionLocal()
            try:
                r = SignCheck(filename=name, file_size=size, sha256=digest.hexdigest(),
                              source=source, status="pending", created_by=user_id)
                db.add(r)
                db.commit()
                os.replace(part, svc.file_path(r.id))
                return r.id
            finally:
                db.close()

        return await run_in_threadpool(_create)
    except BaseException:
        # 含客户端中途断开（ClientDisconnect）——半截文件不留盘
        try:
            part.unlink()
        except OSError:
            pass
        raise


# ==================== 平台用户（JWT）====================

@router.get("/status")
def service_status(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """页面顶部状态条：功能是否启用、有没有校验机在线、当前排队数。"""
    _reap(db)
    agents = _online_agents()
    queued = db.query(SignCheck).filter(SignCheck.status.in_(("pending", "running"))).count()
    return ok({
        "enabled": bool(settings.SIGNCHECK_AGENT_TOKEN),
        "agent_online": bool(agents),
        "agents": agents,
        "queued": queued,
        "max_mb": settings.SIGNCHECK_MAX_MB,
    })


def upload_user_id(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> int:
    """上传专用鉴权：用完即还连接，只带回用户 id。

    不能直接 Depends(get_current_user)：它依赖的 get_db 会话要到请求结束才关，而上传大文件要
    收很久，这条连接一直空闲，会被 MySQL/中间层掐断，收尾 rollback 时报 2013 Lost connection
    （文件其实已收完，却给用户返回 500）。
    """
    db = SessionLocal()
    try:
        return get_current_user(creds, db).id
    finally:
        db.close()


@router.post("")
async def upload(
    request: Request,
    filename: str | None = Query(None, description="原文件名（也可放请求头 X-File-Name，URL 编码）"),
    user_id: int = Depends(upload_user_id),
):
    """上传一个待校验文件（请求体即文件原始字节）。返回 pending 记录，前端轮询 GET /{id} 取结果。"""
    cid = await _receive(request, filename, "web", user_id)

    def _load() -> dict:
        db = SessionLocal()
        try:
            return _to_out(db, db.get(SignCheck, cid))
        finally:
            db.close()

    return ok(await run_in_threadpool(_load))


@router.get("")
def list_checks(
    mine: bool = False,
    keyword: str | None = Query(None, max_length=100),
    verdict: str | None = Query(None, max_length=16),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """历史记录（全体登录用户可见，最新在前）。keyword 匹配文件名或 sha256 前缀。"""
    _reap(db)
    q = db.query(SignCheck)
    if mine:
        q = q.filter(SignCheck.created_by == user.id)
    if keyword and keyword.strip():
        kw = keyword.strip()
        q = q.filter(SignCheck.filename.contains(kw, autoescape=True) | SignCheck.sha256.startswith(kw.lower(), autoescape=True))
    if verdict:
        q = q.filter(SignCheck.verdict == verdict)
    total = q.count()
    rows = q.order_by(SignCheck.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"total": total, "items": [_to_out(db, r) for r in rows]})


# ==================== 校验机 agent（Bearer SIGNCHECK_AGENT_TOKEN）====================
# 注意：/agent/* 这些固定路径必须注册在下方 /{cid} 之前。

def require_agent(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> None:
    """校验机鉴权：独立长期 token（与用户 JWT、runner token 分离）。未配置即整条通道关闭。"""
    if not settings.SIGNCHECK_AGENT_TOKEN or creds.credentials != settings.SIGNCHECK_AGENT_TOKEN:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="无效的签名校验机令牌")


@router.get("/agent/next")
def agent_next(
    agent: str = Query("win-signcheck", min_length=1, max_length=64),
    db: Session = Depends(get_db),
    _: None = Depends(require_agent),
):
    """agent 轮询：刷新心跳，并原子认领最早一条 pending。没有待办返回 data=null。"""
    _agents[agent] = datetime.utcnow()
    _reap(db)
    for r in db.query(SignCheck).filter(SignCheck.status == "pending").order_by(SignCheck.id).limit(5).all():
        if not svc.file_path(r.id).is_file():
            _fail(r, "服务端找不到已上传的文件（可能被清理），请重新上传。")
            db.commit()
            continue
        # 条件更新抢占：多台校验机同时轮询时只有一台能认领成功
        won = db.execute(
            update(SignCheck)
            .where(SignCheck.id == r.id, SignCheck.status == "pending")
            .values(status="running", agent=agent, started_at=datetime.utcnow())
        ).rowcount
        db.commit()
        if won:
            return ok({"id": r.id, "filename": r.filename, "file_size": r.file_size, "sha256": r.sha256})
    return ok(None)


@router.get("/agent/{cid}/file")
def agent_file(cid: int, db: Session = Depends(get_db), _: None = Depends(require_agent)):
    r = db.get(SignCheck, cid)
    path = svc.file_path(cid)
    if not r or r.status != "running" or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="该校验不在进行中或文件已清理")
    return FileResponse(path, media_type="application/octet-stream")


def _b64(text: str | None) -> bytes:
    if not text:
        return b""
    try:
        return base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="stdout_b64/stderr_b64 不是合法 base64")


@router.post("/agent/{cid}/result")
def agent_result(
    cid: int,
    body: SignCheckResultIn,
    db: Session = Depends(get_db),
    _: None = Depends(require_agent),
):
    """agent 回传原始输出；由后端解码、解析四项状态并落终态。回传后服务端删除上传文件。"""
    r = db.get(SignCheck, cid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记录不存在")
    if r.status != "running":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该校验不在进行中")
    stdout = svc.decode_output(_b64(body.stdout_b64))
    stderr = svc.decode_output(_b64(body.stderr_b64))
    r.exit_code = body.exit_code
    r.stdout = svc.clip(stdout)
    r.stderr = svc.clip(stderr)
    r.duration_ms = body.duration_ms
    if body.agent:
        r.agent = body.agent
    r.finished_at = datetime.utcnow()
    if body.error:
        r.status, r.verdict, r.error = "failed", "error", body.error
        r.signatures_json = json.dumps(svc.parse_output(""), ensure_ascii=False)
    else:
        signatures = svc.parse_output(stdout)
        verdict, diagnostic = svc.summarize(signatures, body.exit_code, stdout)
        r.signatures_json = json.dumps(signatures, ensure_ascii=False)
        r.verdict = verdict
        r.error = diagnostic
        r.status = "failed" if verdict == "error" else "completed"
    db.commit()
    svc.remove_file(cid)
    logger.info("签名校验 #%s %s -> %s/%s agent=%s", cid, r.filename, r.status, r.verdict, r.agent)
    return ok({"id": cid, "status": r.status, "verdict": r.verdict})


# ==================== 平台用户：单条 ====================

@router.get("/{cid}")
def get_check(cid: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    _reap(db)
    r = db.get(SignCheck, cid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记录不存在")
    out = _to_out(db, r, full=True)
    out["agent_online"] = bool(_online_agents())
    return ok(out)


@router.delete("/{cid}")
def delete_check(cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(SignCheck, cid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记录不存在")
    if not user.is_platform_admin and r.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只能删除自己提交的记录")
    db.delete(r)
    db.commit()
    svc.remove_file(cid)
    return ok({"deleted": cid})


# ==================== 流水线/脚本（X-CI-Token）====================

def _hook_out(cid: int) -> dict | None:
    db = SessionLocal()
    try:
        _reap(db)
        r = db.get(SignCheck, cid)
        return _to_out(db, r, full=True) if r else None
    finally:
        db.close()


@hook_router.post("")
async def hook_upload(
    request: Request,
    filename: str | None = Query(None),
    wait: int = Query(0, ge=0, le=300, description="同步等待结果的最长秒数；0=立即返回，自行轮询"),
    _: None = Depends(require_hook_auth),
):
    """脚本/流水线上传文件校验。wait>0 时阻塞到出结果或超时（超时仍返回当前状态，可继续轮询）。

    例：curl -sS -X POST "$PLATFORM/api/hooks/signcheck?filename=setup.exe&wait=120" \\
          -H "X-CI-Token: $CI_HOOK_TOKEN" -H "Content-Type: application/octet-stream" \\
          --data-binary @setup.exe
    """
    cid = await _receive(request, filename, "api", None)

    def _wait() -> dict:
        deadline = time.monotonic() + wait
        while True:
            out = _hook_out(cid)
            if not out or out["status"] in _FINAL or time.monotonic() >= deadline:
                return out
            time.sleep(1)

    return ok(await run_in_threadpool(_wait))


@hook_router.get("/{cid}")
def hook_get(cid: int, _: None = Depends(require_hook_auth)):
    out = _hook_out(cid)
    if not out:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记录不存在")
    return ok(out)
