"""反馈测试模块入参 schema。project_id 不在体里——反馈用例固定属专用项目，由端点内部定位。"""
from pydantic import BaseModel, Field


class CaseUpdateIn(BaseModel):
    """编辑反馈用例（仅传要改的字段）。"""
    title: str | None = Field(None, max_length=512)
    precondition: str | None = None
    steps: str | None = None
    expected: str | None = None
    category: str | None = Field(None, max_length=16)
    priority: str | None = Field(None, max_length=8)
    exec_kind: str | None = Field(None, pattern="^(gui|api|cli|e2e|manual)$")


class RunCasesIn(BaseModel):
    """③ 勾选反馈用例直接执行（ad-hoc，不入集）。"""
    case_ids: list[int] = Field(..., min_length=1)
    runner: str = Field("mac-01", max_length=64)


class SetCreateIn(BaseModel):
    """建回归用例集。"""
    name: str = Field(..., max_length=255)
    description: str | None = None
    runner: str = Field("mac-01", max_length=64)


class SetUpdateIn(BaseModel):
    """改集元信息（名/描述/runner）。"""
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    runner: str | None = Field(None, max_length=64)


class SetCasesIn(BaseModel):
    """集内用例增删（幂等）。"""
    case_ids: list[int] = Field(..., min_length=1)


class ScheduleIn(BaseModel):
    """设置集的定时回归。cron 5 段（分 时 日 月 周）；enabled 开关。"""
    cron: str | None = Field(None, max_length=64)
    enabled: bool = False
