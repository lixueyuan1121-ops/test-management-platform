"""日报勾「已上线」→ 任务状态联动 online 的自测(内存库 + TestClient)。
运行: cd backend && python -m scripts.test_report_online_link

覆盖:
  A. is_online=True → 任务置 online + 刷 online_at。
  B. status_locked=True(人工接管)→ 不覆盖。
  C. 仅单向:同一任务再提 is_online=False → 任务保持 online(不回退)。
"""
from datetime import date
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import Project, Task, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(User(id=1, username="u", password_hash="x", name="U", is_platform_admin=True))
# 任务1:testing,未锁;任务2:testing,已被人工接管(锁)
_s.add(Task(id=1, project_id=1, title="待上线任务", assigned_to=1, assigned_by=1, assigned_date=date(2026, 8, 19),
            status="testing", status_locked=False))
_s.add(Task(id=2, project_id=1, title="人工接管任务", assigned_to=1, assigned_by=1, assigned_date=date(2026, 8, 19),
            status="testing", status_locked=True))
_s.commit()


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def _report(task_id, is_online):
    return {
        "task_id": task_id, "report_date": "2026-08-19", "progress_pct": 100,
        "is_online": is_online, "online_time": None, "workload_hours": 2, "summary": "冒烟通过", "issues": [],
    }


def main():
    # A. 勾上线 → 任务1 online + online_at
    r = client.post("/api/daily-reports", json=_report(1, True))
    assert r.json()["code"] == 0, r.text
    _s.expire_all()
    t1 = _s.get(Task, 1)
    assert t1.status.value == "online", f"应联动 online,实际 {t1.status.value}"
    assert t1.online_at is not None, "应刷 online_at"
    assert t1.status_locked is False, "自动联动不应置人工接管锁"

    # B. 已锁任务 → 不覆盖
    r2 = client.post("/api/daily-reports", json=_report(2, True))
    assert r2.json()["code"] == 0, r2.text
    _s.expire_all()
    t2 = _s.get(Task, 2)
    assert t2.status.value == "testing", f"已锁任务不应被日报改动,实际 {t2.status.value}"
    assert t2.online_at is None

    # C. 仅单向:任务1 再提 is_online=False → 保持 online(不回退)
    r3 = client.post("/api/daily-reports", json=_report(1, False))
    assert r3.json()["code"] == 0, r3.text
    _s.expire_all()
    assert _s.get(Task, 1).status.value == "online", "取消勾选不应回退任务状态"

    print("OK test_report_online_link")


if __name__ == "__main__":
    main()
