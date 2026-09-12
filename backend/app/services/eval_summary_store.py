"""批次综合评价存储，以及旧任务级字段的兼容读取。"""
from types import SimpleNamespace
import hashlib
import json

from app.models.ai_eval import EvalBatchSummary
from app.models import EvalRun

SUMMARY_FIELDS = ("summary_html", "summary_status", "summary_provider", "summary_at", "summary_share_code")


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
    return SimpleNamespace(
        id=task.id, name=task.name, last_batch_id=bid,
        detail_html=row.detail_html if row else None,
        **{field: getattr(source, field) if source else None for field in SUMMARY_FIELDS},
    )


def sync_current_summary(task, row):
    if task.last_batch_id == row.batch_id:
        for field in SUMMARY_FIELDS:
            setattr(task, field, getattr(row, field))


def invalidate_summary(db, task, batch_id):
    row = batch_summary(db, task, batch_id, create=True)
    row.generation_token = None  # 使已在生成的旧请求无法写回
    for field in SUMMARY_FIELDS:
        if field != "summary_share_code":
            setattr(row, field, None)
    row.detail_html = None
    sync_current_summary(task, row)
