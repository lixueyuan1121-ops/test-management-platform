"""对话式测试指挥官(Commander) 子系统。

对外暴露注册表 API；`from . import caps` 在末尾触发所有能力注册（import 副作用）。
——只要 `import app.services.commander`，全部能力就已就绪；漏了这行则问题静默落空。
"""
from app.services.commander.registry import (
    Capability,
    REGISTRY,
    get_capability,
    list_capabilities,
    register,
)

__all__ = [
    "Capability",
    "REGISTRY",
    "get_capability",
    "list_capabilities",
    "register",
]

# ↓↓↓ 必须放最后：触发 caps 注册（registry 不得反向 import caps，避免循环）。
from app.services.commander import caps  # noqa: E402,F401
