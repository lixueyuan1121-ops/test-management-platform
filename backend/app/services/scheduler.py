"""反馈测试定时回归调度器（APScheduler 内置）。

BackgroundScheduler + SQLAlchemyJobStore（复用同一 DB，重启不丢 job）。
每个启用定时的回归集对应一个 job（id=fbset-<set_id>），到点调 _dispatch_cases(trigger=auto)。

进程模型：平台生产/开发均单进程 uvicorn（start-all.bat 不带 --reload），故调度器只有一个实例，
不会重复触发。job 用 replace_existing=True 幂等，重启时从 DB 现有 enabled 集重建。
"""
import logging
from datetime import datetime

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings

logger = logging.getLogger("test_platform")

_scheduler: BackgroundScheduler | None = None


def _job_id(set_id: int) -> str:
    return f"fbset-{set_id}"


def run_regression_job(set_id: int) -> None:
    """到点回调（模块级，供 SQLAlchemyJobStore 序列化）。

    下发集内**可自动化**用例（跳过 manual，无人值守不能因一个 manual 整批失败）+ 更新 last_run_at。
    """
    from app.api.feedback import _auto_case_ids_of_set, _dispatch_cases
    from app.db.session import SessionLocal
    from app.models import FeedbackRegressionSet

    db = SessionLocal()
    try:
        s = db.get(FeedbackRegressionSet, set_id)
        if not s or not s.schedule_enabled:
            return
        case_ids = _auto_case_ids_of_set(db, set_id)
        if not case_ids:
            logger.warning("定时回归跳过：集 %s 无可自动化用例", set_id)
            return
        project_id, runner = s.project_id, s.runner
        try:
            _dispatch_cases(db, project_id, case_ids, runner,
                            trigger="auto", set_id=set_id, started_by=None)
        except Exception:
            logger.exception("定时回归下发失败 set=%s", set_id)
            return
        s2 = db.get(FeedbackRegressionSet, set_id)
        if s2:
            s2.last_run_at = datetime.now()
            db.commit()
        logger.info("定时回归已触发 set=%s（%d 用例）", set_id, len(case_ids))
    finally:
        db.close()


def reap_stale_eval_runs() -> None:
    """周期收口:running 超 6 小时的对话测评 run 自动标记失败(模块级,供 job store 序列化)。

    执行器单条上限 5 小时(responseTimeout),超 6 小时仍 running 必是执行机中断/回写失败的
    僵尸条目——不收口则设备看板长期显示「执行中」(线上出现过卡 89h 的案例)、任务永远收不了口。
    只收 running 不收 pending:pending 是排队,执行机重新上线仍会拉走执行,不算死。
    created_at 由数据库 func.now() 生成(SQLite/docker MySQL 均为 UTC),比较用 utcnow 对齐。
    """
    from datetime import timedelta

    from app.core.enums import EvalRunStatus
    from app.db.session import SessionLocal
    from app.models import EvalRun

    cutoff = datetime.utcnow() - timedelta(hours=6)
    db = SessionLocal()
    try:
        rows = (db.query(EvalRun)
                .filter(EvalRun.status == EvalRunStatus.running, EvalRun.created_at < cutoff)
                .all())
        for r in rows:
            r.status = EvalRunStatus.failed
            r.reason = "自动收口:执行超 6 小时未回填(执行机中断),标记失败"
        if rows:
            db.commit()
            logger.info("自动收口 %d 条超龄 running eval_run: %s", len(rows), [r.id for r in rows])
    except Exception:
        logger.exception("自动收口超龄 eval_run 失败")
        db.rollback()
    finally:
        db.close()


