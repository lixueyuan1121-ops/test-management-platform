"""md 任务模块入参 schema。"""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints


class MdRunDocument(BaseModel):
    """前端下发：上传/粘贴一份提测 md，指定执行机与环境分组。

    content 是 md 原文（含 frontmatter 也行，后端不解析 frontmatter，
    只从显式字段取 req_no/env_group/client_version —— 避免两处真相）。
    """
    project_id: int | None = None
    task_id: int | None = None
    req_no: str | None = Field(None, max_length=64)
    title: str = Field(..., min_length=1, max_length=255)
    env_group: str = Field(..., min_length=1, max_length=64)
    client_version: str | None = Field(None, max_length=64)
    content: str = Field(..., min_length=1, max_length=400_000)
    source: str = Field("page", max_length=8)


RunnerId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class MdRunCreate(MdRunDocument):
    runner: RunnerId


class MdRunBatchCreate(MdRunDocument):
    runners: list[RunnerId] = Field(min_length=1)
    env_group: Literal["online", "pre", "test"]


class MdEvidenceSummary(BaseModel):
    expected: int = Field(ge=0, le=32)
    notes: list[Annotated[str, StringConstraints(max_length=300)]] = Field(default_factory=list, max_length=35)


class MdRunReportIn(BaseModel):
    """runner 回写（PATCH /queue/{id}）。

    completed 表示执行机正常结束，由服务端解析 result_md。兼容直接回写 pass / fail / blocked。
    findings: 结构化缺陷列表，供人工复核后一键上报极库云（不自动报，避免误报污染缺陷库）。
    """
    verdict: Literal["completed", "pass", "fail", "blocked"]
    reason: str | None = Field(None, max_length=4000)
    result_md: str | None = Field(None, max_length=400_000)
    findings: list[dict[str, Any]] | None = None
    evidence_summary: MdEvidenceSummary | None = None
    duration_ms: int | None = None
    env_check: dict[str, Any] | None = None


class MdRunBugsIn(BaseModel):
    """人工确认后把 findings 上报极库云。

    indexes: 只报这几条（findings 的下标）；不传=报全部尚未上报的。
    """
    indexes: list[int] | None = None
    executor_email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254,
                                                    pattern=r"^[^\s@]+@[^\s@]+$")] | None = None
    receipt_action: Literal["check", "link", "retry", "attachments"] | None = None
    review_key: str | None = Field(None, max_length=64)
    matter_id: int | None = Field(None, gt=0)
