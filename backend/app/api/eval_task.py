"""对话测评任务路由：CRUD + 执行 + AI 综合评价(SSE 流式)。

测评任务(EvalTask)是一组定制用例的集合，可整体执行、整体判定、最后产出 AI 综合评价 HTML。
与普通下发的区别：run 上会回填 eval_task_id + batch，结果页可按任务维度聚合。
"""
import json
import logging
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import EvalRunStatus, EvalTaskStatus, ProjectRole
from app.db.session import SessionLocal, get_db
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


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


# ─── 序列化 ────────────────────────────────────────────────────────────────────

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
        "schedule_enabled": bool(task.schedule_enabled),
        "schedule_cron": task.schedule_cron,
        "schedule_runner": task.schedule_runner,
        "last_auto_run_at": task.last_auto_run_at.isoformat() if task.last_auto_run_at else None,
        "run_count": run_count,
        "done_count": done_count,
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
    runner: str = Field(..., max_length=64)
    target_engine: str = Field("namiwork", max_length=32)
    target_device: str | None = Field(None, max_length=64)
    # 本次执行统一指定的对话选项 {model?,chatMode?,thinkingDepth?}；None/空=客户端默认
    dialog_options: dict | None = None
    # A/B 对比执行:非空时每道题下发两条 run(A=dialog_options,B=本组),结果页配对出胜率。
    # 对齐主流测评平台的双配置对战(如 LMArena 双模型盲比),用于回答「哪套配置/模型更强」。
    dialog_options_b: dict | None = None


