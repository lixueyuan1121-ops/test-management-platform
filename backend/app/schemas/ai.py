from pydantic import BaseModel, Field, model_validator

from app.core.enums import AiInputType, ExecKind, ReviewStatus


class TestCaseGenIn(BaseModel):
    project_id: int
    task_id: int | None = None
    input_type: AiInputType = AiInputType.text
    provider: str | None = None  # 生成引擎 claude/deepseek/...；空/非法由后端 normalize 回落 claude
    requirement: str = Field(min_length=1, max_length=20000)  # M1：需求正文（url/file 由前端取文后填此字段）
    pages: list[str] | None = None  # 目标页面(选择器管理里的 page):①收窄注入的 key ②给该批无 key 用例兜底打页面标


class TestCaseReviewIn(BaseModel):
    """编辑一条测试点:评审三态 / 执行类型 / 正文字段,均可选,至少填一个。

    - review_status:采纳/否决/置回待定（含清单回流副作用）。
    - exec_kind:改自动化执行类型。
    - title/steps/expected/category/priority:编辑用例正文（人工修订）。
    """
    review_status: ReviewStatus | None = None
    exec_kind: ExecKind | None = None
    title: str | None = Field(None, min_length=1, max_length=512)
    steps: str | None = None
    expected: str | None = None
    category: str | None = Field(None, max_length=32)
    priority: str | None = Field(None, max_length=8)
    page: str | None = Field(None, max_length=255)  # 关联页面(逗号分隔多页);手动指定用例所属页面
    is_regression: bool | None = None  # 是否纳入回归用例库(单条切换)

    @model_validator(mode="after")
    def _at_least_one(self):
        if all(getattr(self, f) is None for f in
               ("review_status", "exec_kind", "title", "steps", "expected", "category", "priority", "page", "is_regression")):
            raise ValueError("至少提供一个要修改的字段")
        return self


class ExtractUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class BulkRegressionIn(BaseModel):
    """批量标记/取消回归。ids 为用例 id 列表,is_regression 目标值。"""
    ids: list[int] = Field(..., min_length=1)
    is_regression: bool
