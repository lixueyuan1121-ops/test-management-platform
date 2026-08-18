"""get_api_env 自测。运行: python -m scripts.test_api_env_service"""
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.session import Base
from app.models.api_env import ApiEnv
from app.services.api_env import get_api_env

def main():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        assert get_api_env(s, 1) is None, "无配置应返回 None"
        s.add(ApiEnv(project_id=1, base_url="https://x", auth_type="fixed",
                     auth_json=json.dumps({"headers": {"Authorization": "Bearer t"}})))
        s.commit()
        env = get_api_env(s, 1)
        assert env["base_url"] == "https://x"
        assert env["auth"]["headers"]["Authorization"] == "Bearer t"
        # auth_json 坏值兜底空 dict
        s.query(ApiEnv).filter_by(project_id=1).one().auth_json = "not-json"
        s.commit()
        assert get_api_env(s, 1)["auth"] == {}
    print("OK test_api_env_service")

if __name__ == "__main__":
    main()
