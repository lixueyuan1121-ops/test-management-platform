"""dimension-stats 按被测产品(by_engine)分组自测(内存库 + TestClient)。

构造同项目 namiwork/workbuddy 各若干已判定 run(带 dimension),断言 by_engine 拆两组、
各组 overall_rate 与该引擎数据吻合。默认(不传 by_engine)返回结构不含 by_engine(向后兼容)。
"""
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db.session import Base, get_db
from app.models import EvalRun, EvalQuery, Project, User
from app.core.enums import EvalRunStatus
from app.main import app
from app.core.deps import get_current_user

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)

s = _Session()
s.add_all([
    User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"),
    Project(id=1, name="P", code="P1", status="active"),
    EvalQuery(id=1, project_id=1, title="q1", prompt="p", dimension="thinking"),
    EvalQuery(id=2, project_id=1, title="q2", prompt="p", dimension="tool_use"),
])
# namiwork:2 题都 pass(通过率 100%);workbuddy:1 pass 1 fail(通过率 50%)
s.add_all([
    EvalRun(project_id=1, eval_query_id=1, target_engine="namiwork", status=EvalRunStatus.judged, verdict="pass"),
    EvalRun(project_id=1, eval_query_id=2, target_engine="namiwork", status=EvalRunStatus.judged, verdict="pass"),
    EvalRun(project_id=1, eval_query_id=1, target_engine="workbuddy", status=EvalRunStatus.judged, verdict="pass"),
    EvalRun(project_id=1, eval_query_id=2, target_engine="workbuddy", status=EvalRunStatus.judged, verdict="fail"),
])
s.commit()

_FAKE = type("U", (), {"id": 1, "is_platform_admin": True})()
app.dependency_overrides[get_current_user] = lambda: _FAKE
app.dependency_overrides[get_db] = lambda: (yield s)
client = TestClient(app)

# 默认不传 by_engine：返回结构无 by_engine（向后兼容）
r0 = client.get("/api/eval-judge/dimension-stats", params={"project_id": 1}).json()
assert r0["code"] == 0 and "by_engine" not in r0["data"], "默认不应含 by_engine"

# by_engine=true：两组，通过率对得上
r = client.get("/api/eval-judge/dimension-stats", params={"project_id": 1, "by_engine": "true"}).json()
assert r["code"] == 0, r
be = {e["engine"]: e for e in r["data"]["by_engine"]}
assert set(be) == {"namiwork", "workbuddy"}, f"引擎组错: {list(be)}"
assert be["namiwork"]["overall_rate"] == 100.0, f"namiwork 应 100%: {be['namiwork']['overall_rate']}"
assert be["workbuddy"]["overall_rate"] == 50.0, f"workbuddy 应 50%: {be['workbuddy']['overall_rate']}"
assert be["namiwork"]["label"] == "纳米Work" and be["workbuddy"]["label"] == "WorkBuddy"
print("PASS: dimension-stats by_engine 分组正确(纳米100% / WorkBuddy50%)")
