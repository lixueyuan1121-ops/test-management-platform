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


def main():
    test_table_created()
    test_score_monotonic()
    test_history_signals_and_rank()
    print("OK test_rts")


if __name__ == "__main__":
    main()
