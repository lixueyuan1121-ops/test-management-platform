"""device-overview 聚合端点自测(TestClient + 依赖覆盖 + 内存库)。
运行: cd backend && python -m scripts.test_device_overview

覆盖:
- 平台管理员看全平台设备(正常用户 403)。
- 在线判定:last_seen_at 距今 ≤60s → online=True,>60s → False。
- 每设备按 runner_id 聚合 exec_run 各状态计数(running/pending/passed/failed/blocked)。
- today 聚合(仅 exec_run.created_at 当天)与全量计数区分。
- active_runs 只含当前 running 的执行项,含标题/project/已用时长。
- owner 姓名、last_seen_at 序列化。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import ExecRun, RunnerDevice, User

# 内存库 + StaticPool:所有连接共享同一 :memory:(否则 TestClient 跨线程拿到空库)
_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _seed():
    """种 2 用户 + 2 设备 + 一组 exec_run。返回 (admin_id, normal_id)。"""
    admin = User(username="admin", name="平台管理员", password_hash="x", is_platform_admin=True)
    bob = User(username="bob", name="鲍勃", password_hash="x")
    _s.add_all([admin, bob])
    _s.flush()

    now = datetime.now()
    # last_seen_at 必须模拟真实 runner 的写入方式：exec_queue/perf/eval/probe 均用 datetime.utcnow()。
    # 用 utcnow 而非 now，才能覆盖"写入用 UTC、判定也须用 UTC"的时区一致性（否则本地测试会假绿）。
    seen_now = datetime.utcnow()
    # 设备 A: 5 秒前 heartbeat → 在线
    dev_a = RunnerDevice(owner_id=admin.id, runner_id="mac-01", name="主开发机",
                         token="tok-a", last_seen_at=seen_now - timedelta(seconds=5))
    # 设备 B: 3 分钟前 heartbeat → 离线
    dev_b = RunnerDevice(owner_id=bob.id, runner_id="win-02", name="测试温机",
                         token="tok-b", last_seen_at=seen_now - timedelta(minutes=3))
    # 设备 C: 10 秒前 heartbeat → 在线但无执行中任务(档位②在线空闲，用于排序覆盖)
    dev_c = RunnerDevice(owner_id=admin.id, runner_id="lin-03", name="空闲机",
                         token="tok-c", last_seen_at=seen_now - timedelta(seconds=10))
    # 设备 D: 在线 + 1 running(与 A 同档①，A 有 2 running 应排在 D 前，验证档内"执行中多→少")
    dev_d = RunnerDevice(owner_id=admin.id, runner_id="mac-04", name="次开发机",
                         token="tok-d", last_seen_at=seen_now - timedelta(seconds=8))
    _s.add_all([dev_a, dev_b, dev_c, dev_d])
    _s.flush()

    def run(runner_id, status, project_id=1, created_at=None, verdict=None, fail_kind=None):
        r = ExecRun(runner=runner_id, status=status, project_id=project_id,
                    payload="{}", verdict=verdict, fail_kind=fail_kind)
        _s.add(r)
        _s.flush()
        # 显式写本地时间(而非依赖 server_default):SQLite 的 CURRENT_TIMESTAMP 是 UTC,
        # 本地过午夜后与 today(本地) 跨日错位会让 today 聚合断言随钟点漂移(生产 MySQL NOW() 为本地无此问题)
        r.created_at = created_at or now
        return r

    # 设备 A: 2 running + 1 pending + 今日 1 passed(全量)+ 1 failed(今日)
    #  + 1 blocked + 1 failed(非今日,不应计入 today)
    run("mac-01", "running", project_id=10)
    run("mac-01", "running", project_id=20)
    run("mac-01", "pending")
    run("mac-01", "passed", verdict="pass")
    run("mac-01", "failed", verdict="fail", fail_kind="business")
    run("mac-01", "blocked", verdict="fail", fail_kind="selector")
    run("mac-01", "failed", verdict="fail", fail_kind="business",
        created_at=now - timedelta(days=2))
    # 设备 B: 1 running + 今日 1 passed
    run("win-02", "running", project_id=30)
    run("win-02", "passed", verdict="pass")
    # 设备 D: 1 running(档①内 running 数少于 A，应排 A 之后)
    run("mac-04", "running", project_id=40)

    _s.commit()
    return admin.id, bob.id


_seed()


def _override_db():
    yield _s


_ADMIN = SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_current_user] = lambda: _ADMIN
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def main():
    # ---- 平台管理员可访问 ----
    r = client.get("/api/devices/overview")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["total_devices"] == 4, d["total_devices"]
    assert set(x["runner_id"] for x in d["devices"]) == {"mac-01", "win-02", "lin-03", "mac-04"}
    assert d["online_devices"] == 3, d          # mac-01 + lin-03 + mac-04 在线
    assert d["running_devices"] == 3, d         # mac-01 + mac-04 + win-02(离线但有 running)

    dev_a = next(x for x in d["devices"] if x["runner_id"] == "mac-01")
    dev_b = next(x for x in d["devices"] if x["runner_id"] == "win-02")
    dev_c = next(x for x in d["devices"] if x["runner_id"] == "lin-03")

    # ---- 排序：①在线且执行中(running 多→少) → ②在线空闲 → ③离线 ----
    order = [x["runner_id"] for x in d["devices"]]
    assert order == ["mac-01", "mac-04", "lin-03", "win-02"], f"排序错误: {order}"

    # ---- 在线判定(60s 阈值) ----
    assert dev_a["online"] is True, "5s 前 heartbeat 应在 60s 内在线"
    assert dev_b["online"] is False, "3 分钟前 heartbeat 应离线"

    # ---- owner 姓名 ----
    assert dev_a["owner"]["name"] == "平台管理员"
    assert dev_b["owner"]["name"] == "鲍勃"

    # ---- 各状态计数(全量,跨日) ----
    assert dev_a["run_counts"] == {"running": 2, "pending": 1,
                                   "passed": 1, "failed": 2, "blocked": 1}, dev_a["run_counts"]
    assert dev_b["run_counts"] == {"running": 1, "pending": 0,
                                   "passed": 1, "failed": 0, "blocked": 0}, dev_b["run_counts"]

    # ---- today(仅当日) ----
    assert dev_a["today"] == {"passed": 1, "failed": 1, "blocked": 1}, dev_a["today"]
    assert dev_b["today"] == {"passed": 1, "failed": 0, "blocked": 0}, dev_b["today"]

    # ---- active_runs(仅 running,含标题/project/时长) ----
    assert len(dev_a["active_runs"]) == 2
    ar = dev_a["active_runs"][0]
    assert "run_id" in ar and "elapsed_ms" in ar and "project" in ar
    assert len(dev_b["active_runs"]) == 1
    assert dev_b["active_runs"][0]["project"] == 30

    # ---- 字段完整 ----
    for dev in (dev_a, dev_b):
        assert "id" in dev and "name" in dev and "last_seen_at" in dev

    # ---- 非平台管理员 → 403 ----
    bob = SimpleNamespace(id=2, is_platform_admin=False)
    app.dependency_overrides[get_current_user] = lambda: bob
    r403 = client.get("/api/devices/overview")
    assert r403.status_code == 403, r403.text
    app.dependency_overrides[get_current_user] = lambda: _ADMIN

    print("OK test_device_overview")


if __name__ == "__main__":
    main()
