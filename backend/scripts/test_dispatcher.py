"""设备池自动调度(建议项⑩)自测。
运行: cd backend && python -m scripts.test_dispatcher

覆盖:
- pick_runner: 平台匹配/在线过滤(心跳窗口+running 视为在线)/负载最小/并列取 id 小/无设备 None
- enqueue-cases runner=auto: 自动挑设备落库;同批同平台同一台;无在线设备 400
- reassign_stranded_runs: 离线设备 pending 改派到同平台在线负载最小设备(reason 打标);
  在线设备 pending 不动;running 不动;无目标原地等;未登记 runner 不动
"""
import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.core.enums import ExecStatus, ReviewStatus
from app.db.session import Base, get_db
from app.main import app
from app.models import ExecRun, Project, RunnerDevice, TestCase, User
from app.services.dispatcher import pick_runner, reassign_stranded_runs

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


def _dev(rid, platform="web", seen_ago_sec=None, eval_ago=None):
    """seen_ago_sec:最近作为【功能 runner】拉 exec-queue 的秒数前 → 设 last_seen_at + last_exec_at
    (在线且在跑功能 runner,现有功能派单测试的默认语境)。eval_ago:最近作为【测评 runner】拉 eval-queue。
    运行时感知下,派单/看板据 last_exec_at/last_eval_at 判断设备当前在跑哪类 runner。"""
    d = RunnerDevice(owner_id=1, runner_id=rid, name=rid, platform=platform, token=f"tk-{rid}")
    now = datetime.utcnow()
    if seen_ago_sec is not None:
        t = now - timedelta(seconds=seen_ago_sec)
        d.last_seen_at = t
        d.last_exec_at = t
    if eval_ago is not None:
        t = now - timedelta(seconds=eval_ago)
        d.last_eval_at = t
        if d.last_seen_at is None:
            d.last_seen_at = t
    _s.add(d)
    _s.commit()
    return d


def _run(runner, status_, case_id=None, batch="b"):
    r = ExecRun(project_id=100, test_case_id=case_id, runner=runner, payload="{}",
                status=status_, batch_id=batch)
    _s.add(r)
    _s.commit()
    return r


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="P1", code="p1"),
    ])
    _s.flush()
    for cid, plat in [(1, "web"), (2, "web"), (3, "android")]:
        _s.add(TestCase(id=cid, ai_task_id=1, project_id=100, title=f"用例{cid}",
                        exec_kind="gui", platform=plat, review_status=ReviewStatus.adopted,
                        script='[{"action":"click","selector":"k"}]'))
    _s.commit()


def test_pick():
    assert pick_runner(_s, "web") is None, "无设备应 None"
    _dev("mac-01", "web", seen_ago_sec=10)          # 在线
    _dev("mac-02", "web", seen_ago_sec=10)          # 在线
    _dev("mac-off", "web", seen_ago_sec=9999)       # 离线
    _dev("droid-01", "android", seen_ago_sec=None)  # 无心跳(离线)
    assert pick_runner(_s, "android") is None, "android 无在线设备"

    # 负载均衡:mac-01 压 2 条,应挑 mac-02
    _run("mac-01", ExecStatus.pending)
    _run("mac-01", ExecStatus.running)
    assert pick_runner(_s, "web") == "mac-02"
    # mac-02 压 3 条后应挑 mac-01
    for _ in range(3):
        _run("mac-02", ExecStatus.pending)
    assert pick_runner(_s, "web") == "mac-01"
    # running 视为在线:droid-01 挂 1 条 running → android 有可选设备
    _run("droid-01", ExecStatus.running)
    assert pick_runner(_s, "android") == "droid-01"
    # 清场
    _s.query(ExecRun).delete()
    _s.commit()
    print("OK pick_runner")


def test_enqueue_auto():
    d = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "auto", "test_case_ids": [1, 2],
    }).json()
    assert d["code"] == 0, d
    rows = [_s.get(ExecRun, rid) for rid in d["data"]["run_ids"]]
    assert len({r.runner for r in rows}) == 1, "同批同平台应落同一台"
    assert rows[0].runner in ("mac-01", "mac-02")
    assert d["data"]["runner"]["web"] == rows[0].runner

    # android 无在线设备(droid-01 的 running 已清)→ 400 整批拒绝
    d2 = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "auto", "test_case_ids": [3],
    }).json()
    assert d2["code"] == 400 and "自动调度失败" in d2["msg"], d2

    # 显式指定 runner 不受影响
    d3 = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "mac-off", "test_case_ids": [1],
    }).json()
    assert d3["code"] == 0
    _s.query(ExecRun).delete()
    _s.commit()
    print("OK enqueue auto")


