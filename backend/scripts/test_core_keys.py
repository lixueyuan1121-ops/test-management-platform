"""L3:核心 key 清单(core_key_set)服务 + 单一事实源一致性 自测。
运行: cd backend && python -m scripts.test_core_keys

覆盖:
  A. core_key_set 从内置 selectors.json 顶层 coreKeys 读出非空集合;
  B. 单一事实源一致性:每个核心 key 都真实存在于 selectors.json 的 registry(避免巡检一个不存在的 key);
  C. 与 runner 侧 core-keys.mjs 同源(读同一份 json,故清单必然一致)——此处校验后端读到的 == json.coreKeys。
"""
import json
import os

from app.services.selectors import core_key_set

_JSON_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "tools", "qalab-runner", "gui-mcp", "selectors.json"))


def _raw():
    with open(_JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_core_key_set_nonempty():
    ck = core_key_set()
    assert isinstance(ck, set) and ck, f"核心 key 清单应非空,实际 {ck}"


def test_core_keys_all_registered():
    """每个核心 key 必须在 registry 里真实存在(单一事实源自洽,巡检不指向幽灵 key)。"""
    raw = _raw()
    reg = raw.get("registry", {})
    for k in core_key_set():
        assert k in reg, f"核心 key「{k}」不在 selectors.json registry 内(清单与注册表脱节)"


def test_core_key_set_matches_json():
    """后端 core_key_set 读到的 == selectors.json 顶层 coreKeys(与 runner core-keys.mjs 同源)。"""
    assert core_key_set() == set(_raw().get("coreKeys", []))


def main():
    test_core_key_set_nonempty()
    test_core_keys_all_registered()
    test_core_key_set_matches_json()
    print("OK test_core_keys")


if __name__ == "__main__":
    main()
