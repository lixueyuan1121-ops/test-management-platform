from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.enums import TaskPriority, TaskStatus


class TaskLedgerFields(BaseModel):
    risk_level: Literal['green', 'yellow', 'red'] | None = None
    target_version: str | None = Field(default=None, max_length=64)
    progress_note: str | None = Field(default=None, max_length=10000)
    expected_completion: date | None = None
    next_action: str | None = Field(default=None, max_length=10000)
    action_owner: str | None = Field(default=None, max_length=128)
    feedback_due_at: datetime | None = None
    affects_release: bool | None = None
    conclusion: str | None = Field(default=None, max_length=10000)

    @field_validator('target_version', 'progress_note', 'next_action', 'action_owner', 'conclusion', mode='before')
    @classmethod
    def trim_optional_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class TaskCreate(TaskLedgerFields):
    project_id: int
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    module: str | None = None
    requirement_url: str | None = None
    developer: str | None = None
    priority: TaskPriority = TaskPriority.p2
    assigned_to: int | None = Field(default=None, gt=0)
    assigned_to_ids: list[int] | None = Field(default=None, max_length=100)
    assigned_date: date


class TaskUpdate(TaskLedgerFields):
    title: str | None = None
    description: str | None = None
    module: str | None = None
    requirement_url: str | None = None
    developer: str | None = None
    priority: TaskPriority | None = None
    assigned_to: int | None = Field(default=None, gt=0)
    assigned_to_ids: list[int] | None = Field(default=None, max_length=100)
    assigned_date: date | None = None
    status: TaskStatus | None = None
    close_note: str | None = None   # 关闭任务时的备注（通常与 status='closed' 一起提交）


class TaskOut(TaskLedgerFields):
    id: int
    project_id: int
    assigned_by: int
    assigned_by_name: str
    assigned_to: int | None
    assigned_to_name: str
    assigned_to_ids: list[int] = Field(default_factory=list)
    title: str
    description: str | None = None
    module: str | None = None
    requirement_url: str | None = None
    developer: str | None = None
    priority: str
    assigned_date: date
    status: str
    online_at: datetime | None = None
    closed_at: datetime | None = None
    close_note: str | None = None
    release_record_id: int | None = None
    created_at: datetime

    source_group_id: str | None = None
    source_message_id: str | None = None
    is_ingested: bool = False

    class Config:
        from_attributes = True


class TaskIngest(BaseModel):
    """转发机器人提交已收集的提测信息；不接受负责人或工作流状态。"""
    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    project_id: int = Field(gt=0)
    source_group_id: Literal['7652669649127481'] = '7652669649127481'
    source_message_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=20000)
    module: str | None = Field(default=None, max_length=128)
    requirement_url: str | None = Field(default=None, max_length=512)
    developer: str | None = Field(default=None, max_length=64)
    priority: TaskPriority = TaskPriority.p2
    assigned_date: date | None = None
    target_version: str | None = Field(default=None, max_length=64)
    expected_completion: date | None = None

    @field_validator('requirement_url')
    @classmethod
    def validate_requirement_url(cls, value):
        from urllib.parse import urlsplit
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('需求地址必须为 http/https 链接')
        return value
