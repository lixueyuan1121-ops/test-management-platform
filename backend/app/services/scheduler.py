"""定时回归调度器（APScheduler 内置）+ exec/eval 超龄收口 + 飞书通知定时 job。

BackgroundScheduler + SQLAlchemyJobStore（复用同一 DB，重启不丢 job）。
每个启用定时的回归集 / 测试计划对应一个 job（id=fbset-<set_id> / plan-<plan_id>），
到点调各自的下发函数（feedback 走 _dispatch_cases，plan 走 _dispatch_plan）。

进程模型：平台生产/开发均单进程 uvicorn（start-all.bat 不带 --reload），故调度器只有一个实例，
不会重复触发。job 用 replace_existing=True 幂等，重启时从 DB 现有 enabled 集/计划重建。
"""
import logging
from datetime import datetime, timedelta

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.models import ExecRun, Project  # noqa: F401

logger = logging.getLogger("test_platform")

# 超龄收口阈值（小时）：exec_run 超此时长仍 running 即收口为 failed
EXEC_STALE_HOURS = 2

_scheduler: BackgroundScheduler | None = None


def _job_id(set_id: int) -> str:
    return f"fbset-{set_id}"


def _plan_job_id(plan_id: int) -> str:
    return f"plan-{plan_id}"


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


def reap_stale_exec_runs(session_factory=None) -> int:
    """周期收口:running 超 2 小时的 exec_run 自动标记 failed(执行机中断兜底)。

    与 reap_stale_eval_runs 同款收口——exec_run 是"执行机崩溃即永久 running"的重灾地:
    runner 中途死掉/网络断连不会回写,run 永远卡 running,既占设备看板"执行中"、又让
    /release/quality 等统计把没跑完的算进全部执行里。
    - 不设 fail_kind(收口归因=执行机中断,不是选择器/断言);reason 打「自动收口」前缀留痕。
    - 只收 running 不收 pending:pending 是排队,执行机恢复会继续拉走。
    - 收口后该批若因此凑齐终态,则复用 exec-queue 的批次完成钩子发飞书告警(定时批次)。
    - session_factory 参数供自测注入内存库;缺省用项目 SessionLocal。
    """
    from datetime import timedelta

    from app.core.enums import ExecStatus
    from app.db.session import SessionLocal

    sf = session_factory or SessionLocal
    cutoff = datetime.utcnow() - timedelta(hours=EXEC_STALE_HOURS)
    db = sf()
    reaped = 0
    try:
        rows = (db.query(ExecRun)
                .filter(ExecRun.status == ExecStatus.running, ExecRun.updated_at < cutoff)
                .all())
        for r in rows:
            r.status = ExecStatus.failed
            r.fail_kind = "timeout"   # L2 细化:收口归因=执行超时(非 selector/business,不入真bug统计)
            r.reason = "自动收口:执行超 2 小时未回填(执行机中断),标记失败"
            reaped += 1
        if rows:
            db.commit()
            logger.info("自动收口 %d 条超龄 running exec_run", reaped)
            # 收口后的批次若凑齐终态 → 复用批次完成钩子告警(真正的定时回归需有人知道挂了)
            for _b in {r.batch_id for r in rows}:
                if _b:
                    try:
                        from app.api.exec_queue import notify_batch_if_done
                        notify_batch_if_done(db, _b)
                    except Exception:
                        logger.exception("收口后批次告警失败 batch=%s", _b)
    except Exception:
        logger.exception("自动收口超龄 exec_run 失败")
        db.rollback()
    finally:
        db.close()
    return reaped


def _now_sh() -> datetime:
    """当前 Asia/Shanghai 时间(调度器时区),供"今天"判定。"""
    from datetime import timezone
    return datetime.now(timezone.utc).astimezone().replace(tzinfo=None)


