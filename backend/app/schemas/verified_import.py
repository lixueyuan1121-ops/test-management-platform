"""Import contract for externally verified automation."""
import json
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.script_targets import validate_targets

class ImportResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["reuse", "create"]
    case_id: int | None = Field(default=None, gt=0)
    token: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=5, max_length=1000)

    @model_validator(mode="after")
    def target(self):
        if (self.action == "reuse") != (self.case_id is not None):
            raise ValueError("reuse 须指定 case_id，create 不得指定 case_id")
        if len(self.reason.strip()) < 5:
            raise ValueError("请说明复用或独立建例的依据")
        return self


class VerifiedCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution: ImportResolution | None = None
    title: str = Field(min_length=1, max_length=512)
    category: str = Field(default="功能", max_length=32)
    priority: Literal["P0", "P1", "P2", "P3"] = "P1"
    page: str = Field(default="", max_length=255)
    exec_kind: Literal["gui", "e2e", "api"] = "e2e"
    steps: str = Field(min_length=1, max_length=12000)
    expected: str = Field(min_length=1, max_length=12000)
    precondition: str = Field(default="", max_length=4000)
    script: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    report: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    verdict: Literal["pass"]
    executor: str = Field(min_length=1, max_length=120)
    environment: str = Field(min_length=1, max_length=500)
    scope: str = Field(min_length=1, max_length=1000)
    finished_at: datetime
    duration_ms: int = Field(ge=0, le=2147483647)

    @model_validator(mode="after")
    def complete_evidence(self):
        if self.exec_kind in ("gui", "e2e"):
            validate_targets(self.script, self.title)
        if self.finished_at.tzinfo is None:
            raise ValueError("finished_at 必须包含时区")
        if self.finished_at.timestamp() > datetime.now(timezone.utc).timestamp() + 300:
            raise ValueError("不能导入未来执行结果")
        if len(self.script) != len(self.report):
            raise ValueError("须提交完整脚本逐步报告，不能以部分检查代替完整执行")
        for step, result in zip(self.script, self.report):
            if result.get("action") != step.get("action") or result.get("ok") is not True:
                raise ValueError("报告动作须匹配脚本且每步均通过")
            check = result.get("check")
            if str(step.get("action", "")).startswith("assert") and (not isinstance(check, dict) or "actual" not in check or "expected" not in check):
                raise ValueError("断言须包含 actual/expected 证据（通过状态取步骤 ok）")
            if isinstance(check, dict) and check.get("pass") is False:
                raise ValueError("报告包含失败断言")
        for name in ("script", "report", "steps", "expected"):
            if len(json.dumps(getattr(self, name), ensure_ascii=False).encode()) > 60000:
                raise ValueError(f"{name} 超过存储上限")
        return self

DEFAULT_IMPORT_TASK = "codex导入用例"


class VerifiedImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(gt=0)
    runner_device_id: int = Field(gt=0)
    sub_product: str = Field(default="", max_length=32)
    external_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    requirement: str = Field(min_length=1, max_length=512)
    task_name: str = Field(default=DEFAULT_IMPORT_TASK, min_length=1, max_length=255)

    @field_validator("task_name", mode="before")
    @classmethod
    def normalize_task_name(cls, value):
        if value is None:
            return DEFAULT_IMPORT_TASK
        return (value.strip() or DEFAULT_IMPORT_TASK) if isinstance(value, str) else value

    cases: list[VerifiedCase] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_titles(self):
        if len({c.title for c in self.cases}) != len(self.cases):
            raise ValueError("同一批用例标题须唯一")
        return self
