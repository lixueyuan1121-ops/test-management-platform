"""对话式测试指挥官(Commander) 能力注册表。

纯编排层：不建新表。每个「能力」是对既有 read/analyze/draft 服务的薄封装，
runner 统一签名 `(db, user, params: dict) -> dict`。

注意：本模块**不得** import caps（避免与 caps 的 `from .registry import ...` 循环）。
注册的触发在 `commander/__init__.py` 末尾 `from . import caps`——`import
app.services.commander` 即完成全部能力注册（同 ai_jobs 的 import 副作用注册范式）。
"""
from dataclasses import dataclass
from typing import Callable


@dataclass
class Capability:
    name: str
    desc: str            # 给 AI 看的能力说明
    params: dict         # {参数名: 说明字符串}
    kind: str            # "read" | "analyze" | "draft"
    runner: Callable     # (db, user, params: dict) -> dict


REGISTRY: dict[str, Capability] = {}


def register(cap: Capability) -> None:
    REGISTRY[cap.name] = cap


def get_capability(name: str) -> Capability | None:
    return REGISTRY.get(name)


def list_capabilities() -> list[dict]:
    return [
        {"name": c.name, "desc": c.desc, "params": c.params, "kind": c.kind}
        for c in REGISTRY.values()
    ]
