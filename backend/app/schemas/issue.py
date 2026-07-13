from datetime import datetime

from pydantic import BaseModel

from app.core.enums import IssueSeverity, IssueStatus


class IssueUpdate(BaseModel):
    status: IssueStatus | None = None
    owner: int | None = None
    external_ref: str | None = None


class IssueOut(BaseModel):
    id: int
    report_id: int
    project_id: int
    title: str
    description: str | None = None
    severity: str
    status: str
    owner: int | None = None
    owner_name: str = ""
    external_ref: str | None = None
    task_title: str = ""
    created_at: datetime
    resolved_at: datetime | None = None

    class Config:
        from_attributes = True
