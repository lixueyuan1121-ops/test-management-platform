"""对话测评任务路由：CRUD + 执行 + AI 综合评价(SSE 流式)。

测评任务(EvalTask)是一组定制用例的集合，可整体执行、整体判定、最后产出 AI 综合评价 HTML。
与普通下发的区别：run 上会回填 eval_task_id + batch，结果页可按任务维度聚合。
"""
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import EvalRunStatus, EvalTaskStatus, ProjectRole
from app.db.session import get_db
from app.models import EvalQuery, EvalRun, User
from app.models.ai_eval import EvalTask
from app.schemas.common import ok
from app.services import generators, claude_runner

logger = logging.getLogger("test_platform")
router = APIRouter(prefix="/api/eval-tasks", tags=["eval-task"])
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)

# HTML 消毒:只允许安全标签/属性,防 XSS。
_ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "p", "br", "b", "strong", "i", "em", "u", "s",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
    "blockquote", "pre", "code", "span", "div", "section",
    "a",  # 仅 href 属性
}
_TAG_RE = re.compile(r"<(/?)(\w+)([^>]*)>", re.S)
_ATTR_SAFE = re.compile(r'\s+(href)="([^"]*)"', re.I)
_ATTR_SCRUB = re.compile(r"\s+\w[\w\-]*\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)", re.I)


def _sanitize_html(html: str) -> str:
    """去掉不在白名单里的标签,并删除 <a> 以外标签的所有属性、<a> 只保留 href(且必须 http/https)。"""
    def _repl(m):
        tag = m.group(2).lower()
        if tag not in _ALLOWED_TAGS:
            return ""
        attrs_raw = m.group(3)
        if tag == "a":
            href_m = _ATTR_SAFE.search(attrs_raw)
            href = href_m.group(2) if href_m else ""
            href = href.strip()
            if not re.match(r"^https?://", href, re.I):
                href = ""
            attrs = f' href="{href}"' if href else ""
        else:
            attrs = ""
        close = m.group(1)
        return f"<{close}{tag}{attrs}>"
    return _TAG_RE.sub(_repl, html or "")


# ─── 序列化 ────────────────────────────────────────────────────────────────────

def _parse_bean(raw) -> int:
    """算力豆变动字符串 → 整数(容错)。形如 "-12"/"+5"/"12"/None/""/"—" → -12/5/12/0/0/0。"""
    if not raw:
        return 0
    m = re.search(r"-?\d+", str(raw).replace("+", ""))
    return int(m.group()) if m else 0


def _batch_totals(db: Session, task: EvalTask) -> dict:
    """任务最近批次的耗时/算力豆聚合(列表页展示)。bean_cost 是字符串,Python 侧容错累加。"""
    if not task.last_batch_id:
        return {"total_duration_ms": 0, "total_bean_cost": 0}
    rows = (db.query(EvalRun.duration_ms, EvalRun.bean_cost)
            .filter(EvalRun.eval_task_id == task.id,
                    EvalRun.batch_id == task.last_batch_id).all())
    total_ms = sum((d or 0) for d, _ in rows)
    total_bean = sum(_parse_bean(b) for _, b in rows)
    return {"total_duration_ms": total_ms, "total_bean_cost": total_bean}


