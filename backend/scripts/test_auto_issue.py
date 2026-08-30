"""失败自动建缺陷草稿(建议项⑦)自测。
运行: cd backend && python -m scripts.test_auto_issue

覆盖:
- auto 批次完成:business 失败建草稿(P0→blocker 映射/标题前缀/exec_run_id·task_id 回指),
  selector 阻塞与 passed 不建
- 去重:同用例已有 open 草稿 → 下一批失败不重复开单;resolved 后再失败 → 重新生成
- manual 批次不建;AUTO_ISSUE_ON_FAIL=false 不建
- 飞书卡带「已自动生成 N 条缺陷草稿」行(捕获 send 验证)
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.enums import ExecStatus, IssueStatus
from app.db.session import Base
from app.models import ExecRun, FeedbackRun, Project, RemainingIssue, Task, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="纳米Work", code="nw"),
    ])
    _s.flush()
    _s.add(Task(id=7, project_id=100, assigned_by=1, assigned_to=1, title="T",
                assigned_date=__import__("datetime").date.today()))
    _s.commit()


def _run(status_, batch, case_id=None, fail_kind=None, priority=None, task_id=None, reason=None):
    payload = {"title": f"用例{case_id or '?'}", "priority": priority}
    r = ExecRun(project_id=100, test_case_id=case_id, task_id=task_id, runner="m",
                payload=json.dumps(payload, ensure_ascii=False),
                status=status_, batch_id=batch, fail_kind=fail_kind, reason=reason,
                evidence_url="/uploads/x.png" if fail_kind else None)
    _s.add(r)
    _s.commit()
    return r


def _meta(batch, trigger):
    _s.add(FeedbackRun(project_id=100, batch_id=batch, trigger=trigger, case_count=1))
    _s.commit()


def _issues():
    return _s.query(RemainingIssue).order_by(RemainingIssue.id).all()


def test_auto_create_and_mapping():
    from app.api.exec_queue import notify_batch_if_done

    _meta("b1", "auto")
    r_fail = _run(ExecStatus.failed, "b1", case_id=11, fail_kind="business",
                  priority="P0", task_id=7, reason="断言失败:弹窗未出现")
    _run(ExecStatus.blocked, "b1", case_id=12, fail_kind="selector")
    _run(ExecStatus.passed, "b1", case_id=13)
    notify_batch_if_done(_s, "b1")

    rows = _issues()
    assert len(rows) == 1, f"只有 business 失败建草稿,实际 {len(rows)}"
    it = rows[0]
    assert it.title.startswith("[自动] 回归失败：用例11"), it.title
    assert it.severity.value == "blocker", it.severity   # P0 → blocker
    assert it.exec_run_id == r_fail.id and it.task_id == 7 and it.project_id == 100
    assert "断言失败" in (it.description or "") and "证据" in it.description
    print("OK auto create + mapping")


def test_dedupe_and_resolve_cycle():
    from app.api.exec_queue import notify_batch_if_done

    # 同用例第二晚再失败 → 已有 open 草稿,不重复开
    _meta("b2", "auto")
    _run(ExecStatus.failed, "b2", case_id=11, fail_kind="business", priority="P1")
    notify_batch_if_done(_s, "b2")
    assert len(_issues()) == 1, "open 草稿去重失败"

    # 草稿被处理(resolved)后再失败 → 重新生成(P1→major)
    it = _issues()[0]
    it.status = IssueStatus.resolved
    _s.commit()
    _meta("b3", "auto")
    _run(ExecStatus.failed, "b3", case_id=11, fail_kind="business", priority="P1")
    notify_batch_if_done(_s, "b3")
    rows = _issues()
    assert len(rows) == 2, rows
    assert rows[1].severity.value == "major"
    print("OK dedupe + resolve cycle")


def test_manual_and_toggle():
    from app.api.exec_queue import notify_batch_if_done

    _meta("b4", "manual")
    _run(ExecStatus.failed, "b4", case_id=21, fail_kind="business")
    notify_batch_if_done(_s, "b4")
    assert len(_issues()) == 2, "manual 批次不应建草稿"

    orig = settings.AUTO_ISSUE_ON_FAIL
    settings.AUTO_ISSUE_ON_FAIL = False
    try:
        _meta("b5", "auto")
        _run(ExecStatus.failed, "b5", case_id=22, fail_kind="business")
        notify_batch_if_done(_s, "b5")
        assert len(_issues()) == 2, "开关关闭不应建草稿"
    finally:
        settings.AUTO_ISSUE_ON_FAIL = orig
    print("OK manual + toggle off")


def test_card_mentions_drafts():
    from app.api.exec_queue import notify_batch_if_done
    from app.services import notify

    sent = []
    orig_send, orig_url = notify._send_async, settings.FEISHU_WEBHOOK_URL
    notify._send_async = lambda body: sent.append(body)
    settings.FEISHU_WEBHOOK_URL = "http://fake.local/x"
    try:
        _meta("b6", "auto")
        _run(ExecStatus.failed, "b6", case_id=33, fail_kind="business")
        notify_batch_if_done(_s, "b6")
        assert len(sent) == 1
        assert "已自动生成 1 条缺陷草稿" in str(sent[0]), str(sent[0])[:400]
    finally:
        notify._send_async = orig_send
        settings.FEISHU_WEBHOOK_URL = orig_url
    print("OK card mentions drafts")


def test_auto_report_geelib():
    """开 GEELIB_AUTO_REPORT:新草稿自动上报极库云并回填 external_ref;report_defect 抛异常被吞不影响建草稿。"""
    from app.api import exec_queue
    from app.services import geelib

    _s.query(RemainingIssue).delete()
    _s.commit()

    orig_auto = settings.GEELIB_AUTO_REPORT
    orig_enabled = settings.GEELIB_ENABLED
    orig_sub_map = settings.GEELIB_SUB_MAP
    orig_report = geelib.report_defect
    settings.GEELIB_AUTO_REPORT = True
    settings.GEELIB_ENABLED = True
    settings.GEELIB_SUB_MAP = "nw:419"
    calls = []

    def fake_report(**kw):
        calls.append(kw)
        return {"ok": True, "matter_id": 90001, "ref": "geelib#90001", "reason": None}

    geelib.report_defect = fake_report
    try:
        _meta("g1", "auto")
        _run(ExecStatus.failed, "g1", case_id=41, fail_kind="business", priority="P0",
             reason="断言失败:自动上报")
        exec_queue.notify_batch_if_done(_s, "g1")
        rows = _issues()
        assert len(rows) == 1, rows
        assert rows[0].external_ref == "geelib#90001", rows[0].external_ref
        assert calls and calls[0]["sub_id"] == 419, calls
        print("OK auto report geelib (回填 external_ref)")

        # report_defect 抛异常 → 草稿照建、external_ref 留空、主流程不炸
        geelib.report_defect = lambda **k: (_ for _ in ()).throw(geelib.GeelibError("极库云 500"))
        _meta("g2", "auto")
        _run(ExecStatus.failed, "g2", case_id=42, fail_kind="business", reason="x")
        exec_queue.notify_batch_if_done(_s, "g2")   # 不应抛
        it2 = _s.query(RemainingIssue).filter(RemainingIssue.title.contains("用例42")).first()
        assert it2 is not None and it2.external_ref is None, it2
        print("OK auto report geelib (异常安全,草稿仍建)")
    finally:
        settings.GEELIB_AUTO_REPORT = orig_auto
        settings.GEELIB_ENABLED = orig_enabled
        settings.GEELIB_SUB_MAP = orig_sub_map
        geelib.report_defect = orig_report


def main():
    _seed()
    test_auto_create_and_mapping()
    test_dedupe_and_resolve_cycle()
    test_manual_and_toggle()
    test_card_mentions_drafts()
    test_auto_report_geelib()
    print("OK test_auto_issue")


if __name__ == "__main__":
    main()
