from pydantic import BaseModel, Field


class ModuleEntryIn(BaseModel):
    project_id: int
    sub_product: str = ""
    page: str
    nav_keys: list[str] = Field(default_factory=list)
    ready_key: str = ""
    desc: str = ""


class ModuleEntryOut(BaseModel):
    id: int
    project_id: int
    sub_product: str
    page: str
    nav_keys: list[str]
    ready_key: str
    desc: str
    key_count: int = 0
