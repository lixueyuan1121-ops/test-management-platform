"""list_pending 多轮会话「整组不拆」保证的自测(纯函数 _take_whole_groups + 端点级)。
运行: cd backend && python -m scripts.test_eval_queue_grouping

背景:同一多轮会话(conversation_group 相同)的各轮 run 必须整组下发给同一执行机、同一轮询批次;
否则轮次0所在对话在上批结束(pool.close)时已关,轮次1接不上上下文。故 fetchPending 取 limit 条时,
绝不能把一个会话组切一半——要么整组给、要么整组不给(顺延下批)。

覆盖:
  A. 纯函数 _take_whole_groups:limit 落在组中间时,整组纳入(可超 limit),绝不半组;
  B. 单轮 run(无 conversation_group)按条独立,不受影响;
  C. 组内按 id(=turn 顺序)升序;
  D. 端点 GET /api/eval-queue 返回结果里,任一 conversation_group 要么全在、要么全不在。
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import require_runner_ctx, RunnerCtx
from app.db.session import Base, get_db
from app.models import Project, EvalRun, User
from app.core.enums import EvalRunStatus
from app.api.eval_queue import _take_whole_groups, _conv_group


_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_admin = User(id=1, username="admin", name="A", password_hash="x", is_platform_admin=True, status="active")
_s.add(_admin)
_s.add(Project(id=1, name="P", code="P1", status="active"))


def _payload(group, turn):
    return json.dumps({"conversation_group": group, "turn_index": turn}, ensure_ascii=False)


# 待执行 run(runner=mac-01):组A 三轮(id 1,2,3)、单轮(id 4,5)、组B 两轮(id 6,7)
_rows_spec = [
    (1, "A", 0), (2, "A", 1), (3, "A", 2),
    (4, None, 0), (5, None, 0),
    (6, "B", 0), (7, "B", 1),
]
for rid, grp, turn in _rows_spec:
    _s.add(EvalRun(id=rid, project_id=1, runner="mac-01",
                   status=EvalRunStatus.pending, payload=_payload(grp, turn)))
_s.commit()


def _override_db():
    yield _s


def _fake_runner_ctx():
    return RunnerCtx(device=None)  # 共享 token 兜底:靠 query 的 runner 字符串区分


app.dependency_overrides[get_db] = _override_db
app.dependency_overrides[require_runner_ctx] = _fake_runner_ctx
client = TestClient(app)


def _all_pending():
    return _s.query(EvalRun).filter(EvalRun.status == EvalRunStatus.pending).order_by(EvalRun.id).all()


def test_take_whole_groups_never_splits_a_group():
    rows = _all_pending()
    # limit=2 落在组A(3轮)中间:必须整组纳入 → [1,2,3](超 limit 也不切半组)
    sel = _take_whole_groups(rows, 2)
    ids = [r.id for r in sel]
    assert ids == [1, 2, 3], f"组A 必须整组返回、不被 limit 切半,得 {ids}"


def test_take_whole_groups_singletons_independent():
    rows = _all_pending()
    # limit=4:组A(3) + 单轮 id4(1) = 4 达标停止 → [1,2,3,4]
    sel = _take_whole_groups(rows, 4)
    ids = [r.id for r in sel]
    assert ids == [1, 2, 3, 4], f"单轮应按条独立纳入,得 {ids}"


def test_take_whole_groups_group_order_and_completeness():
    rows = _all_pending()
    # limit=5:组A(3)+单4+单5=5 达标 → 不应带出组B 的任何一轮
    sel = _take_whole_groups(rows, 5)
    ids = [r.id for r in sel]
    assert ids == [1, 2, 3, 4, 5], f"达到 limit 后不应纳入下一组,得 {ids}"
    groups = {_conv_group(r) for r in sel}
    assert "B" not in groups, "组B 不应被部分带出"


def test_take_whole_groups_within_group_sorted_by_turn():
    rows = _all_pending()
    sel = _take_whole_groups(rows, 2)
    a = [r.id for r in sel if _conv_group(r) == "A"]
    assert a == [1, 2, 3], f"组内应按 id(=turn 顺序)升序,得 {a}"


def test_endpoint_returns_whole_groups_only():
    r = client.get("/api/eval-queue?runner=mac-01&limit=2")
    assert r.json()["code"] == 0, r.text
    runs = r.json()["data"]
    # 端点结果里,出现的每个 conversation_group 必须「全在」——统计各组轮数应等于该组总待执行数
    got_groups = {}
    for run in runs:
        g = (run.get("payload") or {}).get("conversation_group")
        if g:
            got_groups[g] = got_groups.get(g, 0) + 1
    assert got_groups.get("A") == 3, f"组A 必须整组(3轮)出现在端点结果,得 {got_groups}"
    assert "B" not in got_groups, f"组B 不应被部分带出,得 {got_groups}"


def main():
    test_take_whole_groups_never_splits_a_group()
    test_take_whole_groups_singletons_independent()
    test_take_whole_groups_group_order_and_completeness()
    test_take_whole_groups_within_group_sorted_by_turn()
    test_endpoint_returns_whole_groups_only()
    print("OK test_eval_queue_grouping")


if __name__ == "__main__":
    main()
