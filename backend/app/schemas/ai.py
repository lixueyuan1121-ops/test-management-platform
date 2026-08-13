from pydantic import BaseModel, Field, model_validator

from app.core.enums import AiInputType, ExecKind, ReviewStatus


class TestCaseGenIn(BaseModel):
    project_id: int
    task_id: int | None = None
    input_type: AiInputType = AiInputType.text
    requirement: str = Field(min_length=1, max_length=20000)  # M1：需求正文（url/file 由前端取文后填此字段）


class TestCaseReviewIn(BaseModel):
    """编辑一条测试点：评审三态 和/或 执行类型。两者都可选，但至少填一个。

    - 只传 review_status：采纳/否决/置回待定（含清单回流副作用）。
    - 只传 exec_kind：改自动化执行类型（gui/api/cli），不动评审态。
    - 两者都传：一次改完。
    """
    review_status: ReviewStatus | None = None
    exec_kind: ExecKind | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.review_status is None and self.exec_kind is None:
            raise ValueError("review_status 与 exec_kind 至少提供一个")
        return self


class ExtractUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
