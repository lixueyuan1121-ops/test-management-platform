"""失败聚类去噪自测。跑法：cd backend && .venv/bin/python -m scripts.test_fail_cluster"""
import os, sys
os.environ["DATABASE_URL"] = "sqlite:///./tmp_test_fail_cluster.db"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp_test_fail_cluster.db")
if os.path.exists(_DB):
    os.remove(_DB)

from app.main import app  # noqa: F401  触发模型注册
from app.db.session import Base, engine
from app.models import FailCluster
from app.services import fail_cluster as fc


def test_table_created():
    Base.metadata.create_all(engine)
    assert "fail_cluster" in Base.metadata.tables
    cols = set(FailCluster.__table__.c.keys())
    for c in ("id", "project_id", "release_id", "root_cause_title", "summary",
              "triage_kind", "fingerprint", "run_ids", "requirement_ids",
              "member_count", "severity", "confidence", "issue_id",
              "batch_key", "created_at"):
        assert c in cols, f"缺字段 {c}"


def test_normalize_reason():
    # 行号/毫秒数/十六进制 id/query 归一后骨架相等
    a = fc.normalize_reason("元素 #btn-123 在 1500ms 后未出现 at line 42")
    b = fc.normalize_reason("元素 #btn-456 在 2200ms 后未出现 at line 88")
    assert a == b, (a, b)
    # 不同根因不相等
    c = fc.normalize_reason("接口 /api/pay 返回 500")
    assert c != a
    # None 安全
    assert fc.normalize_reason(None) == ""


def test_fingerprint_and_cluster():
    runs = [
        {"id": 1, "triage_kind": "environment", "reason": "接口 /api/pay 返回 500", "fail_kind": None, "report": None, "requirement_id": 10},
        {"id": 2, "triage_kind": "environment", "reason": "接口 /api/pay 返回 500", "fail_kind": None, "report": None, "requirement_id": 11},
        {"id": 3, "triage_kind": "selector", "reason": "元素 #a-1 在 1000ms 后未出现", "fail_kind": None, "report": None, "requirement_id": 10},
        {"id": 4, "triage_kind": "selector", "reason": "元素 #a-2 在 3000ms 后未出现", "fail_kind": None, "report": None, "requirement_id": 12},
    ]
    clusters = fc.rule_cluster(runs)
    # run1/2 同根因(同 triage+归一reason)→一簇; run3/4 归一后同→一簇; 共2簇
    assert len(clusters) == 2, [c["fingerprint"] for c in clusters]
    by_size = sorted(clusters, key=lambda c: -c["member_count"])
    assert by_size[0]["member_count"] == 2
    # 涉及需求聚合去重
    env = next(c for c in clusters if c["triage_kind"] == "environment")
    assert sorted(env["requirement_ids"]) == [10, 11]
    sel = next(c for c in clusters if c["triage_kind"] == "selector")
    assert sorted(sel["requirement_ids"]) == [10, 12]


def test_parse_naming():
    raw = '```json\n{"root_cause_title":"支付接口500","summary":"网关异常","severity":"critical","confidence":0.9}\n```'
    d = fc.parse_naming(raw)
    assert d["root_cause_title"] == "支付接口500"
    assert d["severity"] == "critical"
    assert 0.0 <= d["confidence"] <= 1.0
    # 非法严重度回落 major
    d2 = fc.parse_naming('{"root_cause_title":"x","severity":"nonsense","confidence":2}')
    assert d2["severity"] == "major"
    assert d2["confidence"] == 1.0
    # 无 JSON → error
    assert fc.parse_naming("没有json").get("error")


