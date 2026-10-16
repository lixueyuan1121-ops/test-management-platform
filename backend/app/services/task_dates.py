"""Shared daily/carryover visibility for task rows and checklist summaries."""
from sqlalchemy import func, or_
from fastapi import HTTPException

from app.core.enums import TaskStatus
from app.models import Task


def filter_task_dates(query, dates, start_date=None, end_date=None):
    if start_date is not None or end_date is not None:
        if start_date is None or end_date is None or start_date > end_date:
            raise HTTPException(422, '请提供完整日期范围，开始日期不能晚于结束日期')
        if dates:
            raise HTTPException(422, '日期范围不能与单日或多日期参数同时使用')
        # Explicit range is an assignment-date filter, not a daily carryover view.
        return query.filter(Task.assigned_date.between(start_date, end_date))
    if not dates:
        return query
    # Union of each selected day's tasks; querying Task directly keeps IDs unique.
    return query.filter(or_(
        Task.assigned_date.in_(dates),
        (Task.assigned_date < max(dates)) & Task.status.notin_(
            [TaskStatus.online, TaskStatus.closed]
        ),
        func.date(Task.online_at).in_(dates),
        func.date(Task.closed_at).in_(dates),
    ))
