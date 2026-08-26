from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.services.selector_ranking import is_valid_candidate


def _validate_candidates(v: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for i, c in enumerate(v or []):
        if not is_valid_candidate(c):
            raise ValueError(
                f"候选[{i}]非法:须含 by(testid/role/label/text/placeholder/css)且 value 非空"
            )
    return v


_PLATFORMS = ("web", "android", "ios")


def _validate_platform(v: str) -> str:
    if v not in _PLATFORMS:
        raise ValueError(f"platform 须为 {_PLATFORMS} 之一")
    return v


class SelectorKeyIn(BaseModel):
    project_id: int
    sub_product: str = ""
    # platform: web(PC端) / android / ios；默认 web 保持向后兼容。
    platform: str = "web"
    key: str = Field(min_length=1, max_length=64)
    frame: str = "auto"
    page: str = ""
    desc: str = ""
    candidates: list[dict[str, Any]] = []

    @field_validator("platform")
    @classmethod
    def _v_platform(cls, v):
        return _validate_platform(v)

    @field_validator("candidates")
    @classmethod
    def _v_candidates(cls, v):
        return _validate_candidates(v)


class SelectorKeyPatch(BaseModel):
    platform: str | None = None
    frame: str | None = None
    page: str | None = None
    desc: str | None = None
    candidates: list[dict[str, Any]] | None = None

    @field_validator("platform")
    @classmethod
    def _v_platform(cls, v):
        return v if v is None else _validate_platform(v)

    @field_validator("candidates")
    @classmethod
    def _v_candidates(cls, v):
        return v if v is None else _validate_candidates(v)


class SelectorScopeIn(BaseModel):
    project_id: int
    sub_product: str = ""
    vm_iframe: str = ""
