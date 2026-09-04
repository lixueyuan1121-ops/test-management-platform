"""RTS 回归智选自测。跑法：cd backend && .venv/bin/python -m scripts.test_rts"""
import os, sys
os.environ["DATABASE_URL"] = "sqlite:///./tmp_test_rts.db"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp_test_rts.db")
if os.path.exists(_DB):
    os.remove(_DB)

from app.main import app  # noqa: F401
from app.db.session import Base, engine
from app.models import RtsRecommendation


def test_table_created():
    Base.metadata.create_all(engine)
    cols = set(RtsRecommendation.__table__.c.keys())
    for c in ("id", "project_id", "release_id", "overall_risk", "summary",
              "rationale", "focus_points", "candidate_count", "recommended_count",
              "provider", "generated_at"):
        assert c in cols, f"缺字段 {c}"


def main():
    test_table_created()
    print("OK test_rts")


if __name__ == "__main__":
    main()
