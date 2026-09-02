"""AI 任务队列 + 进程内 worker 池(方案2 核心)。

见 docs/superpowers/specs/2026-09-01-ai-job-queue-design.md。本模块负责:
- enqueue:特性端点建 pending job(输入快照),立即返回;唤醒空闲 worker。
- claim_next:worker 用条件 UPDATE 原子抢占最早 pending→running(rowcount==1 才算抢到),
  防多线程双跑(沿用 eval_pipeline 抢占范式)。
- queue_position:同 pending 中排在本 job 前的条数(读时现算,不落列)。
- run_job:另开 session,按 kind→handler 跑;成功置 done+result,失败置 failed+error(不覆盖域数据)。
- worker 池 / 启动收口 / 生命周期:见 Task4。
"""
import json
import logging
import threading
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import AiJob

logger = logging.getLogger("test_platform")

# kind → handler(db, job) -> result dict。各特性模块 import 时 register。
_HANDLERS: dict = {}


def register_handler(kind: str, fn) -> None:
    _HANDLERS[kind] = fn


# ── 入队 ─────────────────────────────────────────────────────────────────────────

def enqueue(db: Session, kind: str, *, provider: str | None = None,
            project_id: int | None = None, user_id: int | None = None,
            input: dict | None = None, ref_kind: str | None = None,
            ref_id: int | None = None) -> AiJob:
    """建一条 pending job(输入快照 json.dumps),commit,唤醒 worker。返回该 job。"""
    job = AiJob(
        kind=kind,
        provider=(provider or "claude"),
        status="pending",
        project_id=project_id,
        user_id=user_id,
        input=json.dumps(input or {}, ensure_ascii=False),
        ref_kind=ref_kind,
        ref_id=ref_id,
    )
    db.add(job); db.commit(); db.refresh(job)
    notify_new_job()
    return job


# ── 抢占 / 排队 ───────────────────────────────────────────────────────────────────

def claim_next(db: Session, worker: str | None = None) -> AiJob | None:
    """原子抢占最早的一条 pending → running。返回抢到的 job,无则 None。

    条件 UPDATE(WHERE id=? AND status='pending')+rowcount 判定,防多 worker 双跑。
    """
    row = (db.query(AiJob.id)
           .filter(AiJob.status == "pending")
           .order_by(AiJob.created_at.asc(), AiJob.id.asc())
           .first())
    if not row:
        return None
    job_id = row[0]
    res = db.execute(
        update(AiJob)
        .where(AiJob.id == job_id, AiJob.status == "pending")
        .values(status="running", worker=(worker or threading.current_thread().name),
                claimed_at=datetime.now())
    )
    db.commit()
    if res.rowcount != 1:
        return None  # 被其他 worker 抢先
    return db.get(AiJob, job_id)


def queue_position(db: Session, job: AiJob) -> int:
    """本 job 在 pending 队列里前面还有几条(running/done/… 返回 0)。

    按 id 计数(id 自增,与入队顺序一致;避开 created_at 秒级精度并列的坑)。
    """
    if getattr(job, "status", None) != "pending":
        return 0
    return (db.query(AiJob)
            .filter(AiJob.status == "pending", AiJob.id < job.id)
            .count())


def get_job(db: Session, job_id: int) -> AiJob | None:
    return db.get(AiJob, job_id)


# ── 执行 ─────────────────────────────────────────────────────────────────────────

def _ensure_handlers() -> None:
    """惰性 import 各特性模块,触发其 register_handler(避免循环导入,worker 启动/首跑前确保就位)。"""
    if "triage" not in _HANDLERS:
        import app.services.exec_triage  # noqa: F401  (import 时 register triage handler)
    if "eval_judge" not in _HANDLERS:
        import app.services.eval_judge  # noqa: F401  (import 时 register eval_judge handler)
    if "script_gen" not in _HANDLERS:
        import app.api.ai  # noqa: F401  (import 时 register script_gen + testcase_gen handler)
    if "eval_query_gen" not in _HANDLERS:
        import app.api.ai_eval  # noqa: F401  (import 时 register eval_query_gen handler)
    if "eval_summary" not in _HANDLERS:
        import app.api.eval_task  # noqa: F401  (import 时 register eval_summary handler)


def _fail_job_isolated(session_factory, job_id: int, kind: str, err: str) -> None:
    """用**独立新 session** 把 job 落 failed。

    handler 执行期间若 DB 断连(2013 Lost connection),原 session 已进入 rollback 态、
    其对象全部 expired——在它上面再读/写任何属性都会触发 lazy-load 二次崩溃(PendingRollbackError),
    failed 状态落不下去 → job 永久僵死 running(诊断文档 P0)。故这里全程只用新 session、
    直连 UPDATE,不碰任何旧的 ORM 对象。新 session 自身再失败也吞掉(reaper 兜底收口)。
    """
    try:
        s2 = session_factory()
        try:
            s2.execute(
                update(AiJob).where(AiJob.id == job_id,
                                    AiJob.status.notin_(["done", "cancelled"]))
                .values(status="failed", error=(err or "执行失败")[:2000])
            )
            s2.commit()
        finally:
            s2.close()
    except Exception:  # noqa: BLE001  连落 failed 都失败(DB 仍不可用)→ 交给启动/定时 reaper 兜底
        logger.exception("落 failed 失败(独立 session) job_id=%s kind=%s", job_id, kind)


