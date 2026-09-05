"""测评 run 重跑保留历史快照自测(python -m scripts.test_eval_run_history)。

背景:reset_run_for_retry 原地复位同一 EvalRun,重跑覆盖旧结果 → 只剩一条,看不到历次执行。
改为:复位前把当前结果快照进 eval_run_history(attempt 递增),run 本体仍复位重跑。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import EvalRun, EvalRunHistory  # noqa: F401  EvalRunHistory 为本次新增
from app.core.enums import EvalRunStatus
from app.api.eval_queue import reset_run_for_retry

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)


def _clear():
    s = _Session()
    s.query(EvalRunHistory).delete(); s.query(EvalRun).delete(); s.commit(); s.close()


def _done_run(**kw):
    s = _Session()
    r = EvalRun(project_id=1, batch_id="b1", status=EvalRunStatus.failed,
                answer="旧答案", share_link="https://x/old", score=3, verdict="fail",
                verdict_reason="旧理由", reason="旧失败原因", duration_ms=1000, **kw)
    s.add(r); s.commit()
    rid = r.id; s.close()
    return rid


def test_retry_archives_old_result_then_resets():
    _clear()
    rid = _done_run()
    s = _Session()
    r = s.get(EvalRun, rid)
    reset_run_for_retry(s, r)          # 新签名:带 db,复位前先快照
    s.commit()
    # 旧结果进了 history
    hist = s.query(EvalRunHistory).filter_by(eval_run_id=rid).all()
    assert len(hist) == 1, f"应快照 1 条历史,实际 {len(hist)}"
    h = hist[0]
    assert h.answer == "旧答案" and h.share_link == "https://x/old" and h.score == 3, "快照应含旧结果"
    assert h.attempt == 1, f"首次快照 attempt=1,实际 {h.attempt}"
    # run 本体已复位
    r = s.get(EvalRun, rid)
    assert r.status == EvalRunStatus.pending and r.answer is None and r.share_link is None, "run 应复位"
    s.close()
    print("✓ 重跑:旧结果快照进 history,run 复位")


def test_multiple_retries_accumulate_attempts():
    _clear()
    rid = _done_run()
    for i in range(3):
        s = _Session()
        r = s.get(EvalRun, rid)
        r.answer = f"第{i}次答案"; r.status = EvalRunStatus.failed  # 模拟每次跑完又失败
        s.commit()
        reset_run_for_retry(s, r); s.commit(); s.close()
    s = _Session()
    hist = s.query(EvalRunHistory).filter_by(eval_run_id=rid).order_by(EvalRunHistory.attempt).all()
    attempts = [h.attempt for h in hist]
    s.close()
    assert attempts == [1, 2, 3], f"多次重跑 attempt 应递增 [1,2,3],实际 {attempts}"
    print("✓ 多次重跑:attempt 递增累积多条历史")


def test_empty_run_not_archived():
    # 从未执行过(无答案/无判定)的 run 复位不应产生垃圾历史
    _clear()
    s = _Session()
    r = EvalRun(project_id=1, batch_id="b1", status=EvalRunStatus.pending)
    s.add(r); s.commit(); rid = r.id
    reset_run_for_retry(s, r); s.commit()
    n = s.query(EvalRunHistory).filter_by(eval_run_id=rid).count()
    s.close()
    assert n == 0, f"空 run 复位不应快照,实际 {n}"
    print("✓ 空 run 复位不产生垃圾历史")


def main():
    test_retry_archives_old_result_then_resets()
    test_multiple_retries_accumulate_attempts()
    test_empty_run_not_archived()
    print("\n✅ 重跑历史快照 全部通过")


if __name__ == "__main__":
    main()
