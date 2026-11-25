"""平台签名校验：Windows 签名校验与服务端 macOS 静态初筛。

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
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import unquote

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, Response

from app.api.hooks import require_hook_auth
from app.core.config import settings
from app.core.security import create_mac_agent_token, decode_token
from app.core.deps import get_current_user
from app.db.session import SessionLocal, get_db
from app.models import MacNotaryAgent, SignCheck, SignCheckAgent, User
from app.schemas.common import ok
from app.schemas.signcheck import SignCheckResultIn
from app.services import mac_notary, signcheck as svc
from app.services.mac_static import jobs as static_checks

CheckPlatform = Literal["windows", "macos"]

logger = logging.getLogger("test_platform")
router = APIRouter(prefix="/api/signcheck", tags=["signcheck"])
hook_router = APIRouter(prefix="/api/hooks/signcheck", tags=["ci-hooks"])

_bearer = HTTPBearer(auto_error=True)

_ONLINE_WINDOW = timedelta(seconds=90)
# 认领后迟迟不回传（agent 断网/关机后一直没回来）→ 判 failed；agent 重启回来的情况见 agent_next 的孤儿重派，避免页面永久「校验中」。原程序硬超时 120s，这里留足余量。
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
        "platform": "macos" if r.platform == static_checks.PLATFORM else r.platform,
        "check_mode": {"macos": "mac-native", static_checks.PLATFORM: "server-static"}.get(r.platform, "windows"),
        "team_id": r.team_id,
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
        if r.platform in ("macos", static_checks.PLATFORM) and r.mac_output:
            out.update(json.loads(r.mac_output))
    return out


def _online_agents(db: Session, platform: CheckPlatform, owner: int | None) -> list[str]:
    now = datetime.utcnow()
    if platform == "macos":
        rows = db.query(MacNotaryAgent.agent).filter(
            MacNotaryAgent.last_seen_at >= now - _ONLINE_WINDOW,
            MacNotaryAgent.owner_id.in_({0, owner or 0}),
        ).distinct().all()
        return sorted(name for (name,) in rows)
    seen = db.query(SignCheckAgent.agent).filter(SignCheckAgent.last_seen_at >= now - _ONLINE_WINDOW).all()
    busy = db.query(SignCheck.agent).filter(
        SignCheck.platform == "windows", SignCheck.status == "running",
        SignCheck.started_at >= now - _RUNNING_TTL,
    ).distinct().all()
    return sorted({name for (name,) in seen + busy if name})


def _touch(db: Session, agent: str, scope: "AgentScope") -> None:
    now = datetime.utcnow()
    model = MacNotaryAgent if scope.platform == "macos" else SignCheckAgent
    key = {"agent": agent}
    if scope.platform == "macos":
        key["owner_id"] = scope.owner or 0
    query = db.query(model).filter_by(**key)
    if not query.update({"last_seen_at": now}):
        try:
            db.add(model(**key, last_seen_at=now))
            db.flush()
        except IntegrityError:
            db.rollback()
            query.update({"last_seen_at": now})
        if scope.platform == "macos":
            db.query(MacNotaryAgent).filter(MacNotaryAgent.last_seen_at < now - timedelta(days=1)).delete()
    db.commit()


def _fail(r: SignCheck, reason: str) -> None:
    r.status = "failed"
    r.verdict = "error"
    r.error = reason
    r.finished_at = datetime.utcnow()
    svc.remove_file(r.id)


def _reap(db: Session) -> None:
    """惰性收口卡住的记录（读接口顺带调用，不另起定时任务）。"""
    now = datetime.utcnow()
    expired = (
        (SignCheck.status == "running",
         func.coalesce(SignCheck.heartbeat_at, SignCheck.started_at) < now - _RUNNING_TTL,
         "校验机认领后超时未回传结果，请重新上传校验。"),
        (SignCheck.status == "pending",
         SignCheck.created_at < now - timedelta(minutes=settings.SIGNCHECK_PENDING_TTL_MINUTES),
         "长时间没有支持此任务的校验机认领（未在线），请启动校验机后重新上传。"),
    )
    for state, cutoff, reason in expired:
        rows = db.query(SignCheck.id).filter(SignCheck.platform != static_checks.PLATFORM, state, cutoff).all()
        for (cid,) in rows:
            # 领取、回传或心跳可能发生在读到过期记录之后。
            changed = db.execute(update(SignCheck).where(SignCheck.id == cid, state, cutoff).values(
                status="failed", verdict="error", error=reason, finished_at=now,
            ).execution_options(synchronize_session=False)).rowcount
            db.commit()
            if changed:
                svc.remove_file(cid)


def _clean_filename(raw: str | None) -> str:
    """只保留展示/回传用的文件名本体：去路径、去控制字符、限长。落盘用的是记录 id，不用它。"""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    if not name or name in (".", ".."):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="缺少文件名（query 参数 filename）")
    if len(name) > 200:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文件名过长，请缩短文件名后重试")
    return name


async def _receive(request: Request, filename: str | None, source: str, user_id: int | None,
                   platform: CheckPlatform, team_id: str | None) -> int:
    """把请求体流式写盘并建一条 pending 记录，返回记录 id。"""
    storage_platform = static_checks.PLATFORM if platform == "macos" else platform
    if platform == "windows" and not _agent_token(platform):
        raise HTTPException(503, detail="该平台校验未启用，请为校验机配置独立令牌。")
    # query 参数已由框架解码；请求头只能放 ASCII，所以约定为 URL 编码，这里解一次
    name = _clean_filename(filename or unquote(request.headers.get("x-file-name") or ""))
    if platform == "macos" and len(name.encode("utf-8")) > 255:
        raise HTTPException(422, detail="文件名过长，请缩短文件名后再上传。")
    if platform == "macos" and not name.lower().endswith((".dmg", ".zip")):
        raise HTTPException(422, detail="macOS 静态初筛支持 DMG、ZIP，请先将 APP 或目录压缩为 ZIP。")
    if team_id and platform != "macos":
        raise HTTPException(422, detail="预期 Team ID 仅适用于 Mac 包。")
    max_bytes = settings.SIGNCHECK_MAX_MB * 1024 * 1024
    too_large = HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                              detail=f"单个文件不能超过 {settings.SIGNCHECK_MAX_MB} MB")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise too_large

    def _queue_full() -> bool:
        db = SessionLocal()
        try:
            n = db.query(SignCheck).filter(SignCheck.platform == storage_platform, SignCheck.status.in_(("pending", "running"))).count()
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
                              source=source, status="pending", created_by=user_id, platform=storage_platform, team_id=team_id)
                db.add(r)
                db.flush()
                cid = r.id
                os.replace(part, svc.file_path(cid))
                try:
                    db.commit()
                except BaseException:
                    svc.remove_file(cid)
                    raise
                return cid
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
def service_status(platform: CheckPlatform = "windows", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """页面顶部状态条：功能是否启用、有没有校验机在线、当前排队数。"""
    _reap(db)
    is_static = platform == "macos"
    # Keep legacy agent telemetry for clients finishing a pre-upgrade native job.
    agents = _online_agents(db, platform, user.id)
    storage_platform = static_checks.PLATFORM if is_static else platform
    queued = db.query(SignCheck).filter(SignCheck.platform == storage_platform, SignCheck.status.in_(("pending", "running"))).count()
    return ok({
        "enabled": platform == "macos" or bool(_agent_token(platform)),
        "check_mode": "server-static" if is_static else "windows",
        "agent_online": bool(agents),
        "agents": agents,
        "queued": queued,
        "max_mb": settings.SIGNCHECK_MAX_MB,
        "pending_minutes": settings.SIGNCHECK_PENDING_TTL_MINUTES,
    })


@router.get("/mac-agent.zip")
def download_mac_agent(user: User = Depends(get_current_user)):
    archive = mac_notary.agent_archive(settings.PLATFORM_BASE_URL or "https://qalab.claw.qihoo.net", create_mac_agent_token(user.id))
    return Response(archive, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="qalab-mac-notary.zip"',
        "Cache-Control": "private, no-store",
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
    platform: CheckPlatform = "windows",
    team_id: str | None = Query(None, pattern=r"^[A-Z0-9]{10}$"),
    user_id: int = Depends(upload_user_id),
):
    """上传一个待校验文件（请求体即文件原始字节）。返回 pending 记录，前端轮询 GET /{id} 取结果。"""
    cid = await _receive(request, filename, "web", user_id, platform, team_id)

    def _load() -> dict:
        db = SessionLocal()
        try:
            return _to_out(db, db.get(SignCheck, cid))
        finally:
            db.close()

    return ok(await run_in_threadpool(_load))


@router.get("")
def list_checks(
    platform: CheckPlatform = "windows",
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
    platforms = ("macos", static_checks.PLATFORM) if platform == "macos" else ("windows",)
    q = db.query(SignCheck).filter(SignCheck.platform.in_(platforms))
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

def _agent_token(platform: CheckPlatform) -> str:
    if platform == "macos":
        token = settings.SIGNCHECK_MAC_AGENT_TOKEN
        return token if token != settings.SIGNCHECK_AGENT_TOKEN else ""
    return settings.SIGNCHECK_AGENT_TOKEN


@dataclass(frozen=True)
class AgentScope:
    platform: CheckPlatform
    owner: int | None = None

    def jobs(self, db: Session):
        query = db.query(SignCheck).filter(SignCheck.platform == self.platform)
        return query if self.owner is None else query.filter(SignCheck.created_by == self.owner)


def require_agent(platform: CheckPlatform = "windows", creds: HTTPAuthorizationCredentials = Depends(_bearer),
                  db: Session = Depends(get_db)) -> AgentScope:
    token = _agent_token(platform)
    if token and secrets.compare_digest(creds.credentials.encode(), token.encode()):
        return AgentScope(platform)
    if platform == "macos":
        claims = decode_token(creds.credentials)
        if claims and claims.get("type") == "mac_notary_agent" and str(claims.get("sub")).isdigit():
            user = db.get(User, int(claims["sub"]))
            if user and user.status.value == "active":
                return AgentScope(platform, user.id)
    raise HTTPException(401, detail="校验机凭据无效或已过期")


def _job(r: SignCheck) -> dict:
    job = {"id": r.id, "filename": r.filename, "file_size": r.file_size, "sha256": r.sha256}
    if r.platform == "macos":
        job.update(platform="macos", team_id=r.team_id)
    return job


@router.get("/agent/next")
def agent_next(
    agent: str = Query("win-signcheck", min_length=1, max_length=64),
    db: Session = Depends(get_db),
    scope: AgentScope = Depends(require_agent),
):
    """agent 轮询：刷新心跳，并原子认领最早一条 pending。没有待办返回 data=null。

    agent 是单线程的：它来轮询，就说明手上没有在处理的活。所以此时仍挂在它名下的 running
    记录一定是孤儿（校验到一半 agent 被关掉/重启/断电），直接重新交给它，而不是让用户
    干等到超时判失败。
    """
    _touch(db, agent, scope)
    eligible = scope.jobs(db)
    _reap(db)
    orphans = (
        eligible.filter(SignCheck.status == "running", SignCheck.agent == agent)
        .order_by(SignCheck.id).all()
    )
    for r in orphans:
        if not svc.file_path(r.id).is_file():
            _fail(r, "校验被中断且服务端文件已清理，请重新上传。")
            db.commit()
            continue
        r.started_at = datetime.utcnow()
        r.heartbeat_at = None
        db.commit()
        logger.info("签名校验 #%s 上次被中断，重新交给校验机 %s", r.id, agent)
        return ok(_job(r))
    for r in eligible.filter(SignCheck.status == "pending").order_by(SignCheck.id).limit(5).all():
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
            return ok(_job(r))
    return ok(None)


@router.post("/agent/{cid}/heartbeat")
def agent_heartbeat(cid: int, agent: str = Query(min_length=1, max_length=64),
                    db: Session = Depends(get_db), scope: AgentScope = Depends(require_agent)):
    if scope.platform != "macos":
        raise HTTPException(403, detail="此心跳仅用于 Mac 校验机")
    now = datetime.utcnow()
    active = scope.jobs(db).filter_by(id=cid, status="running", agent=agent).update({"heartbeat_at": now})
    db.commit()
    if not active:
        raise HTTPException(409, detail="预检任务已结束或由其他校验机认领")
    _touch(db, agent, scope)
    return ok(None)


@router.get("/agent/{cid}/file")
def agent_file(cid: int, agent: str | None = None, db: Session = Depends(get_db), scope: AgentScope = Depends(require_agent)):
    r = scope.jobs(db).filter_by(id=cid).first()
    path = svc.file_path(cid)
    if (not r or r.status != "running" or not path.is_file()
            or (scope.platform == "macos" and r.agent != agent)):
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
    scope: AgentScope = Depends(require_agent),
):
    """agent 回传原始输出；由后端解码、解析四项状态并落终态。回传后服务端删除上传文件。"""
    r = scope.jobs(db).filter_by(id=cid).first()
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记录不存在")
    if r.status != "running":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该校验不在进行中")
    if r.platform == "macos":
        if body.agent != r.agent:
            raise HTTPException(409, detail="结果来自其他校验机")
        stdout = _b64(body.stdout_b64).decode("utf-8", errors="replace")
        stderr = _b64(body.stderr_b64).decode("utf-8", errors="replace")
        verdict, reason = mac_notary.summarize(body.report, body.exit_code, r.filename, r.team_id)
        r.verdict, r.error = ("error", body.error) if body.error else (verdict, reason)
        r.mac_output = json.dumps({"report": body.report, "stdout": stdout, "stderr": stderr}, ensure_ascii=False)
        r.status = "failed" if r.verdict == "error" else "completed"
        r.exit_code, r.duration_ms, r.finished_at = body.exit_code, body.duration_ms, datetime.utcnow()
        db.commit()
        svc.remove_file(cid)
        return ok({"id": cid, "status": r.status, "verdict": r.verdict})
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
    out["agent_online"] = None if r.platform == static_checks.PLATFORM else bool(_online_agents(db, r.platform, r.created_by))
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
    platform: CheckPlatform = "windows",
    team_id: str | None = Query(None, pattern=r"^[A-Z0-9]{10}$"),
    wait: int = Query(0, ge=0, le=300, description="同步等待结果的最长秒数；0=立即返回，自行轮询"),
    _: None = Depends(require_hook_auth),
):
    """脚本/流水线上传文件校验。wait>0 时阻塞到出结果或超时（超时仍返回当前状态，可继续轮询）。

    例：curl -sS -X POST "$PLATFORM/api/hooks/signcheck?filename=setup.exe&wait=120" \\
          -H "X-CI-Token: $CI_HOOK_TOKEN" -H "Content-Type: application/octet-stream" \\
          --data-binary @setup.exe
    """
    cid = await _receive(request, filename, "api", None, platform, team_id)

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
