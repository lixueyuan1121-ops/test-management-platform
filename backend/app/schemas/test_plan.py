"""测试计划模块入参 schema。"""
from pydantic import BaseModel, Field


class MobilePlanConfig(BaseModel):
    mobile_targets: dict[str, str] | None = None
    mobile_builds: dict[str, str] | None = None


class PlanCreateIn(MobilePlanConfig):
    """建测试计划。"""
    seed_mobile_smoke: bool = False
    project_id: int
    name: str = Field(..., max_length=255)
    description: str | None = None
    runner: str = Field("mac-01", max_length=64)


class PlanUpdateIn(MobilePlanConfig):
    """改计划元信息（名/描述/runner）。"""
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    runner: str | None = Field(None, max_length=64)


class PlanCasesIn(BaseModel):
    """计划内用例增删（幂等）。"""
    case_ids: list[int] = Field(..., min_length=1)


class PlanScheduleIn(BaseModel):
    """设置计划的定时执行。cron 5 段（分 时 日 月 周）；enabled 开关。"""
    cron: str | None = Field(None, max_length=64)
    enabled: bool = False


class PlanRunIn(BaseModel):
    """手动整计划执行（可临时换 runner；不传用计划默认）。"""
    mobile_builds: dict[str, str] | None = None
    runner: str | None = Field(None, max_length=64)
