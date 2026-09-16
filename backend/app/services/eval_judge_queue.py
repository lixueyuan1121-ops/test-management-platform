"""手动和自动判定共用的持久队列；编排只等待，不另开模型调用线程池。"""
import json
import logging
import time

from sqlalchemy import update

from app.core.config import settings
from app.core.enums import EvalRunStatus
from app.models import AiJob, EvalRun, EvalTask
from app.services import ai_jobs, generators

logger = logging.getLogger("test_platform")
_ACTIVE = ("pending", "running")
_TERMINAL = ("done", "failed", "cancelled")


class BatchSuperseded(RuntimeError):
    """任务已换批；停止旧批编排，不生成覆盖新批的报告。"""


def enqueue_batch(db, project_id, *, run_ids=None, batch_id=None, provider=None, votes=1,
                  user_id=None, pipeline_task_id=None):
    q = db.query(EvalRun.id).filter(EvalRun.project_id == project_id)
    if run_ids is not None:
        q = q.filter(EvalRun.id.in_(run_ids))
    elif batch_id:
        q = q.filter(EvalRun.batch_id == batch_id)
    else:
        q = q.filter(EvalRun.status == EvalRunStatus.done)
    ids = [r[0] for r in q.order_by(EvalRun.id).all()]
    db.rollback()  # 结束读快照，每条入队独立短事务。
    job_ids, skipped, reused = [], [], []
    for run_id in ids:
        try:
            # 同一 run 的入队串行化，涵盖 pending 时重复点击/自动与手动同时触发。
            # 同值 UPDATE 在 SQLite 也取得写锁；MySQL 则取得该行的排他锁。
            db.execute(update(EvalRun).where(EvalRun.id == run_id, EvalRun.project_id == project_id)
                       .values(status=EvalRun.status))
            run = db.get(EvalRun, run_id, populate_existing=True)
            if run is None or run.project_id != project_id:
                skipped.append({"run_id": run_id, "skipped": True, "reason": "执行项不存在"})
                db.rollback()
                continue
            active = (db.query(AiJob).filter(AiJob.kind == "eval_judge",
                       AiJob.ref_kind == "eval_run", AiJob.ref_id == run_id,
                       AiJob.status.in_(_ACTIVE)).order_by(AiJob.id).first())
            st = getattr(run.status, "value", run.status)
            if active and pipeline_task_id is None and active.status == "pending":
                previous = json.loads(active.input or "{}")
                owner = db.get(EvalTask, previous["pipeline_task_id"]) if previous.get("pipeline_task_id") else None
                if previous.get("pipeline_task_id") and (not owner or owner.last_batch_id != previous.get("pipeline_batch_id")):
                    # 人工重判历史批次时，不能复用已经失效、稍后必定跳过的自动 job。
                    cancelled = db.execute(update(AiJob).where(AiJob.id == active.id, AiJob.status == "pending")
                                           .values(status="cancelled", error="旧批自动判定已由人工重判替代"))
                    if cancelled.rowcount == 1:
                        active = None
            if active and st in ("done", "judged", "judging"):
                job_ids.append(active.id)
                reused.append(run_id)
                db.commit()
                continue
            if st not in ("done", "judged"):
                skipped.append({"run_id": run_id, "skipped": True, "reason": f"状态 {st},不判定"})
                db.rollback()
                continue
            inp = {"run_id": run_id, "provider": provider, "votes": max(1, min(5, int(votes or 1)))}
            if pipeline_task_id is not None:
                inp.update(pipeline_task_id=pipeline_task_id, pipeline_batch_id=batch_id)
            job = ai_jobs.enqueue(db, "eval_judge", provider=generators.normalize_provider(provider),
                project_id=project_id, user_id=user_id, input=inp,
                ref_kind="eval_run", ref_id=run_id, commit=False)
            job_ids.append(job.id)
            db.commit()
        except Exception:
            db.rollback()
            raise
    ai_jobs.notify_new_job()
    return {"job_ids": job_ids, "count": len(job_ids), "skipped": skipped, "reused": reused}


def wait_for_batch(session_factory, job_ids, *, pipeline_task_id=None, batch_id=None,
                   timeout=None, poll_interval=0.5):
    """用短 session 轮询整批终态；等待期间不占用 DB 连接或 AI 工作线程。"""
    pending = set(job_ids)
    results = {}
    # 给排队及单条最坏耗时留预算；不以某个模型请求的超时来截断整批。
    if timeout is None:
        timeout = max(600, (len(pending) + 1) * 5 *
                      (settings.AI_TIMEOUT_SECONDS + settings.AI_ACQUIRE_TIMEOUT_SECONDS + 120))
    deadline = time.monotonic() + timeout
    previous_progress = None
    while pending:
        if ai_jobs._stop.is_set():
            raise RuntimeError("服务正在停止，批量判定尚未完成")
        with session_factory() as db:
            if pipeline_task_id is not None:
                task = db.get(EvalTask, pipeline_task_id)
                if not task or task.last_batch_id != batch_id:
                    raise BatchSuperseded("测评任务已切换批次")
            rows = db.query(AiJob).filter(AiJob.id.in_(pending)).all()
            if len(rows) != len(pending):
                raise RuntimeError("判定任务记录缺失，不能生成完整综合评价")
            running = sum(j.status == "running" for j in rows)
            for job in rows:
                if job.status not in _TERMINAL:
                    continue
                if job.status == "done":
                    result = json.loads(job.result or "{}")
                    result = {"run_id": job.ref_id, **result}
                else:
                    result = {"run_id": job.ref_id, "error": job.error or f"判定任务{job.status}"}
                results[job.id] = result
                pending.remove(job.id)
        progress = (len(results), running, len(pending))
        if progress != previous_progress:
            logger.info("批量判定 batch=%s 完成=%d/%d 运行=%d 等待=%d", batch_id,
                        len(results), len(job_ids), running, max(0, len(pending) - running))
            previous_progress = progress
        if pending:
            if time.monotonic() >= deadline:
                raise TimeoutError("批量判定等待超时，已完成结果已保存；尚未生成综合评价")
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
    return [results[job_id] for job_id in job_ids]
