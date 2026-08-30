"""一条龙 × 综合评价在线短链 端到端集成自测(不 stub 综合评价本体)。
运行: cd backend && .venv/bin/python -m scripts.test_eval_pipeline_sharelink

覆盖真实链路:run_pipeline → _summary_with_retry → generate_task_summary_headless(真跑,假引擎) →
ensure_share_code → _summary_share_url → 推推通知带「在线报告」短链;summary_status=done、
summary_share_code 生成、pipeline_status=done。验证 Problem 2 修复(全新 session 落终态,不卡 running)
与短链承载一并成立。
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.enums import EvalRunStatus
from app.db.session import Base
from app.models import EvalRun, Project, User
from app.models.ai_eval import EvalTask
from app.services import eval_pipeline
from app.api import eval_judge as judge_mod
from app.services import notify as notify_mod
from app.services import generators

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _sf():
    return _Session()


_notifications = []


class _FakeEngine:
    def is_available(self):
        return True

    def stream_generate(self, *a, **k):
        yield {"type": "delta", "text": "<h2>总体结论</h2><p>整体表现良好</p>"}


def _fake_run_batch_judge(db, project_id, batch_id, provider=None):
    rows = db.query(EvalRun).filter(EvalRun.batch_id == batch_id,
                                    EvalRun.status == EvalRunStatus.done).all()
    for i, r in enumerate(rows):
        r.status = EvalRunStatus.judged
        r.verdict = "pass" if i % 2 == 0 else "fail"
        r.score = 5 if i % 2 == 0 else 2
    db.commit()
    return len(rows)


def _fake_notify(task_name, project_id, title, lines, color="blue"):
    _notifications.append((title, list(lines)))


judge_mod._run_batch_judge = _fake_run_batch_judge
notify_mod.notify_eval_pipeline = _fake_notify
eval_pipeline._spawn_pipeline = lambda *a: None   # 不真起线程

# 让短链拼接用固定外网基址(推推里发的就是它);headless 用假引擎。
settings.PLATFORM_BASE_URL = "http://eval.example.com"
# 关掉 nami 部署:本测试验证【自托管回落 /r/<code>】路径(nami 需真实 cookie+网络,单独手测)。
settings.NAMI_DEPLOY_ENABLED = False
generators.get_provider = lambda pid: _FakeEngine()
generators.normalize_provider = lambda p: "claude"
# app.db.session.SessionLocal 用于短链路由/兜底;这里让它落在同一 in-memory 库(以防触达)
import app.db.session as _sess
_sess.SessionLocal = _Session


def _seed():
    _s.add_all([
        User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"),
        Project(id=1, name="P", code="P1", status="active"),
    ])
    _s.commit()


def test_pipeline_generates_sharelink():
    t = EvalTask(project_id=1, name="任务X", query_ids=json.dumps([1]),
                 last_batch_id="bx", auto_pipeline=True, pipeline_status="running")
    _s.add(t); _s.commit()
    for _ in range(2):
        r = EvalRun(eval_query_id=None, project_id=1, batch_id="bx", eval_task_id=t.id,
                    runner="r1", status=EvalRunStatus.done, payload="{}", answer="回答内容")
        _s.add(r)
    _s.commit()

    eval_pipeline.run_pipeline(_sf, t.id, 1, "任务X", "bx")

    _s.refresh(t)
    assert t.summary_status == "done", f"综合评价应 done,实际 {t.summary_status}"
    assert t.summary_html and "总体结论" in t.summary_html, t.summary_html
    assert t.summary_share_code and len(t.summary_share_code) == 16, t.summary_share_code
    assert t.pipeline_status == "done", t.pipeline_status

    expected_url = f"http://eval.example.com/r/{t.summary_share_code}"
    # 综合评价成功通知(第3条)与最终摘要(第4条)都应带在线报告短链
    joined = str(_notifications)
    assert expected_url in joined, f"通知应包含在线报告短链 {expected_url}:{joined}"
    titles = [n[0] for n in _notifications]
    assert any("综合评价" in x for x in titles) and any("执行完毕" in x for x in titles), titles
    print("OK 一条龙真跑综合评价 → 自托管短链回落 → 推推带在线报告链接")


def test_pipeline_prefers_nami_shortlink():
    """nami 可用时:一条龙优先用 nami 公网短链(而非自托管 /r),并推进推推。"""
    _notifications.clear()
    # 打开 nami 并 mock 部署服务:is_configured True、deploy_html 返回固定短链
    settings.NAMI_DEPLOY_ENABLED = True
    from app.services import nami_deploy
    orig_cfg, orig_dep = nami_deploy.is_configured, nami_deploy.deploy_html
    nami_deploy.is_configured = lambda: True
    nami_deploy.deploy_html = lambda html: "https://p8rxf30dc.zhaomi.cn/"
    try:
        t = EvalTask(project_id=1, name="任务Y", query_ids=json.dumps([1]),
                     last_batch_id="by", auto_pipeline=True, pipeline_status="running")
        _s.add(t); _s.commit()
        for _ in range(2):
            _s.add(EvalRun(eval_query_id=None, project_id=1, batch_id="by", eval_task_id=t.id,
                           runner="r1", status=EvalRunStatus.done, payload="{}", answer="答"))
        _s.commit()
        eval_pipeline.run_pipeline(_sf, t.id, 1, "任务Y", "by")
    finally:
        nami_deploy.is_configured, nami_deploy.deploy_html = orig_cfg, orig_dep
        settings.NAMI_DEPLOY_ENABLED = False
    _s.refresh(t)
    assert t.summary_status == "done" and t.pipeline_status == "done"
    joined = str(_notifications)
    assert "https://p8rxf30dc.zhaomi.cn/" in joined, f"应优先推 nami 短链:{joined}"
    # 不应再退回自托管 /r(nami 已成功)
    assert f"/r/{t.summary_share_code}" not in joined, "nami 成功时不应再带自托管 /r"
    print("OK nami 可用 → 一条龙优先推 nami 公网短链")


def main():
    _seed()
    test_pipeline_generates_sharelink()
    test_pipeline_prefers_nami_shortlink()
    print("OK test_eval_pipeline_sharelink")


if __name__ == "__main__":
    main()
