"""测试计划执行完成 → 推推结果回执通知 自测。
运行: cd backend && python -m scripts.test_plan_notify

覆盖(需求：测试计划手动+定时都发、成败都发一条回执；feedback 行为不变):
- 批次未完成(有 pending/running) → 不发
- 计划·手动·全通过 → 发绿色回执(共/通过/失败/阻塞)
- 计划·手动·有失败 → 发红色回执
- 计划·定时(auto) → 发回执(触发方式=定时)
- feedback 回归集来源 → 仍走原「失败告警」口径(全通过不发、失败才发)，行为不变
- NOTIFY_PLAN_RESULT=false 关掉计划回执后，计划批次不发
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.enums import ExecStatus
from app.db.session import Base
from app.models import ExecRun, FeedbackRun, Project, TestPlan, TestPlanRun, User
from app.services import notify

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()

# 通道置为「已开启」，并捕获实际发送内容（不真的发网络）
settings.TUITUI_BOT_APPID = "app"
settings.TUITUI_BOT_SECRET = "sec"
settings.TUITUI_BOT_GROUP = "grp"
settings.NOTIFY_EXEC_FAIL = True
settings.NOTIFY_PLAN_RESULT = True

_sent: list[str] = []
notify._tuitui_send = lambda content, group=None: _sent.append(content)   # type: ignore


def _reset_sent():
    _sent.clear()


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="纳米Work", code="nami-work"),
    ])
    _s.commit()


def _mk_batch(batch_id: str, statuses: list[ExecStatus], project_id: int = 100):
    """按给定状态列表造一批 exec_run。"""
    import json
    for i, st in enumerate(statuses):
        _s.add(ExecRun(
            test_case_id=1000 + i, project_id=project_id, batch_id=batch_id,
            runner="mac-01", status=st,
            payload=json.dumps({"title": f"用例{i}"}, ensure_ascii=False),
            verdict=("pass" if st == ExecStatus.passed else "fail"),
        ))
    _s.commit()


def _call(batch_id: str):
    from app.api.exec_queue import notify_batch_if_done
    notify_batch_if_done(_s, batch_id)


def test_unfinished_no_send():
    _reset_sent()
    _mk_batch("b-unfin", [ExecStatus.passed, ExecStatus.running])
    _s.add(TestPlanRun(project_id=100, plan_id=None, batch_id="b-unfin",
                       trigger="manual", case_count=2))
    _s.commit()
    _call("b-unfin")
    assert not _sent, f"批次未完成不应发送: {_sent}"
    print("OK 未完成不发")


def test_plan_manual_all_pass_green():
    _reset_sent()
    _mk_batch("b-mp", [ExecStatus.passed, ExecStatus.passed])
    _s.add(TestPlanRun(project_id=100, plan_id=None, batch_id="b-mp",
                       trigger="manual", case_count=2))
    _s.commit()
    _call("b-mp")
    assert len(_sent) == 1, f"计划手动全通过应发一条回执: {_sent}"
    body = _sent[0]
    assert "✅" in body, f"全通过应为绿色✅: {body}"      # ✅
    assert "手动" in body, f"应标注触发方式=手动: {body}"
    assert "通过 2" in body and "失败 0" in body, f"应含成败统计: {body}"
    print("OK 计划手动全通过发绿回执")


def test_plan_manual_with_fail_red():
    _reset_sent()
    _mk_batch("b-mf", [ExecStatus.passed, ExecStatus.failed])
    _s.add(TestPlanRun(project_id=100, plan_id=None, batch_id="b-mf",
                       trigger="manual", case_count=2))
    _s.commit()
    _call("b-mf")
    assert len(_sent) == 1, f"计划手动有失败应发一条回执: {_sent}"
    body = _sent[0]
    assert "\U0001f534" in body, f"有失败应为红色🔴: {body}"   # 🔴
    assert "失败 1" in body, f"应含失败数: {body}"
    print("OK 计划手动有失败发红回执")


def test_plan_auto_send():
    _reset_sent()
    _mk_batch("b-auto", [ExecStatus.passed, ExecStatus.passed])
    _s.add(TestPlanRun(project_id=100, plan_id=None, batch_id="b-auto",
                       trigger="auto", case_count=2))
    _s.commit()
    _call("b-auto")
    assert len(_sent) == 1, f"计划定时应发回执: {_sent}"
    assert "定时" in _sent[0], f"应标注触发方式=定时: {_sent[0]}"
    print("OK 计划定时发回执")


def test_feedback_all_pass_unchanged():
    """feedback 来源全通过 → 沿用原口径不发(行为不变)。"""
    _reset_sent()
    _mk_batch("b-fb-pass", [ExecStatus.passed, ExecStatus.passed])
    _s.add(FeedbackRun(project_id=100, batch_id="b-fb-pass", trigger="auto"))
    _s.commit()
    _call("b-fb-pass")
    assert not _sent, f"feedback 全通过应不发(行为不变): {_sent}"
    print("OK feedback 全通过不发(行为不变)")


def test_feedback_fail_unchanged():
    """feedback 来源有失败 → 仍走原失败告警。"""
    _reset_sent()
    _mk_batch("b-fb-fail", [ExecStatus.failed, ExecStatus.passed])
    _s.add(FeedbackRun(project_id=100, batch_id="b-fb-fail", trigger="auto"))
    _s.commit()
    _call("b-fb-fail")
    assert len(_sent) == 1, f"feedback 有失败应发失败告警: {_sent}"
    assert "回归失败告警" in _sent[0], f"应为原失败告警文案: {_sent[0]}"
    print("OK feedback 有失败仍走原告警(行为不变)")


def test_switch_off():
    _reset_sent()
    settings.NOTIFY_PLAN_RESULT = False
    try:
        _mk_batch("b-off", [ExecStatus.passed, ExecStatus.passed])
        _s.add(TestPlanRun(project_id=100, plan_id=None, batch_id="b-off",
                           trigger="manual", case_count=2))
        _s.commit()
        _call("b-off")
        assert not _sent, f"关掉开关后计划批次不应发: {_sent}"
    finally:
        settings.NOTIFY_PLAN_RESULT = True
    print("OK 开关关闭不发")


if __name__ == "__main__":
    _seed()
    test_unfinished_no_send()
    test_plan_manual_all_pass_green()
    test_plan_manual_with_fail_red()
    test_plan_auto_send()
    test_feedback_all_pass_unchanged()
    test_feedback_fail_unchanged()
    test_switch_off()
    print("\n全部通过 ✅")
