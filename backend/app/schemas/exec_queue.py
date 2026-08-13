from pydantic import BaseModel, Field


class EnqueueExecIn(BaseModel):
    """「发送到本地执行」：把若干验收清单项下发到指定 runner 执行。

    project_id 走体外鉴权（assert_project_role）；checklist_item_ids 里每项都会校验
    存在、属于该项目，然后据其关联 test_case 组装 payload 快照并入队。
    """
    project_id: int
    runner: str = Field("mac-01", max_length=64)
    checklist_item_ids: list[int] = Field(..., min_length=1)


class ExecReportIn(BaseModel):
    """runner 回写结果。verdict 用 runner 契约的 pass/fail；平台映射到 passed/failed。"""
    verdict: str  # "pass" | "fail"
    reason: str | None = None
    evidence_url: str | None = None
    duration_ms: int | None = None
