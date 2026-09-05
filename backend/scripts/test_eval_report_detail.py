"""HTML 综合评价报告「逐条执行明细表(含耗时)」自测(python -m scripts.test_eval_report_detail)。

需求:详细执行信息(含耗时)放进 HTML 报告,推送消息只留进度/汇总。
故 render_report_page 在 AI 综合评价片段后追加一张逐条明细表(用例/维度/结果/评分/耗时);
耗时口径与前端详情一致(duration_ms,fmtDur:不进位小时)。
"""
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import EvalRun, EvalQuery
from app.models.ai_eval import EvalTask
from app.core.enums import EvalRunStatus
from app.api.eval_report import _fmt_dur, _detail_table_html, render_report_page

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)


def test_fmt_dur():
    assert _fmt_dur(None) == "—" and _fmt_dur(0) == "—"
    assert _fmt_dur(138000) == "2m18s" and _fmt_dur(60000) == "1m" and _fmt_dur(16181000) == "269m41s"
    print("✓ _fmt_dur 与前端同口径")


def _seed():
    s = _Session()
    s.query(EvalRun).delete(); s.query(EvalQuery).delete(); s.query(EvalTask).delete(); s.commit()
    s.add(EvalTask(id=1, project_id=1, name="图片场景", query_ids="[]",
                   last_batch_id="b1", summary_status="done", summary_html="<h2>综合评价</h2><p>不错</p>"))
    s.add_all([
        EvalRun(project_id=1, eval_task_id=1, batch_id="b1", status=EvalRunStatus.judged,
                verdict="pass", score=5, duration_ms=138000,
                payload=json.dumps({"title": "图片超分辨率放大", "dimension": "tool_use"}, ensure_ascii=False)),
        EvalRun(project_id=1, eval_task_id=1, batch_id="b1", status=EvalRunStatus.judged,
                verdict="fail", score=3, duration_ms=16181000,
                payload=json.dumps({"title": "抠图去除背景", "dimension": "instruct"}, ensure_ascii=False)),
        EvalRun(project_id=1, eval_task_id=1, batch_id="b1", status=EvalRunStatus.cancelled,
                payload=json.dumps({"title": "已取消"}, ensure_ascii=False)),
    ])
    s.commit()
    return s


def test_detail_table_has_rows_and_duration():
    s = _seed()
    task = s.get(EvalTask, 1)
    html = _detail_table_html(s, task)
    s.close()
    assert "逐条执行明细" in html, "应有明细表标题"
    assert "图片超分辨率放大" in html and "抠图去除背景" in html, "应含各用例标题"
    assert "2m18s" in html and "269m41s" in html, "应含每条耗时"
    assert "已取消" not in html, "cancelled 不计入"
    assert "通过" in html and "不通过" in html, "结果中文化"
    print("✓ 明细表含逐条(用例/结果/评分/耗时),排除 cancelled")


def test_render_page_appends_detail():
    s = _seed()
    task = s.get(EvalTask, 1)
    page = render_report_page(task, s)   # 新签名:带 db
    s.close()
    assert "综合评价" in page and "逐条执行明细" in page, "报告页应含 AI 评价 + 逐条明细表"
    assert "2m18s" in page, "报告页应含耗时"
    print("✓ render_report_page 在评价后追加明细表")


def test_render_page_without_db_backward_compatible():
    s = _seed()
    task = s.get(EvalTask, 1)
    page = render_report_page(task)   # 不传 db:向后兼容,只渲染评价片段,不炸
    s.close()
    assert "综合评价" in page, "不传 db 仍渲染评价"
    print("✓ 不传 db 向后兼容(只渲染评价,不报错)")


def main():
    test_fmt_dur()
    test_detail_table_has_rows_and_duration()
    test_render_page_appends_detail()
    test_render_page_without_db_backward_compatible()
    print("\n✅ HTML 报告逐条明细 全部通过")


if __name__ == "__main__":
    main()
