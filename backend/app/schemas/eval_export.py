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