def test_collect_failed_runs():
    # 建版本→需求→用例→失败执行 的完整链，验证双路径回溯
    from datetime import date
    from app.db.session import SessionLocal
    from app.models import Project, AiTask, TestCase, ExecRun, ReleaseRecord, Requirement
    from app.core.enums import ExecStatus, ExecKind
    db = SessionLocal()
    pj = Project(name="P-fc", code="P-FC"); db.add(pj); db.flush()
    rel = ReleaseRecord(project_id=pj.id, version="v9", release_date=date(2026, 9, 1)); db.add(rel); db.flush()
    req = Requirement(project_id=pj.id, title="需求A", release_id=rel.id); db.add(req); db.flush()
    # TestCase.ai_task_id NOT NULL：先建 AiTask 再引用（种子约定，见报告）
    _at = AiTask(project_id=pj.id, user_id=1, kind="testcase_gen", input_ref="r"); db.add(_at); db.flush()
    tc = TestCase(ai_task_id=_at.id, project_id=pj.id, title="用例1", requirement_id=req.id, exec_kind="gui"); db.add(tc); db.flush()
    # 链路径失败（挂用例，用例→需求→版本）
    r1 = ExecRun(project_id=pj.id, runner="m", payload="{}", status=ExecStatus.failed,
                 test_case_id=tc.id, reason="接口 500", triage_kind="environment")
    # 直路径失败（直接挂 release_id，无用例）
    r2 = ExecRun(project_id=pj.id, runner="m", payload="{}", status=ExecStatus.blocked,
                 release_id=rel.id, reason="超时", triage_kind="environment")
    # 非本版本失败（不该纳入）
    r3 = ExecRun(project_id=pj.id, runner="m", payload="{}", status=ExecStatus.failed, reason="无关")
    db.add_all([r1, r2, r3]); db.commit()
    runs = fc.collect_failed_runs(db, release_id=rel.id)
    ids = {r["id"] for r in runs}
    assert r1.id in ids and r2.id in ids, ids
    assert r3.id not in ids, "无关失败不应纳入"
    # 链路径的执行能回溯到 requirement_id
    row1 = next(r for r in runs if r["id"] == r1.id)
    assert row1["requirement_id"] == req.id
    db.close()


def test_handler_one_call_per_cluster():
    import types, json as _json
    from app.db.session import SessionLocal
    from app.models import Project, ExecRun, FailCluster
    from app.core.enums import ExecStatus
    from app.services import generators
    db = SessionLocal()
    pj = Project(name="P-h", code="P-H"); db.add(pj); db.flush()
    # 3 条失败：2 条同根因(env 500) + 1 条(selector) → 2 簇
    for reason, tk in [("接口 500", "environment"), ("接口 500", "environment"), ("元素未现", "selector")]:
        db.add(ExecRun(project_id=pj.id, runner="m", payload="{}", status=ExecStatus.failed,
                       release_id=None, reason=reason, triage_kind=tk))
    db.commit()
    # 直接喂 runs 走 rule_cluster + 命名，避免依赖真实回溯（此处校验调用计数）
    calls = {"n": 0}

    class _Fake:
        def is_available(self): return True
        def stream_generate(self, *a, **k):
            calls["n"] += 1
            yield {"type": "result", "text": '{"root_cause_title":"根因X","severity":"major","confidence":0.8}'}

    orig = generators.get_provider
    generators.get_provider = lambda name: _Fake()
    try:
        runs = [{"id": i + 1, "triage_kind": tk, "reason": r, "fail_kind": None, "report": None, "requirement_id": None}
                for i, (r, tk) in enumerate([("接口 500", "environment"), ("接口 500", "environment"), ("元素未现", "selector")])]
        clusters = fc.rule_cluster(runs)
        for c in clusters:
            fc._name_one(_Fake(), c)
        assert len(clusters) == 2, clusters
        assert calls["n"] == 2, f"应一簇一次调用，实际 {calls['n']}"
    finally:
        generators.get_provider = orig
    db.close()


