from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.ai import REQUIREMENT_MAX_LEN


class ReviewModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


class AcceptanceCriterion(ReviewModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    text: str = Field(min_length=1, max_length=2000)


class AcceptanceRule(ReviewModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    title: str = Field(min_length=1, max_length=300)
    module: str = Field(default="", max_length=200)
    platform: str = Field(default="", max_length=200)
    condition: str = Field(default="", max_length=2000)
    action: str = Field(default="", max_length=2000)
    expected: str = Field(default="", max_length=3000)
    forbidden: str = Field(default="", max_length=2000)
    boundaries: str = Field(default="", max_length=2000)
    evidence: str = Field(default="", max_length=2000)
    source_type: Literal["explicit", "inferred"] = "inferred"
    source_quote: str = Field(default="", max_length=3000)
    source_section: str = Field(default="", max_length=300)
    source_material_ids: list[str] = Field(default_factory=list, max_length=32)
    criteria: list[AcceptanceCriterion] = Field(default_factory=list, max_length=16)
    status: Literal["pending", "confirmed", "excluded"] = "pending"
    review_note: str = Field(default="", max_length=3000)


class ClarificationQuestion(ReviewModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    question: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(default="", max_length=3000)
    options: list[str] = Field(default_factory=list, max_length=8)
    rule_ids: list[str] = Field(default_factory=list, max_length=120)
    blocking: bool = True
    answer: str = Field(default="", max_length=3000)


class RequirementDraft(ReviewModel):
    summary: str = Field(default="", max_length=10000)
    scope: str = Field(default="", max_length=10000)
    out_of_scope: str = Field(default="", max_length=10000)
    flow: str = Field(default="", max_length=10000)
    rules: list[AcceptanceRule] = Field(min_length=1, max_length=120)
    questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=120)

    @model_validator(mode="after")
    def unique_references(self):
        rule_ids = [r.id for r in self.rules]
        criteria = [c.id for r in self.rules for c in r.criteria]
        questions = [q.id for q in self.questions]
        for label, values in (("规则", rule_ids), ("验收条件", criteria), ("问题", questions)):
            if len(set(values)) != len(values):
                raise ValueError(f"{label}编号重复")
        if len(criteria) > 500:
            raise ValueError("单次最多 500 个验收条件，请按独立模块拆分需求")
        for q in self.questions:
            if set(q.rule_ids) - set(rule_ids):
                raise ValueError(f"问题 {q.id} 引用了不存在的规则")
        return self


class RequirementAnalyzeIn(ReviewModel):
    project_id: int
    task_id: int
    provider: str = "claude"
    requirement: str = Field(min_length=1, max_length=REQUIREMENT_MAX_LEN)
    source_url: str = Field(default="", max_length=2048)
    source_title: str = Field(default="", max_length=512)
    input_type: Literal["text", "url", "file"] = "text"
    source_id: int | None = None
    # Warnings from extraction are retained in the source snapshot, outside the editable draft.
    source_warnings: list[str] = Field(default_factory=list, max_length=100)


class RequirementDraftIn(ReviewModel):
    revision: int = Field(ge=1)
    draft: RequirementDraft


class RequirementConfirmIn(ReviewModel):
    revision: int = Field(ge=1)
    source_hash: str
    scope_reviewed: bool
    confirmation_note: str = Field(default="", max_length=5000)
