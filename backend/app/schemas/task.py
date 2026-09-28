from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.core.enums import TaskPriority, TaskStatus


class TaskCreate(BaseModel):
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


class TaskUpdate(BaseModel):
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


class TaskOut(BaseModel):
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
    created_at: datetime

    class Config:
        from_attributes = True
