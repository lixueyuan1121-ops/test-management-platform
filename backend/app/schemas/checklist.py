from pydantic import BaseModel, Field

from app.core.enums import ChecklistStatus, IssueSeverity


class AttachChecklistIn(BaseModel):
    """手动补挂：把若干已采纳的 test_case 加入某任务清单。"""
    test_case_ids: list[int] = Field(..., min_length=1)


class ChecklistTickIn(BaseModel):
    """勾执行结果。"""
    exec_status: ChecklistStatus


class ChecklistToIssueIn(BaseModel):
    """失败转遗留问题。title 缺省用 test_case.title。"""
    title: str | None = None
    severity: IssueSeverity = IssueSeverity.major
    owner: int | None = None
    external_ref: str | None = None