def test_handler_end_to_end():
    # 真正走 run_fail_cluster_job 本体：真回溯 + 真落库 + 重跑幂等 + issue_id 迁移
    import json as _j
    from datetime import date
    from types import SimpleNamespace
    from app.db.session import SessionLocal
    from app.models import Project, AiTask, ExecRun, ReleaseRecord, Requirement, TestCase, FailCluster
    from app.core.enums import ExecStatus
    from app.services import generators, fail_cluster as _fc

    db = SessionLocal()
    pj = Project(name="P-e2e", code="P-E2E"); db.add(pj); db.flush()
    rel = ReleaseRecord(project_id=pj.id, version="v-e2e", release_date=date(2026, 9, 1)); db.add(rel); db.flush()
    req = Requirement(project_id=pj.id, title="需求E2E", release_id=rel.id); db.add(req); db.flush()
    # TestCase.ai_task_id NOT NULL：先建 AiTask 再引用（种子约定，见报告）
    _at = AiTask(project_id=pj.id, user_id=1, kind="testcase_gen", input_ref="r"); db.add(_at); db.flush()
    tc = TestCase(ai_task_id=_at.id, project_id=pj.id, title="用例E2E", requirement_id=req.id, exec_kind="gui"); db.add(tc); db.flush()
    # 2 条同根因失败（挂用例，链路径回溯 用例→需求→版本）
    for reason in ("接口 500", "接口 500"):
        db.add(ExecRun(project_id=pj.id, runner="m", payload="{}", status=ExecStatus.failed,
                       test_case_id=tc.id, reason=reason, triage_kind="environment"))
    db.commit()
    rel_id = rel.id

    class _Fake:
        def is_available(self): return True
        def stream_generate(self, *a, **k):
            yield {"type": "result", "text": '{"root_cause_title":"支付500","severity":"critical","confidence":0.9}'}

    orig = generators.get_provider
    generators.get_provider = lambda name: _Fake()
    try:
        job = SimpleNamespace(id=999, project_id=pj.id,
                              input=_j.dumps({"release_id": rel_id, "batch_key": f"rel{rel_id}"}))
        # 首跑：真回溯 → 粗聚 → AI 命名 → 落库
        res = _fc.run_fail_cluster_job(db, job)
        assert res["cluster_count"] == 1, res
        assert res["fail_count"] == 2, res
        rows = db.query(FailCluster).filter(FailCluster.release_id == rel_id).all()
        assert len(rows) == 1 and rows[0].member_count == 2, [(r.fingerprint, r.member_count) for r in rows]
        assert rows[0].root_cause_title == "支付500" and rows[0].severity == "critical", \
            (rows[0].root_cause_title, rows[0].severity)
        # 重跑同 batch_key 不重复堆积
        _fc.run_fail_cluster_job(db, job)
        rows2 = db.query(FailCluster).filter(FailCluster.release_id == rel_id).all()
        assert len(rows2) == 1, f"重跑应覆盖不堆积，实际 {len(rows2)}"
        # issue_id 迁移：给现有簇挂缺陷 → 重跑后按 fingerprint 迁移保留
        fp = rows2[0].fingerprint
        rows2[0].issue_id = 42
        db.commit()
        _fc.run_fail_cluster_job(db, job)
        rows3 = db.query(FailCluster).filter(FailCluster.release_id == rel_id).all()
        assert len(rows3) == 1, f"重跑仍应恰 1 行，实际 {len(rows3)}"
        assert rows3[0].fingerprint == fp, (rows3[0].fingerprint, fp)
        assert rows3[0].issue_id == 42, f"issue_id 应按 fingerprint 迁移保留，实际 {rows3[0].issue_id}"
    finally:
        generators.get_provider = orig
    db.close()


