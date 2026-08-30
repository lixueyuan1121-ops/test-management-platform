"""exec_run 超龄收口 + 批次完成通知钩子自测。
运行: cd backend && python -m scripts.test_exec_reaper_notify

覆盖:
- reap_stale_exec_runs: running 超过阈值 → failed(带收口原因);近期 running / pending / 终态不动。
- 收口后若整批完成且为定时(auto)反馈批次 → 触发批次告警(捕获 send 而非真发)。
- notify 服务纯函数: _esc 去 markdown 字符/截断、_sign 稳定性、未配 URL 时 is_enabled=False。
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.session import Base
from app.models import ExecRun, FeedbackRun, Project, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)


def _seed(s):
    """幂等种子:已存在则复用(两个测试共享一个内存库)。"""
    p = s.query(Project).filter_by(code="nw").first()
    if p:
        return p
    u = User(username="admin", name="管理员", password_hash="x", is_platform_admin=True)
    p = Project(name="纳米Work", code="nw")
    s.add_all([u, p])
    s.flush()
    return p


def _run(s, pid, status, minutes_ago, batch="b-1", payload='{"title":"用例A"}', fail_kind=None):
    r = ExecRun(project_id=pid, runner="mac-01", payload=payload, status=status,
                batch_id=batch, fail_kind=fail_kind)
    s.add(r)
    s.flush()
    r.updated_at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    r.created_at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    return r


def test_reaper():
    from app.services.scheduler import reap_stale_exec_runs

    s = _Session()
    p = _seed(s)
    stale = _run(s, p.id, "running", minutes_ago=200, batch="b-stale")     # 超 2h → 收
    fresh = _run(s, p.id, "running", minutes_ago=10, batch="b-fresh")      # 未超 → 留
    pend = _run(s, p.id, "pending", minutes_ago=999, batch="b-pend")       # pending → 留
    done = _run(s, p.id, "passed", minutes_ago=999, batch="b-done")        # 终态 → 留
    s.commit()

    reap_stale_exec_runs(session_factory=_Session)

    s2 = _Session()
    assert s2.get(ExecRun, stale.id).status.value == "failed", "超龄 running 应被收口为 failed"
    assert "自动收口" in (s2.get(ExecRun, stale.id).reason or "")
    assert s2.get(ExecRun, fresh.id).status.value == "running", "近期 running 不应动"
    assert s2.get(ExecRun, pend.id).status.value == "pending", "pending 不应动"
    assert s2.get(ExecRun, done.id).status.value == "passed", "终态不应动"
    s2.close()
    s.close()
    print("OK reaper")


def test_reaper_uses_db_clock_not_process_utc():
    """reaper 时间基准须取自数据库(func.now())而非进程 utcnow——否则 DB 时区非 UTC 时收口失效。

    生产是内网 MySQL 5.6,NOW() 用服务器时区(很可能东八区),存进 created_at/updated_at;
    reaper 却用 datetime.utcnow() 作基准,两者差 8h → cutoff 被推早 8h,超龄 run 被误判"还很新"漏收,
    设备看板长期卡"执行中"。这里 mock 进程 utcnow 比真实(≈SQLite DB now)早 8h 复现该错配。
    """
    import app.services.scheduler as sched

    s = _Session()
    p = _seed(s)
    # 模拟生产:DB 会话时区东八区,NOW() 比进程 utcnow 大 8h
    db_now = datetime.utcnow() + timedelta(hours=8)
    r = _run(s, p.id, "running", minutes_ago=0, batch="b-tz")
    r.updated_at = db_now - timedelta(hours=3)   # DB 时钟下的"3 小时前",已超 2h 阈值应收口
    s.commit()

    orig = sched._db_now
    sched._db_now = lambda db: db_now            # reaper 应以数据库时钟为基准,而非进程 utcnow
    try:
        sched.reap_stale_exec_runs(session_factory=_Session)
    finally:
        sched._db_now = orig

    s2 = _Session()
    got = s2.get(ExecRun, r.id).status.value
    s2.close(); s.close()
    assert got == "failed", f"应以数据库时钟为基准收口,实际 {got}(reaper 误用进程 utcnow → DB 东八区时漏收)"
    print("OK reaper 用数据库时钟(时区无关)")


def test_batch_notify_hook():
    from app.api.exec_queue import notify_batch_if_done
    from app.services import notify

    sent = []
    orig = notify._send_async
    notify._send_async = lambda body: sent.append(body)
    orig_url = settings.FEISHU_WEBHOOK_URL
    settings.FEISHU_WEBHOOK_URL = "http://fake-webhook.local/x"
    try:
        s = _Session()
        p = _seed(s)
        # auto 批次:1 failed(business) + 1 passed,全部终态 → 应发卡
        _run(s, p.id, "failed", 5, batch="b-auto", payload='{"title":"登录失败用例"}', fail_kind="business")
        _run(s, p.id, "passed", 5, batch="b-auto")
        s.add(FeedbackRun(project_id=p.id, batch_id="b-auto", trigger="auto", case_count=2))
        # auto 批次但还有 running → 不发
        _run(s, p.id, "failed", 5, batch="b-open", fail_kind="business")
        _run(s, p.id, "running", 5, batch="b-open")
        s.add(FeedbackRun(project_id=p.id, batch_id="b-open", trigger="auto", case_count=2))
        # manual 批次全终态有失败 → 不发(页面上看着,不打扰)
        _run(s, p.id, "failed", 5, batch="b-man", fail_kind="business")
        s.add(FeedbackRun(project_id=p.id, batch_id="b-man", trigger="manual", case_count=1))
        # auto 批次全 passed → 不发(无失败不告警)
        _run(s, p.id, "passed", 5, batch="b-green")
        s.add(FeedbackRun(project_id=p.id, batch_id="b-green", trigger="auto", case_count=1))
        s.commit()

        notify_batch_if_done(s, "b-auto")
        assert len(sent) == 1, f"auto 完成带失败应发 1 张卡,实际 {len(sent)}"
        card_text = str(sent[0])
        assert "登录失败用例" in card_text and "纳米Work" in card_text

        notify_batch_if_done(s, "b-open")
        assert len(sent) == 1, "批次未完成不应发"
        notify_batch_if_done(s, "b-man")
        assert len(sent) == 1, "manual 批次不应发"
        notify_batch_if_done(s, "b-green")
        assert len(sent) == 1, "全 passed 不应发"
        s.close()
    finally:
        notify._send_async = orig
        settings.FEISHU_WEBHOOK_URL = orig_url
    print("OK batch notify hook")


def test_notify_pure():
    from app.services import notify

    assert notify._esc("a*b_c[d]`e~") == "a b c d  e", notify._esc("a*b_c[d]`e~")
    assert len(notify._esc("x" * 999)) == 200
    assert notify._esc(None) == ""
    # 签名算法自检:固定输入产出固定 base64(算法回归锚点)
    sig = notify._sign("secret", "1700000000")
    assert isinstance(sig, str) and len(sig) > 20
    assert sig == notify._sign("secret", "1700000000")
    orig_url = settings.FEISHU_WEBHOOK_URL
    settings.FEISHU_WEBHOOK_URL = ""
    try:
        assert not notify.is_enabled()
        notify.send_card("t", ["l"])   # 未配置直接静默,不抛
    finally:
        settings.FEISHU_WEBHOOK_URL = orig_url
    print("OK notify pure")


def main():
    test_notify_pure()
    test_reaper()
    test_reaper_uses_db_clock_not_process_utc()
    test_batch_notify_hook()
    print("OK test_exec_reaper_notify")


if __name__ == "__main__":
    main()
