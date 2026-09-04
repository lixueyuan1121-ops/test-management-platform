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
from app.services import fail_cluster as fc


def test_table_created():
    Base.metadata.create_all(engine)
    assert "fail_cluster" in Base.metadata.tables
    cols = set(FailCluster.__table__.c.keys())
    for c in ("id", "project_id", "release_id", "root_cause_title", "summary",
              "triage_kind", "fingerprint", "run_ids", "requirement_ids",
              "member_count", "severity", "confidence", "issue_id",
              "batch_key", "created_at"):
        assert c in cols, f"缺字段 {c}"


def test_normalize_reason():
    # 行号/毫秒数/十六进制 id/query 归一后骨架相等
    a = fc.normalize_reason("元素 #btn-123 在 1500ms 后未出现 at line 42")
    b = fc.normalize_reason("元素 #btn-456 在 2200ms 后未出现 at line 88")
    assert a == b, (a, b)
    # 不同根因不相等
    c = fc.normalize_reason("接口 /api/pay 返回 500")
    assert c != a
    # None 安全
    assert fc.normalize_reason(None) == ""


def test_fingerprint_and_cluster():
    runs = [
        {"id": 1, "triage_kind": "environment", "reason": "接口 /api/pay 返回 500", "fail_kind": None, "report": None, "requirement_id": 10},
        {"id": 2, "triage_kind": "environment", "reason": "接口 /api/pay 返回 500", "fail_kind": None, "report": None, "requirement_id": 11},
        {"id": 3, "triage_kind": "selector", "reason": "元素 #a-1 在 1000ms 后未出现", "fail_kind": None, "report": None, "requirement_id": 10},
        {"id": 4, "triage_kind": "selector", "reason": "元素 #a-2 在 3000ms 后未出现", "fail_kind": None, "report": None, "requirement_id": 12},
    ]
    clusters = fc.rule_cluster(runs)
    # run1/2 同根因(同 triage+归一reason)→一簇; run3/4 归一后同→一簇; 共2簇
    assert len(clusters) == 2, [c["fingerprint"] for c in clusters]
    by_size = sorted(clusters, key=lambda c: -c["member_count"])
    assert by_size[0]["member_count"] == 2
    # 涉及需求聚合去重
    env = next(c for c in clusters if c["triage_kind"] == "environment")
    assert sorted(env["requirement_ids"]) == [10, 11]
    sel = next(c for c in clusters if c["triage_kind"] == "selector")
    assert sorted(sel["requirement_ids"]) == [10, 12]


def main():
    test_table_created()
    test_normalize_reason()
    test_fingerprint_and_cluster()
    print("OK test_fail_cluster")


if __name__ == "__main__":
    main()
