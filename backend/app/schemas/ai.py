from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.core.enums import AiInputType, ExecKind, ReviewStatus

# 需求正文（送去 AI 生成的 prompt 正文）长度上限。放宽到 6 万字以支持从网页/飞书/上传文档
# 抓取的长需求（claude/deepseek 上下文足够）。注意：这只约束「送去生成的正文」、不入库；
# 存库的 input_ref 仍单独截断到 20000（MySQL TEXT 列 64KB 上限保护，见 api/ai.py）。
REQUIREMENT_MAX_LEN = 60000


class TestCaseGenIn(BaseModel):
    project_id: int
    task_id: int | None = None
    input_type: AiInputType = AiInputType.text
    provider: str | None = None  # 生成引擎 claude/deepseek/...；空/非法由后端 normalize 回落 claude
    requirement: str = Field(min_length=1, max_length=REQUIREMENT_MAX_LEN)  # M1：需求正文（url/file 由前端取文后填此字段）
    pages: list[str] | None = None  # 目标页面(选择器管理里的 page):①收窄注入的 key ②给该批无 key 用例兜底打页面标


class EvalQueryGenIn(BaseModel):
    project_id: int
    task_id: int | None = None  # 关联需求任务(task.id,可选)
    eval_task_id: int | None = None  # 关联测评任务(EvalTask.id,可选):生成后自动挂进任务用例集
    input_type: AiInputType = AiInputType.text
    provider: str | None = None  # claude/deepseek;空/非法后端 normalize 回落
    requirement: str = Field(min_length=1, max_length=REQUIREMENT_MAX_LEN)  # 需求正文(url/file 由前端取文后填)
    dimensions: list[str] = Field(min_length=1)  # 至少一个对话测评维度


class TestCaseReviewIn(BaseModel):
    """编辑一条测试点:评审三态 / 执行类型 / 正文字段,均可选,至少填一个。

    - review_status:采纳/否决/置回待定（含清单回流副作用）。
    - exec_kind:改自动化执行类型。
    - title/steps/expected/category/priority:编辑用例正文（人工修订）。
    - script:直接编辑结构化步骤数组（按 kind 校验合法性后入库,并按新 script 重推 page）。
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
    script: list[Any] | None = None  # 直接编辑的结构化步骤数组(gui/e2e/api);按 kind 校验后入库

    @model_validator(mode="after")
    def _at_least_one(self):
        if all(getattr(self, f) is None for f in
               ("review_status", "exec_kind", "title", "steps", "expected", "category", "priority", "page", "is_regression", "script")):
            raise ValueError("至少提供一个要修改的字段")
        return self


class ExtractUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class BulkRegressionIn(BaseModel):
    """批量标记/取消回归。ids 为用例 id 列表,is_regression 目标值。"""
    ids: list[int] = Field(..., min_length=1)
    is_regression: bool
