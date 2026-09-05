"""测评任务「按执行批次的历史」自测(python -m scripts.test_eval_task_batches)。

背景:每次执行任务=新 batch_id 的整组 run,旧批次 run 全保留。历史按批次呈现:列该任务历次批次
(时间/条数/完成数/通过率/均分),前端下拉切换查看任一批。task_runs?batch_id= 已支持拉某批。
本测覆盖 list_task_batches 端点:聚合正确、按时间倒序、跨任务隔离。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import EvalRun, EvalTask, Project, User
from app.core.enums import EvalRunStatus

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
    t = EvalTask(id=1, project_id=1, name="任务X", query_ids="[]")
    t2 = EvalTask(id=2, project_id=1, name="任务Y", query_ids="[]")
    s.add_all([t, t2]); s.commit()
    # 任务1:批次 b1(2条:1过1不过,评分4/2)、b2(1条:通过,评分5)
    s.add_all([
        EvalRun(project_id=1, eval_task_id=1, batch_id="b1", status=EvalRunStatus.judged, verdict="pass", score=4),
        EvalRun(project_id=1, eval_task_id=1, batch_id="b1", status=EvalRunStatus.judged, verdict="fail", score=2),
        EvalRun(project_id=1, eval_task_id=1, batch_id="b2", status=EvalRunStatus.judged, verdict="pass", score=5),
    ])
    # 任务2:批次 bx(1条)——用于验证跨任务隔离
    s.add(EvalRun(project_id=1, eval_task_id=2, batch_id="bx", status=EvalRunStatus.judged, verdict="pass", score=5))
    s.commit(); s.close()


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import eval_task as et
    from app.db.session import get_db
    from app.core.deps import get_current_user

    app = FastAPI()
    app.include_router(et.router)

    def _odb():
        db = _Session()
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db] = _odb
    app.dependency_overrides[get_current_user] = lambda: _Session().get(User, 1)
    return TestClient(app)


def test_batches_aggregation_and_order():
    _seed()
    c = _client()
    r = c.get("/api/eval-tasks/1/batches")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    batches = data["batches"]
    assert len(batches) == 2, f"任务1 应有 2 个批次,实际 {len(batches)}"
    # 只含本任务的批次(隔离):不含任务2 的 bx
    ids = {b["batch_id"] for b in batches}
    assert ids == {"b1", "b2"}, f"批次应只含本任务的,实际 {ids}"
    by = {b["batch_id"]: b for b in batches}
    assert by["b1"]["total"] == 2 and by["b1"]["passed"] == 1 and by["b1"]["failed"] == 1, by["b1"]
    assert abs(by["b1"]["avg_score"] - 3.0) < 0.01, f"b1 均分应 3.0,实际 {by['b1']['avg_score']}"
    assert by["b2"]["total"] == 1 and by["b2"]["passed"] == 1, by["b2"]
    print("✓ 批次聚合正确 + 跨任务隔离")


def test_batches_empty_task():
    _seed()
    c = _client()
    data = c.get("/api/eval-tasks/2/batches").json()["data"]
    assert len(data["batches"]) == 1 and data["batches"][0]["batch_id"] == "bx"
    print("✓ 任务2 只见自己的批次")


def main():
    test_batches_aggregation_and_order()
    test_batches_empty_task()
    print("\n✅ 任务批次历史 全部通过")


if __name__ == "__main__":
    main()
