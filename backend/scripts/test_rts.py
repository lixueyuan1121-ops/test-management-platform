"""RTS 回归智选自测。跑法：cd backend && .venv/bin/python -m scripts.test_rts"""
import os, sys
os.environ["DATABASE_URL"] = "sqlite:///./tmp_test_rts.db"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp_test_rts.db")
if os.path.exists(_DB):
    os.remove(_DB)

from app.main import app  # noqa: F401
from app.db.session import Base, engine
from app.models import RtsRecommendation
from app.services import rts


def test_table_created():
    Base.metadata.create_all(engine)
    cols = set(RtsRecommendation.__table__.c.keys())
    for c in ("id", "project_id", "release_id", "overall_risk", "summary",
              "rationale", "focus_points", "candidate_count", "recommended_count",
              "provider", "generated_at"):
        assert c in cols, f"缺字段 {c}"


def test_score_monotonic():
    # 属本版本 > 不属；失败率高 > 低；P0 > P3；flaky/had_bug 加分；陈旧加分
    base_sig = {"runs": 10, "fails": 0, "flaky": False, "had_bug": False, "last_days": 1}
    s_in, _ = rts.score_case({"priority": "P2"}, base_sig, True)
    s_out, _ = rts.score_case({"priority": "P2"}, base_sig, False)
    assert s_in > s_out, (s_in, s_out)
    hi_fail = {**base_sig, "fails": 8}
    assert rts.score_case({"priority": "P2"}, hi_fail, True)[0] > rts.score_case({"priority": "P2"}, base_sig, True)[0]
    assert rts.score_case({"priority": "P0"}, base_sig, True)[0] > rts.score_case({"priority": "P3"}, base_sig, True)[0]
    assert rts.score_case({"priority": "P2"}, {**base_sig, "flaky": True}, True)[0] > s_in
    assert rts.score_case({"priority": "P2"}, {**base_sig, "had_bug": True}, True)[0] > s_in
    assert rts.score_case({"priority": "P2"}, {**base_sig, "last_days": 90}, True)[0] > s_in
    # 分数封顶 [0,100]
    top, _ = rts.score_case({"priority": "P0"}, {"runs": 10, "fails": 10, "flaky": True, "had_bug": True, "last_days": 999}, True)
    assert 0 <= top <= 100


def test_history_signals_and_rank():
    from datetime import date
    from app.db.session import SessionLocal
    from app.models import Project, TestCase, ExecRun, ReleaseRecord, Requirement, AiTask
    from app.core.enums import ExecStatus, ReviewStatus
    db = SessionLocal()
    pj = Project(name="P-rts", code="P-RTS"); db.add(pj); db.flush()
    rel = ReleaseRecord(project_id=pj.id, version="v-rts", release_date=date(2026, 9, 1)); db.add(rel); db.flush()
    req = Requirement(project_id=pj.id, title="需求RTS", release_id=rel.id); db.add(req); db.flush()
    at = AiTask(project_id=pj.id, user_id=1, kind="testcase_gen", input_ref="r"); db.add(at); db.flush()
    # c1 属本版本(挂需求)、adopted gui；c2 不属、adopted gui；c3 manual(不入候选)
    c1 = TestCase(ai_task_id=at.id, project_id=pj.id, title="c1", requirement_id=req.id, exec_kind="gui", review_status=ReviewStatus.adopted, priority="P1")
    c2 = TestCase(ai_task_id=at.id, project_id=pj.id, title="c2", exec_kind="gui", review_status=ReviewStatus.adopted, priority="P2")
    c3 = TestCase(ai_task_id=at.id, project_id=pj.id, title="c3", exec_kind="manual", review_status=ReviewStatus.adopted, priority="P0")
    db.add_all([c1, c2, c3]); db.flush()
    # c1 两次失败一次通过
    for st in (ExecStatus.failed, ExecStatus.failed, ExecStatus.passed):
        db.add(ExecRun(project_id=pj.id, runner="m", payload="{}", status=st, test_case_id=c1.id))
    # c2 一条 flaky + bug 的执行记录(端到端验证聚合分支 flaky/had_bug/last_days)
    db.add(ExecRun(project_id=pj.id, runner="m", payload="{}", status=ExecStatus.failed,
                   test_case_id=c2.id, flaky=True, triage_kind="bug"))
    db.commit()
    cases, in_rel = rts.cases_for_release(db, rel.id)
    ids = {c["id"] for c in cases}
    assert c1.id in ids and c2.id in ids and c3.id not in ids, "候选=adopted+非manual"
    assert c1.id in in_rel and c2.id not in in_rel, "属本版本判定"
    sig = rts.exec_history_signals(db, [c1.id, c2.id])
    assert sig[c1.id]["runs"] == 3 and sig[c1.id]["fails"] == 2, sig[c1.id]
    # 聚合分支端到端断言：flaky/had_bug 布尔、last_days 为 >=0 整数
    assert sig[c2.id]["flaky"] is True and sig[c2.id]["had_bug"] is True, sig[c2.id]
    assert isinstance(sig[c2.id]["last_days"], int) and sig[c2.id]["last_days"] >= 0, sig[c2.id]
    ranked = rts.rank_candidates(db, rel.id)
    assert [r["risk_score"] for r in ranked] == sorted([r["risk_score"] for r in ranked], reverse=True), "降序"
    # c1(属本版本+有失败历史+P1) 应排在 c2(不属+无历史+P2) 前
    assert ranked[0]["case_id"] == c1.id, ranked
    db.close()


