from typing import Any
import math

from pydantic import BaseModel, Field, field_validator


class RecordStartIn(BaseModel):
    project_id: int
    sub_product: str = ""
    runner: str
    runner_device_id: int | None = None


class RecordEventsIn(BaseModel):
    """runner 增量上报捕获事件(每项为一步操作,见 record_session.events schema)。"""
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    consumer_id: str = Field(min_length=1, max_length=64)
    final: bool = False

    @field_validator("events")
    @classmethod
    def valid_timestamps(cls, events):
        for event in events:
            ts = event.get("ts", 0)
            if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts) or ts < 0:
                raise ValueError("事件 ts 须为非负有限时间戳")
        return events


class RecordSaveIn(BaseModel):
    """保存为用例:标题 + 关联任务必填;events 可传前端复审后的编辑版(不传则用库里已录的)。"""
    title: str = Field(min_length=1, max_length=200)
    task_id: int = Field(..., description="关联任务(必填):录制生成的 e2e 用例须归属某任务")
    precondition: str | None = None
    events: list[dict[str, Any]] | None = None