def run_eval_task_job(task_id: int) -> None:
    """测评任务定时执行回调(模块级,供 job store 序列化):到点自动下发整任务(CI 回归守卫)。

    沿用任务最近一次执行的对话选项(含 compareB 的 A/B 对比);上一批还有 pending/running
    时跳过本次(执行机没跑完,堆新批只会排队挤压,下个周期再试)。
    """
    import json as _json

    from app.core.enums import EvalRunStatus
    from app.db.session import SessionLocal
    from app.models import EvalRun
    from app.models.ai_eval import EvalTask

    db = SessionLocal()
    try:
        task = db.get(EvalTask, task_id)
        if not task or not task.schedule_enabled or not task.schedule_runner:
            return
        pending_cnt = (db.query(EvalRun)
                       .filter(EvalRun.eval_task_id == task_id,
                               EvalRun.batch_id == task.last_batch_id,
                               EvalRun.status.in_([EvalRunStatus.pending, EvalRunStatus.running]))
                       .count()) if task.last_batch_id else 0
        if pending_cnt:
            logger.info("定时测评跳过:任务 %s 上一批还有 %d 条未执行完", task_id, pending_cnt)
            return
        try:
            stored = _json.loads(task.dialog_options) if task.dialog_options else {}
        except (ValueError, TypeError):
            stored = {}
        opts_b = stored.pop("compareB", None) if isinstance(stored, dict) else None
        opts = stored if isinstance(stored, dict) else {}
        from app.api.eval_task import dispatch_task_runs
        try:
            created, batch_id = dispatch_task_runs(
                db, task, task.schedule_runner, "namiwork", None, opts, opts_b, None)
        except ValueError as e:
            logger.warning("定时测评跳过:任务 %s %s", task_id, e)
            db.rollback()
            return
        task.last_auto_run_at = datetime.now()
        db.commit()
        logger.info("定时测评已下发 任务=%s 批次=%s(%d 条)", task_id, batch_id, len(created))
    except Exception:
        logger.exception("定时测评执行失败 任务=%s", task_id)
        db.rollback()
    finally:
        db.close()


def sync_eval_task_job(task_id: int, cron: str | None, enabled: bool):
    """按测评任务的 cron/开关 增删定时 job(replace_existing 幂等)。"""
    if _scheduler is None:
        return None
    jid = f"evaltask-{task_id}"
    if enabled and cron:
        trigger = CronTrigger.from_crontab(cron, timezone="Asia/Shanghai")
        job = _scheduler.add_job(
            run_eval_task_job, trigger=trigger, id=jid,
            args=[task_id], replace_existing=True, misfire_grace_time=300,
        )
        return job.next_run_time
    try:
        _scheduler.remove_job(jid)
    except Exception:
        pass  # 本就没有该 job
    return None


def start_scheduler() -> None:
    """启动调度器 + 从 DB 现有 enabled 集重建 job。startup 调用（幂等）。"""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=settings.sqlalchemy_url)},
        timezone="Asia/Shanghai",
    )
    _scheduler.start()
    logger.info("反馈定时调度器已启动")

    # 固定周期 job:自动收口超龄 running 的测评 run(每 30 分钟;replace_existing 幂等,重启不重复)
    _scheduler.add_job(
        reap_stale_eval_runs, trigger=IntervalTrigger(minutes=30), id="evalrun-reaper",
        replace_existing=True, misfire_grace_time=600,
    )

    # 从 DB 重建 job（job store 里可能已有持久化 job；这里以 DB 集状态为准覆盖，避免漂移）
    from app.db.session import SessionLocal
    from app.models import FeedbackRegressionSet
    db = SessionLocal()
    try:
        sets = (db.query(FeedbackRegressionSet)
                .filter(FeedbackRegressionSet.schedule_enabled.is_(True),
                        FeedbackRegressionSet.schedule_cron.isnot(None))
                .all())
        for s in sets:
            try:
                sync_set_job(s.id, s.schedule_cron, True)
            except Exception:
                logger.exception("重建定时 job 失败 set=%s", s.id)
        # 测评任务定时(CI 回归守卫)同款重建
        from app.models.ai_eval import EvalTask
        tasks = (db.query(EvalTask)
                 .filter(EvalTask.schedule_enabled.is_(True), EvalTask.schedule_cron.isnot(None))
                 .all())
        for t in tasks:
            try:
                sync_eval_task_job(t.id, t.schedule_cron, True)
            except Exception:
                logger.exception("重建测评定时 job 失败 task=%s", t.id)
    finally:
        db.close()


def shutdown_scheduler() -> None:
    """关闭调度器。shutdown 调用。"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("反馈定时调度器已关闭")


def sync_set_job(set_id: int, cron: str | None, enabled: bool) -> datetime | None:
    """按集的 cron/开关 增删 job，返回下次触发时间（供回填 next_run_at）。

    enabled 且 cron 合法 → add_job（replace_existing 幂等）；否则 remove_job。
    """
    if _scheduler is None:
        return None
    jid = _job_id(set_id)
    if enabled and cron:
        trigger = CronTrigger.from_crontab(cron, timezone="Asia/Shanghai")
        job = _scheduler.add_job(
            run_regression_job, trigger=trigger, id=jid,
            args=[set_id], replace_existing=True, misfire_grace_time=300,
        )
        return job.next_run_time
    else:
        try:
            _scheduler.remove_job(jid)
        except Exception:
            pass  # 本就没有该 job
        return None
