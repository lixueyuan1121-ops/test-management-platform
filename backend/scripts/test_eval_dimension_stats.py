"""eval dimension-stats 端点自测。
运行: cd backend && python -m scripts.test_eval_dimension_stats

覆盖:维度按 pass/fail 统计、error/NULL 不计、窗口过滤、未标注归入"未标注"、
dims 按 total 降序、overall_rate、缺 project_id 422。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import Project, User
from app.models.ai_eval import EvalQuery, EvalRun

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _seed():
    u = User(username="admin", name="管理员", password_hash="x", is_platform_admin=True)
    p = Project(name="P1", code="p1")
    _s.add_all([u, p]); _s.flush()

    def q(dim):
        o = EvalQuery(project_id=p.id, title=f"q-{dim}", dimension=dim, prompt="p")
        _s.add(o); _s.flush()
        return o

    def run(q_id, verdict, days_ago=0):
        r = EvalRun(project_id=p.id, eval_query_id=q_id, runner="m",
                    status="judged", verdict=verdict)
        _s.add(r); _s.flush()
        r.created_at = datetime.now() - timedelta(days=days_ago)

    q1 = q("准确性")
    q2 = q("准确性")
    q3 = q("安全性")
    q4 = q(None)     # 无标注 → 归入"未标注"

    # 准确性: q1→pass+fail(2判定), q2→pass(1判定) → total3 passed2 rate66.7
    run(q1.id, "pass"); run(q1.id, "fail")
    run(q2.id, "pass")
    # 安全性: pass×2 → total2 passed2 rate100.0
    run(q3.id, "pass"); run(q3.id, "pass")
    # 未标注: pass×1 fail×1 → total2 passed1 rate50.0
    run(q4.id, "pass"); run(q4.id, "fail")
    # error/NULL → 不计
    run(q1.id, "error")
    run(q1.id, None)  # NULL
    # 窗口外(40天前): pass → 不计
    run(q1.id, "pass", days_ago=40)
    _s.commit()


_seed()


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def main():
    r = client.get("/api/eval-judge/dimension-stats", params={"project_id": 1})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]

    dims = {x["dimension"]: x for x in d["dims"]}
    assert "准确性" in dims and "安全性" in dims and "未标注" in dims, list(dims)

    assert dims["准确性"]["total"] == 3, dims["准确性"]
    assert dims["准确性"]["passed"] == 2, dims["准确性"]
    assert dims["准确性"]["pass_rate"] == 66.7, dims["准确性"]

    assert dims["安全性"]["total"] == 2 and dims["安全性"]["pass_rate"] == 100.0, dims["安全性"]
    assert dims["未标注"]["total"] == 2 and dims["未标注"]["pass_rate"] == 50.0, dims["未标注"]

    # dims 按 total 降序(准确性3 > 安全性2 = 未标注2)
    assert d["dims"][0]["dimension"] == "准确性", d["dims"]

    assert d["judged_total"] == 7, d   # 准确性3 + 安全性2 + 未标注2(error/NULL/窗外不计)
    assert d["overall_rate"] == round(5 / 7 * 100, 1), d

    # 缺 project_id → 422
    assert client.get("/api/eval-judge/dimension-stats").status_code == 422

    # days 过滤:只看今天(days=1),窗外 pass 不计(已种入)
    r2 = client.get("/api/eval-judge/dimension-stats", params={"project_id": 1, "days": 1})
    d2 = r2.json()["data"]
    assert d2["judged_total"] == 7   # 同上(窗外那条原本就是40天前,days=1 仍不计)

    print("OK test_eval_dimension_stats")


if __name__ == "__main__":
    main()
