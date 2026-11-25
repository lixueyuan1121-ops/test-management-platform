"""Consume static screenings from the database, one bounded subprocess per worker."""
import json
import logging
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import and_, or_, update

from app.db.session import SessionLocal
from app.models import SignCheck
from app.services import signcheck

PLATFORM = "mac_static"
TIMEOUT = 360
BACKEND = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)
_stop = threading.Event()
_thread = None


def _eligible():
    return or_(SignCheck.status == "pending", and_(SignCheck.status == "running",
               SignCheck.started_at < datetime.utcnow() - timedelta(seconds=TIMEOUT + 60)))


def _scan(path, filename, team_id):
    if not path.is_file():
        raise ValueError("服务端找不到已上传的安装包，请重新上传。")
    with tempfile.TemporaryDirectory(prefix="qalab-mac-static-") as tmp:
        output = Path(tmp) / "report.json"
        args = [sys.executable, "-m", "app.services.mac_static.scan", str(path.resolve()),
                "--label=" + filename, "--report", str(output)]
        if team_id:
            args.extend(["--team-id", team_id])
        process = subprocess.run(args, cwd=BACKEND, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=TIMEOUT)
        if process.returncode or not output.is_file():
            raise ValueError(f"静态解析进程中断（退出码 {process.returncode}），请检查文件或服务端资源。")
        if output.stat().st_size > 8 * 1024**2:
            raise ValueError("报告超过 8 MiB，请将安装包拆分后重试。")
        report = json.loads(output.read_bytes())
    if report["failed"]:
        verdict = "invalid"
    elif report["warnings"] or not report["checked"]:
        verdict = "partial"
    else:
        verdict = "no_findings"
    summary = "未扫描到可判读的原生文件。"
    if report["checked"]:
        finding = f"{report['failed']} 个文件存在签名问题" if report["failed"] else "未发现签名问题"
        summary = f"已扫描 {report['checked']} 个原生文件，{finding}。"
    log = "\n".join([summary, *(f"[扫描范围] {msg}" for msg in report["warnings"])])
    return dict(status="completed", verdict=verdict, exit_code=0,
                mac_output=json.dumps({"report": report, "stdout": log}, ensure_ascii=False))


def process_next(factory=SessionLocal):
    claim = "static-" + uuid4().hex
    with factory() as db:
        row = (db.query(SignCheck.id, SignCheck.filename, SignCheck.team_id)
               .filter(SignCheck.platform == PLATFORM, _eligible()).order_by(SignCheck.id).first())
        if row is None:
            return False
        won = db.execute(update(SignCheck).where(SignCheck.id == row.id, _eligible()).values(
            status="running", agent=claim, started_at=datetime.utcnow())).rowcount
        db.commit()
    if not won:
        return False
    start = time.monotonic()
    try:
        result = _scan(signcheck.file_path(row.id), row.filename, row.team_id)
    except subprocess.TimeoutExpired:
        result = dict(status="failed", verdict="error", error="静态初筛超过 6 分钟，请拆分安装包后重试。")
    except (OSError, ValueError) as exc:
        logger.warning("Static check #%s failed: %s", row.id, exc)
        result = dict(status="failed", verdict="error", error=str(exc)[:500])
    with factory() as db:
        saved = db.execute(update(SignCheck).where(SignCheck.id == row.id, SignCheck.agent == claim,
            SignCheck.status == "running").values(**result, finished_at=datetime.utcnow(),
            duration_ms=int((time.monotonic() - start) * 1000))).rowcount
        db.commit()
    if saved:
        signcheck.remove_file(row.id)
    return True


def _loop():
    while not _stop.is_set():
        try:
            if process_next():
                continue
        except Exception:
            logger.exception("静态初筛队列处理失败")
        _stop.wait(2)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="mac-static-screening", daemon=True)
    _thread.start()


def stop():
    _stop.set()
    if _thread:
        _thread.join(timeout=2)
