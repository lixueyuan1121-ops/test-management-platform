"""失败自动重试 + flaky 识别(建议项⑧)自测。
运行: cd backend && python -m scripts.test_retry_flaky

覆盖:
- auto 批次失败回写 → 自动补发重试(同批/同快照/retry_of/attempt=2);批次不收口(不发卡不建缺陷)
- 重试通过 → flaky=True;有效口径 1 条 passed,不建缺陷、不发卡(全过);门禁 gate=pass 且 flaky=1
- 重试仍失败 → 不再三试;有效口径只计重试行 1 条失败,自动缺陷恰 1 条(挂重试 run)
- manual 批次不重试;EXEC_AUTO_RETRY=0 不重试;blocked(selector) 也重试
- reaper 收口的 run 打 fail_kind=timeout(不入真bug统计口径)
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.deps import require_runner_ctx, RunnerCtx
from app.core.enums import ExecStatus
from app.db.session import Base, get_db
from app.main import app
from app.models import ExecRun, FeedbackRun, Project, RemainingIssue, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _get_db():
    yield _s


app.dependency_overrides[get_db] = _get_db
app.dependency_overrides[require_runner_ctx] = lambda: RunnerCtx(device=None)
client = TestClient(app)


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="纳米Work", code="nw"),
    ])
    _s.commit()


def _pending(batch, case_id, trigger="auto", runner="mac-01"):
    if trigger and not _s.query(FeedbackRun).filter_by(batch_id=batch).first():
        _s.add(FeedbackRun(project_id=100, batch_id=batch, trigger=trigger, case_count=1))
    r = ExecRun(project_id=100, test_case_id=case_id, runner=runner,
                payload=json.dumps({"title": f"用例{case_id}", "priority": "P1"}),
                status=ExecStatus.pending, batch_id=batch)
    _s.add(r)
    _s.commit()
    return r


def _report(run_id, verdict, fail_kind=None, runner="mac-01"):
    return client.patch(f"/api/exec-queue/{run_id}", params={"runner": runner},
                        json={"verdict": verdict, "fail_kind": fail_kind,
                              "reason": "断言失败" if verdict == "fail" else None}).json()


def _retry_row(orig_id):
    return _s.query(ExecRun).filter(ExecRun.retry_of == orig_id).first()


def test_retry_then_pass_flaky():
    r = _pending("b1", 11)
    d = _report(r.id, "fail", "business")
    assert d["code"] == 0
    retry = _retry_row(r.id)
    assert retry is not None, "auto 批次失败应补发重试"
    assert retry.batch_id == "b1" and retry.attempt == 2 and retry.payload == r.payload
    assert retry.status == ExecStatus.pending
    assert _s.query(RemainingIssue).count() == 0, "批次未收口不应建缺陷"

    # 重试通过 → flaky;批次收口:有效口径全过 → 不建缺陷
    d2 = _report(retry.id, "pass")
    assert d2["code"] == 0 and d2["data"]["flaky"] is True
    _s.refresh(retry)
    assert retry.flaky is True
    assert _s.query(RemainingIssue).count() == 0, "flaky 通过不应开缺陷单"
    # 门禁:有效 1 条 passed → pass,flaky=1,total=1(原始失败行不计)
    settings.CI_HOOK_TOKEN = "t"
    g = client.get("/api/hooks/gate", params={"batch_id": "b1"},
                   headers={"X-CI-Token": "t"}).json()["data"]
    assert g["gate"] == "pass" and g["total"] == 1 and g["flaky"] == 1, g
    print("OK retry→pass flaky")


def test_retry_fail_again():
    r = _pending("b2", 12)
    _report(r.id, "fail", "business")
    retry = _retry_row(r.id)
    d = _report(retry.id, "fail", "business")
    assert d["code"] == 0
    assert _retry_row(retry.id) is None, "重试行失败不应三试"
    # 批次收口:有效口径 1 失败 → 自动缺陷恰 1 条,挂重试 run
    issues = _s.query(RemainingIssue).all()
    assert len(issues) == 1, f"应恰 1 条缺陷,实际 {len(issues)}"
    assert issues[0].exec_run_id == retry.id
    g = client.get("/api/hooks/gate", params={"batch_id": "b2"},
                   headers={"X-CI-Token": "t"}).json()["data"]
    assert g["gate"] == "fail" and g["total"] == 1 and g["failed"] == 1, g
    print("OK retry→fail again (no 3rd try, single issue)")


def test_blocked_also_retries_and_manual_no_retry():
    r = _pending("b3", 13)
    _report(r.id, "fail", "selector")   # → blocked
    assert _retry_row(r.id) is not None, "selector 阻塞也应重试(瞬态环境可自愈)"

    m = _pending("b4", 14, trigger="manual")
    _report(m.id, "fail", "business")
    assert _retry_row(m.id) is None, "manual 批次不应自动重试"

    orig = settings.EXEC_AUTO_RETRY
    settings.EXEC_AUTO_RETRY = 0
    try:
        z = _pending("b5", 15)
        _report(z.id, "fail", "business")
        assert _retry_row(z.id) is None, "EXEC_AUTO_RETRY=0 不应重试"
    finally:
        settings.EXEC_AUTO_RETRY = orig
    print("OK blocked retries / manual & toggle off no retry")


def test_reaper_timeout_kind():
    from datetime import datetime, timedelta
    from app.services.scheduler import reap_stale_exec_runs

    r = _pending("b6", 16)
    r.status = ExecStatus.running
    _s.flush()
    r.updated_at = datetime.utcnow() - timedelta(hours=3)
    _s.commit()
    reap_stale_exec_runs(session_factory=_Session)
    s2 = _Session()
    row = s2.get(ExecRun, r.id)
    assert row.status.value == "failed" and row.fail_kind == "timeout", (row.status, row.fail_kind)
    s2.close()
    print("OK reaper timeout fail_kind")


def main():
    _seed()
    test_retry_then_pass_flaky()
    test_retry_fail_again()
    test_blocked_also_retries_and_manual_no_retry()
    test_reaper_timeout_kind()
    print("OK test_retry_flaky")


if __name__ == "__main__":
    main()