def test_reassign():
    # 纯离线设备 mac-off(无 running):2 条 pending → 应改派
    p1 = _run("mac-off", ExecStatus.pending)
    p2 = _run("mac-off", ExecStatus.pending)
    # mac-off2:心跳超窗但挂着 running → 按「running 视为在线」口径不算离线(执行期心跳滞后),
    # 其 pending 不改派(保守:设备可能正干活,等 reaper 收口 running 后下轮再判)
    _dev("mac-off2", "web", seen_ago_sec=9999)
    rn = _run("mac-off2", ExecStatus.running)
    p3 = _run("mac-off2", ExecStatus.pending)
    # 在线设备 mac-01 自己的 pending 不动
    keep = _run("mac-01", ExecStatus.pending)
    # 未登记 runner 的 pending 不动
    ghost = _run("ghost-99", ExecStatus.pending)
    # android 离线设备的 pending:无同平台在线目标 → 原地等
    droid_p = _run("droid-01", ExecStatus.pending)

    moved = reassign_stranded_runs(_s)
    assert moved == 2, f"应改派 2 条,实际 {moved}"
    _s.expire_all()
    assert _s.get(ExecRun, p1.id).runner in ("mac-01", "mac-02")
    assert _s.get(ExecRun, p2.id).runner in ("mac-01", "mac-02")
    assert "[自动改派]" in (_s.get(ExecRun, p1.id).reason or "")
    assert _s.get(ExecRun, rn.id).runner == "mac-off2", "running 不应改派"
    assert _s.get(ExecRun, p3.id).runner == "mac-off2", "有 running 的设备视为在线,其 pending 不动"
    assert _s.get(ExecRun, keep.id).runner == "mac-01", "在线设备 pending 不应动"
    assert _s.get(ExecRun, ghost.id).runner == "ghost-99", "未登记 runner 不应动"
    assert _s.get(ExecRun, droid_p.id).runner == "droid-01", "无同平台目标应原地等"
    # 幂等:再跑一次无新改派
    assert reassign_stranded_runs(_s) == 0
    print("OK reassign stranded")


def test_shared_token_heartbeat():
    """共享 token 拉取应刷新对应登记设备的心跳,使其被调度正确视为在线。

    根因回归:此前心跳仅在设备 token 分支更新,共享 token 正常工作的设备在
    pick_runner/reassign/看板眼里「永远离线」——auto 不选它、reassign 误抢它的 pending。
    """
    from app.core.config import settings
    from app.services.dispatcher import touch_runner_heartbeat

    settings.RUNNER_TOKEN = "shared-tok"   # 启用共享 token 兜底
    _s.query(ExecRun).delete()
    _s.query(RunnerDevice).delete()        # 清前序用例残留设备,隔离本用例
    _s.commit()

    # 一台登记设备,从未用设备 token 上报过(last_seen_at=None)→ 修复前恒离线
    _dev("share-01", "web", seen_ago_sec=None)
    assert pick_runner(_s, "web") is None, "无心跳设备不应被选中(前置)"

    # 共享 token GET 队列(runner=share-01)→ 反查登记设备刷心跳
    r = client.get("/api/exec-queue", params={"runner": "share-01"},
                   headers={"Authorization": "Bearer shared-tok"})
    assert r.status_code == 200, r.text
    _s.expire_all()
    dev = _s.query(RunnerDevice).filter(RunnerDevice.runner_id == "share-01").first()
    assert dev.last_seen_at is not None, "共享 token 拉取后心跳应被刷新"
    assert pick_runner(_s, "web") == "share-01", "刷新心跳后 auto 应能选中它"

    # 未登记 runner 的共享 token 拉取:反查为空,无副作用(纯老 runner 向后兼容)
    before = _s.query(RunnerDevice).count()
    touch_runner_heartbeat(_s, "nobody-registers-this")
    assert _s.query(RunnerDevice).count() == before, "未登记 runner 不应新建设备"

    # 共享 token 正常工作的设备(刚刷过心跳)其 pending 不应被 reassign 误抢
    keep = _run("share-01", ExecStatus.pending)
    assert reassign_stranded_runs(_s) == 0, "在线(刚心跳)设备的 pending 不应改派"
    assert _s.get(ExecRun, keep.id).runner == "share-01"

    _s.query(ExecRun).delete()
    _s.query(RunnerDevice).filter(RunnerDevice.runner_id == "share-01").delete()
    _s.commit()
    print("OK shared-token heartbeat")


