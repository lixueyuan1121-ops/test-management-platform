"""综合评价在线短链自测。
运行: cd backend && .venv/bin/python -m scripts.test_eval_report_share

覆盖:
- ensure_share_code:首次生成 16 位 hex 码;重复调用复用(稳定不变)
- render_report_page:done 内联 summary_html;running/failed/未生成 → 提示页(不泄露)
- GET /r/<code>:命中渲染报告;未命中 404 提示页(不 500、不泄露其它任务)
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import Project, User
from app.models.ai_eval import EvalTask
from app.api import eval_report as er

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _seed():
    _s.add_all([
        User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"),
        Project(id=1, name="P", code="P1", status="active"),
    ])
    _s.commit()


def _task(**kw):
    t = EvalTask(project_id=1, name="任务X", query_ids="[1]", **kw)
    _s.add(t); _s.commit()
    return t


def test_ensure_share_code_stable():
    t = _task()
    c1 = er.ensure_share_code(_s, t); _s.commit()
    assert c1 and len(c1) == 16, c1
    c2 = er.ensure_share_code(_s, t); _s.commit()
    assert c2 == c1, "重复调用须复用同一短链码"
    print("OK 短链码首次生成 + 稳定复用")


def test_render_done_inlines_html():
    t = _task(summary_status="done", summary_html="<h2>结论</h2><p>好</p>", summary_provider="claude")
    page = er.render_report_page(t)
    assert "<h2>结论</h2>" in page and "好" in page, page[:200]
    assert "<!DOCTYPE html>" in page
    print("OK done 内联 summary_html")


def test_render_pending_tip():
    for st, kw in [("running", {"summary_status": "running"}),
                   ("failed", {"summary_status": "failed"}),
                   ("none", {})]:
        t = _task(**kw)
        page = er.render_report_page(t)
        assert "tip" in page, st
        assert "<script" not in page.lower()
    print("OK running/failed/未生成 → 提示页")


def test_route_hit_and_miss():
    # patch SessionLocal 到 in-memory 库,让路由用同一库
    import app.db.session as sess
    orig = sess.SessionLocal
    sess.SessionLocal = _Session
    try:
        t = _task(summary_status="done", summary_html="<p>报告内容XYZ</p>", summary_provider="claude")
        code = er.ensure_share_code(_s, t); _s.commit()
        resp = er.view_shared_report(code)
        assert resp.status_code == 200, resp.status_code
        assert b"XYZ" in resp.body, resp.body[:200]
        miss = er.view_shared_report("deadbeefdeadbeef")
        assert miss.status_code == 404, miss.status_code
        assert b"XYZ" not in miss.body, "未命中不得泄露其它任务内容"
    finally:
        sess.SessionLocal = orig
    print("OK 路由命中渲染 + 未命中 404 不泄露")


def main():
    _seed()
    test_ensure_share_code_stable()
    test_render_done_inlines_html()
    test_render_pending_tip()
    test_route_hit_and_miss()
    print("OK test_eval_report_share")


if __name__ == "__main__":
    main()
