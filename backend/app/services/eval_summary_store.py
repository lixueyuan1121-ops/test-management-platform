"""批次综合评价存储，以及旧任务级字段的兼容读取。"""
from types import SimpleNamespace
from datetime import datetime
import hashlib
import json

from app.models.ai_eval import EvalBatchSummary
from app.models import EvalRun

SUMMARY_FIELDS = ("summary_html", "summary_status", "summary_provider", "summary_at", "summary_share_code")


def read_progress(row):
    try:
        value = json.loads(getattr(row, "summary_progress", None) or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def update_progress(row, *, reset=False, **values):
    progress = {} if reset else read_progress(row)
    progress.update(values, updated_at=datetime.now().isoformat())
    row.summary_progress = json.dumps(progress, ensure_ascii=False)


def batch_revision(db, task_id, batch_id):
    rows = db.query(EvalRun).filter_by(eval_task_id=task_id, batch_id=batch_id).order_by(EvalRun.id).populate_existing().all()
    values = [{c.name: getattr(row, c.name) for c in EvalRun.__table__.columns} for row in rows]
    return hashlib.sha256(json.dumps(values, default=str, sort_keys=True).encode()).hexdigest()


def batch_summary(db, task, batch_id, *, create=False):
    row = db.query(EvalBatchSummary).filter_by(eval_task_id=task.id, batch_id=batch_id).first()
    if row is None and create:
        row = EvalBatchSummary(eval_task_id=task.id, batch_id=batch_id)
        if batch_id == task.last_batch_id and task.summary_status:
            for field in SUMMARY_FIELDS:
                setattr(row, field, getattr(task, field))
            if task.summary_status == "done":
                from app.api.eval_report import _detail_table_html
                row.detail_html = _detail_table_html(db, task)
        db.add(row)
        db.flush()
    return row


def summary_view(db, task, batch_id=None):
    """用于序列化/渲染的只读对象，不改写任务的当前批次。"""
    bid = batch_id or task.last_batch_id
    row = batch_summary(db, task, bid) if bid else None
    source = row or (task if bid == task.last_batch_id and task.summary_status else None)
    fields = {field: getattr(source, field) if source else None for field in SUMMARY_FIELDS}
    progress = read_progress(row)
    # 入队后启动前失败/取消也可恢复；不依赖浏览器保存 job_id。
    if fields["summary_status"] in ("queued", "running") and progress.get("job_id"):
        from app.models import AiJob
        job = db.get(AiJob, progress["job_id"])
        if job is None or job.status in ("failed", "cancelled", "done"):
            fields["summary_status"] = "failed"
            if job and job.updated_at:
                progress["finished_at"] = job.updated_at.isoformat()
            progress["error"] = (job.error if job else None) or "生成任务已结束但未保存报告，请重新生成"
            if job and job.status == "done":
                try:
                    progress["error"] = json.loads(job.result or "{}").get("reason") or progress["error"]
                except (TypeError, ValueError):
                    pass
    status = fields["summary_status"]
    if status in ("done", "failed"):
        progress["stage"] = status
    if status == "failed":
        progress["error"] = progress.get("error") or "生成已中断或失败，请重新生成"
    started = progress.get("queued_at") or progress.get("started_at")
    if started:
        try:
            end_at = progress.get("finished_at") or (progress.get("updated_at") if status in ("done", "failed") else None)
            end = datetime.fromisoformat(end_at) if end_at else datetime.now()
            progress["elapsed_seconds"] = max(0, int((end - datetime.fromisoformat(started)).total_seconds()))
        except (TypeError, ValueError):
            pass
    return SimpleNamespace(
        id=task.id, project_id=task.project_id, name=task.name, last_batch_id=bid,
        detail_html=row.detail_html if row else None,
        summary_progress=progress, **fields,
    )


def sync_current_summary(task, row):
    if task.last_batch_id == row.batch_id:
        for field in SUMMARY_FIELDS:
            setattr(task, field, getattr(row, field))


def invalidate_summary(db, task, batch_id):
    from app.models.ai_eval import EvalSummaryCheckpoint
    db.query(EvalSummaryCheckpoint).filter_by(eval_task_id=task.id, batch_id=batch_id).delete(synchronize_session=False)
    row = batch_summary(db, task, batch_id, create=True)
    row.generation_token = None  # 使已在生成的旧请求无法写回
    for field in SUMMARY_FIELDS:
        if field != "summary_share_code":
            setattr(row, field, None)
    row.detail_html = None
    row.summary_progress = None
    sync_current_summary(task, row)
