from datetime import date, datetime

from pydantic import BaseModel, Field

from app.core.enums import IssueSeverity, IssueStatus


class IssueItem(BaseModel):
    """日报里附带的遗留问题。提交时整体替换该日报的遗留问题。"""
    id: int | None = None  # 已有时回填
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    severity: IssueSeverity = IssueSeverity.minor
    status: IssueStatus = IssueStatus.open
    owner: int | None = None
    external_ref: str | None = None


class ReportUpsert(BaseModel):
    """成员提交/更新日报。按 (task_id, report_date) upsert。"""
    task_id: int
    report_date: date
    progress_pct: int = Field(ge=0, le=100, default=0)
    is_online: bool = False
    online_time: datetime | None = None
    workload_hours: float = Field(ge=0, default=0)
    summary: str | None = None
    issues: list[IssueItem] = []


class ReportOut(BaseModel):
    id: int
    task_id: int
    user_id: int
    user_name: str
    project_id: int
    report_date: date
    progress_pct: int
    is_online: bool
    online_time: datetime | None = None
    workload_hours: float
    summary: str | None = None
    issues: list[IssueItem] = []
    created_at: datetime

    class Config:
        from_attributes = True
