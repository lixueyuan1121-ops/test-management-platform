"""测评任务多设备分片下发自测。
运行: cd backend && python -m scripts.test_eval_multi_device

覆盖:
- _resolve_runners: 多台 runners 去重保序 / 单台 / auto 取在线执行机 / auto 无设备报错 / 空报错
- dispatch_task_runs 多台分片:按会话组轮转分片到多台;总 run 数不变(不丢不重);
  多轮会话整组落同一台(上下文不断);A/B 单轮各自成组可分散
- run_task 端点:runners 多台下发,返回 runners 列表;单台向后兼容
"""
import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.core.enums import EvalRunStatus
from app.db.session import Base, get_db
from app.main import app
from app.models import EvalQuery, EvalRun, Project, RunnerDevice, User
from app.models.ai_eval import EvalTask
from app.api.eval_task import _resolve_runners, dispatch_task_runs

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


def _dev(rid, seen_ago_sec=None):
    d = RunnerDevice(owner_id=1, runner_id=rid, name=rid, platform="web", token=f"tk-{rid}")
    if seen_ago_sec is not None:
        d.last_seen_at = datetime.utcnow() - timedelta(seconds=seen_ago_sec)
    _s.add(d); _s.commit()
    return d


def _query(qid, group=None, turn=0):
    q = EvalQuery(id=qid, project_id=1, title=f"题{qid}", prompt=f"p{qid}",
                  conversation_group=group, turn_index=turn)
    _s.add(q); _s.commit()
    return q


def _task(qids):
    t = EvalTask(project_id=1, name="T", query_ids=json.dumps(qids))
    _s.add(t); _s.commit()
    return t


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True, status="active"),
        Project(id=1, name="P1", code="p1", status="active"),
    ])
    _s.commit()


def test_resolve_runners():
    # 多台:去重保序
    assert _resolve_runners(_s, None, ["a", "b", "a", "c"]) == ["a", "b", "c"]
    # 多台含空串:过滤
    assert _resolve_runners(_s, None, ["a", "", "  ", "b"]) == ["a", "b"]
    # 单台
    assert _resolve_runners(_s, "mac-01", None) == ["mac-01"]
    # runners 优先于 runner
    assert _resolve_runners(_s, "mac-01", ["x", "y"]) == ["x", "y"]
    # 空 → 报错
    for bad in [(None, None), ("", None), ("  ", []), (None, ["", "  "])]:
        try:
            _resolve_runners(_s, *bad); assert False, f"{bad} 应报错"
        except ValueError:
            pass
    print("OK _resolve_runners 基础")


def test_resolve_auto():
    # auto 无在线设备 → 报错
    try:
        _resolve_runners(_s, "auto", None); assert False
    except ValueError as e:
        assert "自动调度失败" in str(e)
    _dev("on-1", seen_ago_sec=10)
    _dev("on-2", seen_ago_sec=10)
    _dev("off-1", seen_ago_sec=9999)   # 离线不入池
    got = _resolve_runners(_s, "auto", None)
    assert got == ["on-1", "on-2"], f"auto 应取在线执行机(升序),得 {got}"
    print("OK _resolve_runners auto")


def test_shard_singletons():
    # 4 道单轮题,分到 2 台 → 每台 2 条,总数 4(不丢不重)
    t = _task([10, 11, 12, 13])
    for qid in [10, 11, 12, 13]:
        _query(qid)
    created, batch = dispatch_task_runs(_s, t, ["r1", "r2"], "namiwork", None, {}, None, 1)
    _s.commit()
    rows = [_s.get(EvalRun, rid) for rid in created]
    assert len(rows) == 4, "总 run 数应=题数"
    by_runner = {}
    for r in rows:
        by_runner.setdefault(r.runner, []).append(r)
    assert set(by_runner) == {"r1", "r2"}, f"应铺到两台,得 {set(by_runner)}"
    assert len(by_runner["r1"]) == 2 and len(by_runner["r2"]) == 2, "两台应均衡各 2 条"
    print("OK 单轮分片均衡")


def test_shard_multiturn_stays_together():
    # 组G 三轮 + 两道单轮,分到 2 台:组G 三轮必须整组同机
    t = _task([20, 21, 22, 23, 24])
    _query(20, group="G", turn=0); _query(21, group="G", turn=1); _query(22, group="G", turn=2)
    _query(23); _query(24)
    created, batch = dispatch_task_runs(_s, t, ["ra", "rb"], "namiwork", None, {}, None, 1)
    _s.commit()
    rows = [_s.get(EvalRun, rid) for rid in created]
    g_runners = set()
    for r in rows:
        p = json.loads(r.payload)
        if p.get("conversation_group") == "G":
            g_runners.add(r.runner)
    assert len(g_runners) == 1, f"多轮会话组 G 必须整组落同一台,却落到 {g_runners}"
    assert len(rows) == 5
    print("OK 多轮整组同机")


def test_shard_ab_compare():
    # A/B 对比 + 2 道单轮题 + 2 台:每题 2 条(A/B),共 4 条;A/B 各自成组可分散
    t = _task([30, 31])
    _query(30); _query(31)
    created, batch = dispatch_task_runs(_s, t, ["m1", "m2"], "namiwork", None,
                                        {"model": "x"}, {"model": "y"}, 1)
    _s.commit()
    rows = [_s.get(EvalRun, rid) for rid in created]
    assert len(rows) == 4, f"A/B 每题两条,应 4 条,得 {len(rows)}"
    groups = {p.get("compare_group") for p in (json.loads(r.payload) for r in rows)}
    assert groups == {"A", "B"}, f"应有 A/B 两组,得 {groups}"
    # 分到两台(轮转:4 个独立组 → 2+2)
    assert len({r.runner for r in rows}) == 2, "A/B 单轮各成组,应能分散到两台"
    print("OK A/B 分片")


def test_run_endpoint_multi():
    t = _task([40, 41, 42])
    for qid in [40, 41, 42]:
        _query(qid)
    # 端点:runners 多台
    d = client.post(f"/api/eval-tasks/{t.id}/run", json={
        "runners": ["e1", "e2", "e3"], "target_engine": "namiwork",
    }).json()
    assert d["code"] == 0, d
    assert set(d["data"]["runners"]) == {"e1", "e2", "e3"}
    rows = [_s.get(EvalRun, rid) for rid in d["data"]["run_ids"]]
    assert {r.runner for r in rows} == {"e1", "e2", "e3"}, "3 题 3 台各一"

    # 端点:单台向后兼容(runner 字符串)
    t2 = _task([43])
    _query(43)
    d2 = client.post(f"/api/eval-tasks/{t2.id}/run", json={
        "runner": "solo", "target_engine": "namiwork",
    }).json()
    assert d2["code"] == 0, d2
    assert d2["data"]["runners"] == ["solo"]
    print("OK run 端点多台/单台")


def main():
    _seed()
    test_resolve_runners()
    test_resolve_auto()
    test_shard_singletons()
    test_shard_multiturn_stays_together()
    test_shard_ab_compare()
    test_run_endpoint_multi()
    print("OK test_eval_multi_device")


if __name__ == "__main__":
    main()
