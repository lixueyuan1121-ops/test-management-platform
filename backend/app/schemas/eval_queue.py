from pydantic import BaseModel, Field


class EvalEnqueueIn(BaseModel):
    project_id: int
    runner: str = Field("mac-01", max_length=64)
    target_engine: str = Field("namiwork", max_length=32)
    target_device: str | None = Field(None, max_length=64)
    eval_query_ids: list[int] = Field(..., min_length=1)
    # 下发时统一指定的对话选项 {model?,chatMode?,thinkingDepth?}；None/空 = 用题面存量（通常为空=客户端默认）
    dialog_options: dict | None = None


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
