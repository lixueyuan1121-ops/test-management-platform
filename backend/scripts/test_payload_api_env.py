"""_payload_of 注入 api_env 自测。运行: cd backend && python -m scripts.test_payload_api_env

注:TestCase.ai_task_id 为 NOT NULL 无默认,构造时补 ai_task_id=1(SQLite 默认不强制 FK,
无需建 ai_task 行);核心断言为 api 用例注入 api_env、gui 用例不注入。"""
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.session import Base
from app.models.api_env import ApiEnv
from app.models.ai import TestCase
from app.api.exec_queue import _payload_of


def main():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(ApiEnv(project_id=7, base_url="https://svc", auth_type="fixed",
                     auth_json=json.dumps({"headers": {"Authorization": "Bearer t"}})))
        # api 用例 → 带 api_env 快照
        tc_api = TestCase(
            ai_task_id=1, project_id=7, title="api 用例", exec_kind="api",
            script=json.dumps([{"name": "x", "request": {"method": "GET", "path": "/a"},
                                "asserts": [{"type": "status", "op": "eq", "value": 200}]}]),
        )
        s.add(tc_api)
        s.flush()
        p = _payload_of(tc_api, s)
        assert p["api_env"]["base_url"] == "https://svc", p
        assert p["api_env"]["auth"]["headers"]["Authorization"] == "Bearer t", p
        assert p["api_env"]["auth_type"] == "fixed", p
        assert isinstance(p["script"], list) and p["script"], "script 应解析为非空数组"
        # gui 用例 → 不带 api_env
        tc_gui = TestCase(ai_task_id=1, project_id=7, title="gui 用例", exec_kind="gui")
        s.add(tc_gui)
        s.flush()
        assert "api_env" not in _payload_of(tc_gui, s), "gui 用例不应注入 api_env"
        # api 用例但项目无 api_env 配置 → api_env 快照各字段兜底(空串/fixed/空dict)
        tc_api2 = TestCase(ai_task_id=1, project_id=999, title="无配置的 api 用例", exec_kind="api")
        s.add(tc_api2)
        s.flush()
        p2 = _payload_of(tc_api2, s)
        assert p2["api_env"] == {"base_url": "", "auth_type": "fixed", "auth": {}}, p2
    print("OK test_payload_api_env")


if __name__ == "__main__":
    main()
