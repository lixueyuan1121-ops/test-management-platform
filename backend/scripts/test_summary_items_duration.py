"""回归:综合评价素材 _summary_items 应带上每条 run 的耗时(duration_s,纯秒)。
运行: cd backend && .venv/bin/python -m scripts.test_summary_items_duration

耗时口径复用 _parse_seconds(reported_duration):纯秒/分秒/时分秒原文都换算成秒;
缺失/解析不出→0(不渲染耗时行)。综合评价靠这个字段做「结果相当而更快=优势」的横向对比。
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import EvalRunStatus
from app.db.session import Base
from app.models import EvalRun, Project, User
from app.api import eval_task as task_mod

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add_all([
    User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"),
    Project(id=1, name="P", code="P1", status="active"),
])
_s.commit()


def _mk_run(reported_duration):
    r = EvalRun(eval_query_id=None, project_id=1, batch_id="b1", eval_task_id=1,
                runner="r1", status=EvalRunStatus.judged, payload="{}", verdict="pass",
                score=5, answer="ok", reported_duration=reported_duration)
    _s.add(r); _s.commit()
    return r


def test_duration_seconds_parsed():
    runs = [_mk_run("120"), _mk_run("2分43秒"), _mk_run(None)]
    items = task_mod._summary_items(_s, runs)
    assert items[0]["duration_s"] == 120, items[0]["duration_s"]
    assert items[1]["duration_s"] == 163, items[1]["duration_s"]
    assert items[2]["duration_s"] == 0, "缺耗时应为 0"
    print("OK _summary_items 带 duration_s(纯秒,容错)")


def main():
    test_duration_seconds_parsed()
    print("\n[PASS] _summary_items 耗时字段通过")


if __name__ == "__main__":
    main()
