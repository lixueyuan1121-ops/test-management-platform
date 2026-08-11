from pydantic import BaseModel, Field

from app.core.enums import AiInputType, ReviewStatus


class TestCaseGenIn(BaseModel):
    project_id: int
    task_id: int | None = None
    input_type: AiInputType = AiInputType.text
    requirement: str = Field(min_length=1, max_length=20000)  # M1：需求正文（url/file 由前端取文后填此字段）


class TestCaseReviewIn(BaseModel):
    review_status: ReviewStatus


class ExtractUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