def test_runtime_kind_filter():
    """运行时 runner 类型感知:设备在跑哪类 runner(拉哪个队列)决定它接哪类任务,而非静态配置。

    根因:两套 runner 抢同一客户端不能并行,一台机同时刻只跑一类。功能 runner 拉 exec-queue
    刷 last_exec_at、测评 runner 拉 eval-queue 刷 last_eval_at;据此运行时判断当前在跑哪类,
    auto 派单/手动拦截/看板皆用,从根上杜绝「测评任务派到只跑功能测试的机器」。
    """
    from app.services.dispatcher import online_eval_runners

    _s.query(ExecRun).delete()
    _s.query(RunnerDevice).delete()
    _s.commit()

    # 三台在线设备:正在跑功能 runner / 正在跑测评 runner / 注册但没启动任何 runner(空闲)
    _dev("func-run", "web", seen_ago_sec=10)          # last_exec_at 新鲜 → 在跑功能
    _dev("eval-run", "web", eval_ago=10)              # last_eval_at 新鲜 → 在跑测评
    _dev("idle-dev", "web")                           # 两个时间戳都空 → 空闲

    # 功能 auto(pick_runner)只选在跑功能 runner 的机,绝不选在跑测评的
    picks = {pick_runner(_s, "web") for _ in range(5)}
    assert picks == {"func-run"}, f"功能 auto 应只选在跑功能 runner 的机: {picks}"

    # 测评 auto(online_eval_runners)只返回在跑测评 runner 的机
    ev = set(online_eval_runners(_s))
    assert ev == {"eval-run"}, f"测评 auto 应只含在跑测评 runner 的机: {ev}"

    # 手动下发功能用例到「正在跑测评 runner」的机 → 400 拦截
    d = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "eval-run", "test_case_ids": [1],
    }).json()
    assert d["code"] == 400 and "对话测评" in d["msg"], d

    # 手动下发功能用例到「正在跑功能 runner」的机 → 放行
    d2 = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "func-run", "test_case_ids": [1],
    }).json()
    assert d2["code"] == 0, d2

    # 手动下发功能用例到「空闲」机 → 放行(不拦,用户可能随后启动功能 runner)
    d3 = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "idle-dev", "test_case_ids": [1],
    }).json()
    assert d3["code"] == 0, d3

    # 手动下发测评到「正在跑功能 runner」的机 → _check_eval_capability 抛 ValueError
    from app.api.eval_task import _check_eval_capability
    try:
        _check_eval_capability(_s, "func-run")
        assert False, "在跑功能 runner 的机应拒绝测评下发"
    except ValueError as e:
        assert "功能测试" in str(e), e
    _check_eval_capability(_s, "eval-run")   # 在跑测评 runner:放行(不抛)
    _check_eval_capability(_s, "idle-dev")   # 空闲:放行
    _check_eval_capability(_s, "ghost-xx")   # 未登记:放行(向后兼容)

    # 未登记 runner:功能手动下发也放行(向后兼容旧共享 token runner)
    d4 = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "ghost-xx", "test_case_ids": [1],
    }).json()
    assert d4["code"] == 0, d4

    _s.query(ExecRun).delete()
    _s.query(RunnerDevice).delete()
    _s.commit()
    print("OK runtime kind filter")


def main():
    _seed()
    test_pick()
    test_enqueue_auto()
    test_reassign()
    test_shared_token_heartbeat()
    test_runtime_kind_filter()
    print("OK test_dispatcher")


if __name__ == "__main__":
    main()
