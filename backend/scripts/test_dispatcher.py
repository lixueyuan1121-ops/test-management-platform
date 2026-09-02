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


def _dev(rid, platform="web", seen_ago_sec=None, caps="func,eval"):
    d = RunnerDevice(owner_id=1, runner_id=rid, name=rid, platform=platform,
                     capabilities=caps, token=f"tk-{rid}")
    if seen_ago_sec is not None:
        d.last_seen_at = datetime.utcnow() - timedelta(seconds=seen_ago_sec)
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


def test_capability_filter():
    """设备能力标识(func/eval)精准下发:auto 按能力过滤,手动指定能力不符报错。

    根因:两套 runner 抢同一客户端不能并行,一台机实际只承接一类任务。此前测评 auto
    (online_eval_runners)不看能力、把所有在线设备铺开 → 测评任务错派到只跑功能测试的机器。
    """
    from app.services.dispatcher import online_eval_runners

    _s.query(ExecRun).delete()
    _s.query(RunnerDevice).delete()
    _s.commit()

    # 三台在线设备:全能力 / 只功能 / 只测评
    _dev("both-01", "web", seen_ago_sec=10, caps="func,eval")
    _dev("func-only", "web", seen_ago_sec=10, caps="func")
    _dev("eval-only", "web", seen_ago_sec=10, caps="eval")

    # 功能 auto(pick_runner)只在 func 能力设备里选,绝不选 eval-only
    picks = {pick_runner(_s, "web") for _ in range(5)}   # 负载相同,取 id 最小者稳定
    assert picks <= {"both-01", "func-only"}, f"功能 auto 不应选中 eval-only: {picks}"

    # 测评 auto(online_eval_runners)只返回含 eval 的设备,绝不含 func-only
    ev = set(online_eval_runners(_s))
    assert ev == {"both-01", "eval-only"}, f"测评 auto 应仅含 eval 能力设备,实际 {ev}"

    # 手动下发功能用例到只测评的机器 → 400 拦截
    d = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "eval-only", "test_case_ids": [1],
    }).json()
    assert d["code"] == 400 and "功能测试" in d["msg"], d

    # 手动下发功能用例到功能机 → 放行
    d2 = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "func-only", "test_case_ids": [1],
    }).json()
    assert d2["code"] == 0, d2

    # 手动下发测评到只功能的机器 → _check_eval_capability 抛 ValueError
    from app.api.eval_task import _check_eval_capability
    try:
        _check_eval_capability(_s, "func-only")
        assert False, "只功能设备应拒绝测评下发"
    except ValueError as e:
        assert "对话测评" in str(e), e
    _check_eval_capability(_s, "eval-only")   # 测评机放行(不抛)
    _check_eval_capability(_s, "ghost-xx")    # 未登记设备放行(向后兼容)

    # 未登记 runner:功能手动下发也放行(向后兼容旧 runner)
    d3 = client.post("/api/exec-queue/enqueue-cases", json={
        "project_id": 100, "runner": "ghost-xx", "test_case_ids": [1],
    }).json()
    assert d3["code"] == 0, d3

    _s.query(ExecRun).delete()
    _s.query(RunnerDevice).delete()
    _s.commit()
    print("OK capability filter")


def main():
    _seed()
    test_pick()
    test_enqueue_auto()
    test_reassign()
    test_shared_token_heartbeat()
    test_capability_filter()
    print("OK test_dispatcher")


if __name__ == "__main__":
    main()
