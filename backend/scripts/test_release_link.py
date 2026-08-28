"""发版实体关联(建议项⑤)自测:exec_run.release_id 挂接 + 质量卡实体优先/窗口回落 + 上线清单通过率。
运行: cd backend && python -m scripts.test_release_link

覆盖:
- enqueue-cases 带 release_id:落库打标;跨项目/不存在 → 400 整批拒绝
- /releases/quality:有挂接 run 的版本按实体聚合(exec_scope=linked,窗口外的挂接 run 也算进来、
  窗口内未挂接的不算);无挂接版本回落时间窗(exec_scope=window,旧口径)
- 上线清单通过率:每用例取口径内最新一次执行结论;清单为空 → checklist_passed=None
"""
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.enums import ReviewStatus
from app.db.session import Base, get_db
from app.main import app
from app.models import (
    ExecRun, Project, ReleaseChecklistItem, ReleaseRecord, TestCase, User,
)
from app.core.deps import get_current_user

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _get_db():
    yield _s


app.dependency_overrides[get_db] = _get_db
app.dependency_overrides[get_current_user] = lambda: _s.get(User, 1)
client = TestClient(app)

today = date.today()


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="P1", code="p1"),
        Project(id=200, name="P2", code="p2"),
    ])
    _s.flush()
    # v1.0 20 天前、v2.0 5 天前 → v2.0 窗=(D-20,D-5]
    _s.add(ReleaseRecord(id=11, project_id=100, version="v1.0",
                         release_date=today - timedelta(days=20)))
    _s.add(ReleaseRecord(id=12, project_id=100, version="v2.0",
                         release_date=today - timedelta(days=5)))
    _s.add(ReleaseRecord(id=21, project_id=200, version="x1.0", release_date=today))
    # 用例(已采纳 gui,带 script)
    for cid, title in [(1, "登录"), (2, "下单"), (3, "支付")]:
        _s.add(TestCase(id=cid, ai_task_id=1, project_id=100, title=title, exec_kind="gui",
                        review_status=ReviewStatus.adopted,
                        script='[{"action":"click","selector":"k"}]'))
    # 上线清单:用例 1、2
    _s.add_all([ReleaseChecklistItem(project_id=100, test_case_id=1),
                ReleaseChecklistItem(project_id=100, test_case_id=2)])
    _s.commit()


def _run(case_id, status_, days_ago, release_id=None, fail_kind=None):
    r = ExecRun(project_id=100, test_case_id=case_id, runner="m", payload='{"title":"t"}',
                status=status_, release_id=release_id, fail_kind=fail_kind)
    _s.add(r)
    _s.flush()
    r.created_at = datetime.now() - timedelta(days=days_ago)
    _s.commit()
    return r


def test_enqueue_stamps_release():
    d = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "mac-01", "test_case_ids": [3], "release_id": 12,
    }).json()
    assert d["code"] == 0, d
    rid = d["data"]["run_ids"][0]
    assert _s.get(ExecRun, rid).release_id == 12
    # 跨项目发版 → 400
    d2 = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "mac-01", "test_case_ids": [3], "release_id": 21,
    }).json()
    assert d2["code"] == 400, d2
    # 不存在 → 400
    d3 = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "mac-01", "test_case_ids": [3], "release_id": 999,
    }).json()
    assert d3["code"] == 400, d3
    # 不带 release_id 照常
    d4 = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "mac-01", "test_case_ids": [3],
    }).json()
    assert d4["code"] == 0
    assert _s.get(ExecRun, d4["data"]["run_ids"][0]).release_id is None
    # 清理本测试产生的 pending(不干扰后续统计)
    _s.query(ExecRun).delete()
    _s.commit()
    print("OK enqueue stamps release_id")


def test_quality_linked_vs_window():
    # v2.0 实体挂接:2 passed + 1 failed(business),其中一条**窗口外**(30 天前)也要算进 linked
    _run(1, "passed", 2, release_id=12)
    _run(2, "failed", 2, release_id=12, fail_kind="business")
    _run(3, "passed", 30, release_id=12)   # 窗口外但显式挂接 → linked 口径应包含
    # 窗口内但未挂接的杂音 run → linked 口径应排除
    _run(3, "failed", 3, fail_kind="business")
    # v1.0 无挂接 → 窗口回落:(D-34,D-20] 放 1 条 passed
    _run(3, "passed", 25)

    d = client.get("/api/releases/quality", params={"project_id": 100}).json()
    assert d["code"] == 0, d
    items = {it["version"]: it for it in d["data"]["items"]}
    v2, v1 = items["v2.0"], items["v1.0"]

    assert v2["exec_scope"] == "linked", v2
    assert v2["exec_total"] == 3, v2       # 含窗口外挂接那条;不含未挂接杂音
    assert v2["exec_passed"] == 2 and v2["bugs_found"] == 1, v2
    assert v2["pass_rate"] == 66.7, v2

    assert v1["exec_scope"] == "window", v1
    assert v1["exec_total"] == 1 and v1["pass_rate"] == 100.0, v1
    print("OK quality linked vs window")


def test_checklist_leg():
    d = client.get("/api/releases/quality", params={"project_id": 100}).json()
    items = {it["version"]: it for it in d["data"]["items"]}
    v2 = items["v2.0"]
    # 清单=用例1、2;linked 口径内:用例1 最新 passed,用例2 最新 failed → 1/2
    assert v2["checklist_total"] == 2 and v2["checklist_passed"] == 1, v2

    # 用例2 复测通过(挂同版本、更新)→ 最新结论翻正 → 2/2
    _run(2, "passed", 1, release_id=12)
    d2 = client.get("/api/releases/quality", params={"project_id": 100}).json()
    v2b = {it["version"]: it for it in d2["data"]["items"]}["v2.0"]
    assert v2b["checklist_passed"] == 2, v2b

    # 清单为空的项目形态:清掉清单 → checklist_passed=None
    _s.query(ReleaseChecklistItem).delete()
    _s.commit()
    d3 = client.get("/api/releases/quality", params={"project_id": 100}).json()
    v2c = {it["version"]: it for it in d3["data"]["items"]}["v2.0"]
    assert v2c["checklist_total"] == 0 and v2c["checklist_passed"] is None, v2c
    print("OK checklist leg")


def main():
    _seed()
    test_enqueue_stamps_release()
    test_quality_linked_vs_window()
    test_checklist_leg()
    print("OK test_release_link")


if __name__ == "__main__":
    main()
