"""TestPlan（测试计划）垂直切片自测——对位 feedback_regression_set 的泛化形态。
运行: cd backend && python -m scripts.test_test_plan

测试点：
1. CRUD：建计划 / 查列表 / 查单计划 / 改元信息 / 删计划
2. 用例管理：幂等增删 / 候选用例查询（过滤已在计划内）/ 跨项目拒绝
3. 定时调度：设置 cron + enabled / cron 非法 400 / next_run_at 回填
4. 立即执行：真实下发（不 mock）——exec_run 生成、manual 跳过、test_plan_run 元数据、last_run_at
5. 定时路径：run_plan_job 到点触发 → trigger=auto 的批次生成
6. 执行历史：list_runs 聚合口径 / get_run 详情
"""
import json
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import ExecStatus, ProjectRole, ReviewStatus
from app.db.session import Base
from app.models import (
    ExecRun, Project, ProjectMember, TestCase, TestPlan, TestPlanCase, TestPlanRun, User,
)

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)


def seed(db: Session):
    """种入基础数据（幂等：已有即跳过）。"""
    if db.get(User, 1):
        return
    u = User(id=1, username="tester", name="tester", email="t@ex.com",
             password_hash="x", is_platform_admin=False)
    proj = Project(id=100, name="TestProj", code="test-proj")
    proj2 = Project(id=200, name="OtherProj", code="other-proj")
    pm = ProjectMember(user_id=1, project_id=100, role=ProjectRole.admin)
    tc1 = TestCase(id=1001, ai_task_id=1, project_id=100, title="登录成功", exec_kind="gui",
                   review_status=ReviewStatus.adopted, is_regression=True,
                   script='[{"action":"fill","selector":"user","args":{"text":"admin"}}]')
    tc2 = TestCase(id=1002, ai_task_id=1, project_id=100, title="输入校验", exec_kind="gui",
                   review_status=ReviewStatus.adopted,
                   script='[{"action":"click","selector":"submit"}]')
    tc3 = TestCase(id=1003, ai_task_id=1, project_id=100, title="人工探索测试", exec_kind="manual",
                   review_status=ReviewStatus.adopted)
    tc_other = TestCase(id=2001, ai_task_id=1, project_id=200, title="别家项目用例", exec_kind="gui",
                        review_status=ReviewStatus.adopted)
    db.add_all([u, proj, proj2, pm, tc1, tc2, tc3, tc_other])
    db.commit()


def test_crud_and_cases(db: Session):
    from fastapi import HTTPException

    from app.api.test_plan import (
        add_cases, candidate_cases, create_plan, delete_plan, get_plan,
        list_plans, remove_cases, update_plan,
    )
    from app.schemas.test_plan import PlanCasesIn, PlanCreateIn, PlanUpdateIn

    u = db.get(User, 1)
    resp = create_plan(PlanCreateIn(project_id=100, name="冒烟回归", description="核心流程",
                                    runner="mac-01"), db, u)
    assert resp["code"] == 0, resp
    plan_id = resp["data"]["id"]
    assert resp["data"]["case_count"] == 0

    assert len(list_plans(project_id=100, db=db, user=u)["data"]) == 1

    r3 = update_plan(plan_id, PlanUpdateIn(name="冒烟回归v2", runner="mac-02"), db, u)
    assert r3["data"]["name"] == "冒烟回归v2" and r3["data"]["runner"] == "mac-02"

    # 候选用例：项目 100 已采纳 3 条，均未入计划
    cand = candidate_cases(plan_id, keyword=None, only_regression=False, db=db, user=u)["data"]
    assert len(cand) == 3 and all(not c["in_plan"] for c in cand)
    # only_regression 过滤后只剩 1001
    cand_reg = candidate_cases(plan_id, keyword=None, only_regression=True, db=db, user=u)["data"]
    assert [c["id"] for c in cand_reg] == [1001]

    # 幂等加用例（manual 也允许加，执行时才跳过）
    r5 = add_cases(plan_id, PlanCasesIn(case_ids=[1001, 1002, 1003]), db, u)
    assert r5["data"]["added"] == 3
    r6 = add_cases(plan_id, PlanCasesIn(case_ids=[1001, 1002, 1003]), db, u)
    assert r6["data"]["added"] == 0 and r6["data"]["skipped"] == 3

    # 跨项目用例整批拒绝
    try:
        add_cases(plan_id, PlanCasesIn(case_ids=[2001]), db, u)
        assert False, "跨项目应 400"
    except HTTPException as e:
        assert e.status_code == 400

    detail = get_plan(plan_id, db, u)["data"]
    assert detail["case_count"] == 3 and len(detail["cases"]) == 3

    assert remove_cases(plan_id, PlanCasesIn(case_ids=[1003]), db, u)["data"]["removed"] == 1

    assert delete_plan(plan_id, db, u)["data"]["deleted"] == plan_id
    assert db.get(TestPlan, plan_id) is None
    print("OK crud+cases")


