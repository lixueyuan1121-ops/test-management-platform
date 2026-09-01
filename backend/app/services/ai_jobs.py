"""AI 任务队列 + 进程内 worker 池(方案2 核心)。

见 docs/superpowers/specs/2026-09-01-ai-job-queue-design.md。本模块负责:
- enqueue:特性端点建 pending job(输入快照),立即返回;唤醒空闲 worker。
- claim_next:worker 用条件 UPDATE 原子抢占最早 pending→running(rowcount==1 才算抢到),
  防多线程双跑(沿用 eval_pipeline 抢占范式)。
- queue_position:同 pending 中排在本 job 前的条数(读时现算,不落列)。
- run_job:另开 session,按 kind→handler 跑;成功置 done+result,失败置 failed+error(不覆盖域数据)。
- worker 池 / 启动收口 / 生命周期:见 Task4。
"""
import json
import logging
import threading
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import AiJob

logger = logging.getLogger("test_platform")

# kind → handler(db, job) -> result dict。各特性模块 import 时 register。
_HANDLERS: dict = {}


def register_handler(kind: str, fn) -> None:
    _HANDLERS[kind] = fn


# ── 入队 ─────────────────────────────────────────────────────────────────────────

def enqueue(db: Session, kind: str, *, provider: str | None = None,
            project_id: int | None = None, user_id: int | None = None,
            input: dict | None = None, ref_kind: str | None = None,
            ref_id: int | None = None) -> AiJob:
    """建一条 pending job(输入快照 json.dumps),commit,唤醒 worker。返回该 job。"""
    job = AiJob(
        kind=kind,
        provider=(provider or "claude"),
        status="pending",
        project_id=project_id,
        user_id=user_id,
        input=json.dumps(input or {}, ensure_ascii=False),
        ref_kind=ref_kind,
        ref_id=ref_id,
    )
    db.add(job); db.commit(); db.refresh(job)
    notify_new_job()
    return job


# ── 抢占 / 排队 ───────────────────────────────────────────────────────────────────

def claim_next(db: Session, worker: str | None = None) -> AiJob | None:
    """原子抢占最早的一条 pending → running。返回抢到的 job,无则 None。

    条件 UPDATE(WHERE id=? AND status='pending')+rowcount 判定,防多 worker 双跑。
    """
    row = (db.query(AiJob.id)
           .filter(AiJob.status == "pending")
           .order_by(AiJob.created_at.asc(), AiJob.id.asc())
           .first())
    if not row:
        return None
    job_id = row[0]
    res = db.execute(
        update(AiJob)
        .where(AiJob.id == job_id, AiJob.status == "pending")
        .values(status="running", worker=(worker or threading.current_thread().name),
                claimed_at=datetime.now())
    )
    db.commit()
    if res.rowcount != 1:
        return None  # 被其他 worker 抢先
    return db.get(AiJob, job_id)


def queue_position(db: Session, job: AiJob) -> int:
    """本 job 在 pending 队列里前面还有几条(running/done/… 返回 0)。

    按 id 计数(id 自增,与入队顺序一致;避开 created_at 秒级精度并列的坑)。
    """
    if getattr(job, "status", None) != "pending":
        return 0
    return (db.query(AiJob)
            .filter(AiJob.status == "pending", AiJob.id < job.id)
            .count())


def get_job(db: Session, job_id: int) -> AiJob | None:
    return db.get(AiJob, job_id)


# ── 唤醒(Task4 填实现;此处占位,enqueue 已可调用) ──────────────────────────────────
_wake = threading.Event()


def notify_new_job() -> None:
    _wake.set()
