"""defense-calendar 回归防线日历端点自测。
运行: cd backend && python -m scripts.test_defense_calendar

覆盖:绿/红/灰 state、streak 连续值守天数、total_guard_days、从今天往回扫、
FeedbackRun.batch_id → ExecRun 聚合。
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
from app.models import ExecRun, Project, User
from app.models.feedback import FeedbackRun

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _seed():
    u = User(username="admin", name="管理员", password_hash="x", is_platform_admin=True)
    p = Project(name="P1", code="p1")
    _s.add_all([u, p]); _s.flush()
    now = datetime.now()

    def fb_run(batch, days_ago):
        fr = FeedbackRun(project_id=p.id, batch_id=batch, trigger="auto", case_count=2)
        _s.add(fr); _s.flush()
        fr.created_at = now - timedelta(days=days_ago)

    def run(batch, status, days_ago):
        r = ExecRun(project_id=p.id, runner="m", payload="{}", status=status, batch_id=batch)
        _s.add(r); _s.flush()
        r.created_at = now - timedelta(days=days_ago)

    # D-1：green（passed × 3）
    fb_run("b1", 1); run("b1", "passed", 1); run("b1", "passed", 1); run("b1", "passed", 1)
    # D-2：red（1 failed(business)）
    fb_run("b2", 2); run("b2", "passed", 2); run("b2", "failed", 2)
    # D-3：gray（无 FeedbackRun）
    # D-4：green（1 passed）
    fb_run("b4", 4); run("b4", "passed", 4)
    _s.commit()


_seed()


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def main():
    r = client.get("/api/feedback/defense-calendar", params={"weeks": 2})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert "days" in d and "streak" in d and "total_guard_days" in d, d

    by_date = {x["date"]: x for x in d["days"]}
    from datetime import date, timedelta as td
    today = date.today()
    d1 = str(today - td(days=1))
    d2 = str(today - td(days=2))
    d3 = str(today - td(days=3))
    d4 = str(today - td(days=4))

    assert d1 in by_date, f"{d1} not in days"
    assert by_date[d1]["state"] == "green", by_date[d1]
    assert by_date[d2]["state"] == "red", by_date[d2]
    assert by_date[d3]["state"] == "gray", by_date[d3]
    assert by_date[d4]["state"] == "green", by_date[d4]

    # streak: 从今天往回数连续非 gray——今天=gray，从 D-1 起: D-1 green + D-2 red + D-3 断 → streak=2
    assert d["streak"] == 2, d["streak"]
    # total_guard_days: 3（D-1 D-2 D-4）
    total = sum(1 for x in d["days"] if x["state"] != "gray")
    assert total == 3, d["days"]

    # 字段完整
    sample = by_date[d1]
    assert "runs" in sample and "cases" in sample and "failed" in sample, sample

    print("OK test_defense_calendar")


if __name__ == "__main__":
    main()
