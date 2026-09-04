"""失败聚类去噪自测。跑法：cd backend && .venv/bin/python -m scripts.test_fail_cluster"""
import os, sys
os.environ["DATABASE_URL"] = "sqlite:///./tmp_test_fail_cluster.db"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp_test_fail_cluster.db")
if os.path.exists(_DB):
    os.remove(_DB)

from app.main import app  # noqa: F401  触发模型注册
from app.db.session import Base, engine
from app.models import FailCluster


def test_table_created():
    Base.metadata.create_all(engine)
    assert "fail_cluster" in Base.metadata.tables
    cols = set(FailCluster.__table__.c.keys())
    for c in ("id", "project_id", "release_id", "root_cause_title", "summary",
              "triage_kind", "fingerprint", "run_ids", "requirement_ids",
              "member_count", "severity", "confidence", "issue_id",
              "batch_key", "created_at"):
        assert c in cols, f"缺字段 {c}"


def main():
    test_table_created()
    print("OK test_fail_cluster")


if __name__ == "__main__":
    main()
