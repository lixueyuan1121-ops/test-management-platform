from pydantic import BaseModel, Field


class EvalEnqueueIn(BaseModel):
    project_id: int
    runner: str = Field("mac-01", max_length=64)
    target_engine: str = Field("namiwork", max_length=32)
    target_device: str | None = Field(None, max_length=64)
    eval_query_ids: list[int] = Field(..., min_length=1)
    # 下发时统一指定的对话选项 {model?,chatMode?,thinkingDepth?}；None/空 = 用题面存量（通常为空=客户端默认）
    dialog_options: dict | None = None


class EvalRetryFailedIn(BaseModel):
    """批量重跑失败入参:run_ids 指定(任务详情用)优先;否则按 batch_id 限定;都空=项目全部 failed。"""
    project_id: int
    batch_id: str | None = None
    run_ids: list[int] | None = None


class EvalReportIn(BaseModel):
    status: str  # "done" | "failed"
    share_link: str | None = None
    artifact_share_link: str | None = None
    answer: str | None = None
    reported_duration: str | None = None
    bean_cost: str | None = None
    tokens: str | None = None
    session_id: str | None = None
    reason: str | None = None
    duration_ms: int | None = None