def dispatch_task_runs(db: Session, task: EvalTask, runner: str, target_engine: str,
                       target_device: str | None, opts: dict, opts_b: dict | None,
                       user_id: int | None) -> tuple[list[int], str]:
    """下发任务内全部用例(手动执行端点与定时 job 共用;opts_b is not None 即 A/B 对比)。

    校验失败抛 ValueError(端点转 400,定时 job 记日志跳过);调用方负责 commit。
    """
    from app.api.eval_queue import _new_batch_id, _payload_of
    from app.core.enums import EvalDeviceKind

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
    for qid in qids:
        q = found[qid]
        for tag, vopts in variants:
            payload = _payload_of(q, vopts)
            if tag:
                payload["compare_group"] = tag
                if payload.get("conversation_group"):
                    payload["conversation_group"] = f"{payload['conversation_group']}#{tag}"
            row = EvalRun(
                eval_query_id=q.id, project_id=q.project_id, batch_id=batch_id,
                eval_task_id=task.id,
                runner=runner, target_engine=target_engine,
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
    return created, batch_id


@router.post("/{task_id}/run")
def run_task(task_id: int, body: EvalTaskRunIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """把任务内全部用例入 eval_run 队列(盖 eval_task_id + 新批次),复用 eval-queue 的下发口径。"""
    from app.api.eval_queue import _clean_dialog_options

    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)
    opts = _clean_dialog_options(body.dialog_options)
    opts_b = _clean_dialog_options(body.dialog_options_b) if body.dialog_options_b is not None else None
    try:
        created, batch_id = dispatch_task_runs(
            db, task, body.runner, body.target_engine, body.target_device, opts, opts_b, user.id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    # 记录本次执行的对话选项(列表展示+下次执行回填);对比模式把 B 组挂在 compareB 键下。
    # 没指定则清空=默认,始终反映最近一次执行
    stored = dict(opts)
    if body.dialog_options_b is not None:
        stored["compareB"] = opts_b
    task.dialog_options = json.dumps(stored, ensure_ascii=False) if stored else None
    db.commit()
    return ok({"run_ids": created, "batch_id": batch_id})


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
    return ok(_run_out(r))


@router.post("/{task_id}/runs/{run_id}/retry")
def retry_run(task_id: int, run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """单条重跑失败用例:failed run 原地复位回 pending,执行机重新拉走。

    原地复位而非新建行:同批次内不产生同题重复行(统计/胜率配对/趋势口径都干净)。
    复位清空全部回填与判定字段(payload 快照保留——仍按下发那一刻的配置重跑)。
    仅 failed 可重跑;判定出错用「重判」,执行中的用「标记失败」先收口。
    """
    from app.api.eval_queue import _to_out as _run_out

    task = db.get(EvalTask, task_id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)
    r = db.get(EvalRun, run_id)
    if not r or r.eval_task_id != task.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在或不属于该任务")
    if getattr(r.status, "value", r.status) != "failed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="仅执行失败(failed)的可重跑")
    r.status = EvalRunStatus.pending
    r.reason = None
    r.session_id = None; r.share_link = None; r.artifact_share_link = None
    r.answer = None; r.trace = None
    r.reported_duration = None; r.bean_cost = None; r.tokens = None; r.duration_ms = None
    r.verdict = None; r.score = None; r.verdict_dims = None; r.verdict_reason = None
    r.judged_by = None; r.is_abnormal = False
    r.review_mark = None; r.review_note = None
    # 任务若已收口(done)则拉回 running,详情页状态与实际一致
    if task.status == EvalTaskStatus.done:
        task.status = EvalTaskStatus.running
    db.commit(); db.refresh(r)
    return ok(_run_out(r))


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
    # 未执行完/未回填的 run(pending/running)不进评价素材:执行机中断未回写时它们没有任何可评内容,
    # 混入只会让报告把「没跑」当「跑差」。全被排除则明示,不给引擎喂空素材。
    runs = [r for r in runs if getattr(r.status, "value", r.status) not in ("pending", "running")]
    if not runs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="该批次的执行尚未回填(全部 pending/running),等执行完成或重跑后再生成")

    # 组装素材(在请求 db 存活期内取齐)
    qmap = {}
    qids = [r.eval_query_id for r in runs if r.eval_query_id]
    if qids:
        for q in db.query(EvalQuery).filter(EvalQuery.id.in_(qids)).all():
            qmap[q.id] = q
    items = []
    for r in runs:
        payload = json.loads(r.payload) if r.payload else {}
        q = qmap.get(r.eval_query_id)
        cg = payload.get("compare_group")  # A/B 对比批次:标题拼组名,综合评价可按组对比总结
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
    prompt = claude_runner.build_eval_task_summary_prompt(task.name, task.description or "", items)
    task_pk = task.id
    # ⚠️ 必须在 commit 前取成局部变量:commit 会 expire ORM 属性,而 sse 生成器在请求 db 关闭后才迭代,
    # 届时再访问 task.name 会抛 DetachedInstanceError(fastapi≥0.106 yield 依赖在流开始前收尾)。
    task_name = task.name

    # 标记生成中(前端轮询/重进页面可见)
    task.summary_status = "running"
    db.commit()

    def sse():
        raw = ""
        err = None
        t0 = time.monotonic()
        try:
            for evt in engine.stream_generate(
                task_name,
                prompt_builder=lambda _p=prompt: _p,
                system_prompt=claude_runner.EVAL_TASK_SUMMARY_SYSTEM_PROMPT,
            ):
                etype = evt.get("type")
                if etype == "heartbeat":
                    yield ": hb\n\n"
                elif etype == "delta":
                    raw += evt["text"]
                    yield _sse({"type": "delta", "text": evt["text"]})
                elif etype == "result":
                    if evt.get("text"):
                        raw = evt["text"]
                elif etype == "error":
                    err = evt.get("msg")
                    yield _sse({"type": "error", "msg": err})
        except Exception as e:  # noqa: BLE001
            logger.exception("测评任务综合评价生成异常")
            err = err or f"生成中断:{e}"

        s = SessionLocal()
        try:
            t2 = s.get(EvalTask, task_pk)
            if t2 is None:
                yield _sse({"type": "error", "msg": "任务记录丢失"})
                return
            html = claude_runner.extract_html_fragment(raw)
            if err or not html:
                t2.summary_status = "failed"
                s.commit()
                yield _sse({"type": "done", "status": "failed",
                            "msg": err or "引擎没有产出有效 HTML 评价", "summary_html": None})
                return
            from datetime import datetime
            t2.summary_html = _sanitize_html(html)
            t2.summary_status = "done"
            t2.summary_provider = provider_id
            t2.summary_at = datetime.now()
            s.commit()
            yield _sse({"type": "done", "status": "done",
                        "summary_html": t2.summary_html,
                        "duration_ms": int((time.monotonic() - t0) * 1000)})
        except Exception as e:  # noqa: BLE001
            logger.exception("综合评价落库失败")
            s.rollback()
            yield _sse({"type": "error", "msg": f"落库失败:{e}"})
        finally:
            s.close()

    return StreamingResponse(sse(), media_type="text/event-stream")
