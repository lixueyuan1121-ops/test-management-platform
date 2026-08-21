"""L2:执行失败分类(fail_kind + verdict blocked)回写端点 自测(内存库 + TestClient)。
运行: cd backend && python -m scripts.test_exec_blocked

覆盖回写端点 PATCH /exec-queue/{id} 的 L2 语义:
  A. fail_kind="selector" → verdict=blocked、status=blocked,同步 checklist_item.exec_status=blocked;
  B. fail_kind="business" → verdict=fail、status=failed,checklist.exec_status=failed;
  C. verdict=pass → passed(回归,不受 L2 影响);
  D. _to_out 返回 fail_kind(供前端三态展示)。
  E. 缺 fail_kind 的旧 runner 回写 verdict=fail → 仍 failed(向后兼容,不炸)。
"""
from types import SimpleNamespace
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import require_runner_ctx
from app.db.session import Base, get_db
from app.models import Project, AiTask, TestCase, ExecRun, ChecklistItem, Task

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
_s.add(Task(id=1, project_id=1, title="T", assigned_by=1, assigned_to=1, assigned_date=date(2026, 8, 20)))
for cid in (101, 102, 103):
    _s.add(TestCase(id=cid, ai_task_id=1, project_id=1, title=f"gui用例{cid}", exec_kind="gui", review_status="pending"))
# 三条挂清单项的 run(验证 exec_status 同步,各挂不同用例避开 (task,case) 唯一约束) + 一条裸 run(无清单项)
_s.add(ChecklistItem(id=11, task_id=1, project_id=1, test_case_id=101, exec_status="pending"))
_s.add(ChecklistItem(id=12, task_id=1, project_id=1, test_case_id=102, exec_status="pending"))
_s.add(ChecklistItem(id=13, task_id=1, project_id=1, test_case_id=103, exec_status="pending"))
for rid, cid, tc in [(1001, 11, 101), (1002, 12, 102), (1003, 13, 103)]:
    _s.add(ExecRun(id=rid, checklist_item_id=cid, test_case_id=tc, task_id=1, project_id=1,
                   runner="mac-01", kind="gui", status="running", payload="{}"))
_s.add(ExecRun(id=1004, checklist_item_id=None, test_case_id=101, task_id=1, project_id=1,
               runner="mac-01", kind="gui", status="running", payload="{}"))
_s.commit()


def _override_db():
    yield _s


app.dependency_overrides[get_db] = _override_db
app.dependency_overrides[require_runner_ctx] = lambda: SimpleNamespace(device=None, runner="mac-01")
client = TestClient(app)


def _patch(run_id, body):
    return client.patch(f"/api/exec-queue/{run_id}", params={"runner": "mac-01"}, json=body)


def test_selector_fail_maps_to_blocked():
    r = _patch(1001, {"verdict": "fail", "fail_kind": "selector", "reason": "未命中 key homepageTitle"})
    assert r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["verdict"] == "blocked", f"selector 失败应记 verdict=blocked,实际 {d['verdict']}"
    assert d["status"] == "blocked", f"status 应 blocked,实际 {d['status']}"
    assert d["fail_kind"] == "selector"
    _s.expire_all()
    assert getattr(_s.get(ChecklistItem, 11).exec_status, "value", _s.get(ChecklistItem, 11).exec_status) == "blocked", \
        "清单项 exec_status 应同步 blocked"


def test_business_fail_maps_to_failed():
    r = _patch(1002, {"verdict": "fail", "fail_kind": "business", "reason": "断言文本失败"})
    d = r.json()["data"]
    assert d["verdict"] == "fail", "business 失败仍是 fail(真 bug)"
    assert d["status"] == "failed"
    assert d["fail_kind"] == "business"
    _s.expire_all()
    assert getattr(_s.get(ChecklistItem, 12).exec_status, "value", _s.get(ChecklistItem, 12).exec_status) == "failed"


def test_pass_unaffected():
    r = _patch(1003, {"verdict": "pass", "reason": "通过"})
    d = r.json()["data"]
    assert d["verdict"] == "pass" and d["status"] == "passed"
    assert d["fail_kind"] is None
    _s.expire_all()
    assert getattr(_s.get(ChecklistItem, 13).exec_status, "value", _s.get(ChecklistItem, 13).exec_status) == "passed"


def test_legacy_fail_without_fail_kind():
    """旧 runner 不带 fail_kind 的 fail 回写 → 向后兼容,仍 failed(不炸、不误判 blocked)。"""
    r = _patch(1004, {"verdict": "fail", "reason": "老 runner 回写"})
    d = r.json()["data"]
    assert d["verdict"] == "fail" and d["status"] == "failed", f"缺 fail_kind 的 fail 应保持 failed,实际 {d}"
    assert d["fail_kind"] is None


def main():
    test_selector_fail_maps_to_blocked()
    test_business_fail_maps_to_failed()
    test_pass_unaffected()
    test_legacy_fail_without_fail_kind()
    print("OK test_exec_blocked")


if __name__ == "__main__":
    main()
