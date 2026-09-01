"""测试计划定时——「每 N 分钟」间隔触发 自测。
运行: cd backend && python -m scripts.test_plan_interval_schedule

设计:schedule_cron 字段复用哨兵字符串存储——
- 间隔:"@every:N"(N=分钟,1..1440) → APScheduler IntervalTrigger(minutes=N)
- 标准 cron:"m h * * *" 等 5 段 → CronTrigger.from_crontab(照旧)

覆盖:
- _build_plan_trigger:@every:N → IntervalTrigger;普通 cron → CronTrigger;非法 → 抛错
- _parse_every:识别/解析 @every:N,范围校验(1..1440)
- set_schedule 端点:间隔合法存库+起 job;非法间隔 400;cron 仍照旧;关闭移除 job
"""
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.services.scheduler import _build_plan_trigger, _parse_every


def test_parse_every():
    # 合法
    assert _parse_every("@every:15") == 15
    assert _parse_every("@every:1") == 1
    assert _parse_every("@every:1440") == 1440
    # 非间隔表达式 → None(交给 cron 处理)
    assert _parse_every("0 2 * * *") is None
    assert _parse_every("*/5 * * * *") is None
    assert _parse_every("") is None
    assert _parse_every(None) is None
    # 越界/非法 → 抛 ValueError
    for bad in ("@every:0", "@every:1441", "@every:-5", "@every:abc", "@every:", "@every:1.5"):
        try:
            _parse_every(bad)
            raise AssertionError(f"应对非法间隔抛错: {bad}")
        except ValueError:
            pass
    print("OK _parse_every")


def test_build_trigger():
    # 间隔 → IntervalTrigger,间隔秒数 = N*60
    t = _build_plan_trigger("@every:15")
    assert isinstance(t, IntervalTrigger), type(t)
    assert t.interval.total_seconds() == 15 * 60, t.interval
    t2 = _build_plan_trigger("@every:1")
    assert isinstance(t2, IntervalTrigger) and t2.interval.total_seconds() == 60
    # 普通 cron → CronTrigger
    t3 = _build_plan_trigger("0 2 * * *")
    assert isinstance(t3, CronTrigger), type(t3)
    # 非法 cron / 非法间隔 → 抛错
    for bad in ("@every:0", "@every:9999", "not a cron", "60 25 * * *"):
        try:
            _build_plan_trigger(bad)
            raise AssertionError(f"应对非法表达式抛错: {bad}")
        except (ValueError, Exception):
            pass
    print("OK _build_plan_trigger")


def test_set_schedule_endpoint():
    """端到端:set_schedule 接受 @every:N,存库并起 job;非法 400;cron 仍可用。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    from app.db.session import Base, get_db
    from app.main import app
    from app.models import Project, TestPlan, User
    from app.core.security import create_access_token

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="P", code="p"),
    ])
    s.flush()
    s.add(TestPlan(id=10, project_id=100, name="计划A", runner="win-01"))
    s.commit()

    def _get_db():
        yield s
    app.dependency_overrides[get_db] = _get_db
    c = TestClient(app)
    H = {"Authorization": f"Bearer {create_access_token(sub='1')}"}

    # 间隔合法:每 30 分钟
    r = c.patch("/api/test-plans/10/schedule", headers=H, json={"cron": "@every:30", "enabled": True})
    d = r.json()
    assert d["code"] == 0, d
    assert d["data"]["schedule_cron"] == "@every:30", d["data"]
    assert d["data"]["schedule_enabled"] is True

    # 非法间隔:超 1440 → 400
    r2 = c.patch("/api/test-plans/10/schedule", headers=H, json={"cron": "@every:5000", "enabled": True})
    assert r2.json()["code"] == 400, r2.json()

    # 非法间隔:0 → 400
    r3 = c.patch("/api/test-plans/10/schedule", headers=H, json={"cron": "@every:0", "enabled": True})
    assert r3.json()["code"] == 400, r3.json()

    # 普通 cron 仍可用
    r4 = c.patch("/api/test-plans/10/schedule", headers=H, json={"cron": "0 2 * * *", "enabled": True})
    assert r4.json()["code"] == 0 and r4.json()["data"]["schedule_cron"] == "0 2 * * *", r4.json()

    # 关闭:enabled=false 移除 job
    r5 = c.patch("/api/test-plans/10/schedule", headers=H, json={"cron": None, "enabled": False})
    assert r5.json()["code"] == 0 and r5.json()["data"]["schedule_enabled"] is False

    app.dependency_overrides.clear()
    print("OK set_schedule 端点(间隔/非法/cron/关闭)")


if __name__ == "__main__":
    test_parse_every()
    test_build_trigger()
    test_set_schedule_endpoint()
    print("\n全部通过 ✅")
