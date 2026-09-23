import math

from pydantic import BaseModel, Field, field_validator


class EvalEnqueueIn(BaseModel):
    project_id: int
    # 用例库下发时创建关联任务；旧 runner/API 调用不传则保持原行为。
    task_name: str | None = Field(None, min_length=1, max_length=128)
    runner: str = Field("mac-01", max_length=64)
    target_engine: str = Field("namiwork", max_length=32)
    target_device: str | None = Field(None, max_length=64)
    eval_query_ids: list[int] = Field(..., min_length=1)
    # 下发时统一指定的对话选项 {model?,chatMode?,thinkingDepth?}；None/空 = 用题面存量（通常为空=客户端默认）
    dialog_options: dict | None = None
    trial_count: int = Field(1, ge=1, le=5, strict=True)

    @field_validator("task_name")
    @classmethod
    def validate_task_name(cls, value):
        if value is not None and not value.strip():
            raise ValueError("测评任务名称不能为空")
        return value.strip() if value is not None else None


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
    raw_message: str | None = None  # WorkBuddy「复制 message」原始结构化 JSON（供后续分析）
    reported_duration: str | None = None
    bean_cost: str | None = None
    tokens: str | None = None
    session_id: str | None = None
    reason: str | None = None
    duration_ms: int | None = None

    @field_validator("reported_duration", "bean_cost", "tokens", mode="before")
    @classmethod
    def normalize_numeric_metrics(cls, value):
        # 兼容已分发的 QWork runner 数值上报；不影响纳米/WorkBuddy 原有字符串格式。
        # 不使用全局 coerce_numbers_to_str，其他文本字段仍需通过原有类型校验。
        if type(value) in (int, float):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("指标必须是有限数值")
            return str(value)
        return value
