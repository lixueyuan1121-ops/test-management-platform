"""性能红线告警(perf 阈值,优化项)自测。
运行: cd backend && python -m scripts.test_perf_guard

覆盖:
- parse_thresholds: 合法/坏 JSON/非法 key/非法结构过滤
- check_violations: max 超线/min 低于/达标/指标缺失跳过/多指标
- PATCH /perf/report-sets/{id}/thresholds: 白名单校验/数值校验/清空/权限(非创建者 403)
- queue_report 回写 completed → 超线推飞书卡(捕获 send);达标不发;failed 不查
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.deps import get_current_user, require_runner_ctx, RunnerCtx
from app.db.session import Base, get_db
from app.main import app
from app.models import PerfReportSet, PerfRun, Project, User
from app.services.perf_guard import check_violations, parse_thresholds

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _get_db():
    yield _s


app.dependency_overrides[get_db] = _get_db
app.dependency_overrides[get_current_user] = lambda: _s.get(User, 1)
app.dependency_overrides[require_runner_ctx] = lambda: RunnerCtx(device=None)
client = TestClient(app)


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=False),
        User(id=2, username="other", name="别人", password_hash="x", is_platform_admin=False),
        Project(id=100, name="P1", code="p1"),
    ])
    _s.flush()
    _s.add(PerfReportSet(id=5, name="v2.0 验证", created_by=1))
    _s.add(PerfReportSet(id=6, name="别人的集", created_by=2))
    _s.commit()


def test_parse_and_check():
    assert parse_thresholds(None) == {}
    assert parse_thresholds("not json") == {}
    assert parse_thresholds('{"badKey":{"max":1},"cpuPeak":"notdict","memPeak":{"max":"x"}}') == {}
    t = parse_thresholds('{"cpuPeak":{"max":80},"fpsAvg":{"min":30},"ttftMs":{"max":2000}}')
    assert t == {"cpuPeak": {"max": 80.0}, "fpsAvg": {"min": 30.0}, "ttftMs": {"max": 2000.0}}

    summary = {"cpu": {"peak": 92.5, "avg": 40}, "fps": {"avg": 25},
               "net": {"ttftMs": 1500}, "mem": {"peak": 800}}
    vio = check_violations(summary, t)
    keys = {(v["key"], v["bound"]) for v in vio}
    assert keys == {("cpuPeak", "max"), ("fpsAvg", "min")}, vio   # ttft 达标;mem 未设不查
    assert not check_violations(None, t)
    assert not check_violations(summary, {})
    # 指标缺失跳过(不误报)
    assert not check_violations({"gpu": {}}, {"gpuPeak": {"max": 50}})
    print("OK parse+check")


def test_thresholds_api():
    d = client.patch("/api/perf/report-sets/5/thresholds", json={
        "thresholds": {"cpuPeak": {"max": 80}, "fpsAvg": {"min": 30}}}).json()
    assert d["code"] == 0 and d["data"]["thresholds"]["cpuPeak"]["max"] == 80, d
    # 非法 key / 非数值
    assert client.patch("/api/perf/report-sets/5/thresholds",
                        json={"thresholds": {"nope": {"max": 1}}}).json()["code"] == 400
    assert client.patch("/api/perf/report-sets/5/thresholds",
                        json={"thresholds": {"cpuPeak": {"max": "80"}}}).json()["code"] == 400
    # 非创建者(非平台管理员) 403
    assert client.patch("/api/perf/report-sets/6/thresholds",
                        json={"thresholds": {}}).json()["code"] == 403
    # 清空
    d2 = client.patch("/api/perf/report-sets/5/thresholds", json={"thresholds": {}}).json()
    assert d2["code"] == 0 and d2["data"]["thresholds"] == {}
    # 复原供下个测试用
    client.patch("/api/perf/report-sets/5/thresholds", json={
        "thresholds": {"cpuPeak": {"max": 80}}})
    print("OK thresholds api")


def test_report_triggers_alert():
    from app.services import notify

    sent = []
    orig_send = notify._tuitui_send
    orig_cfg = (settings.TUITUI_BOT_APPID, settings.TUITUI_BOT_SECRET, settings.TUITUI_BOT_GROUP)
    notify._tuitui_send = lambda content, group=None: sent.append(content)
    settings.TUITUI_BOT_APPID, settings.TUITUI_BOT_SECRET, settings.TUITUI_BOT_GROUP = "a", "s", "g"
    try:
        # 超线 run(cpu.peak 95 > 80)→ 发卡
        r1 = PerfRun(id=901, project_id=100, report_set_id=5, runner="win-01",
                     scenario="对话", variant="v2.0", status="pending", source="dispatch")
        _s.add(r1)
        _s.commit()
        d = client.patch("/api/perf/queue/901", params={"runner": "win-01"}, json={
            "outcome": "completed",
            "meta": {"scenario": "对话", "variant": "v2.0",
                     "summary": {"cpu": {"peak": 95}}}}).json()
        assert d["code"] == 0, d
        assert len(sent) == 1, f"超线应发 1 张卡,实际 {len(sent)}"
        txt = str(sent[0])
        assert "性能红线告警" in txt and "CPU峰值" in txt and "v2.0 验证" in txt

        # 达标 run → 不发
        r2 = PerfRun(id=902, project_id=100, report_set_id=5, runner="win-01",
                     scenario="对话", variant="v2.1", status="pending", source="dispatch")
        _s.add(r2)
        _s.commit()
        client.patch("/api/perf/queue/902", params={"runner": "win-01"}, json={
            "outcome": "completed",
            "meta": {"summary": {"cpu": {"peak": 60}}}})
        assert len(sent) == 1, "达标不应发卡"

        # 采集失败 → 不查红线不发卡
        r3 = PerfRun(id=903, project_id=100, report_set_id=5, runner="win-01",
                     scenario="对话", variant="v2.2", status="pending", source="dispatch")
        _s.add(r3)
        _s.commit()
        client.patch("/api/perf/queue/903", params={"runner": "win-01"}, json={
            "outcome": "failed", "error": "采集中断",
            "meta": {"summary": {"cpu": {"peak": 99}}}})
        assert len(sent) == 1, "failed 不应发卡"
    finally:
        notify._tuitui_send = orig_send
        settings.TUITUI_BOT_APPID, settings.TUITUI_BOT_SECRET, settings.TUITUI_BOT_GROUP = orig_cfg
    print("OK report triggers alert")


def main():
    _seed()
    test_parse_and_check()
    test_thresholds_api()
    test_report_triggers_alert()
    print("OK test_perf_guard")


if __name__ == "__main__":
    main()
