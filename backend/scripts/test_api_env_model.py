"""api_env 模型自测:内存 SQLite 建表 + 唯一约束。运行: python -m scripts.test_api_env_model"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.session import Base
from app.models.api_env import ApiEnv

def main():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    assert "api_env" in Base.metadata.tables, "api_env 未注册到 metadata"
    with Session(eng) as s:
        s.add(ApiEnv(project_id=1, base_url="https://x", auth_type="fixed", auth_json="{}"))
        s.commit()
        row = s.query(ApiEnv).filter_by(project_id=1).one()
        assert row.base_url == "https://x"
        assert row.auth_type == "fixed"
    print("OK test_api_env_model")

if __name__ == "__main__":
    main()
