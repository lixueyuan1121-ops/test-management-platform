from pydantic import BaseModel, Field


class EnqueueExecIn(BaseModel):
    """「发送到本地执行」：把若干验收清单项下发到指定 runner 执行。

    project_id 走体外鉴权（assert_project_role）；checklist_item_ids 里每项都会校验
    存在、属于该项目，然后据其关联 test_case 组装 payload 快照并入队。
    release_id 可选:显式挂到某次发版记录 → /releases/quality 按实体聚合该批结果。
    """
    project_id: int
    runner: str = Field("mac-01", max_length=64)
    checklist_item_ids: list[int] = Field(..., min_length=1)
    release_id: int | None = None


class EnqueueCasesIn(BaseModel):
    """回归执行:直接按用例 id 下发,不经验收清单(不依赖任务/采纳)。

    project_id 走体外鉴权;test_case_ids 每项校验存在、属于该项目、非 manual,
    然后据用例组装 payload 快照入队(ExecRun.checklist_item_id=None,回写不回流清单)。
    release_id 可选:上线 checklist 回归时挂到目标发版 → 质量卡实体级统计。
    """
    project_id: int
    runner: str = Field("mac-01", max_length=64)
    test_case_ids: list[int] = Field(..., min_length=1)
    release_id: int | None = None


class ExecReportIn(BaseModel):
    """runner 回写结果。verdict 用 runner 契约的 pass/fail;平台按 fail_kind 映射 passed/failed/blocked。"""
    verdict: str  # "pass" | "fail"
    fail_kind: str | None = None  # selector(选择器/环境阻塞->blocked) | business(功能失败->failed);pass 时 None
    reason: str | None = None
    evidence_url: str | None = None
    duration_ms: int | None = None
    report: list | dict | None = None  # 逐步执行报告(每步 action/desc/ok/截图 URL + 结论);gui/e2e 由 runner 回写


class ExecCorrectIn(BaseModel):
    """人工纠偏执行结果(用户 JWT,非 runner)。verdict 三态 pass/fail/blocked;可选备注。

    与 runner 回写的区别:这是人对机器判定的复核修正,reason 会被打上「[人工纠偏]」前缀留痕。
    fail_kind 由 verdict 推定(fail→business 真 bug、blocked→selector 环境阻塞、pass→None),
    与 report 端点的映射保持一致。
    """
    verdict: str = Field(..., pattern="^(pass|fail|blocked)$")
    reason: str | None = Field(None, max_length=2000)