def run_job(session_factory, job_id: int) -> None:
    """另开 session 跑一条 job:查 handler → 执行 → 成功 done+result / 失败 failed+error。

    handler 负责写域表(TestCase/ExecRun.triage/…)并返回 result dict;失败(抛异常)时
    **不覆盖域数据**(handler 内在解析失败前不写域表)。异常全捕获——worker 线程不能抛。
    session_factory 供测试注入(=SessionLocal)。

    连接管理(诊断文档):handler(含 claude 生成的百秒级耗时)期间不应持有 DB 连接,否则连接
    空闲被中间层掐断、写库时 2013 Lost connection。生成/写库的分段由 handler 自己负责
    (见 run_testcase_gen_job);本函数只保证异常兜底用**独立 session**,不复用已损坏的 s。
    """
    _ensure_handlers()
    s = session_factory()
    kind = None
    try:
        job = s.get(AiJob, job_id)
        if job is None:
            return
        kind = job.kind   # 缓存:session 健康时取出,异常日志/落 failed 都不再回读 ORM 属性
        handler = _HANDLERS.get(kind)
        if handler is None:
            job.status = "failed"
            job.error = f"未知的 AI 任务类型:{kind}"
            s.commit()
            return
        t0 = datetime.now()
        try:
            result = handler(s, job) or {}
        except Exception as e:  # noqa: BLE001
            logger.exception("AI job 执行失败 id=%s kind=%s", job_id, kind)
            try:
                s.rollback()
            except Exception:  # noqa: BLE001  断连时 rollback 本身也可能抛,忽略
                pass
            # 用独立新 session 落 failed(旧 s 可能已断连/expired,复用会二次崩溃 → 永久僵死)
            _fail_job_isolated(session_factory, job_id, kind, str(e))
            return
        job.status = "done"
        job.result = json.dumps(result, ensure_ascii=False)
        if result.get("output_raw"):
            job.output_raw = result.get("output_raw")
        job.duration_ms = int((datetime.now() - t0).total_seconds() * 1000)
        s.commit()
    except Exception as e:  # noqa: BLE001  成功路径的 commit 等也可能断连,同样用独立 session 兜底
        logger.exception("AI job 收尾失败 id=%s kind=%s", job_id, kind)
        try:
            s.rollback()
        except Exception:  # noqa: BLE001
            pass
        _fail_job_isolated(session_factory, job_id, kind or "", str(e))
    finally:
        s.close()


# ── 唤醒 / worker 池 ──────────────────────────────────────────────────────────────
_wake = threading.Event()
_stop = threading.Event()
_threads: list[threading.Thread] = []
_IDLE_POLL_SEC = 2.0  # 空转兜底轮询间隔(错过唤醒也能捡起 pending)


def notify_new_job() -> None:
    _wake.set()


def reap_stale_ai_jobs_on_startup(db: Session) -> int:
    """启动收口:把重启打断残留的 running job 落 failed(防前端永久「生成中」)。返回条数。"""
    res = db.execute(
        update(AiJob).where(AiJob.status == "running")
        .values(status="failed", error="服务重启中断,请重试")
    )
    db.commit()
    return res.rowcount or 0


def _drain_once(session_factory) -> bool:
    """抢占并执行一条 pending(有则跑、返回 True;空队列返回 False)。worker 循环与测试共用。"""
    s = session_factory()
    try:
        job = claim_next(s)
    finally:
        s.close()
    if job is None:
        return False
    run_job(session_factory, job.id)
    return True


def _worker_loop(session_factory) -> None:
    """worker 线程主体:连续抢占执行,空转时等唤醒(带兜底超时),收到停止即退出。"""
    while not _stop.is_set():
        try:
            worked = _drain_once(session_factory)
        except Exception:  # noqa: BLE001
            logger.exception("AI worker 循环异常(继续)")
            worked = False
        if worked:
            continue  # 还有活尽快接着抢
        _wake.wait(timeout=_IDLE_POLL_SEC)
        _wake.clear()


def start_pool(size: int | None = None, factory=None) -> None:
    """启动 worker 线程池(daemon)。size 缺省取 AI_WORKER_CONCURRENCY;factory 供测试注入。"""
    from app.core.config import settings
    from app.db.session import SessionLocal

    _ensure_handlers()
    sf = factory or SessionLocal
    n = size if size is not None else max(1, getattr(settings, "AI_WORKER_CONCURRENCY", 2))
    _stop.clear()
    for i in range(n):
        t = threading.Thread(target=_worker_loop, args=(sf,), name=f"ai-worker-{i}", daemon=True)
        t.start()
        _threads.append(t)
    logger.info("AI worker 池已启动:%d 线程", n)


def stop_pool(timeout: float = 2.0) -> None:
    """停止 worker 池:置停止标志 + 唤醒,等线程退出(尽力而为)。"""
    _stop.set()
    _wake.set()
    for t in _threads:
        t.join(timeout=timeout)
    _threads.clear()
