from pydantic import BaseModel, Field


class EnqueueExecIn(BaseModel):
    """「发送到本地执行」：把若干验收清单项下发到指定 runner 执行。

    project_id 走体外鉴权（assert_project_role）；checklist_item_ids 里每项都会校验
    存在、属于该项目，然后据其关联 test_case 组装 payload 快照并入队。
    """
    project_id: int
    runner: str = Field("mac-01", max_length=64)
    checklist_item_ids: list[int] = Field(..., min_length=1)


class EnqueueCasesIn(BaseModel):
    """回归执行:直接按用例 id 下发,不经验收清单(不依赖任务/采纳)。

    project_id 走体外鉴权;test_case_ids 每项校验存在、属于该项目、非 manual,
    然后据用例组装 payload 快照入队(ExecRun.checklist_item_id=None,回写不回流清单)。
    """
    project_id: int
    runner: str = Field("mac-01", max_length=64)
    test_case_ids: list[int] = Field(..., min_length=1)


class ExecReportIn(BaseModel):
    """runner 回写结果。verdict 用 runner 契约的 pass/fail；平台映射到 passed/failed。"""
    verdict: str  # "pass" | "fail"
    reason: str | None = None
    evidence_url: str | None = None
    duration_ms: int | None = None
