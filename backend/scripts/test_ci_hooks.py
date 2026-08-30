"""CI 钩子（/api/hooks/run-plan + /gate）自测。
运行: cd backend && python -m scripts.test_ci_hooks

覆盖:
- 鉴权:未配 token 403 / token 错 401 / token 对放行
- run-plan:按 plan_id 触发、按 project_code+plan_name 定位、trigger=ci、manual 跳过、无计划 404
- gate:pending(有未完) → pass(全过) → fail(有失败,含 failures 摘要) → strict 把 blocked 算失败
  → min_pass_rate 放宽
- ci 批次完成+有失败 → 走飞书批次告警(捕获 send)
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.enums import ExecStatus, ReviewStatus
from app.db.session import Base, get_db
from app.main import app
from app.models import ExecRun, Project, TestCase, TestPlan, TestPlanCase, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()

def _get_db():
    yield _s


app.dependency_overrides[get_db] = _get_db
client = TestClient(app)


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="纳米Work", code="nami-work"),
    ])
    _s.flush()
    _s.add_all([
        TestCase(id=1, ai_task_id=1, project_id=100, title="登录", exec_kind="gui",
                 review_status=ReviewStatus.adopted, script='[{"action":"click","selector":"k"}]'),
        TestCase(id=2, ai_task_id=1, project_id=100, title="下单", exec_kind="gui",
                 review_status=ReviewStatus.adopted),
        TestCase(id=3, ai_task_id=1, project_id=100, title="人工检查", exec_kind="manual",
                 review_status=ReviewStatus.adopted),
    ])
    p = TestPlan(id=10, project_id=100, name="发版前冒烟", runner="mac-01")
    _s.add(p)
    _s.flush()
    _s.add_all([TestPlanCase(plan_id=10, case_id=1), TestPlanCase(plan_id=10, case_id=2),
                TestPlanCase(plan_id=10, case_id=3)])
    _s.commit()


def test_auth():
    orig = settings.CI_HOOK_TOKEN
    settings.CI_HOOK_TOKEN = ""
    try:
        r = client.post("/api/hooks/run-plan", json={"plan_id": 10})
        assert r.json()["code"] == 403, r.json()   # 未配置 → 通道关闭
    finally:
        settings.CI_HOOK_TOKEN = orig
    settings.CI_HOOK_TOKEN = "sec-token"
    r2 = client.post("/api/hooks/run-plan", json={"plan_id": 10},
                     headers={"X-CI-Token": "wrong"})
    assert r2.json()["code"] == 401, r2.json()
    print("OK auth")


def test_run_plan():
    settings.CI_HOOK_TOKEN = "sec-token"
    h = {"X-CI-Token": "sec-token"}
    # 按 plan_id
    r = client.post("/api/hooks/run-plan", json={"plan_id": 10, "note": "pipe#1"}, headers=h)
    d = r.json()
    assert d["code"] == 0, d
    assert d["data"]["case_count"] == 2, "manual 应被跳过"
    batch1 = d["data"]["batch_id"]
    from app.models import TestPlanRun
    pr = _s.query(TestPlanRun).filter_by(batch_id=batch1).first()
    assert pr and pr.trigger == "ci" and pr.started_by is None

    # 按 project_code + plan_name
    r2 = client.post("/api/hooks/run-plan",
                     json={"project_code": "nami-work", "plan_name": "发版前冒烟"}, headers=h)
    assert r2.json()["code"] == 0, r2.json()

    # 不存在
    r3 = client.post("/api/hooks/run-plan", json={"plan_id": 999}, headers=h)
    assert r3.json()["code"] == 404
    r4 = client.post("/api/hooks/run-plan", json={}, headers=h)
    assert r4.json()["code"] == 404
    print("OK run-plan")
    return batch1


def test_gate(batch_id: str):
    h = {"X-CI-Token": "sec-token"}
    # 初始全 pending → gate=pending
    g = client.get("/api/hooks/gate", params={"batch_id": batch_id}, headers=h).json()["data"]
    assert g["gate"] == "pending" and not g["finished"], g

    runs = _s.query(ExecRun).filter(ExecRun.batch_id == batch_id).order_by(ExecRun.id).all()
    # 全过 → pass
    for r in runs:
        r.status = ExecStatus.passed
        r.verdict = "pass"
    _s.commit()
    g2 = client.get("/api/hooks/gate", params={"batch_id": batch_id}, headers=h).json()["data"]
    assert g2["gate"] == "pass" and g2["pass_rate"] == 100.0, g2

    # 一条失败 → fail + failures 摘要
    runs[0].status = ExecStatus.failed
    runs[0].verdict = "fail"
    runs[0].fail_kind = "business"
    runs[0].reason = "断言失败:未见成功提示"
    _s.commit()
    g3 = client.get("/api/hooks/gate", params={"batch_id": batch_id}, headers=h).json()["data"]
    assert g3["gate"] == "fail" and g3["pass_rate"] == 50.0, g3
    assert g3["failures"] and g3["failures"][0]["fail_kind"] == "business"

    # min_pass_rate 放宽到 50 → pass
    g4 = client.get("/api/hooks/gate",
                    params={"batch_id": batch_id, "min_pass_rate": 50}, headers=h).json()["data"]
    assert g4["gate"] == "pass", g4

    # blocked 默认不拖垮门禁;strict=1 时算失败
    runs[0].status = ExecStatus.blocked
    runs[0].verdict = "blocked"
    runs[0].fail_kind = "selector"
    _s.commit()
    g5 = client.get("/api/hooks/gate", params={"batch_id": batch_id}, headers=h).json()["data"]
    assert g5["gate"] == "pass" and g5["blocked"] == 1, g5
    g6 = client.get("/api/hooks/gate",
                    params={"batch_id": batch_id, "strict": True}, headers=h).json()["data"]
    assert g6["gate"] == "fail", g6

    # 未知批次 404
    g7 = client.get("/api/hooks/gate", params={"batch_id": "nope"}, headers=h).json()
    assert g7["code"] == 404
    print("OK gate")


def test_ci_batch_notify(batch_id: str):
    """ci 批次全终态且有失败 → notify_batch_if_done 应发卡（与 auto 同策略）。"""
    from app.api.exec_queue import notify_batch_if_done
    from app.services import notify

    sent = []
    orig_send = notify._tuitui_send
    orig_cfg = (settings.TUITUI_BOT_APPID, settings.TUITUI_BOT_SECRET, settings.TUITUI_BOT_GROUP)
    notify._tuitui_send = lambda content, group=None: sent.append(content)
    settings.TUITUI_BOT_APPID, settings.TUITUI_BOT_SECRET, settings.TUITUI_BOT_GROUP = "a", "s", "g"
    try:
        runs = _s.query(ExecRun).filter(ExecRun.batch_id == batch_id).all()
        runs[0].status = ExecStatus.failed
        runs[0].verdict = "fail"
        runs[0].fail_kind = "business"
        _s.commit()
        notify_batch_if_done(_s, batch_id)
        assert len(sent) == 1, f"ci 批次完成有失败应发 1 张卡,实际 {len(sent)}"
    finally:
        notify._tuitui_send = orig_send
        settings.TUITUI_BOT_APPID, settings.TUITUI_BOT_SECRET, settings.TUITUI_BOT_GROUP = orig_cfg
    print("OK ci batch notify")


def main():
    _seed()
    test_auth()
    batch = test_run_plan()
    test_gate(batch)
    test_ci_batch_notify(batch)
    print("OK test_ci_hooks")


if __name__ == "__main__":
    main()