def remind_missing_reports() -> None:
    """日报缺交提醒:遍历有当日任务的项目,谁该交没交 → 每项目一张飞书卡。

    统计口径与 /stats/daily 完全一致(应交=当日任务 assigned_to 去重,已交=当日有日报的 user),
    重复实现一次而非调 API——定时 job 无用户上下文,且调外部接口绕一层反而脆。
    未配置 FEISHU_WEBHOOK_URL 或没到配置时刻时无副作用(到点才由 cron 调)。
    """
    if not settings.NOTIFY_REPORT_MISSING:
        return
    from app.models import DailyReport, Task, User  # noqa: F401
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        today = _now_sh().date()
        # 只扫"今天有任务"的项目(项目数少,直接查;不扫全部项目避免空卡片)
        proj_ids = [pid for (pid,) in db.query(Task.project_id)
                    .filter(Task.assigned_date == today).distinct().all()]
        for pid in proj_ids:
            tasks = db.query(Task).filter(
                Task.project_id == pid, Task.assigned_date == today).all()
            if not tasks:
                continue
            should = sorted({t.assigned_to for t in tasks if t.assigned_to})
            task_ids = [t.id for t in tasks]
            submitted = {r.user_id for r in db.query(DailyReport)
                         .filter(DailyReport.task_id.in_(task_ids),
                                 DailyReport.report_date == today).all() if r.user_id}
            missing = [u for u in should if u not in submitted]
            if not missing:
                continue
            names = dict(db.query(User.id, User.name).filter(User.id.in_(missing)).all())
            proj = db.get(Project, pid)
            from app.services.notify import notify_reports_missing
            notify_reports_missing(
                project_name=proj.name if proj else f"项目#{pid}",
                report_date=str(today),
                missing_names=[names.get(uid, str(uid)) for uid in missing],
                submitted=len(submitted), expected=len(should),
            )
    except Exception:
        logger.exception("日报缺交提醒失败")
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


def check_devices_offline() -> None:
    """执行机离线巡检:有 pending 排队但设备超时未心跳 → 飞书告警。

    判定口径与设备看板一致(ONLINE_WINDOW_SEC 秒内无 last_seen 即离线;有 running 看作在线)。
    同一台设备在"告警一次且仍未恢复"期间不再重复发(模块级已警集合,恢复后清除)。
    """
    from app.api.devices import ONLINE_WINDOW_SEC, ACTIVE_RUNS_LIMIT  # noqa: F401
    from app.core.enums import ExecStatus
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_WINDOW_SEC)
        # 只查"尚有 pending 未认领"的 runner —— 离线但没积压任务的不打扰
        pending_runners = [r for (r,) in db.query(ExecRun.runner)
                           .filter(ExecRun.status == ExecStatus.pending).distinct().all()]
        if not pending_runners:
            return
        from app.models import RunnerDevice
        agg = {}
        for rd in db.query(RunnerDevice).filter(RunnerDevice.runner_id.in_(pending_runners)).all():
            agg.setdefault(rd.runner_id, {"name": rd.name, "last": rd.last_seen_at})
        busy = {r for (r,) in db.query(ExecRun.runner)
                .filter(ExecRun.status == ExecStatus.running).distinct().all()}
        offline = []
        for rid, info in agg.items():
            if rid in busy:
                continue  # 正执行中必是活的
            last = info["last"]
            if last is None or last < cutoff:
                offline.append((rid, info["name"]))
        for rid, name in offline:
            if rid in _OFFLINE_NOTIFIED:
                continue
            pend_cnt = db.query(ExecRun).filter(
                ExecRun.runner == rid, ExecRun.status == ExecStatus.pending).count()
            from app.services.notify import notify_devices_offline
            notify_devices_offline([name], pend_cnt)
            _OFFLINE_NOTIFIED.add(rid)
        gone = set(agg.keys()) - {rid for rid, _ in offline}
        _OFFLINE_NOTIFIED -= gone  # 恢复上线的设备清告警集合,下次再断可再警
    except Exception:
        logger.exception("设备离线巡检失败")
    finally:
        db.close()


# 已告警的离线 runner 集合(会话级;上线后清除,允许再次告警)
_OFFLINE_NOTIFIED: set[str] = set()


