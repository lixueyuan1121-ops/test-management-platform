"""对话式测试指挥官(Commander) 能力注册表 + read 能力 自测。

跑法：cd backend && .venv/bin/python -m scripts.test_commander
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./tmp_test_commander.db"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp_test_commander.db")
if os.path.exists(_DB):
    os.remove(_DB)

from app.main import app  # noqa: F401,E402
from app.db.session import Base, engine  # noqa: E402

Base.metadata.create_all(engine)

# 首批 read 能力（薄封装既有服务）。缺任一即视为注册回退，测试失败。
EXPECTED_READ = [
    "list_releases",
    "rts_recommendation",
    "rts_candidates",
    "fail_cluster_list",
    "stats_overview",
    "stats_ai_funnel",
]


def test_import_side_effect_registers():
    """import app.services.commander 必须触发 caps 注册（__init__ 的 import 副作用）。

    若漏了 `from . import caps`，REGISTRY 会是空的、所有问题静默落空——此测专防这条
    （同 ai_jobs 的 import 副作用注册教训）。
    """
    import app.services.commander as commander
    assert commander.REGISTRY, "REGISTRY 为空：caps 未被注册（检查 __init__ 的 `from . import caps`）"
    for name in EXPECTED_READ:
        assert name in commander.REGISTRY, f"缺 read 能力：{name}"


def test_get_capability_hit_and_miss():
    import app.services.commander as commander
    cap = commander.get_capability("list_releases")
    assert cap is not None and cap.name == "list_releases", cap
    assert commander.get_capability("不存在的能力") is None, "未命中应返回 None"


def test_capability_shape():
    """每个 Capability 有 name/desc/params/kind/runner，且首批均为 kind=='read'。"""
    import app.services.commander as commander
    for name in EXPECTED_READ:
        cap = commander.REGISTRY[name]
        assert cap.name == name, cap
        assert isinstance(cap.desc, str) and cap.desc, f"{name} 缺 desc"
        assert isinstance(cap.params, dict), f"{name} 的 params 应为 dict"
        assert cap.kind == "read", f"{name} kind 应为 read，实为 {cap.kind}"
        assert callable(cap.runner), f"{name} runner 应可调用"


def test_list_capabilities():
    import app.services.commander as commander
    items = commander.list_capabilities()
    assert isinstance(items, list) and len(items) >= len(EXPECTED_READ), items
    for it in items:
        assert set(it.keys()) == {"name", "desc", "params", "kind"}, it


def main():
    test_import_side_effect_registers()
    test_get_capability_hit_and_miss()
    test_capability_shape()
    test_list_capabilities()
    print("OK test_commander")


if __name__ == "__main__":
    main()
