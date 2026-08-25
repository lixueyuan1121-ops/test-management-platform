"""releases/quality 版本质量档案端点自测。
运行: cd backend && python -m scripts.test_release_quality

覆盖:版本窗口切分(上版发布日→本版发布日]、通过率/真bug/窗口内 open 问题按严重度、
红黄绿定级、最新在前排序、limit、缺 project_id 422。
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.core.enums import IssueSeverity, IssueStatus
from app.db.session import Base, get_db
from app.models import ExecRun, Project, RemainingIssue, ReleaseRecord, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _seed():
    u = User(username="admin", name="管理员", password_hash="x", is_platform_admin=True)
    p = Project(name="P1", code="p1")
    _s.add_all([u, p])
    _s.flush()
    today = date.today()
    # v1.0 20 天前发布；v2.0 5 天前发布 → v2.0 窗 = (D-20, D-5]，v1.0 窗 = (D-34, D-20]
    _s.add(ReleaseRecord(project_id=p.id, version="v1.0", release_date=today - timedelta(days=20)))
    _s.add(ReleaseRecord(project_id=p.id, version="v2.0", release_date=today - timedelta(days=5),
                         req_count=12))

    def run(status, days_ago, fail_kind=None):
        r = ExecRun(project_id=p.id, runner="m", payload="{}", status=status, fail_kind=fail_kind)
        _s.add(r)
        _s.flush()
        r.created_at = datetime.now() - timedelta(days=days_ago)

    # 12 天前(落 v2.0 窗)：3 passed + 1 failed(business) → total4 pass3 rate75 bugs1
    run("passed", 12)
    run("passed", 12)
    run("passed", 12)
    run("failed", 12, "business")
    # 25 天前(落 v1.0 窗)：1 passed → rate100
    run("passed", 25)

    def issue(sev, st, days_ago):
        i = RemainingIssue(project_id=p.id, title="t", severity=sev, status=st)
        _s.add(i)
        _s.flush()
        i.created_at = datetime.now() - timedelta(days=days_ago)

    # 10 天前 open major(落 v2.0 窗) → v2.0 grade=yellow
    issue(IssueSeverity.major, IssueStatus.open, 10)
    # 40 天前 open blocker(两个窗都不落) → 不影响
    issue(IssueSeverity.blocker, IssueStatus.open, 40)
    # 12 天前但已 resolved → 不计 open
    issue(IssueSeverity.major, IssueStatus.resolved, 12)
    _s.commit()


_seed()


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def main():
    r = client.get("/api/releases/quality", params={"project_id": 1})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 2, items
    v2, v1 = items[0], items[1]
    assert v2["version"] == "v2.0" and v1["version"] == "v1.0", "最新在前"

    assert v2["exec_total"] == 4 and v2["exec_passed"] == 3, v2
    assert v2["pass_rate"] == 75.0, v2
    assert v2["bugs_found"] == 1, v2
    assert v2["issues_open"] == {"blocker": 0, "major": 1, "minor": 0}, v2
    assert v2["grade"] == "yellow", v2       # 有 open major 且 rate<90
    assert v2["req_count"] == 12, v2

    assert v1["exec_total"] == 1 and v1["pass_rate"] == 100.0, v1
    assert v1["issues_open"] == {"blocker": 0, "major": 0, "minor": 0}, v1
    assert v1["grade"] == "green", v1

    # limit=1 只回最新一条
    r2 = client.get("/api/releases/quality", params={"project_id": 1, "limit": 1})
    assert len(r2.json()["data"]["items"]) == 1
    assert r2.json()["data"]["items"][0]["version"] == "v2.0"

    # 缺 project_id → 422
    assert client.get("/api/releases/quality").status_code == 422

    print("OK test_release_quality")


if __name__ == "__main__":
    main()
