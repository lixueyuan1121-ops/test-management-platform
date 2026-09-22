"""Durable worker using database transactions (also compatible with MySQL 5.6).

No in-memory-only queue and no committed 'running' claim: a crashed transaction
rolls back to pending. Project locking serializes matching with legacy imports;
item state, canonical case, execution and receipt commit atomically.
"""
import json
import logging
import threading
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from starlette.responses import JSONResponse
from app.db.session import SessionLocal
from app.models import Project, User, VerifiedImportJob, VerifiedImportItem
from app.schemas.verified_import import VerifiedImport

logger = logging.getLogger(__name__)
_stop = threading.Event()
_thread = None


def process_one(item_id, factory=SessionLocal):
    attempt = None
    with factory() as db:
        try:
            item = db.get(VerifiedImportItem, item_id)
            if not item or item.status != "pending" or item.next_attempt_at > datetime.utcnow():
                return False
            job = db.get(VerifiedImportJob, item.job_id)
            # Always acquire project before item, in the same order as legacy import.
            db.execute(update(Project).where(Project.id == job.project_id).values(id=Project.id, updated_at=Project.updated_at))
            db.refresh(item, with_for_update=True)
            if item.status != "pending" or item.next_attempt_at > datetime.utcnow():
                db.rollback()
                return False
            attempt = item.attempts
            item.status = "processing"  # uncommitted; crash restores pending automatically
            item.attempts += 1
            db.flush()
            user = db.get(User, job.user_id)
            if not user or user.status.value != "active":
                raise HTTPException(403, "提交账号已禁用或不存在")
            source = json.loads(job.payload)
            case = source["cases"][item.position]
            case["resolution"] = json.loads(item.resolution) if item.resolution else None
            source["cases"] = [case]
            # Per-item stable identity. All writes, including receipt, share this transaction.
            source["external_id"] = f"queued-{job.id}-item-{item.position}"
            body = VerifiedImport.model_validate(source)
            from app.api.verified_import import perform_import
            result = perform_import(body, db, user, commit=False)
            if isinstance(result, JSONResponse):
                plan = json.loads(result.body)["data"]["cases"][0]
                item.plan = json.dumps(plan, ensure_ascii=False)
                item.status = "needs_confirmation"
                item.error = plan.get("conflict")
            else:
                receipt = result["data"]
                item.receipt = json.dumps({**receipt["records"][0], "batch_id": receipt["batch_id"]}, ensure_ascii=False)
                item.status = "done"
                item.error = None
                # Audit the actual submitter and administrator separately in the run snapshot.
                from app.models import ExecRun
                run = db.get(ExecRun, receipt["records"][0]["run_id"])
                payload = json.loads(run.payload)
                payload["verified_import"].update({"external_id": job.external_id, "job_id": job.id,
                    "item_id": item.id, "resolved_by": item.resolved_by})
                run.payload = json.dumps(payload, ensure_ascii=False)
            db.commit()
            return True
        except Exception as error:
            db.rollback()
            logger.exception("Background verified import failed item=%s", item_id)
            if attempt is None:
                return False
            transient = isinstance(error, OperationalError)
            # Do not expose SQL, connection details, or raw evidence in the public error field.
            message = str(error.detail)[:2000] if isinstance(error, HTTPException) else (
                "数据库暂时不可用，稍后自动重试" if transient else "后台处理失败，请管理员检查服务日志后重试")
            status = "pending" if transient and attempt < 2 else "failed"
            try:
                db.query(VerifiedImportItem).filter_by(id=item_id, status="pending", attempts=attempt).update(
                    {"status": status, "attempts": attempt + 1, "error": message,
                     "next_attempt_at": datetime.utcnow() + timedelta(seconds=15 * (2 ** attempt))}, synchronize_session=False)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Cannot persist import retry state item=%s", item_id)
            return False


def drain_once(factory=SessionLocal):
    with factory() as db:
        ids = [row[0] for row in db.query(VerifiedImportItem.id).filter(
            VerifiedImportItem.status == "pending", VerifiedImportItem.next_attempt_at <= datetime.utcnow()
        ).order_by(VerifiedImportItem.next_attempt_at, VerifiedImportItem.id).limit(20).all()]
    for item_id in ids:
        process_one(item_id, factory)
    return len(ids)


def _loop():
    while not _stop.is_set():
        try:
            drain_once()
        except Exception:
            logger.exception("Cannot poll durable import queue")
        _stop.wait(2)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="verified-import-worker", daemon=True)
    _thread.start()


def stop():
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
