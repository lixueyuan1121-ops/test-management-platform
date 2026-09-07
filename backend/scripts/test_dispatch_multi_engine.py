"""dispatch_task_runs 多产品 fan-out 自测(python -m scripts.test_dispatch_multi_engine)。

覆盖:target_engines=[namiwork,workbuddy] + 2 题 → 每题每引擎各一条 run(共4),target_engine 落对;
显式 runner 列表下所有引擎共用该列表(跳过 auto 挑机);会话组分机 key 带 engine 前缀不写进 payload。
"""
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import EvalRun, EvalTask, EvalQuery, Project, User
import app.db.session as db_session
import app.api.eval_task as et

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)


def _seed(s):
    s.add_all([
        User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"),
        Project(id=1, name="P", code="P1", status="active"),
    ])
    s.add_all([
        EvalQuery(id=101, project_id=1, title="题1", prompt="p1"),
        EvalQuery(id=102, project_id=1, title="题2", prompt="p2"),
    ])
    s.add(EvalTask(id=1, project_id=1, name="多产品任务", query_ids=json.dumps([101, 102])))
    s.commit()


def test_fanout_two_engines():
    s = _Session()
    _seed(s)
    task = s.get(EvalTask, 1)
    # 显式 runner 列表(跳过 auto 挑机,不依赖 online 设备);两引擎共用
    created, batch_id = et.dispatch_task_runs(
        s, task, ["dev0", "dev1"], ["namiwork", "workbuddy"], None, {}, None, 1)
    s.commit()
    runs = s.query(EvalRun).filter(EvalRun.id.in_(created)).all()
    assert len(runs) == 4, f"2题×2引擎应4条,实际 {len(runs)}"
    engs = sorted(r.target_engine for r in runs)
    assert engs == ["namiwork", "namiwork", "workbuddy", "workbuddy"], f"引擎分布错: {engs}"
    # 每引擎各2条(2题)
    assert sum(1 for r in runs if r.target_engine == "workbuddy") == 2
    # payload 内 conversation_group 不含 engine 前缀(单轮题无 group,验证 payload 无 '::' 污染)
    for r in runs:
        p = json.loads(r.payload)
        assert "::" not in (p.get("conversation_group") or ""), "payload conversation_group 不应含 engine 前缀"
    s.close()
    print("✓ 两引擎 fan-out:4条 run,各引擎2条,target_engine 落对,payload 未污染")


def test_empty_engines_defaults_namiwork():
    s = _Session()
    task = s.get(EvalTask, 1)
    created, _ = et.dispatch_task_runs(s, task, ["dev0"], [], None, {}, None, 1)
    s.commit()
    runs = s.query(EvalRun).filter(EvalRun.id.in_(created)).all()
    assert all(r.target_engine == "namiwork" for r in runs), "空引擎应默认 namiwork"
    assert len(runs) == 2, f"2题单引擎应2条,实际 {len(runs)}"
    s.close()
    print("✓ 空引擎集 → 默认 namiwork(向后兼容)")


def main():
    test_fanout_two_engines()
    test_empty_engines_defaults_namiwork()
    print("\n✅ dispatch 多产品 fan-out 全部通过")


if __name__ == "__main__":
    main()
