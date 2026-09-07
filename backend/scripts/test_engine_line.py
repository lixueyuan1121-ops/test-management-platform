"""_result_summary 的 engine_line(跨产品胜率)自测。内存库,构造同批多产品 run,断言 engine_line 文案。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import EvalRun, EvalTask, Project, User
from app.core.enums import EvalRunStatus
from app.services.eval_pipeline import _result_summary

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)

s = _Session()
s.add_all([
    User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"),
    Project(id=1, name="P", code="P1", status="active"),
    EvalTask(id=1, project_id=1, name="对比任务", query_ids="[]"),
])
# 同批 b1:namiwork 2/2 pass(100%),workbuddy 1 pass 1 fail(50%)
s.add_all([
    EvalRun(project_id=1, eval_task_id=1, batch_id="b1", target_engine="namiwork", status=EvalRunStatus.judged, verdict="pass"),
    EvalRun(project_id=1, eval_task_id=1, batch_id="b1", target_engine="namiwork", status=EvalRunStatus.judged, verdict="pass"),
    EvalRun(project_id=1, eval_task_id=1, batch_id="b1", target_engine="workbuddy", status=EvalRunStatus.judged, verdict="pass"),
    EvalRun(project_id=1, eval_task_id=1, batch_id="b1", target_engine="workbuddy", status=EvalRunStatus.judged, verdict="fail"),
])
s.commit()

summ = _result_summary(s, 1, "b1")
assert summ["engine_line"], f"应有 engine_line: {summ}"
el = summ["engine_line"]
assert "纳米Work 通过率 100%" in el, f"纳米应 100%: {el}"
assert "WorkBuddy 通过率 50%" in el, f"WorkBuddy 应 50%: {el}"
print(f"PASS: engine_line = {el}")

# 单产品批次不应有 engine_line(无对比意义)
s.add(EvalRun(project_id=1, eval_task_id=1, batch_id="b2", target_engine="namiwork", status=EvalRunStatus.judged, verdict="pass"))
s.commit()
summ2 = _result_summary(s, 1, "b2")
assert summ2["engine_line"] is None, f"单产品不应有 engine_line: {summ2['engine_line']}"
print("PASS: 单产品批次无 engine_line")
s.close()
