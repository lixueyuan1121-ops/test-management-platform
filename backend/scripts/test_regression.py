"""回归用例库 自测(内存库 + TestClient)。
运行: cd backend && python -m scripts.test_regression

覆盖:
  A. PATCH /testcases/{cid} 单条切换 is_regression → 落库 + _to_case_out 返回。
  B. list?is_regression=true 只出回归用例。
  C. PATCH /testcases/regression 批量标记:只改本项目用例,跨项目 id 忽略;路由不被 {cid} 吞。
  D. POST /exec-queue/enqueue-cases:不依赖任务能建 ExecRun(checklist_item_id=None);
     manual 用例 → 400 整批拒绝;跨项目用例 → 400。
"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import Project, AiTask, TestCase, ExecRun

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(Project(id=2, name="Q", code="Q1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
# 项目1:两条可自动化 gui(101 任务页 / 102 登录页)、一条 manual(103)
_s.add(TestCase(id=101, ai_task_id=1, project_id=1, title="任务页gui", exec_kind="gui",
                review_status="pending", page="任务页"))
_s.add(TestCase(id=102, ai_task_id=1, project_id=1, title="登录页gui", exec_kind="gui",
                review_status="pending", page="登录页"))
_s.add(TestCase(id=103, ai_task_id=1, project_id=1, title="人工", exec_kind="manual",
                review_status="pending"))
# 项目2:一条(用于跨项目隔离校验)
_s.add(AiTask(id=2, project_id=2, user_id=1, input_type="text", status="done"))
_s.add(TestCase(id=201, ai_task_id=2, project_id=2, title="别项目", exec_kind="gui", review_status="pending"))
_s.commit()


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def test_single_toggle_and_filter():
    # 单条标记回归
    r = client.patch("/api/ai/testcases/101", json={"is_regression": True})
    assert r.json()["code"] == 0 and r.json()["data"]["is_regression"] is True, r.text
    # list 仅回归 → 只出 101
    d = client.get("/api/ai/cases", params={"project_id": 1, "is_regression": True}).json()["data"]
    ids = [c["id"] for c in d["items"]]
    assert ids == [101], ids
    assert d["items"][0]["is_regression"] is True


def test_bulk_and_cross_project():
    # 批量把 102 + 201(别项目) 标记回归,project_id=1 → 只应改 102
    r = client.patch("/api/ai/testcases/regression", params={"project_id": 1},
                     json={"ids": [102, 201], "is_regression": True})
    assert r.json()["code"] == 0, r.text
    assert r.json()["data"]["updated"] == 1, f"跨项目 id 应被忽略,只改 1 条,实际 {r.json()['data']}"
    _s.expire_all()
    assert _s.get(TestCase, 102).is_regression is True
    assert _s.get(TestCase, 201).is_regression is False, "别项目用例不应被改"
    # 现在项目1 回归用例 = 101,102
    d = client.get("/api/ai/cases", params={"project_id": 1, "is_regression": True}).json()["data"]
    assert sorted(c["id"] for c in d["items"]) == [101, 102]
    # 按页面 + 回归叠加:登录页只出 102
    d2 = client.get("/api/ai/cases", params={"project_id": 1, "is_regression": True, "page": "登录页"}).json()["data"]
    assert [c["id"] for c in d2["items"]] == [102]


def test_enqueue_cases_no_task():
    # 回归执行:直接下发 101+102(无关联任务) → 建 2 条 ExecRun,checklist_item_id 均为 None
    r = client.post("/api/exec-queue/enqueue-cases",
                    json={"project_id": 1, "runner": "mac-01", "test_case_ids": [101, 102]})
    assert r.json()["code"] == 0, r.text
    run_ids = r.json()["data"]["run_ids"]
    assert len(run_ids) == 2, run_ids
    _s.expire_all()
    for rid in run_ids:
        row = _s.get(ExecRun, rid)
        assert row.checklist_item_id is None, "回归执行不应挂清单项"
        assert row.task_id is None and row.test_case_id in (101, 102)
        assert getattr(row.status, "value", row.status) == "pending"

    # manual 混入 → 整批 400
    r2 = client.post("/api/exec-queue/enqueue-cases",
                     json={"project_id": 1, "runner": "mac-01", "test_case_ids": [101, 103]})
    assert r2.json()["code"] != 0 and "manual" in r2.json()["msg"], r2.text

    # 跨项目用例 → 400
    r3 = client.post("/api/exec-queue/enqueue-cases",
                     json={"project_id": 1, "runner": "mac-01", "test_case_ids": [201]})
    assert r3.json()["code"] != 0, "跨项目用例应拒绝"


def main():
    test_single_toggle_and_filter()
    test_bulk_and_cross_project()
    test_enqueue_cases_no_task()
    print("OK test_regression")


if __name__ == "__main__":
    main()
