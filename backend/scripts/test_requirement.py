"""需求实体与覆盖率(建议项⑥)自测。
运行: cd backend && python -m scripts.test_requirement

覆盖:
- upsert_requirement: url 幂等去重/无 url 返回 None/裸 url 标题回填
- CRUD: 建(url 命中转更新)/改(release 挂摘,0=摘除)/删(用例 requirement_id 置空)
- 挂链: link(改挂)/unlink/跨项目拒
- 覆盖四态: uncovered→notrun→failing→partial→passed;重试链有效口径(失败被重试通过后翻正)
- 质量卡需求腿: 挂 release 的需求进 req_coverage;没挂需求的版本为 None
"""
import json
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.core.enums import ExecStatus, ReviewStatus
from app.db.session import Base, get_db
from app.main import app
from app.models import (
    ExecRun, Project, ReleaseRecord, Requirement, TestCase, User,
)

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


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="P1", code="p1"),
        Project(id=200, name="P2", code="p2"),
    ])
    _s.flush()
    _s.add(ReleaseRecord(id=12, project_id=100, version="v2.0", release_date=date.today()))
    for cid in (1, 2, 3):
        _s.add(TestCase(id=cid, ai_task_id=1, project_id=100, title=f"用例{cid}",
                        exec_kind="gui", review_status=ReviewStatus.adopted))
    _s.add(TestCase(id=9, ai_task_id=1, project_id=200, title="别家用例", exec_kind="gui",
                    review_status=ReviewStatus.adopted))
    _s.commit()


def _run(case_id, status_, retry_of=None, flaky=False):
    r = ExecRun(project_id=100, test_case_id=case_id, runner="m", payload="{}",
                status=status_, retry_of=retry_of, flaky=flaky)
    _s.add(r)
    _s.commit()
    return r


def test_upsert():
    from app.api.requirement import upsert_requirement

    assert upsert_requirement(_s, 100, None, "无链接", 1) is None
    assert upsert_requirement(_s, 100, "  ", "空白", 1) is None
    rid = upsert_requirement(_s, 100, "https://x.feishu.cn/docx/abc", None, 1)
    assert rid is not None
    assert _s.get(Requirement, rid).title == "https://x.feishu.cn/docx/abc"  # 无标题回落 url
    rid2 = upsert_requirement(_s, 100, "https://x.feishu.cn/docx/abc", "登录改版需求", 1)
    assert rid2 == rid, "同 url 应幂等"
    assert _s.get(Requirement, rid).title == "登录改版需求", "裸 url 标题应回填"
    print("OK upsert")


def test_crud_and_link():
    from fastapi import HTTPException

    # 建(同 url 命中转更新)
    d = client.post("/api/requirements", json={
        "project_id": 100, "title": "登录改版需求v2", "url": "https://x.feishu.cn/docx/abc",
        "release_id": 12}).json()
    assert d["code"] == 0
    rid = d["data"]["id"]
    assert _s.query(Requirement).count() == 1, "同 url 不应建第二实体"
    assert d["data"]["release_id"] == 12

    # 挂用例 1、2;用例 9 跨项目拒
    d2 = client.post(f"/api/requirements/{rid}/cases", json={"case_ids": [1, 2]}).json()
    assert d2["code"] == 0 and d2["data"]["linked"] == 2
    d3 = client.post(f"/api/requirements/{rid}/cases", json={"case_ids": [9]}).json()
    assert d3["code"] == 400

    # 列表:notrun(挂了没执行)
    lst = client.get("/api/requirements", params={"project_id": 100}).json()["data"]
    assert lst[0]["case_count"] == 2 and lst[0]["state"] == "notrun", lst[0]
    assert lst[0]["release_version"] == "v2.0"

    # failing: 用例1 失败
    _run(1, ExecStatus.failed)
    lst = client.get("/api/requirements", params={"project_id": 100}).json()["data"]
    assert lst[0]["state"] == "failing", lst[0]

    # 重试链翻正: 用例1 的失败被重试通过覆盖 → partial(用例2 还没跑)
    fail_run = _s.query(ExecRun).filter_by(test_case_id=1).first()
    _run(1, ExecStatus.passed, retry_of=fail_run.id, flaky=True)
    lst = client.get("/api/requirements", params={"project_id": 100}).json()["data"]
    assert lst[0]["state"] == "partial" and lst[0]["passed"] == 1, lst[0]

    # passed: 用例2 也通过
    _run(2, ExecStatus.passed)
    lst = client.get("/api/requirements", params={"project_id": 100}).json()["data"]
    assert lst[0]["state"] == "passed", lst[0]

    # 需求下用例明细带最新结论
    cs = client.get(f"/api/requirements/{rid}/cases").json()["data"]
    assert {c["id"]: c["last_exec"] for c in cs} == {1: "passed", 2: "passed"}

    # unlink 用例2 → 覆盖只剩用例1
    d4 = client.request("DELETE", f"/api/requirements/{rid}/cases", json={"case_ids": [2]}).json()
    assert d4["code"] == 0 and d4["data"]["unlinked"] == 1

    # patch 摘除版本(0)
    d5 = client.patch(f"/api/requirements/{rid}", json={"release_id": 0}).json()
    assert d5["code"] == 0 and d5["data"]["release_id"] is None
    d6 = client.patch(f"/api/requirements/{rid}", json={"release_id": 12}).json()
    assert d6["data"]["release_id"] == 12
    print("OK crud + link + coverage states")
    return rid


def test_quality_req_leg(rid):
    d = client.get("/api/releases/quality", params={"project_id": 100}).json()["data"]
    v2 = d["items"][0]
    assert v2["req_coverage"] is not None, v2
    rc = v2["req_coverage"]
    assert rc["total"] == 1 and rc["covered"] == 1 and rc["passed"] == 1, rc

    # 摘除版本关联后 → req_coverage 变 None
    client.patch(f"/api/requirements/{rid}", json={"release_id": 0})
    d2 = client.get("/api/releases/quality", params={"project_id": 100}).json()["data"]
    assert d2["items"][0]["req_coverage"] is None
    print("OK quality req leg")


def test_delete_sets_null(rid):
    assert _s.get(TestCase, 1).requirement_id == rid
    d = client.delete(f"/api/requirements/{rid}").json()
    assert d["code"] == 0
    _s.expire_all()
    assert _s.get(TestCase, 1).requirement_id is None, "删需求应置空用例软链"
    print("OK delete sets null")


def main():
    _seed()
    test_upsert()
    rid = test_crud_and_link()
    test_quality_req_leg(rid)
    test_delete_sets_null(rid)
    print("OK test_requirement")


if __name__ == "__main__":
    main()
