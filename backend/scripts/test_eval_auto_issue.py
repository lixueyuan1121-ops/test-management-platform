"""AI 测评侧缺陷出口自测：eval 批次 fail/abnormal 自动建 RemainingIssue 草稿。
运行: cd backend && python -m scripts.test_eval_auto_issue

对齐 exec 侧 _auto_issue_for_failures：
- verdict=fail 或 is_abnormal 的 run 建草稿（回指 eval_run_id/eval_task_id，标题带 [自动] 前缀）
- verdict=pass 且非 abnormal 不建
- 去重：同 eval_query 已有 open 草稿 → 下一批失败不重复开单
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import EvalRunStatus, IssueStatus
from app.db.session import Base
from app.models import EvalQuery, EvalRun, Project, RemainingIssue
from app.models.ai_eval import EvalTask

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _seed():
    _s.add(Project(id=100, name="纳米Work", code="nw"))
    _s.flush()
    _s.add(EvalTask(id=5, project_id=100, name="回归测评"))
    _s.add(EvalQuery(id=1, project_id=100, title="登录测评", prompt="问题一"))
    _s.add(EvalQuery(id=2, project_id=100, title="搜索测评", prompt="问题二"))
    _s.commit()


def _run(batch, query_id, verdict=None, abnormal=False, reason=None):
    r = EvalRun(project_id=100, eval_task_id=5, eval_query_id=query_id, batch_id=batch,
                status=EvalRunStatus.judged, verdict=verdict, is_abnormal=abnormal,
                verdict_reason=reason, runner="mac-01")
    _s.add(r); _s.commit()
    return r


def _issues():
    return _s.query(RemainingIssue).order_by(RemainingIssue.id).all()


def test_fail_and_abnormal_create_drafts():
    from app.services.eval_pipeline import auto_issue_for_eval_failures

    r_fail = _run("e1", 1, verdict="fail", reason="回答与期望不符")
    _run("e1", 2, verdict="pass")   # pass 不建
    created = auto_issue_for_eval_failures(_s, 5, 100, "e1")

    rows = _issues()
    assert len(rows) == 1, f"只 fail 建草稿,实际 {len(rows)}"
    it = rows[0]
    assert it.eval_run_id == r_fail.id, it.eval_run_id
    assert it.project_id == 100 and it.status == IssueStatus.open
    assert it.title.startswith("[自动]"), it.title
    assert "回答与期望不符" in (it.description or "")
    assert len(created) == 1
    print("OK fail/abnormal → draft, pass 不建")


def test_dedupe_same_query():
    from app.services.eval_pipeline import auto_issue_for_eval_failures

    # 同 eval_query 已有 open 草稿(query 1 上个 test 建过) → 再失败不重复开
    _run("e2", 1, verdict="fail", reason="又失败")
    auto_issue_for_eval_failures(_s, 5, 100, "e2")
    q1_open = [it for it in _issues() if it.eval_run_id and
               _s.get(EvalRun, it.eval_run_id).eval_query_id == 1]
    assert len(q1_open) == 1, f"同 query open 草稿去重失败,实际 {len(q1_open)}"
    print("OK dedupe same query")


def main():
    _seed()
    test_fail_and_abnormal_create_drafts()
    test_dedupe_same_query()
    print("OK test_eval_auto_issue")


if __name__ == "__main__":
    main()
