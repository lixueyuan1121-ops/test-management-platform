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
