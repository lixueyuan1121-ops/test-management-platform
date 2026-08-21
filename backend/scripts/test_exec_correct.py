"""人工纠偏执行结果端点 PATCH /exec-queue/{id}/verdict 自测(内存库 + TestClient)。
运行: cd backend && python -m scripts.test_exec_correct

覆盖(用户 JWT,非 runner):
  A. 把 failed 纠偏为 pass → status=passed、verdict=pass,reason 带「[人工纠偏]」前缀,同步 checklist=passed;
  B. 把 passed 纠偏为 fail(business) → status=failed、verdict=fail,同步 checklist=failed;
  C. 纠偏为 blocked → status=blocked、verdict=blocked,同步 checklist=blocked;
  D. 非法 verdict → 400;
  E. run 不存在 → 404。
"""
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import Project, AiTask, TestCase, ExecRun, ChecklistItem, Task, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_admin = User(id=1, username="admin", name="A", password_hash="x", is_platform_admin=True, status="active")
_s.add(_admin)
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
_s.add(Task(id=1, project_id=1, title="T", assigned_by=1, assigned_to=1, assigned_date=date(2026, 8, 20)))
for cid in (301, 302, 303):
    _s.add(TestCase(id=cid, ai_task_id=1, project_id=1, title=f"用例{cid}", exec_kind="gui", review_status="pending"))
_s.add(ChecklistItem(id=31, task_id=1, project_id=1, test_case_id=301, exec_status="failed"))
_s.add(ChecklistItem(id=32, task_id=1, project_id=1, test_case_id=302, exec_status="passed"))
_s.add(ChecklistItem(id=33, task_id=1, project_id=1, test_case_id=303, exec_status="passed"))
# 3 条已完成的 run(带清单项),初始状态各异
_s.add(ExecRun(id=2001, checklist_item_id=31, test_case_id=301, task_id=1, project_id=1, runner="mac-01", kind="gui", status="failed", verdict="fail", fail_kind="business", reason="断言失败", payload="{}"))
_s.add(ExecRun(id=2002, checklist_item_id=32, test_case_id=302, task_id=1, project_id=1, runner="mac-01", kind="gui", status="passed", verdict="pass", reason="通过", payload="{}"))
_s.add(ExecRun(id=2003, checklist_item_id=33, test_case_id=303, task_id=1, project_id=1, runner="mac-01", kind="gui", status="passed", verdict="pass", reason="通过", payload="{}"))
_s.commit()


def _override_db():
    yield _s


app.dependency_overrides[get_db] = _override_db
app.dependency_overrides[get_current_user] = lambda: _admin
client = TestClient(app)


def _ci(item_id):
    _s.expire_all()
    it = _s.get(ChecklistItem, item_id)
    return getattr(it.exec_status, "value", it.exec_status)


def _correct(run_id, body):
    return client.patch(f"/api/exec-queue/{run_id}/verdict", json=body)


def test_correct_failed_to_pass():
    r = _correct(2001, {"verdict": "pass", "reason": "复核后确认功能正常"})
    assert r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["verdict"] == "pass" and d["status"] == "passed", d
    assert "人工纠偏" in (d["reason"] or ""), f"reason 应带纠偏前缀: {d['reason']}"
    assert _ci(31) == "passed", "清单项应同步 passed"


def test_correct_pass_to_fail():
    r = _correct(2002, {"verdict": "fail", "reason": "实际是 bug"})
    d = r.json()["data"]
    assert d["verdict"] == "fail" and d["status"] == "failed", d
    assert d["fail_kind"] == "business", "纠偏为 fail 应记 business(真 bug)"
    assert _ci(32) == "failed"


def test_correct_to_blocked():
    r = _correct(2003, {"verdict": "blocked", "reason": "其实是环境问题"})
    d = r.json()["data"]
    assert d["verdict"] == "blocked" and d["status"] == "blocked", d
    assert d["fail_kind"] == "selector", "纠偏为 blocked 应记 selector(环境阻塞)"
    assert _ci(33) == "blocked"


def test_invalid_verdict_rejected():
    r = _correct(2001, {"verdict": "maybe"})
    # 非法 verdict:pydantic 校验(422)或业务 400 均属拒绝(统一信封 code 非 0)
    assert r.status_code in (400, 422), r.text
    assert r.json()["code"] != 0, r.text


def test_missing_run_404():
    r = _correct(999999, {"verdict": "pass"})
    assert r.status_code == 404, r.text


def main():
    test_correct_failed_to_pass()
    test_correct_pass_to_fail()
    test_correct_to_blocked()
    test_invalid_verdict_rejected()
    test_missing_run_404()
    print("OK test_exec_correct")


if __name__ == "__main__":
    main()
