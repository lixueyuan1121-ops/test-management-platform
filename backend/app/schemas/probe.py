from typing import Any

from pydantic import BaseModel


class ProbeStartIn(BaseModel):
    """网页侧发起一次设备探测。project_id 走体外鉴权（assert_project_role）。"""
    project_id: int
    sub_product: str = ""
    runner: str
    params: dict[str, Any] = {}


class ProbeReportIn(BaseModel):
    """runner 回写探测结果：有 result → done，否则按 error → failed。"""
    result: dict[str, Any] | None = None
    error: str | None = None
