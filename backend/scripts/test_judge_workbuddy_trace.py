"""验证判定层对 WorkBuddy(target_engine=workbuddy)的 run 零改动可用。

判定链路消费统一 trace + expected,不感知 target_engine——本测证明 engine=workbuddy 的 run
走 judge_run 不会因产品不同报错,返回合法 verdict(pass/fail/error 均可;无引擎配置时 error 也算链路通)。
内存库 + rollback,不落库、不调真 LLM(无引擎时走 error 分支)。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import EvalRun, EvalQuery, Project, User
from app.core.enums import EvalRunStatus
from app.services.eval_judge import judge_run

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)

s = _Session()
s.add_all([
    User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"),
    Project(id=1, name="P", code="P1", status="active"),
    EvalQuery(id=1, project_id=1, title="天气", prompt="北京天气", expected="给出今天天气"),
])
# WorkBuddy run:target_engine=workbuddy,有 answer(has_material 为真,走真判定/无引擎则 error)
run = EvalRun(id=1, project_id=1, eval_query_id=1, target_engine="workbuddy",
              status=EvalRunStatus.done, answer="北京今天阴转阵雨,最高28℃。",
              runner="wb-mac")
s.add(run); s.commit()

res = judge_run(s, run, provider=None, votes=1)
# 关键:不因 engine=workbuddy 抛异常;verdict 是三种合法值之一
assert res.get("verdict") in ("pass", "fail", "error"), f"verdict 非法: {res}"
# run 上也应落了 verdict(判定链路确实跑到)
assert run.verdict in ("pass", "fail", "error"), f"run.verdict 未落: {run.verdict}"
print(f"PASS: 判定层对 WorkBuddy run 零改动可用(verdict={res.get('verdict')})")
s.close()
