"""_load_api_contract / _api_contract_block 自测(monkeypatch get_api_env,免真实 DB)。
运行: cd backend && python -m scripts.test_api_contract_inject
"""
import app.services.api_env as api_env_mod
import app.services.claude_runner as cr


def main():
    # project_id 空 → None(不注入)
    assert cr._load_api_contract(None) is None
    assert cr._load_api_contract(0) is None

    # 有契约 → {base_url, contract};注入块含二者
    api_env_mod.get_api_env = lambda db, pid: {
        "base_url": "https://svc.example.com", "auth_type": "fixed", "auth": {},
        "contract": "GET /api/projects 列表\nPOST /api/projects 创建",
    }
    c = cr._load_api_contract(5)
    assert c is not None and c["base_url"] == "https://svc.example.com", c
    assert "POST /api/projects" in c["contract"]
    block = cr._api_contract_block(5)
    assert "svc.example.com" in block, block
    assert "POST /api/projects" in block

    # base_url 与 contract 皆空 → None(视作无契约)
    api_env_mod.get_api_env = lambda db, pid: {"base_url": "", "auth_type": "fixed", "auth": {}, "contract": ""}
    assert cr._load_api_contract(5) is None
    block2 = cr._api_contract_block(5)
    assert "manual" in block2 and "契约" in block2, block2

    # 只有 contract 没 base_url → 仍算有契约(base_url 下发时补)
    api_env_mod.get_api_env = lambda db, pid: {"base_url": "", "auth_type": "fixed", "auth": {},
                                               "contract": "GET /api/x"}
    c3 = cr._load_api_contract(5)
    assert c3 is not None and c3["contract"] == "GET /api/x", c3

    # 项目未配(env=None) → None
    api_env_mod.get_api_env = lambda db, pid: None
    assert cr._load_api_contract(5) is None

    print("OK test_api_contract_inject")


if __name__ == "__main__":
    main()
