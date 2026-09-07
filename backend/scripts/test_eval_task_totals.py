"""测评任务列表页耗时/算力豆聚合 _batch_totals 自测(python -m scripts.test_eval_task_totals)。

背景:列表页「耗时/算力豆」列口径 2026-09 由墙钟(duration_ms)改为上报耗时(reported_duration,纯秒),
对齐对话页「已完成 Ns」。本测覆盖 _batch_totals 的 total_reported_duration_s 聚合:
- 只聚合 last_batch_id 那一批;纯秒字符串容错累加;历史脏值("23m 38s")取首数字兜底;无批次返回 0。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import EvalRun, EvalTask, Project, User
from app.core.enums import EvalRunStatus
from app.api.eval_task import _batch_totals, _parse_seconds

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)


def _seed():
    s = _Session()
    s.query(EvalRun).delete(); s.query(EvalTask).delete()
    s.query(Project).delete(); s.query(User).delete()
    s.add_all([
        User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"),
        Project(id=1, name="P", code="P1", status="active"),
    ])
    # 任务1 当前批次 b2;b1 是旧批次(不该被计入)
    t1 = EvalTask(id=1, project_id=1, name="任务X", query_ids="[]", last_batch_id="b2")
    t2 = EvalTask(id=2, project_id=1, name="空任务", query_ids="[]")  # 无 last_batch_id
    s.add_all([t1, t2]); s.commit()
    s.add_all([
        # 旧批次 b1:不计入(last_batch_id 是 b2)
        EvalRun(project_id=1, eval_task_id=1, batch_id="b1", status=EvalRunStatus.judged,
                reported_duration="999", duration_ms=999000, bean_cost="-99"),
        # 当前批次 b2:15 + 1418 + 脏值"23m 38s"(取23) + None(0) = 1456
        EvalRun(project_id=1, eval_task_id=1, batch_id="b2", status=EvalRunStatus.judged,
                reported_duration="15", duration_ms=25000, bean_cost="-3"),
        EvalRun(project_id=1, eval_task_id=1, batch_id="b2", status=EvalRunStatus.judged,
                reported_duration="1418", duration_ms=1500000, bean_cost="-12"),
        EvalRun(project_id=1, eval_task_id=1, batch_id="b2", status=EvalRunStatus.judged,
                reported_duration="23m 38s", duration_ms=None, bean_cost="+5"),
        EvalRun(project_id=1, eval_task_id=1, batch_id="b2", status=EvalRunStatus.judged,
                reported_duration=None, duration_ms=None, bean_cost=None),
    ])
    s.commit(); s.close()


def test_parse_seconds():
    cases = [("1418", 1418), ("15s", 15), ("23m 38s", 23), ("", 0), (None, 0),
             ("—", 0), ("无", 0), (296, 296)]
    for inp, exp in cases:
        got = _parse_seconds(inp)
        assert got == exp, f"_parse_seconds({inp!r}) = {got}, 期望 {exp}"
    print("✓ _parse_seconds 容错正确")


def test_batch_totals_reported():
    _seed()
    s = _Session()
    t1 = s.get(EvalTask, 1)
    out = _batch_totals(s, t1)
    # total_reported_duration_s: 只计 b2 = 15 + 1418 + 23 + 0 = 1456(旧批次 b1 的 999 不计入)
    assert out["total_reported_duration_s"] == 1456, out
    # 墙钟仍聚合(兼容):25000 + 1500000 + 0 + 0 = 1525000(b1 的 999000 不计入)
    assert out["total_duration_ms"] == 1525000, out
    # 算力豆:-3 + -12 + 5 + 0 = -10
    assert out["total_bean_cost"] == -10, out
    s.close()
    print("✓ total_reported_duration_s 只聚合当前批次 + 容错累加 + 墙钟/豆兼容")


def test_batch_totals_no_batch():
    _seed()
    s = _Session()
    t2 = s.get(EvalTask, 2)  # 无 last_batch_id
    out = _batch_totals(s, t2)
    assert out == {"total_duration_ms": 0, "total_bean_cost": 0, "total_reported_duration_s": 0}, out
    s.close()
    print("✓ 无批次任务返回全 0(含新字段,向后兼容)")


def main():
    test_parse_seconds()
    test_batch_totals_reported()
    test_batch_totals_no_batch()
    print("\n✅ 任务耗时聚合(上报口径) 全部通过")


if __name__ == "__main__":
    main()
