"""回归:无头综合评价(一条龙步骤3)任何异常都必须收口 summary_status,不得卡在 running。
运行: cd backend && .venv/bin/python -m scripts.test_eval_summary_status

背景:前端"生成中"= summary_status=='running'。headless 设了 running 后,若生成/提取/
消毒/落库任一步异常裸逃,调用链(_summary_with_retry→run_pipeline→后台线程)只兜底
pipeline_status,从不回收 summary_status → 前端永久"生成中"。手动 SSE 端点无此问题。
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import EvalRunStatus
from app.db.session import Base
from app.models import EvalRun, Project, User
from app.models.ai_eval import EvalTask
from app.api import eval_task as task_mod
from app.services import generators

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()

_seeded = {"n": 0}


class _Engine:
    def __init__(self, mode):
        self.mode = mode   # "ok" | "raise"

    def is_available(self):
        return True

    def stream_generate(self, *a, **k):
        if self.mode == "raise":
            raise RuntimeError("stream boom")
        yield {"type": "delta", "text": "<p>综合评价内容</p>"}


def _seed_task():
    _seeded["n"] += 1
    if _seeded["n"] == 1:
        _s.add_all([
            User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"),
            Project(id=1, name="P", code="P1", status="active"),
        ])
        _s.commit()
    bid = f"b{_seeded['n']}"
    t = EvalTask(project_id=1, name="任务X", query_ids=json.dumps([1]),
                 last_batch_id=bid, auto_pipeline=True)
    _s.add(t); _s.commit()
    r = EvalRun(eval_query_id=None, project_id=1, batch_id=bid, eval_task_id=t.id,
                runner="r1", status=EvalRunStatus.judged, payload="{}", verdict="pass",
                score=5, answer="ok")
    _s.add(r); _s.commit()
    return t, bid


def _call(engine_mode="ok", sanitize=None):
    t, bid = _seed_task()
    orig_get, orig_norm, orig_san = (generators.get_provider, generators.normalize_provider,
                                     task_mod._sanitize_html)
    generators.get_provider = lambda pid: _Engine(engine_mode)
    generators.normalize_provider = lambda p: "claude"
    if sanitize is not None:
        task_mod._sanitize_html = sanitize
    try:
        res = task_mod.generate_task_summary_headless(_s, t, bid, provider="claude")
    finally:
        generators.get_provider, generators.normalize_provider, task_mod._sanitize_html = (
            orig_get, orig_norm, orig_san)
    _s.refresh(t)
    return res, t.summary_status


def test_sanitize_raises_falls_to_failed():
    def _boom(html):
        raise RuntimeError("sanitize boom")
    res, st = _call(engine_mode="ok", sanitize=_boom)
    assert "error" in res, res
    assert st == "failed", f"消毒异常须落 failed,实际 {st}"
    print("OK 消毒异常 → failed(不卡 running)")


def test_stream_raises_falls_to_failed():
    res, st = _call(engine_mode="raise")
    assert "error" in res, res
    assert st == "failed", f"流式异常须落 failed,实际 {st}"
    print("OK 流式异常 → failed(不卡 running)")


def test_happy_path_done():
    res, st = _call(engine_mode="ok")
    assert res.get("ok") is True, res
    assert st == "done", f"正常须落 done,实际 {st}"
    print("OK 正常路径 → done")


def main():
    test_happy_path_done()
    test_sanitize_raises_falls_to_failed()
    test_stream_raises_falls_to_failed()
    print("OK test_eval_summary_status")


if __name__ == "__main__":
    main()
