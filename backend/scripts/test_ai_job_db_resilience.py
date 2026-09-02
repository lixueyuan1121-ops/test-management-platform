"""AI job 写库断连自愈自测(诊断文档 P0+P1+P2)。
运行: cd backend && python -m scripts.test_ai_job_db_resilience

背景:生成完成后写库瞬间 MySQL 2013 Lost connection,原实现①单 session 贯穿生成全程 →
连接空闲被中间层掐断;②异常分支复用已损坏 session 访问 job.kind → PendingRollbackError
二次崩溃 → job 永久僵死 running。本测锁定修复不回归。

覆盖:
- P0: handler 抛异常时,run_job 用独立 session 把 job 落 failed(不复用损坏 session)
- P0: _fail_job_isolated 直连 UPDATE,不读 ORM 属性(模拟旧 session 已 expired 也能落 failed)
- P1/P2: _persist_with_retry 遇 OperationalError 重连重试成功;非 DB 异常不重试直接上抛
"""
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import AiJob  # noqa: F401
from app.services import ai_jobs

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)


def _clear():
    s = _Session()
    s.query(AiJob).delete()
    s.commit()
    s.close()


def test_p0_handler_exception_lands_failed_via_isolated_session():
    """handler 抛异常 → run_job 用独立 session 把 job 落 failed(不僵死 running)。"""
    _clear()
    s = _Session()
    # 用自定义 kind:_ensure_handlers 只认内置几种,不会 import 覆盖掉我们注入的 boom
    job = AiJob(id=1, kind="_boom_kind", status="running", provider="claude")
    s.add(job); s.commit(); s.close()

    def boom(_s, _job):
        raise RuntimeError("生成后写库 2013 Lost connection")

    ai_jobs._HANDLERS["_boom_kind"] = boom
    ai_jobs.run_job(_Session, 1)

    s = _Session()
    got = s.get(AiJob, 1)
    assert got.status == "failed", f"应落 failed,实际 {got.status}"
    assert got.error and "2013" in got.error, f"error 应留痕,实际 {got.error}"
    s.close()
    print("✓ P0:handler 异常 → 独立 session 落 failed,不僵死 running")


def test_p0_isolated_fail_survives_broken_original_session():
    """_fail_job_isolated 全程新 session + 直连 UPDATE,即便原 session 已坏也能落 failed。"""
    _clear()
    s = _Session()
    s.add(AiJob(id=2, kind="testcase_gen", status="running")); s.commit(); s.close()

    ai_jobs._fail_job_isolated(_Session, 2, "testcase_gen", "写库断连")
    s = _Session()
    assert s.get(AiJob, 2).status == "failed", "独立 session 应能落 failed"
    s.close()
    print("✓ P0:_fail_job_isolated 直连 UPDATE 落 failed(不碰损坏 ORM 对象)")


def test_p0_isolated_fail_not_overwrite_done():
    """已 done 的 job 不被兜底误伤(WHERE status notin done/cancelled)。"""
    _clear()
    s = _Session()
    s.add(AiJob(id=3, kind="testcase_gen", status="done", result="{}")); s.commit(); s.close()

    ai_jobs._fail_job_isolated(_Session, 3, "testcase_gen", "误触发")
    s = _Session()
    assert s.get(AiJob, 3).status == "done", "done 的 job 不该被兜底改成 failed"
    s.close()
    print("✓ P0:done 的 job 不被兜底误伤")


def test_p1_persist_retry_recovers_from_disconnect():
    """_persist_with_retry:首次 OperationalError,重试成功。"""
    from app.api.ai import _persist_with_retry

    calls = {"n": 0}

    def persist(_s):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("2013 Lost connection"))
        return (["obj"], None)

    with patch("app.api.ai.time.sleep"):   # 免真等退避
        objs, fail = _persist_with_retry(persist, _Session, retries=2)
    assert objs == ["obj"] and fail is None, "重试后应成功"
    assert calls["n"] == 2, f"应重试到第 2 次成功,实际调用 {calls['n']} 次"
    print("✓ P1/P2:写库断连重连重试成功")


def test_p1_persist_non_db_error_no_retry():
    """非 DB 异常(如数据校验)不重试,直接上抛。"""
    from app.api.ai import _persist_with_retry

    calls = {"n": 0}

    def persist(_s):
        calls["n"] += 1
        raise ValueError("数据不合法")

    try:
        _persist_with_retry(persist, _Session, retries=2)
        assert False, "应上抛 ValueError"
    except ValueError:
        pass
    assert calls["n"] == 1, f"非 DB 异常不该重试,实际 {calls['n']} 次"
    print("✓ P1:非 DB 异常不重试,直接上抛")


def main():
    test_p0_handler_exception_lands_failed_via_isolated_session()
    test_p0_isolated_fail_survives_broken_original_session()
    test_p0_isolated_fail_not_overwrite_done()
    test_p1_persist_retry_recovers_from_disconnect()
    test_p1_persist_non_db_error_no_retry()
    print("\n✅ AI job 写库断连自愈 全部通过")


if __name__ == "__main__":
    main()
