"""性能测试模块入参 schema。"""
from typing import Any

from pydantic import BaseModel, Field


class PerfDispatchIn(BaseModel):
    """下发一个性能采集任务到指定执行机(source=dispatch)。

    注意：只有"长监控 --duration / cron"类场景能真正无人值守执行；交互场景
    (冷启动/对话/热启动等)需人工按回车，建议走本地采集 + upload。
    scenario 不做枚举强校验(允许自定义场景名)。
    """
    project_id: int | None = None
    report_set_id: int | None = None
    runner: str = Field("win-01", max_length=64)
    scenario: str = Field(..., min_length=1, max_length=32)
    variant: str = Field("default", max_length=64)
    proc: str | None = Field(None, max_length=128)
    duration: str | None = Field(None, max_length=16)


class PerfReportIn(BaseModel):
    """runner 回写 dispatch 任务的采集结果(PATCH /queue/{id})。"""
    outcome: str | None = None                     # completed/failed/timeout/interrupted
    meta: dict[str, Any] | None = None             # 完整 meta.json
    samples: list[dict[str, Any]] | None = None    # 抽稀时序 [{t,source,metric,value}]
    events: list[dict[str, Any]] | None = None     # 阶段标记 [{t,phase,detail}]
    error: str | None = None


class PerfUploadIn(BaseModel):
    """本地采集结果一次性直传(source=upload)——绕过队列，给交互场景用。

    scenario/variant 由 agent 从 meta.json 提取后显式传入（后端不猜目录名）。
    """
    runner: str = Field("win-01", max_length=64)
    project_id: int | None = None
    report_set_id: int | None = None
    scenario: str = Field(..., min_length=1, max_length=32)
    variant: str = Field("default", max_length=64)
    proc: str | None = Field(None, max_length=128)
    duration: str | None = Field(None, max_length=16)
    outcome: str | None = None
    meta: dict[str, Any]
    samples: list[dict[str, Any]] | None = None
    events: list[dict[str, Any]] | None = None


class PerfReportSetIn(BaseModel):
    """新建/重命名报告集。"""
    name: str = Field(..., min_length=1, max_length=128)


class PerfThresholdsIn(BaseModel):
    """设置报告集性能红线:{metricKey: {max?: n, min?: n}};传 {} 清空(关闭告警)。

    metricKey 白名单与结构校验在端点内做(依赖 perf_guard.METRIC_DEFS,schema 保持薄)。
    """
    thresholds: dict = Field(default_factory=dict)


class PerfPromptIn(BaseModel):
    """agent 上报 perfdog 当前提示行（交互采集控制用）。prompt 为 null 表示清除待办。"""
    prompt: str | None = None
