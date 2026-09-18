from pydantic import BaseModel, Field


class EvalExportFeishuIn(BaseModel):
    project_id: int
    sheet_url: str = Field(min_length=1)
    batch_id: str | None = None
    abnormal_only: bool = False
    start_row: int = 2


class EvalPushMulticaIn(BaseModel):
    project_id: int
    batch_id: str | None = None
    run_ids: list[int] | None = Field(default=None, min_length=1, max_length=200)


class EvalMulticaRetestIn(BaseModel):
    project_id: int
    run_ids: list[int] = Field(min_length=1, max_length=200)
    # None 保留各条原产品/模型；指定产品时每条反馈在这些产品各执行一次。
    target_engines: list[str] | None = Field(default=None, min_length=1, max_length=3)
    model: str | None = Field(default=None, max_length=64)
