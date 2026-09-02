"""ai_task 超龄收口(scheduler.reap_stale_ai_tasks)自测。
运行: cd backend && python -m scripts.test_aitask_reaper

背景(建议 #1:补全库唯独 ai_task 缺失的 reap,治「永久生成中」):
ai_task 入队即建 running(无 pending 态),排队中(对应 ai_job=pending)、执行中(ai_job=running)、
僵尸三者都表现为 running。只按时长收会误杀队头阻塞里合理排队的正常任务,故判据叠加
「无活跃对应 ai_job」。本测锁定该判据不回归。

覆盖:
- 僵尸(running 超龄 + 对应 job=failed / 无 job)→ 收口 failed(留痕 error)
- 排队中(running 超龄 + 对应 job=pending)→ 不收(关键:防误杀队头阻塞)
- 执行中(running 超龄 + 对应 job=running)→ 不收
- 新任务(running 未超龄)→ 不收
- done → 不动
- 启动收口 max_age=0:僵尸立即收,pending 对应的仍不收(自愈任务放过)
"""
import datetime as _dt
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import AiTaskStatus
from app.db.session import Base
from app.models import AiJob, AiTask  # noqa: F401  (建表需模型已导入)
from app.services.scheduler import reap_stale_ai_tasks

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)


def _sf():
    return _Session()


def _mk_task(s, tid, mins_ago, status=AiTaskStatus.running):
    # created_at 显式设(控制新老)。_db_now 在 SQLite 取 CURRENT_TIMESTAMP(UTC),与 utcnow 同口径可比。
    s.add(AiTask(id=tid, project_id=1, user_id=1, status=status,
                 created_at=_dt.datetime.utcnow() - timedelta(minutes=mins_ago)))


def _mk_job(s, jid, task_id, status):
    s.add(AiJob(id=jid, kind="testcase_gen", status=status, ref_kind="ai_task", ref_id=task_id))


def _reset(s):
    s.query(AiJob).delete()
    s.query(AiTask).delete()
    s.commit()


def test_periodic_reap():
    s = _Session()
    _reset(s)
    _mk_task(s, 1, 45); _mk_job(s, 101, 1, "failed")    # A 僵尸(job failed)→ 收
    _mk_task(s, 2, 45); _mk_job(s, 102, 2, "pending")   # B 排队中(job pending)→ 不收【防误杀】
    _mk_task(s, 3, 45); _mk_job(s, 103, 3, "running")   # C 执行中(job running)→ 不收
    _mk_task(s, 4, 5);  _mk_job(s, 104, 4, "running")   # D 新任务(未超龄)→ 不收
    _mk_task(s, 5, 45)                                  # E 无 job 僵尸 → 收
    _mk_task(s, 6, 45, status=AiTaskStatus.done)        # F done → 不动
    s.commit(); s.close()

    reaped = reap_stale_ai_tasks(session_factory=_sf, max_age_minutes=30)
    assert reaped == 2, f"应收 2 条(A/E),实收 {reaped}"

    s = _Session()
    assert s.get(AiTask, 1).status == AiTaskStatus.failed, "A 僵尸(job failed)应被收口"
    assert s.get(AiTask, 5).status == AiTaskStatus.failed, "E 无 job 僵尸应被收口"
    assert s.get(AiTask, 2).status == AiTaskStatus.running, "B 排队中不该被误杀(有 pending job)"
    assert s.get(AiTask, 3).status == AiTaskStatus.running, "C 执行中不该被收(有 running job)"
    assert s.get(AiTask, 4).status == AiTaskStatus.running, "D 新任务不该被收(未超龄)"
    assert s.get(AiTask, 6).status == AiTaskStatus.done, "F done 不该被动"
    assert (s.get(AiTask, 1).error or "").find("收口") >= 0, "收口应写 error 留痕"
    s.close()
    print("✓ 定时收口(max_age=30):A/E 收口,B/C/D/F 原样")


def test_startup_reap_zero_age():
    """启动收口 max_age=0:僵尸立即收,pending 对应的仍不收(靠「无活跃 job」而非时长保护)。"""
    s = _Session()
    _reset(s)
    _mk_task(s, 10, 1); _mk_job(s, 110, 10, "failed")   # 重启残留僵尸(对应 job 已被启动收口成 failed)
    _mk_task(s, 11, 1); _mk_job(s, 111, 11, "pending")  # pending 将被 worker 重跑 → 自愈,不收
    s.commit(); s.close()

    reaped = reap_stale_ai_tasks(session_factory=_sf, max_age_minutes=0)
    assert reaped == 1, f"启动收口应只收 1 条(#10),实收 {reaped}"

    s = _Session()
    assert s.get(AiTask, 10).status == AiTaskStatus.failed, "#10 重启残留僵尸应收"
    assert s.get(AiTask, 11).status == AiTaskStatus.running, "#11 pending 对应的不该收(会自愈)"
    s.close()
    print("✓ 启动收口(max_age=0):僵尸立即收,pending 自愈任务放过")


def main():
    test_periodic_reap()
    test_startup_reap_zero_age()
    print("\n✅ ai_task reaper 全部通过")


if __name__ == "__main__":
    main()
