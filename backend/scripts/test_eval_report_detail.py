"""一条龙最终报告的「逐条明细 + 耗时」自测(python -m scripts.test_eval_report_detail)。

背景:一条龙「🎉 测评任务执行完毕」推推报告原先只有汇总统计,缺逐条结果明细。补上每条
(标题/结果/评分/耗时),耗时口径与前端详情表格一致(duration_ms,fmtDur 同款格式:不进位小时)。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import EvalRun
from app.core.enums import EvalRunStatus
from app.services.eval_pipeline import _fmt_dur, _result_summary

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)


def test_fmt_dur_matches_frontend():
    assert _fmt_dur(None) == "—"
    assert _fmt_dur(0) == "—"
    assert _fmt_dur(45000) == "45s"
    assert _fmt_dur(138000) == "2m18s"        # 前端 269 那条格式
    assert _fmt_dur(60000) == "1m"            # 整分不带秒
    assert _fmt_dur(16181000) == "269m41s"    # 不进位小时(与前端 fmtDur 一致,270 那条 269m41s)
    print("✓ _fmt_dur 与前端 fmtDur 同口径")


def test_result_summary_includes_items():
    import json
    s = _Session()
    s.query(EvalRun).delete(); s.commit()
    s.add_all([
        EvalRun(project_id=1, eval_task_id=1, batch_id="b1", status=EvalRunStatus.judged,
                verdict="pass", score=5, duration_ms=138000,
                payload=json.dumps({"title": "图片超分辨率放大"}, ensure_ascii=False)),
        EvalRun(project_id=1, eval_task_id=1, batch_id="b1", status=EvalRunStatus.judged,
                verdict="fail", score=3, duration_ms=16181000,
                payload=json.dumps({"title": "抠图去除背景"}, ensure_ascii=False)),
        # 取消的不计入
        EvalRun(project_id=1, eval_task_id=1, batch_id="b1", status=EvalRunStatus.cancelled,
                payload=json.dumps({"title": "已取消"}, ensure_ascii=False)),
    ])
    s.commit()
    res = _result_summary(s, 1, "b1")
    s.close()
    assert "items" in res, "结果摘要应含逐条明细 items"
    items = res["items"]
    assert len(items) == 2, f"cancelled 不计入,应 2 条,实际 {len(items)}"
    assert items[0]["title"] == "图片超分辨率放大" and items[0]["duration_ms"] == 138000
    assert items[0]["verdict"] == "pass" and items[0]["score"] == 5
    assert items[1]["title"] == "抠图去除背景" and items[1]["duration_ms"] == 16181000
    print("✓ _result_summary 返回逐条明细(标题/结果/评分/耗时)")


def main():
    test_fmt_dur_matches_frontend()
    test_result_summary_includes_items()
    print("\n✅ 报告逐条明细 全部通过")


if __name__ == "__main__":
    main()
