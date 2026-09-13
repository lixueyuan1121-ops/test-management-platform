from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.requirement_analysis import RequirementAnalyzeIn


class MissionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    project_id: int
    goal: str = Field(min_length=3, max_length=4000)
    analysis_id: int | None = None
    requirement: RequirementAnalyzeIn | None = None
    max_cases: int = Field(20, ge=1, le=50)

    @model_validator(mode="after")
    def source(self):
        if bool(self.analysis_id) == bool(self.requirement):
            raise ValueError("请选择已有需求分析或导入一份新需求")
        if self.requirement and self.requirement.project_id != self.project_id:
            raise ValueError("需求必须属于当前项目")
        return self


class MissionDecision(BaseModel):
    revision: int
    action: str = Field(pattern="^(approve|pause|resume|replan|finish)$")
    runner: str = Field("", max_length=64)
    max_retries: int = Field(0, ge=0, le=1)
    time_budget_minutes: int = Field(60, ge=5, le=240)
    case_ids: list[int] = Field(default_factory=list, max_length=50)
    reviewed: bool = False
    note: str = Field("", max_length=2000)
