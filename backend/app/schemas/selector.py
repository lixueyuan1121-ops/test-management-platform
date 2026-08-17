from pydantic import BaseModel, Field
from typing import Any


class SelectorKeyIn(BaseModel):
    project_id: int
    sub_product: str = ""
    key: str = Field(min_length=1, max_length=64)
    frame: str = "auto"
    desc: str = ""
    candidates: list[dict[str, Any]] = []


class SelectorKeyPatch(BaseModel):
    frame: str | None = None
    desc: str | None = None
    candidates: list[dict[str, Any]] | None = None


class SelectorScopeIn(BaseModel):
    project_id: int
    sub_product: str = ""
    vm_iframe: str = ""