def test_parse_rts():
    raw = '```json\n{"overall_risk":"high","summary":"支付风险高","rationale":"支付历史失败多","focus_points":["支付模块"],"recommended_count":5}\n```'
    d = rts.parse_rts(raw)
    assert d["overall_risk"] == "high" and d["recommended_count"] == 5
    assert isinstance(d["focus_points"], list)
    assert rts.parse_rts("非法输出，无json").get("error")
    # 非法 overall_risk 回落 medium
    assert rts.parse_rts('{"overall_risk":"weird","summary":"x"}')["overall_risk"] == "medium"


def test_rts_handler_registered_subprocess():
    import subprocess, sys
    # 干净进程：只走生产 import 路径，不直接 import app.services.rts
    code = ("import app.main; from app.services import ai_jobs; "
            "ai_jobs._ensure_handlers(); "
            "import sys; sys.exit(0 if 'rts' in ai_jobs._HANDLERS else 3)")
    r = subprocess.run([sys.executable, "-c", code],
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       env={**os.environ, "DATABASE_URL": "sqlite:///./tmp_test_rts.db"},
                       capture_output=True)
    assert r.returncode == 0, f"rts handler 未注册(生产路径),returncode={r.returncode}\n{r.stderr.decode()[:500]}"


def test_run_rts_job():
    import json as _j
    from types import SimpleNamespace
    from datetime import date
    from app.db.session import SessionLocal
    from app.models import Project, ReleaseRecord, RtsRecommendation
    from app.services import generators
    db = SessionLocal()
    pj = Project(name="P-rtsj", code="P-RTSJ"); db.add(pj); db.flush()
    rel = ReleaseRecord(project_id=pj.id, version="v-j", release_date=date(2026, 9, 1)); db.add(rel); db.flush()
    db.commit()
    rel_id = pj_id = None; rel_id = rel.id; pj_id = pj.id

    class _Fake:
        def is_available(self): return True
        def stream_generate(self, *a, **k):
            yield {"type": "result", "text": '{"overall_risk":"medium","summary":"s","rationale":"r","focus_points":["f"],"recommended_count":2}'}

    orig = generators.get_provider
    generators.get_provider = lambda name: _Fake()
    try:
        job = SimpleNamespace(id=1, project_id=pj_id, input=_j.dumps({"release_id": rel_id}))
        res = rts.run_rts_job(db, job)
        assert res["overall_risk"] == "medium", res
        rows = db.query(RtsRecommendation).filter(RtsRecommendation.release_id == rel_id).all()
        assert len(rows) == 1 and rows[0].overall_risk == "medium", rows
        # 重跑覆盖不堆积
        rts.run_rts_job(db, job)
        assert db.query(RtsRecommendation).filter(RtsRecommendation.release_id == rel_id).count() == 1
    finally:
        generators.get_provider = orig
    db.close()


def test_endpoints():
    from types import SimpleNamespace
    from datetime import date
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.deps import get_current_user
    from app.db.session import SessionLocal
    from app.models import Project, ReleaseRecord, Requirement, TestCase, AiTask, RtsRecommendation
    from app.core.enums import ReviewStatus
    db = SessionLocal()
    pj = Project(name="P-ep", code="P-RTSEP"); db.add(pj); db.flush()
    rel = ReleaseRecord(project_id=pj.id, version="v-ep", release_date=date(2026, 9, 1)); db.add(rel); db.flush()
    req = Requirement(project_id=pj.id, title="需求EP", release_id=rel.id); db.add(req); db.flush()
    at = AiTask(project_id=pj.id, user_id=1, kind="testcase_gen", input_ref="r"); db.add(at); db.flush()
    tc = TestCase(ai_task_id=at.id, project_id=pj.id, title="用例EP", requirement_id=req.id, exec_kind="gui", review_status=ReviewStatus.adopted, priority="P1")
    db.add(tc); db.commit()
    pid, rel_id, tc_id = pj.id, rel.id, tc.id
    db.close()

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
    client = TestClient(app)

    r = client.get("/api/rts/candidates", params={"release_id": rel_id})
    assert r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["candidate_count"] >= 1 and any(x["case_id"] == tc_id for x in d["items"]), d
    assert all("risk_score" in x for x in d["items"])

    # recommendation 空
    r0 = client.get("/api/rts/recommendation", params={"release_id": rel_id})
    assert r0.json()["data"].get("exists") is False, r0.text

    # 手动落一条 recommendation，测读取
    db = SessionLocal()
    import json as _j
    db.add(RtsRecommendation(project_id=pid, release_id=rel_id, overall_risk="high", summary="s",
                             rationale="r", focus_points=_j.dumps(["fp"]), candidate_count=1, recommended_count=1))
    db.commit(); db.close()
    r1 = client.get("/api/rts/recommendation", params={"release_id": rel_id})
    assert r1.json()["data"]["overall_risk"] == "high" and r1.json()["data"]["focus_points"] == ["fp"], r1.text
    app.dependency_overrides.clear()


def main():
    test_table_created()
    test_score_monotonic()
    test_history_signals_and_rank()
    test_parse_rts()
    test_rts_handler_registered_subprocess()
    test_run_rts_job()
    test_endpoints()
    print("OK test_rts")


if __name__ == "__main__":
    main()