def run_plan_job(plan_id: int) -> None:
    """到点回调（模块级，供 SQLAlchemyJobStore 序列化）。

    下发计划内**可自动化**用例（跳过 manual） + 更新 last_run_at。
    """
    from app.api.test_plan import _auto_case_ids_of_plan, _dispatch_plan
    from app.db.session import SessionLocal
    from app.models import TestPlan

    db = SessionLocal()
    try:
        p = db.get(TestPlan, plan_id)
        if not p or not p.schedule_enabled:
            return
        case_ids = _auto_case_ids_of_plan(db, plan_id)
        if not case_ids:
            logger.warning("定时计划跳过：计划 %s 无可自动化用例", plan_id)
            return
        try:
            _dispatch_plan(db, p, case_ids, p.runner, trigger="auto", started_by=None)
        except Exception:
            logger.exception("定时计划下发失败 plan=%s", plan_id)
            return
        logger.info("定时计划已触发 plan=%s（%d 用例）", plan_id, len(case_ids))
    finally:
        db.close()


def start_scheduler() -> None:
    """启动调度器 + 从 DB 现有 enabled 集/计划重建 job。startup 调用（幂等）。"""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=settings.sqlalchemy_url)},
        timezone="Asia/Shanghai",
    )
    _scheduler.start()
    logger.info("定时回归调度器已启动")

    # 固定周期 job:自动收口超龄 running 的测评 run(每 30 分钟;replace_existing 幂等,重启不重复)
    _scheduler.add_job(
        reap_stale_eval_runs, trigger=IntervalTrigger(minutes=30), id="evalrun-reaper",
        replace_existing=True, misfire_grace_time=600,
    )
    # 固定周期 job:自动收口超龄 running 的 exec_run(每 30 分钟,与 eval 同频)
    _scheduler.add_job(
        reap_stale_exec_runs, trigger=IntervalTrigger(minutes=30), id="execrun-reaper",
        replace_existing=True, misfire_grace_time=600,
    )
    # 固定周期 job:设备离线巡检(每 5 分钟;pending 堆积但设备掉线才发告警)
    _scheduler.add_job(
        check_devices_offline, trigger=IntervalTrigger(minutes=5), id="device-offline-check",
        replace_existing=True, misfire_grace_time=180,
    )
    # 配置了提醒时刻才建日报缺交提醒 job(每日固定时刻,如 20:00)
    hhmm = (settings.REPORT_REMIND_AT or "").strip()
    if hhmm and ":" in hhmm:
        try:
            h, m = hhmm.split(":")
            _scheduler.add_job(
                remind_missing_reports, trigger=CronTrigger(hour=int(h), minute=int(m)),
                id="report-missing-remind", replace_existing=True, misfire_grace_time=900,
            )
            logger.info("日报缺交提醒已启用: 每日 %s", hhmm)
        except (ValueError, IndexError):
            logger.warning("日报提醒时刻格式错误(需 HH:MM): %s", hhmm)

    # 从 DB 重建 job（job store 里可能已有持久化 job；这里以 DB 集/计划状态为准覆盖，避免漂移）
    from app.db.session import SessionLocal
    from app.models import FeedbackRegressionSet, TestPlan
    db = SessionLocal()
    try:
        # 重建 feedback 定时集
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
        # 重建 test_plan 定时计划
        from app.models import TestPlan
        plans = (db.query(TestPlan)
                 .filter(TestPlan.schedule_enabled.is_(True),
                         TestPlan.schedule_cron.isnot(None))
                 .all())
        for p in plans:
            try:
                sync_plan_job(p.id, p.schedule_cron, True)
            except Exception:
                logger.exception("重建定时 job 失败 plan=%s", p.id)
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


def sync_plan_job(plan_id: int, cron: str | None, enabled: bool) -> datetime | None:
    """按测试计划的 cron/开关 增删 job，返回下次触发时间（供回填 next_run_at）。

    enabled 且 cron 合法 → add_job（replace_existing 幂等）；否则 remove_job。
    """
    if _scheduler is None:
        return None
    jid = _plan_job_id(plan_id)
    if enabled and cron:
        trigger = CronTrigger.from_crontab(cron, timezone="Asia/Shanghai")
        job = _scheduler.add_job(
            run_plan_job, trigger=trigger, id=jid,
            args=[plan_id], replace_existing=True, misfire_grace_time=300,
        )
        return job.next_run_time
    else:
        try:
            _scheduler.remove_job(jid)
        except Exception:
            pass
        return None