def _to_out(task: EvalTask, db: Session) -> dict:
    qids = json.loads(task.query_ids) if task.query_ids else []
    run_count = db.query(EvalRun).filter(
        EvalRun.eval_task_id == task.id,
        EvalRun.batch_id == task.last_batch_id,
    ).count() if task.last_batch_id else 0
    done_count = db.query(EvalRun).filter(
        EvalRun.eval_task_id == task.id,
        EvalRun.batch_id == task.last_batch_id,
        EvalRun.status.in_([EvalRunStatus.done.value, EvalRunStatus.judged.value]),
    ).count() if task.last_batch_id else 0
    return {
        "id": task.id,
        "project_id": task.project_id,
        "name": task.name,
        "description": task.description,
        "query_ids": qids,
        "dialog_options": json.loads(task.dialog_options) if task.dialog_options else None,
        "status": getattr(task.status, "value", task.status),
        "last_batch_id": task.last_batch_id,
        "summary_html": task.summary_html,
        "summary_status": task.summary_status,
        "summary_provider": task.summary_provider,
        "summary_at": task.summary_at.isoformat() if task.summary_at else None,
        "summary_share_code": task.summary_share_code,
        "schedule_enabled": bool(task.schedule_enabled),
        "schedule_cron": task.schedule_cron,
        "schedule_runner": task.schedule_runner,
        "last_auto_run_at": task.last_auto_run_at.isoformat() if task.last_auto_run_at else None,
        "run_count": run_count,
        "done_count": done_count,
        **_batch_totals(db, task),
        "auto_pipeline": bool(task.auto_pipeline),
        "pipeline_status": task.pipeline_status,
        "pipeline_at": task.pipeline_at.isoformat() if task.pipeline_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


# ─── CRUD ──────────────────────────────────────────────────────────────────────

class EvalTaskCreateIn(BaseModel):
    project_id: int
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    query_ids: list[int] = Field(default_factory=list)


class EvalTaskUpdateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = None
    query_ids: list[int] | None = None


@router.post("")
def create_task(body: EvalTaskCreateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    task = EvalTask(
        project_id=body.project_id,
        name=body.name,
        description=body.description,
        query_ids=json.dumps(list(dict.fromkeys(body.query_ids)), ensure_ascii=False),
        status=EvalTaskStatus.draft,
        created_by=user.id,
    )
    db.add(task); db.commit(); db.refresh(task)
    return ok(_to_out(task, db))


@router.get("")
def list_tasks(project_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = db.query(EvalTask).filter(EvalTask.project_id == project_id).order_by(EvalTask.id.desc()).all()
    return ok([_to_out(r, db) for r in rows])


@router.get("/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    return ok(_to_out(task, db))


@router.patch("/{task_id}")
def update_task(task_id: int, body: EvalTaskUpdateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)
    if body.name is not None:
        task.name = body.name
    if body.description is not None:
        task.description = body.description
    if body.query_ids is not None:
        task.query_ids = json.dumps(list(dict.fromkeys(body.query_ids)), ensure_ascii=False)
    db.commit(); db.refresh(task)
    return ok(_to_out(task, db))


@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)
    db.delete(task); db.commit()
    return ok({"deleted": task_id})


# ─── 执行(下发到执行机) ────────────────────────────────────────────────────────

class EvalTaskRunIn(BaseModel):
    # runner 单台;或 runners 多台分片并行;或 "auto" 自动铺到所有在线执行机。三选一(见 _resolve_runners)。
    runner: str | None = Field(None, max_length=64)
    runners: list[str] | None = Field(None, max_length=32)
    target_engine: str = Field("namiwork", max_length=32)
    target_device: str | None = Field(None, max_length=64)
    # 本次执行统一指定的对话选项 {model?,chatMode?,thinkingDepth?}；None/空=客户端默认
    dialog_options: dict | None = None
    # A/B 对比执行:非空时每道题下发两条 run(A=dialog_options,B=本组),结果页配对出胜率。
    # 对齐主流测评平台的双配置对战(如 LMArena 双模型盲比),用于回答「哪套配置/模型更强」。
    dialog_options_b: dict | None = None
    # 一条龙开关:传入即写回任务级 auto_pipeline(执行对话框勾选,下次默认沿用);None=不改动。
    auto_pipeline: bool | None = None


AUTO_RUNNER = "auto"


def _resolve_runners(db: Session, runner: str | None, runners: list[str] | None) -> list[str]:
    """把下发请求的执行机意图归一成一个去重保序的 runner_id 列表。

    三种入参形态(优先级 runners > runner):
    - runners=[...]:显式多台分片并行(过滤空串、去重保序);
    - runner="auto":自动取当前所有在线测评执行机铺开(无在线设备 → ValueError);
    - runner="mac-01":单台(兼容旧客户端与定时 job)。
    统一出口返回 list[str],下游按会话组轮转分片到这些设备。
    """
    if runners:
        out = list(dict.fromkeys(r.strip() for r in runners if r and r.strip()))
        if not out:
            raise ValueError("未指定有效的执行机")
        return out
    r = (runner or "").strip()
    if not r:
        raise ValueError("未指定执行机")
    if r == AUTO_RUNNER:
        from app.services.dispatcher import online_eval_runners
        picked = online_eval_runners(db)
        if not picked:
            raise ValueError("自动调度失败:当前没有在线的测评执行机")
        return picked
    return [r]


def dispatch_task_runs(db: Session, task: EvalTask, runner, target_engine: str,
                       target_device: str | None, opts: dict, opts_b: dict | None,
                       user_id: int | None) -> tuple[list[int], str]:
    """下发任务内全部用例(手动执行端点与定时 job 共用;opts_b is not None 即 A/B 对比)。

    runner 参数兼容三态:单个 runner_id 字符串(旧调用)、"auto"、或 runner_id 列表(多台分片)。
    多台时按「会话组」轮转分片——单轮 query 自成一组、多轮同 conversation_group 整组、A/B 各 variant
    独立成组(带 #A/#B 后缀),同组必落同一台设备(否则 runner 按组名连发的多轮上下文会断裂)。
    分片只决定 run.runner 落哪台,批次(batch_id)仍是一个,结果页按批聚合口径不变。

    校验失败抛 ValueError(端点转 400,定时 job 记日志跳过);调用方负责 commit。
    """
    from app.api.eval_queue import _new_batch_id, _payload_of
    from app.core.enums import EvalDeviceKind

    # 归一执行机列表:允许传入已解析好的 list,或单字符串/"auto"(转 _resolve_runners)。
    runner_list = runner if isinstance(runner, list) else _resolve_runners(db, runner, None)
    if not runner_list:
        raise ValueError("未指定执行机")

    qids = json.loads(task.query_ids) if task.query_ids else []
    if not qids:
        raise ValueError("任务内还没有用例,先添加用例再执行")
    qs = db.query(EvalQuery).filter(EvalQuery.id.in_(qids)).all()
    found = {q.id: q for q in qs}
    missing = [qid for qid in qids if qid not in found]
    if missing:
        raise ValueError(f"用例 {missing} 已不存在,请编辑任务移除")

    batch_id = _new_batch_id()
    created = []
    # 对比模式(opts_b 非 None 即启用,B 三项全空=B 用客户端默认也合法):每题下发 A/B 两条 run。
    # payload 标 compare_group;多轮 conversation_group 加 #A/#B 后缀——runner 按组名把多轮连发进
    # 同一对话,不区分则 A/B 两套会混进一个会话串上下文。
    variants = [("A", opts), ("B", opts_b)] if opts_b is not None else [(None, opts)]

    # 会话组 → 分配到的 runner。轮转分片:同一「最终会话组」的所有轮落同一台(多轮上下文不断);
    # 组间按出现顺序轮流分到 runner_list,负载(会话组数)天然均衡且确定(可复现)。
    group_runner: dict[str, str] = {}
    next_idx = 0

    def _assign(group_key: str) -> str:
        nonlocal next_idx
        if group_key not in group_runner:
            group_runner[group_key] = runner_list[next_idx % len(runner_list)]
            next_idx += 1
        return group_runner[group_key]

    for qid in qids:
        q = found[qid]
        for tag, vopts in variants:
            payload = _payload_of(q, vopts)
            if tag:
                payload["compare_group"] = tag
                if payload.get("conversation_group"):
                    payload["conversation_group"] = f"{payload['conversation_group']}#{tag}"
            # 分片键=最终会话组:多轮用(带 #A/#B 的)conversation_group 整组同机;
            # 单轮无组则用 qid+tag 各自独立成组(可分散到不同设备)。
            group_key = payload.get("conversation_group") or f"q{qid}#{tag or ''}"
            assigned = _assign(group_key)
            row = EvalRun(
                eval_query_id=q.id, project_id=q.project_id, batch_id=batch_id,
                eval_task_id=task.id,
                runner=assigned, target_engine=target_engine,
                target_device=target_device,
                device_kind=EvalDeviceKind.desktop,
                status=EvalRunStatus.pending,
                payload=json.dumps(payload, ensure_ascii=False),
                enqueued_by=user_id,
            )
            db.add(row); db.flush(); created.append(row.id)
    task.last_batch_id = batch_id
    task.status = EvalTaskStatus.running
    # 换批执行后旧综合评价作废(针对旧批次)
    task.summary_status = None
    # 换批重置一条龙门闩(NULL=可抢占):保证新批次能触发一次编排,旧批次的编排状态不残留。
    task.pipeline_status = None
    return created, batch_id


@router.post("/{task_id}/run")
def run_task(task_id: int, body: EvalTaskRunIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """把任务内全部用例入 eval_run 队列(盖 eval_task_id + 新批次),复用 eval-queue 的下发口径。

    执行机可单台(runner)、多台分片并行(runners=[...])或自动铺开(runner="auto")——
    多台时按会话组轮转分片(见 dispatch_task_runs),总执行量不变、多机并行加速。
    """
    from app.api.eval_queue import _clean_dialog_options

    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)
    opts = _clean_dialog_options(body.dialog_options)
    opts_b = _clean_dialog_options(body.dialog_options_b) if body.dialog_options_b is not None else None
    try:
        runner_list = _resolve_runners(db, body.runner, body.runners)
        created, batch_id = dispatch_task_runs(
            db, task, runner_list, body.target_engine, body.target_device, opts, opts_b, user.id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    # 记录本次执行的对话选项(列表展示+下次执行回填);对比模式把 B 组挂在 compareB 键下。
    # 没指定则清空=默认,始终反映最近一次执行
    stored = dict(opts)
    if body.dialog_options_b is not None:
        stored["compareB"] = opts_b
    task.dialog_options = json.dumps(stored, ensure_ascii=False) if stored else None
    # 执行对话框勾选的一条龙开关写回任务级(下次默认沿用);None=不改动既有设置。
    if body.auto_pipeline is not None:
        task.auto_pipeline = body.auto_pipeline
    db.commit()
    # 返回实际分片用到的执行机(前端可提示"已分发到 N 台")
    return ok({"run_ids": created, "batch_id": batch_id, "runners": runner_list})


class EvalTaskScheduleIn(BaseModel):
    """定时执行配置(CI 回归守卫):enabled 时 cron+runner 必填。"""
    enabled: bool
    cron: str | None = Field(None, max_length=64)
    runner: str | None = Field(None, max_length=64)


@router.patch("/{task_id}/schedule")
def set_schedule(task_id: int, body: EvalTaskScheduleIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """设置任务定时执行:到点自动下发整任务(沿用最近一次执行的对话选项,含 A/B 对比)。"""
    from app.services import scheduler as sched

    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)
    if body.enabled:
        if not (body.cron or "").strip() or not (body.runner or "").strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="开启定时需要同时填 cron 表达式与执行机")
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(body.cron.strip(), timezone="Asia/Shanghai")
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="cron 表达式无效(5 段,如「0 9 * * *」=每天 9 点)")
    task.schedule_enabled = body.enabled
    task.schedule_cron = (body.cron or "").strip() or None
    task.schedule_runner = (body.runner or "").strip() or None
    db.commit(); db.refresh(task)
    try:
        sched.sync_eval_task_job(task.id, task.schedule_cron, task.schedule_enabled)
    except Exception:
        logger.exception("同步测评任务定时 job 失败 task=%s", task.id)
    return ok(_to_out(task, db))


@router.post("/{task_id}/runs/{run_id}/mark-failed")
def mark_run_failed(task_id: int, run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """把卡在 pending/running 的未回填 run 手动标记为失败。

    执行机中断/回写失败时 run 会永远停在 running:任务收不了口、批量判定跳过它、
    综合评价也排除它——除重跑外没有任何收口手段。此端点给人工一个明确出口;
    已回填(done/judging/judged/failed)的不允许改,防误伤真实结果。
    """
    from app.api.eval_queue import _to_out as _run_out

    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)
    r = db.get(EvalRun, run_id)
    if not r or r.eval_task_id != task.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在或不属于该任务")
    st = getattr(r.status, "value", r.status)
    if st not in ("pending", "running"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"仅 pending/running 可标记失败(当前 {st})")
    r.status = EvalRunStatus.failed
    r.reason = "手动标记失败(会话未回填/执行中断)"
    db.commit(); db.refresh(r)
    # 手动收口最后一条 pending/running 后,本批可能达终态 → 触发一条龙(幂等门闩)
    if r.eval_task_id and r.batch_id:
        try:
            from app.services.eval_pipeline import on_batch_maybe_done
            on_batch_maybe_done(db, r.batch_id)
        except Exception:
            pass
    return ok(_run_out(r))


@router.post("/{task_id}/runs/{run_id}/retry")
def retry_run(task_id: int, run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """单条重跑失败用例:failed run 原地复位回 pending,执行机重新拉走。

    原地复位而非新建行:同批次内不产生同题重复行(统计/胜率配对/趋势口径都干净)。
    复位清空全部回填与判定字段(payload 快照保留——仍按下发那一刻的配置重跑)。
    仅 failed 可重跑;判定出错用「重判」,执行中的用「标记失败」先收口。
    """
    from app.api.eval_queue import _to_out as _run_out, reset_run_for_retry

    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)
    r = db.get(EvalRun, run_id)
    if not r or r.eval_task_id != task.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在或不属于该任务")
    if getattr(r.status, "value", r.status) != "failed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="仅执行失败(failed)的可重跑")
    reset_run_for_retry(r)
    # 任务若已收口(done)则拉回 running,详情页状态与实际一致
    if task.status == EvalTaskStatus.done:
        task.status = EvalTaskStatus.running
    db.commit(); db.refresh(r)
    return ok(_run_out(r))


@router.post("/{task_id}/stop")
def stop_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """停止测评任务:当前批次(last_batch_id)所有未完成(pending/running)的 run 收口为 cancelled,
    任务置 stopped;若开了定时执行则一并关闭(移除调度 job,保留 cron 便于以后重开)。

    - pending 改后执行机 list_pending 不再拉取 → 「未执行的不再继续」天然成立。
    - running 平台无法远程终止那次对话,标记后其回写会被 eval_queue.report 以 409 拒绝(结果作废、
      不进判定/综合评价)。已终态(done/judging/judged/failed)的 run 不动,保留既有结果。
    """
    from app.services.scheduler import sync_eval_task_job

    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)
    cancelled = 0
    if task.last_batch_id:
        rows = (db.query(EvalRun)
                .filter(EvalRun.eval_task_id == task.id,
                        EvalRun.batch_id == task.last_batch_id,
                        EvalRun.status.in_([EvalRunStatus.pending.value, EvalRunStatus.running.value]))
                .all())
        for r in rows:
            r.status = EvalRunStatus.cancelled
            r.reason = "用户停止测评任务"
        cancelled = len(rows)
    task.status = EvalTaskStatus.stopped
    # 一并关定时:否则到点又自动下发新批次,与「不再继续」矛盾(保留 cron,仅关开关+移除 job)
    if task.schedule_enabled:
        task.schedule_enabled = False
        sync_eval_task_job(task.id, task.schedule_cron, False)
    db.commit(); db.refresh(task)
    return ok({"cancelled_count": cancelled, "task": _to_out(task, db)})


@router.get("/{task_id}/runs")
def task_runs(task_id: int, batch_id: str | None = Query(None),
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """任务的执行结果(默认最近批次)。复用 eval_queue._to_out 序列化口径。"""
    from app.api.eval_queue import _to_out as _run_out

    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    bid = batch_id or task.last_batch_id
    q = db.query(EvalRun).filter(EvalRun.eval_task_id == task.id)
    if bid:
        q = q.filter(EvalRun.batch_id == bid)
    rows = q.order_by(EvalRun.id).all()
    # 执行完批次自动收口任务状态(轻量:读接口顺带校正,不引入后台轮询)
    if task.status == EvalTaskStatus.running and rows and all(
        getattr(r.status, "value", r.status) in ("done", "judged", "failed") for r in rows
    ):
        task.status = EvalTaskStatus.done
        db.commit()
    dim_map = {}
    qids = [r.eval_query_id for r in rows if r.eval_query_id]
    if qids:
        for q_id, dim in db.query(EvalQuery.id, EvalQuery.dimension).filter(EvalQuery.id.in_(qids)).all():
            dim_map[q_id] = dim
    out = []
    for r in rows:
        d = _run_out(r)
        d["dimension"] = (d.get("payload") or {}).get("dimension") or dim_map.get(r.eval_query_id)
        out.append(d)
    return ok({"task": _to_out(task, db), "runs": out})


# ─── AI 综合评价(整理评价,HTML) ────────────────────────────────────────────────

class EvalTaskSummarizeIn(BaseModel):
    provider: str | None = None
    batch_id: str | None = None  # 缺省用 last_batch_id


def _summary_items(db: Session, runs: list) -> list[dict]:
    """把一批 run 组装成综合评价素材(SSE 端点与无头一条龙共用,单一实现避免漂移)。

    A/B 对比批次标题拼 [X组];维度/提问/期望优先取 payload 快照,回落 eval_query。
    调用方负责先滤掉 pending/running/cancelled(无可评内容/已作废)。
    """
    qmap = {}
    qids = [r.eval_query_id for r in runs if r.eval_query_id]
    if qids:
        for q in db.query(EvalQuery).filter(EvalQuery.id.in_(qids)).all():
            qmap[q.id] = q
    items = []
    for r in runs:
        payload = json.loads(r.payload) if r.payload else {}
        q = qmap.get(r.eval_query_id)
        cg = payload.get("compare_group")
        items.append({
            "title": (f"[{cg}组] " if cg else "") + (payload.get("title") or (q.title if q else f"run#{r.id}")),
            "dimension": payload.get("dimension") or (q.dimension if q else None),
            "prompt": payload.get("prompt") or (q.prompt if q else ""),
            "expected": (q.expected if q else "") or "",
            "status": getattr(r.status, "value", r.status),
            "verdict": r.verdict,
            "score": r.score,
            "verdict_reason": r.verdict_reason or "",
            "answer": r.answer or "",
            "reason": r.reason or "",
        })
    return items


def _set_summary_status(session_factory, task_id: int, status: str) -> None:
    """用【全新 session】把某任务的 summary_status 落终态(running/failed/done)。吞一切异常。

    关键:无头综合评价要跑一次长达 AI_TIMEOUT_SECONDS(默认 15min)的 LLM 流,期间若一直
    持有同一条 DB 连接,长跑结束时该连接可能已被 MySQL wait_timeout / 中间层空闲断掉——
    此时在【同一条已死连接】上写终态会失败,summary_status 永久卡 running,前端"一直生成中"。
    每次都开一条全新 session(pool_pre_ping 会在取连接时探活)来写终态,即根治此卡死。
    与手动 SSE 端点在流结束后另开 SessionLocal 落库是同一套路。
    """
    from app.db.session import SessionLocal
    sf = session_factory or SessionLocal
    try:
        s = sf()
    except Exception:  # noqa: BLE001
        logger.exception("综合评价状态落库开 session 失败 task=%s status=%s", task_id, status)
        return
    try:
        t = s.get(EvalTask, task_id)
        if t is not None:
            t.summary_status = status
            s.commit()
    except Exception:  # noqa: BLE001
        logger.exception("综合评价状态落库失败 task=%s status=%s", task_id, status)
        try:
            s.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        s.close()


def generate_task_summary_headless(db: Session, task: EvalTask, batch_id: str,
                                   provider: str | None = None, session_factory=None) -> dict:
    """无头生成综合评价(供一条龙后台编排调用,无 SSE、无前端连接)。

    与 summarize_task 端点同一套素材/prompt/消毒/落库逻辑,只是不流式:累积全文后落
    task.summary_html。返回 {ok, share_code?} / {skipped, reason} / {error}。

    ⚠️ 卡死根治:running/failed/done 三个状态写库都走【全新 session】(_set_summary_status /
    终态段另开 SessionLocal),不复用传入的 db——因为本函数会跑一次最长 15min 的 LLM 流,
    传入的 db 连接极可能在长跑后失效,在它上面写终态会失败→永久卡 running。db 仅用于流开始前
    (连接尚活)读取 runs/items。session_factory 供测试注入(缺省=真实 SessionLocal)。
    """
    from datetime import datetime
    from app.db.session import SessionLocal
    sf = session_factory or SessionLocal
    task_id = task.id

    provider_id = generators.normalize_provider(provider)
    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        return {"skipped": True, "reason": f"评价引擎「{provider_id}」不可用"}
    runs = (db.query(EvalRun)
            .filter(EvalRun.eval_task_id == task.id, EvalRun.batch_id == batch_id)
            .order_by(EvalRun.id).all())
    runs = [r for r in runs if getattr(r.status, "value", r.status) not in ("pending", "running", "cancelled")]
    if not runs:
        return {"skipped": True, "reason": "该批次无可评的执行记录"}
    items = _summary_items(db, runs)
    # 流开始前把要用到的字段取成局部量:后续不再碰传入的 db(其连接会跨 15min 长跑,不可靠),
    # 且释放 db 的读事务——否则单连接(测试 StaticPool)下它与写终态的全新 session 会争同一连接。
    task_name = task.name
    task_desc = task.description or ""
    prompt = claude_runner.build_eval_task_summary_prompt(task_name, task_desc, items)
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass
    logger.info("无头综合评价开始 task=%s batch=%s provider=%s runs=%d prompt_len=%d",
                task_id, batch_id, provider_id, len(runs), len(prompt))
    # 标记生成中(全新 session,不占用长跑连接)
    _set_summary_status(sf, task_id, "running")
    raw = ""
    try:
        for evt in engine.stream_generate(
            task_name,
            prompt_builder=lambda _p=prompt: _p,
            system_prompt=claude_runner.EVAL_TASK_SUMMARY_SYSTEM_PROMPT,
        ):
            etype = evt.get("type")
            if etype == "delta":
                raw += evt["text"]
            elif etype == "result" and evt.get("text"):
                raw = evt["text"]
            elif etype == "error":
                _set_summary_status(sf, task_id, "failed")
                return {"error": evt.get("msg") or "引擎报错"}
        html = claude_runner.extract_html_fragment(raw)
        if not html:
            _set_summary_status(sf, task_id, "failed")
            return {"error": "引擎没有产出有效 HTML 评价"}
        # 终态成功:全新 session 落 summary_html/done/provider/at + 分配短链码。
        from app.api.eval_report import ensure_share_code
        s2 = sf()
        try:
            t2 = s2.get(EvalTask, task_id)
            if t2 is None:
                return {"error": "任务记录丢失"}
            t2.summary_html = _sanitize_html(html)
            t2.summary_status = "done"
            t2.summary_provider = provider_id
            t2.summary_at = datetime.now()
            share_code = ensure_share_code(s2, t2)
            s2.commit()
            html_len = len(t2.summary_html or "")
        finally:
            s2.close()
        logger.info("无头综合评价完成 task=%s batch=%s html_len=%d", task_id, batch_id, html_len)
        return {"ok": True, "share_code": share_code}
    except Exception as e:  # noqa: BLE001
        logger.exception("无头综合评价生成失败 task=%s batch=%s", task_id, batch_id)
        _set_summary_status(sf, task_id, "failed")
        return {"error": f"生成中断:{e}"}


@router.post("/{task_id}/summarize")
def summarize_task(task_id: int, body: EvalTaskSummarizeIn,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """SSE 流式生成任务综合评价:汇总该批次逐条结果喂给引擎,产出 HTML 片段落库。

    事件:delta / error / done(含 summary_html)。与 ai_eval.gen_eval_queries 同款 SSE 骨架:
    生成器在 get_db 关闭后才迭代,故先在函数体内取齐数据,生成器内另开 SessionLocal 落库。
    """
    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)
    provider_id = generators.normalize_provider(body.provider)
    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"评价引擎「{provider_id}」未启用或不可用")
    bid = body.batch_id or task.last_batch_id
    if not bid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="任务还没有执行批次,先执行再生成综合评价")
    runs = (db.query(EvalRun)
            .filter(EvalRun.eval_task_id == task.id, EvalRun.batch_id == bid)
            .order_by(EvalRun.id).all())
    if not runs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="该批次没有执行记录")
    # 未执行完/未回填的 run(pending/running)、已取消的(cancelled)都不进评价素材:前者没有可评内容,
    # 后者是用户主动停止的、结果作废。混入只会让报告把「没跑/已取消」当「跑差」。全被排除则明示。
    runs = [r for r in runs if getattr(r.status, "value", r.status) not in ("pending", "running", "cancelled")]
    if not runs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="该批次没有可评的执行记录(全部未回填或已取消),等执行完成或重跑后再生成")

    # 方案2 P3b:改入队。校验通过即建 eval_summary job,返回 {job_id};worker 调无头生成器
    # (generate_task_summary_headless,自管短命 session 抗长跑连接失效),前端轮询 /api/ai-jobs/{id}。
    from app.services import ai_jobs
    job = ai_jobs.enqueue(
        db, "eval_summary", provider=provider_id, project_id=task.project_id, user_id=user.id,
        input={"task_id": task.id, "batch_id": bid, "provider": provider_id},
        ref_kind="eval_task", ref_id=task.id,
    )
    return ok({"job_id": job.id})


def run_eval_summary_job(db: Session, job) -> dict:
    """AI 任务队列的综合评价 handler(方案2 P3b):复用 generate_task_summary_headless。

    job.input = {task_id, batch_id, provider}。headless 自管短命 session(抗 15min 长跑后连接失效),
    故传一个基于当前引擎的 sessionmaker 作 session_factory。error → 抛错(job failed);
    ok/skipped 原样返回(前端轮询到 done 后重拉任务看 summary_html)。
    """
    from sqlalchemy.orm import sessionmaker
    inp = json.loads(job.input or "{}")
    task = db.get(EvalTask, inp["task_id"])
    if task is None:
        raise ValueError("测评任务不存在")
    sf = sessionmaker(bind=db.get_bind())
    res = generate_task_summary_headless(db, task, inp["batch_id"], provider=inp.get("provider"),
                                         session_factory=sf)
    if res.get("error"):
        raise ValueError(res["error"])
    return res


from app.services import ai_jobs as _ai_jobs_reg  # noqa: E402
_ai_jobs_reg.register_handler("eval_summary", run_eval_summary_job)
