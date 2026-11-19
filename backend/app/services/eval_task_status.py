"""Synchronize task execution status from its current batch, independently of reports."""
from sqlalchemy import case, update

from app.core.enums import EvalRunStatus, EvalTaskStatus
from app.models import EvalRun, EvalTask


def reconcile_task_status(db, task):
    """Close a finished current batch; the caller owns the transaction.

    Judging happens after execution. It must not keep the task executing.
    Conditions are checked in the UPDATE too, so a late read cannot close a new
    batch, override a manual stop, or close a batch that has been retried.
    """
    if task.status != EvalTaskStatus.running or not task.last_batch_id:
        return False
    batch_id = task.last_batch_id
    batch = db.query(EvalRun.id).filter(
        EvalRun.eval_task_id == task.id, EvalRun.batch_id == batch_id)
    unfinished = batch.filter(EvalRun.status.notin_([
        EvalRunStatus.done, EvalRunStatus.judging, EvalRunStatus.judged,
        EvalRunStatus.failed, EvalRunStatus.cancelled,
    ])).exists()
    completed = batch.filter(EvalRun.status != EvalRunStatus.cancelled).exists()
    result = db.execute(update(EvalTask).where(
        EvalTask.id == task.id, EvalTask.last_batch_id == batch_id,
        EvalTask.status == EvalTaskStatus.running, batch.exists(), ~unfinished,
    ).values(status=case((completed, EvalTaskStatus.done.value), else_=EvalTaskStatus.stopped.value))
      .execution_options(synchronize_session=False))
    if result.rowcount:
        db.expire(task, ['status', 'updated_at'])
    return bool(result.rowcount)
