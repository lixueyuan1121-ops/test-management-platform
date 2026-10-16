from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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
    assigned_to_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)
    assigned_date: date

    @model_validator(mode="after")
    def require_assignee(self):
        if self.assigned_to_ids is None and self.assigned_to is None:
            raise ValueError("至少选择一名指派人")
        return self


class TaskUpdate(TaskLedgerFields):
    title: str | None = None
    description: str | None = None
    module: str | None = None
    requirement_url: str | None = None
    developer: str | None = None
    priority: TaskPriority | None = None
    assigned_to: int | None = Field(default=None, gt=0)
    assigned_to_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)
    assigned_date: date | None = None
    status: TaskStatus | None = None
    close_note: str | None = None   # 关闭任务时的备注（通常与 status='closed' 一起提交）


class TaskOut(TaskLedgerFields):
    id: int
    project_id: int
    assigned_by: int
    assigned_by_name: str
    assigned_to: int
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

    class Config:
        from_attributes = True