def test_endpoints():
    from types import SimpleNamespace
    from datetime import date
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.deps import get_current_user
    from app.db.session import get_db, SessionLocal
    from app.models import Project, AiTask, ExecRun, ReleaseRecord, Requirement, TestCase, FailCluster
    from app.core.enums import ExecStatus

    db = SessionLocal()
    pj = Project(name="P-ep", code="P-EP"); db.add(pj); db.flush()
    rel = ReleaseRecord(project_id=pj.id, version="v-ep", release_date=date(2026, 9, 1)); db.add(rel); db.flush()
    req = Requirement(project_id=pj.id, title="需求EP", release_id=rel.id); db.add(req); db.flush()
    # TestCase.ai_task_id NOT NULL：先建 AiTask 再引用（种子约定，见报告）
    _at = AiTask(project_id=pj.id, user_id=1, kind="testcase_gen", input_ref="r"); db.add(_at); db.flush()
    tc = TestCase(ai_task_id=_at.id, project_id=pj.id, title="用例EP", requirement_id=req.id, exec_kind="gui"); db.add(tc); db.flush()
    run = ExecRun(project_id=pj.id, runner="m", payload='{"title":"用例EP"}', status=ExecStatus.failed,
                  test_case_id=tc.id, reason="接口 500", triage_kind="environment")
    db.add(run); db.commit()
    pid, rel_id, req_id, run_id = pj.id, rel.id, req.id, run.id
    db.close()

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
    client = TestClient(app)

    # scope：列出该版本需求 + 失败数
    r = client.get("/api/fail-clusters/scope", params={"release_id": rel_id})
    assert r.json()["code"] == 0, r.text
    reqs = r.json()["data"]["requirements"]
    assert any(x["id"] == req_id and x["fail_count"] >= 1 for x in reqs), reqs

    # 手动落一条 cluster（跳过真实 AI），测 list + create-issue
    db = SessionLocal()
    import json as _j
    fcrow = FailCluster(project_id=pid, release_id=rel_id, root_cause_title="支付500",
                        triage_kind="environment", fingerprint="environment-abc",
                        run_ids=_j.dumps([run_id]), requirement_ids=_j.dumps([req_id]),
                        member_count=1, severity="critical", confidence=0.9, batch_key="b1")
    db.add(fcrow); db.commit(); cid = fcrow.id; db.close()

    rl = client.get("/api/fail-clusters", params={"release_id": rel_id})
    assert rl.json()["code"] == 0 and rl.json()["data"]["cluster_count"] >= 1, rl.text

    # create-issue：建缺陷 + 回填，幂等
    ci = client.post(f"/api/fail-clusters/{cid}/create-issue", json={})
    assert ci.json()["code"] == 0 and ci.json()["data"]["issue_id"], ci.text
    iid = ci.json()["data"]["issue_id"]
    ci2 = client.post(f"/api/fail-clusters/{cid}/create-issue", json={})
    assert ci2.json()["data"]["issue_id"] == iid and ci2.json()["data"]["already"] is True, "应幂等"
    app.dependency_overrides.clear()


def test_severity_mapping():
    """create-issue 的 severity 最近桶映射：critical→blocker、trivial→minor（锁死不回退 major）。"""
    from types import SimpleNamespace
    from datetime import date
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.deps import get_current_user
    from app.db.session import SessionLocal
    from app.models import Project, ReleaseRecord, FailCluster
    from app.models.issue import RemainingIssue
    from app.core.enums import IssueSeverity
    import json as _j

    db = SessionLocal()
    pj = Project(name="P-sev", code="P-SEV"); db.add(pj); db.flush()
    rel = ReleaseRecord(project_id=pj.id, version="v-sev", release_date=date(2026, 9, 1)); db.add(rel); db.flush()
    crit = FailCluster(project_id=pj.id, release_id=rel.id, root_cause_title="严重根因",
                       triage_kind="bug", fingerprint="bug-crit", run_ids=_j.dumps([]),
                       requirement_ids=_j.dumps([]), member_count=1, severity="critical",
                       confidence=0.9, batch_key="bsev")
    triv = FailCluster(project_id=pj.id, release_id=rel.id, root_cause_title="琐碎根因",
                       triage_kind="bug", fingerprint="bug-triv", run_ids=_j.dumps([]),
                       requirement_ids=_j.dumps([]), member_count=1, severity="trivial",
                       confidence=0.5, batch_key="bsev")
    db.add_all([crit, triv]); db.commit()
    crit_id, triv_id = crit.id, triv.id
    db.close()

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
    client = TestClient(app)
    rc = client.post(f"/api/fail-clusters/{crit_id}/create-issue", json={})
    assert rc.json()["code"] == 0, rc.text
    rt = client.post(f"/api/fail-clusters/{triv_id}/create-issue", json={})
    assert rt.json()["code"] == 0, rt.text
    app.dependency_overrides.clear()

    db = SessionLocal()
    crit_issue = db.get(RemainingIssue, rc.json()["data"]["issue_id"])
    triv_issue = db.get(RemainingIssue, rt.json()["data"]["issue_id"])
    assert crit_issue.severity == IssueSeverity.blocker, ("critical→blocker", crit_issue.severity)
    assert triv_issue.severity == IssueSeverity.minor, ("trivial→minor", triv_issue.severity)
    db.close()


def main():
    test_table_created()
    test_normalize_reason()
    test_fingerprint_and_cluster()
    test_parse_naming()
    test_collect_failed_runs()
    test_handler_one_call_per_cluster()
    test_handler_end_to_end()
    test_endpoints()
    test_severity_mapping()
    print("OK test_fail_cluster")


if __name__ == "__main__":
    main()
