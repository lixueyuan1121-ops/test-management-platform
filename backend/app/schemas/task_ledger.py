from datetime import date

from pydantic import BaseModel, Field, field_validator


class TaskSelection(BaseModel):
    project_id: int
    task_ids: list[int] = Field(min_length=1, max_length=5000)


class DigestGenerate(TaskSelection):
    report_date: date
    filter_summary: str = Field(default="", max_length=2000)


class DigestSave(DigestGenerate):
    id: int | None = None
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=60000)

    @field_validator("title", "content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("标题和正文不能为空")
        return value


class TaskReleaseCreate(TaskSelection):
    release_id: int | None = None
    version: str = Field(min_length=1, max_length=64)
    release_date: date
    sub_product: str | None = None
    channel: list[str] | None = None
    content: str = Field(min_length=1, max_length=50000)
    memo: str | None = Field(default=None, max_length=10000)