def test_schedule(db: Session):
    from fastapi import HTTPException

    from app.api.test_plan import create_plan, set_schedule
    from app.schemas.test_plan import PlanCreateIn, PlanScheduleIn

    u = db.get(User, 1)
    plan_id = create_plan(PlanCreateIn(project_id=100, name="每日回归", runner="mac-01"),
                          db, u)["data"]["id"]

    # set_schedule 内部 `from app.services.scheduler import sync_plan_job`——patch 源模块属性
    with patch("app.services.scheduler.sync_plan_job") as mock_sync:
        mock_sync.return_value = datetime(2026, 8, 29, 9, 0, 0)
        r = set_schedule(plan_id, PlanScheduleIn(cron="0 9 * * *", enabled=True), db, u)
        assert r["data"]["schedule_enabled"] is True
        assert r["data"]["schedule_cron"] == "0 9 * * *"
        assert r["data"]["next_run_at"] is not None
        mock_sync.assert_called_once_with(plan_id, "0 9 * * *", True)

    with patch("app.services.scheduler.sync_plan_job") as mock_sync2:
        mock_sync2.return_value = None
        r2 = set_schedule(plan_id, PlanScheduleIn(cron="0 9 * * *", enabled=False), db, u)
        assert r2["data"]["schedule_enabled"] is False
        assert r2["data"]["next_run_at"] is None

    # cron 非法（enabled 时校验）
    try:
        set_schedule(plan_id, PlanScheduleIn(cron="not-a-cron", enabled=True), db, u)
        assert False, "非法 cron 应 400"
    except HTTPException as e:
        assert e.status_code == 400 and "非法" in e.detail
    # enabled 但缺 cron
    try:
        set_schedule(plan_id, PlanScheduleIn(cron=None, enabled=True), db, u)
        assert False, "缺 cron 应 400"
    except HTTPException as e:
        assert e.status_code == 400
    print("OK schedule")


def test_run_real_dispatch(db: Session):
    """真实下发（不 mock）：manual 跳过、exec_run 同构、元数据/last_run_at 落库。"""
    from app.api.test_plan import add_cases, create_plan, get_run, list_runs, run_plan
    from app.schemas.test_plan import PlanCasesIn, PlanCreateIn, PlanRunIn

    u = db.get(User, 1)
    plan_id = create_plan(PlanCreateIn(project_id=100, name="立即回归", runner="mac-01"),
                          db, u)["data"]["id"]
    add_cases(plan_id, PlanCasesIn(case_ids=[1001, 1002, 1003]), db, u)

    res = run_plan(plan_id, PlanRunIn(runner=None), db, u)["data"]
    assert len(res["run_ids"]) == 2, f"manual 应被跳过,实际 {res}"
    batch_id = res["batch_id"]

    runs = db.query(ExecRun).filter(ExecRun.batch_id == batch_id).all()
    assert len(runs) == 2
    assert {r.test_case_id for r in runs} == {1001, 1002}
    assert all(r.status == ExecStatus.pending for r in runs)
    assert all(r.checklist_item_id is None for r in runs)
    payload = json.loads(runs[0].payload)
    assert payload["title"] in ("登录成功", "输入校验") and payload["script"]

    pr = db.get(TestPlanRun, res["plan_run_id"])
    assert pr.trigger == "manual" and pr.case_count == 2 and pr.plan_id == plan_id
    assert db.get(TestPlan, plan_id).last_run_at is not None

    # 模拟执行完成：1 passed + 1 failed(business)
    runs[0].status = ExecStatus.passed
    runs[0].verdict = "pass"
    runs[1].status = ExecStatus.failed
    runs[1].verdict = "fail"
    runs[1].fail_kind = "business"
    db.commit()

    items = list_runs(project_id=100, plan_id=plan_id, db=db, user=u)["data"]
    assert len(items) == 1
    st = items[0]["stats"]
    assert st["total"] == 2 and st["passed"] == 1 and st["failed"] == 1 and st["finished"]
    assert items[0]["plan_name"] == "立即回归"

    detail = get_run(pr.id, db, u)["data"]
    assert len(detail["items"]) == 2
    assert {i["title"] for i in detail["items"]} == {"登录成功", "输入校验"}
    print("OK run real dispatch + history")


def test_scheduled_job_path(db: Session):
    """定时路径：run_plan_job（patch 掉 SessionLocal 指向内存库）→ trigger=auto 批次。"""
    from app.api.test_plan import add_cases, create_plan
    from app.schemas.test_plan import PlanCasesIn, PlanCreateIn
    from app.services.scheduler import run_plan_job

    u = db.get(User, 1)
    plan_id = create_plan(PlanCreateIn(project_id=100, name="定时计划", runner="mac-01"),
                          db, u)["data"]["id"]
    add_cases(plan_id, PlanCasesIn(case_ids=[1001, 1002]), db, u)
    p = db.get(TestPlan, plan_id)
    p.schedule_enabled = True
    p.schedule_cron = "0 3 * * *"
    db.commit()

    # run_plan_job 内部 `from app.db.session import SessionLocal`——patch 该模块属性即可注入内存库
    with patch("app.db.session.SessionLocal", _Session):
        run_plan_job(plan_id)

    pr = (db.query(TestPlanRun)
          .filter(TestPlanRun.plan_id == plan_id).order_by(TestPlanRun.id.desc()).first())
    assert pr is not None and pr.trigger == "auto", pr
    assert pr.case_count == 2
    n = db.query(ExecRun).filter(ExecRun.batch_id == pr.batch_id).count()
    assert n == 2

    # schedule_enabled=False 时 job 空转不下发
    p2 = db.get(TestPlan, plan_id)
    p2.schedule_enabled = False
    db.commit()
    before = db.query(TestPlanRun).count()
    with patch("app.db.session.SessionLocal", _Session):
        run_plan_job(plan_id)
    assert db.query(TestPlanRun).count() == before, "关闭定时后不应再下发"
    print("OK scheduled job path")


def main():
    db = _Session()
    seed(db)
    try:
        test_crud_and_cases(db)
        test_schedule(db)
        test_run_real_dispatch(db)
        test_scheduled_job_path(db)
    finally:
        db.close()
    print("OK test_test_plan")


if __name__ == "__main__":
    main()
