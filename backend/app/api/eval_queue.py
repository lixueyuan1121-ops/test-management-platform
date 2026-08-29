"""对话测评执行队列:下发 eval_query → 执行器拉取/认领/回写 eval_run → trace 上传。

独立于 exec_queue(功能测试点执行)。沿用 {code,msg,data} 信封、手写 _to_out、
require_runner_ctx 双通道鉴权(设备 token 锁 runner_id / 共享 token 兜底)。
trace(会话轨迹)大、走独立 multipart 端点存磁盘,eval_run.trace 存 URL(避 MySQL5.6 TEXT 截断)。
"""
import json
import os
import secrets
import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import EvalDeviceKind, EvalRunStatus, ProjectRole
from app.db.session import get_db
from app.models import EvalQuery, EvalRun, User
from app.schemas.common import ok
from app.schemas.eval_queue import EvalEnqueueIn, EvalReportIn, EvalRetryFailedIn

router = APIRouter(prefix="/api/eval-queue", tags=["eval-queue"])
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def _new_batch_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


_DIALOG_OPTION_KEYS = ("model", "chatMode", "thinkingDepth")


def _clean_dialog_options(raw: dict | None) -> dict:
    """清洗下发时指定的对话选项:只留三个已知键、值须为非空字符串(截 64 字符防超长)。

    chatMode/thinkingDepth 的值必须与被测客户端页面下拉选项文案一致(执行器按文本匹配点选,
    见 qalab-runner eval config: 智能模式/计划模式/目标模式;低/中/标准/高/超高),此处不枚举校验,
    客户端选项改文案时免于两头同步——点不中仅告警不阻断(见 dialog-runner._pickDropdownOption)。
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k in _DIALOG_OPTION_KEYS:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:64]
    return out


def _payload_of(q: EvalQuery, dialog_options: dict | None = None) -> dict:
    """下发 payload 快照:执行器驱动对话要用的字段(避免执行时配置漂移)。
    dimension 一并快照:执行器不用,但结果页/判定按下发那一刻的维度展示与聚焦。
    dialog_options:下发时用户指定的优先(整份替换),否则用题面存量(生成侧刻意留空,通常为 {})。"""
    return {
        "eval_query_id": q.id,
        "title": q.title,
        "prompt": q.prompt,
        "dimension": q.dimension,
        "attachments": json.loads(q.attachments) if q.attachments else [],
        "dialog_options": dialog_options if dialog_options
        else (json.loads(q.dialog_options) if q.dialog_options else {}),
        "conversation_group": q.conversation_group,
        "turn_index": q.turn_index,
    }


def _conv_group(r: EvalRun) -> str | None:
    """从 run 的 payload 快照里读多轮会话分组键;单轮(空/无)返回 None。"""
    try:
        p = json.loads(r.payload) if r.payload else {}
    except (ValueError, TypeError):
        return None
    g = p.get("conversation_group") if isinstance(p, dict) else None
    return g or None  # 空串归一为 None(单轮)


def _take_whole_groups(rows: list[EvalRun], limit: int) -> list[EvalRun]:
    """从按 id 升序的 pending rows 中取约 limit 条,但绝不切分多轮会话组。

    多轮会话(conversation_group 相同)的各轮必须整组下发给同一执行机、同一轮询批次:否则
    轮次0所在对话在上批结束(pool.close)时已关,轮次1接不上上下文。故一旦纳入某组的任一轮,
    就纳入该组全部轮(可超 limit);单轮 run 各自独立计数。达到 limit 后不再纳入下一组。
    """
    selected: list[EvalRun] = []
    seen_groups: set[str] = set()
    count = 0
    for r in rows:
        if count >= limit:
            break
        g = _conv_group(r)
        if not g:
            selected.append(r)
            count += 1
        elif g not in seen_groups:
            group_rows = [x for x in rows if _conv_group(x) == g]  # 该组全部轮(整组不拆)
            selected.extend(group_rows)
            seen_groups.add(g)
            count += len(group_rows)
    return selected


def _to_out(r: EvalRun) -> dict:
    return {
        "run_id": r.id,
        "eval_query_id": r.eval_query_id,
        "project_id": r.project_id,
        "batch_id": r.batch_id,
        "eval_task_id": r.eval_task_id,
        "runner": r.runner,
        "target_engine": r.target_engine,
        "target_device": r.target_device,
        "device_kind": getattr(r.device_kind, "value", r.device_kind),
        "status": getattr(r.status, "value", r.status),
        "payload": json.loads(r.payload) if r.payload else {},
        "session_id": r.session_id,
        "share_link": r.share_link,
        "artifact_share_link": r.artifact_share_link,
        "answer": r.answer,
        "trace": r.trace,
        "reported_duration": r.reported_duration,
        "bean_cost": r.bean_cost,
        "tokens": r.tokens,
        "reason": r.reason,
        "duration_ms": r.duration_ms,
        # 判定三维/总判定（历史页需在加载时即展示已判过的结果，故一并序列化；判定由 eval_judge 落库）
        "verdict": r.verdict,
        "score": r.score,
        "verdict_dims": json.loads(r.verdict_dims) if r.verdict_dims else None,
        "verdict_reason": r.verdict_reason,
        "judged_by": r.judged_by,
        "review_mark": r.review_mark,
        "review_note": r.review_note,
        "is_abnormal": bool(r.is_abnormal),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.post("/enqueue")
def enqueue(body: EvalEnqueueIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    ids = list(dict.fromkeys(body.eval_query_ids))
    qs = db.query(EvalQuery).filter(EvalQuery.id.in_(ids)).all()
    found = {q.id: q for q in qs}
    for qid in ids:
        q = found.get(qid)
        if q is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测评题 {qid} 不存在")
        if q.project_id != body.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测评题 {qid} 不属于该项目")
    created = []
    batch_id = _new_batch_id()
    opts = _clean_dialog_options(body.dialog_options)
    for qid in ids:
        q = found[qid]
        row = EvalRun(
            eval_query_id=q.id, project_id=q.project_id, batch_id=batch_id,
            runner=body.runner, target_engine=body.target_engine,
            target_device=body.target_device,
            device_kind=EvalDeviceKind.desktop,
            status=EvalRunStatus.pending, payload=json.dumps(_payload_of(q, opts), ensure_ascii=False),
            enqueued_by=user.id,
        )
        db.add(row); db.flush(); created.append(row.id)
    db.commit()
    return ok({"run_ids": created, "batch_id": batch_id})


@router.get("")
def list_pending(runner: str = Query("mac-01"), limit: int = Query(5, le=20),
                 db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
        ctx.device.last_seen_at = datetime.utcnow(); db.commit()
    # 取该 runner 全部 pending(按 id 升序),再在内存里「整组不拆」地取约 limit 条:
    # 多轮会话各轮必须同批下发(见 _take_whole_groups),故不能用 SQL .limit() 硬切(会拦腰截断某组)。
    rows = (db.query(EvalRun)
            .filter(EvalRun.status == EvalRunStatus.pending, EvalRun.runner == runner)
            .order_by(EvalRun.id).all())
    return ok([_to_out(r) for r in _take_whole_groups(rows, limit)])


@router.post("/{run_id}/claim")
def claim(run_id: int, runner: str = Query(...), db: Session = Depends(get_db),
          ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(EvalRun, run_id)
    if not r or r.status != EvalRunStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该执行项不可认领")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    r.status = EvalRunStatus.running; db.commit(); db.refresh(r)
    return ok(_to_out(r))


@router.patch("/{run_id}")
def report(run_id: int, body: EvalReportIn, runner: str = Query(...),
           db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    # 终态保护:任务被停止时该 run 已置 cancelled(终态)。执行机若把「停止前已在跑的那条」跑完再回写,
    # 必须拒绝,否则会把 cancelled 冲回 done/failed,让作废的结果混入判定/综合评价。
    if getattr(r.status, "value", r.status) == EvalRunStatus.cancelled.value:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该执行项已被停止(测评任务已停止),回写作废")
    r.status = EvalRunStatus.done if body.status == "done" else EvalRunStatus.failed
    r.share_link = body.share_link
    r.artifact_share_link = body.artifact_share_link
    r.answer = body.answer
    r.reported_duration = body.reported_duration
    r.bean_cost = body.bean_cost
    r.tokens = body.tokens
    r.session_id = body.session_id
    r.reason = body.reason
    r.duration_ms = body.duration_ms
    db.commit(); db.refresh(r)
    return ok(_to_out(r))


_UPLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads")
_TRACE_ROOT = os.path.join(_UPLOADS_DIR, "eval_traces")
_MAX_TRACE_BYTES = 20 * 1024 * 1024


@router.post("/{run_id}/trace")
async def upload_trace(run_id: int, file: UploadFile = File(...), runner: str = Query("mac-01"),
                       db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    """执行器上传会话轨迹 JSON。存 uploads/eval_traces/{run_id}-<hex>.json,回写 eval_run.trace=URL。

    文件名加随机后缀防枚举(/uploads 无鉴权静态挂载)。同 run 重传(重跑)前先删旧 trace 避堆积。
    """
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    data = await file.read()
    if len(data) > _MAX_TRACE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"轨迹过大(>{_MAX_TRACE_BYTES//1024//1024}MB)")
    try:
        json.loads(data)  # 校验是合法 JSON
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="轨迹须为合法 JSON")
    os.makedirs(_TRACE_ROOT, exist_ok=True)
    # 重传前删该 run 的旧 trace 文件(随机名会产生多份,吞异常)
    try:
        for name in os.listdir(_TRACE_ROOT):
            if name.startswith(f"{run_id}-"):
                try:
                    os.remove(os.path.join(_TRACE_ROOT, name))
                except OSError:
                    pass
    except OSError:
        pass
    rel = f"eval_traces/{run_id}-{secrets.token_hex(8)}.json"
    with open(os.path.join(_UPLOADS_DIR, rel), "wb") as f:
        f.write(data)
    r.trace = f"/uploads/{rel}"; db.commit()
    return ok({"trace_url": f"/uploads/{rel}"})


def reset_run_for_retry(r: EvalRun) -> None:
    """failed run 原地复位回 pending(重跑公共逻辑,任务端点与通用端点共用):
    清空全部回填与判定字段,payload 快照保留——仍按下发那一刻的配置重跑。调用方负责 commit。"""
    r.status = EvalRunStatus.pending
    r.reason = None
    r.session_id = None; r.share_link = None; r.artifact_share_link = None
    r.answer = None; r.trace = None
    r.reported_duration = None; r.bean_cost = None; r.tokens = None; r.duration_ms = None
    r.verdict = None; r.score = None; r.verdict_dims = None; r.verdict_reason = None
    r.judged_by = None; r.is_abnormal = False
    r.review_mark = None; r.review_note = None


@router.post("/retry-failed")
def retry_failed_batch(body: EvalRetryFailedIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """批量重跑失败(promptfoo retry-all-failed 同款):范围内全部 failed run 原地复位回 pending。"""
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    q = db.query(EvalRun).filter(EvalRun.project_id == body.project_id,
                                 EvalRun.status == EvalRunStatus.failed)
    if body.run_ids:
        q = q.filter(EvalRun.id.in_(body.run_ids))
    elif body.batch_id:
        q = q.filter(EvalRun.batch_id == body.batch_id)
    rows = q.all()
    for r in rows:
        reset_run_for_retry(r)
    db.commit()
    return ok({"retried": len(rows), "run_ids": [r.id for r in rows]})


@router.post("/{run_id}/retry")
def retry_run_any(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """通用单条重跑(用户鉴权,区别于同前缀下 runner 鉴权的 claim/trace):
    普通题库下发的 failed run 此前无重跑入口(任务重跑端点要求 run 属于任务),补齐对称性。"""
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    if getattr(r.status, "value", r.status) != "failed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="仅执行失败(failed)的可重跑")
    reset_run_for_retry(r)
    db.commit(); db.refresh(r)
    return ok(_to_out(r))


@router.get("/trend")
def batch_trend(project_id: int = Query(...), limit: int = Query(30, le=100),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """按批次聚合的测评趋势:每批次一个点(通过率/均分/判定数),时序回答「模型比上次强吗」。

    对齐主流评测平台的历史曲线(OpenCompass 榜单趋势/W&B Weave eval 历史)。
    现算聚合不建统计表(与 /stats 口径一致);取最近 limit 批,返回按时间升序。
    """
    from sqlalchemy import case, func

    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (db.query(
        EvalRun.batch_id,
        func.min(EvalRun.created_at).label("t0"),
        func.max(EvalRun.eval_task_id).label("task_id"),
        func.count(EvalRun.id).label("total"),
        func.sum(case((EvalRun.verdict == "pass", 1), else_=0)).label("passed"),
        func.sum(case((EvalRun.verdict == "fail", 1), else_=0)).label("failed"),
        func.avg(EvalRun.score).label("avg_score"),
    ).filter(EvalRun.project_id == project_id, EvalRun.batch_id.isnot(None))
     .group_by(EvalRun.batch_id)
     .order_by(func.min(EvalRun.created_at).desc())
     .limit(limit).all())

    # 任务名批量取(一批次至多一个任务;普通题库下发无任务名)
    task_ids = {r.task_id for r in rows if r.task_id}
    name_map = {}
    if task_ids:
        from app.models.ai_eval import EvalTask
        for tid, name in db.query(EvalTask.id, EvalTask.name).filter(EvalTask.id.in_(task_ids)).all():
            name_map[tid] = name

    out = []
    for r in reversed(rows):  # 倒序取最近 N 批 → 回正序(时间升序)供画曲线
        judged = int(r.passed or 0) + int(r.failed or 0)
        out.append({
            "batch_id": r.batch_id,
            "date": r.t0.isoformat() if r.t0 else None,
            "task_name": name_map.get(r.task_id),
            "total": int(r.total or 0),
            "judged": judged,
            "passed": int(r.passed or 0),
            "failed": int(r.failed or 0),
            "pass_rate": round(int(r.passed or 0) / judged * 100, 1) if judged else None,
            "avg_score": round(float(r.avg_score), 2) if r.avg_score is not None else None,
        })
    return ok({"batches": out})


@router.get("/history")
def list_history(project_id: int = Query(...), limit: int = Query(100, le=500),
                 batch_id: str | None = Query(None),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    q = db.query(EvalRun).filter(EvalRun.project_id == project_id)
    if batch_id:
        q = q.filter(EvalRun.batch_id == batch_id)
    rows = q.order_by(EvalRun.id.desc()).limit(limit).all()
    # 维度:优先 payload 快照(下发那一刻);老 run 的快照没有 dimension → 批量回查 eval_query 补上
    out = [_to_out(r) for r in rows]
    need = [(i, rows[i].eval_query_id) for i, d in enumerate(out)
            if not (d.get("payload") or {}).get("dimension") and rows[i].eval_query_id]
    dim_map = {}
    if need:
        ids = list({qid for _, qid in need})
        for qid, dim in db.query(EvalQuery.id, EvalQuery.dimension).filter(EvalQuery.id.in_(ids)).all():
            dim_map[qid] = dim
    for d in out:
        d["dimension"] = (d.get("payload") or {}).get("dimension")
    for i, qid in need:
        out[i]["dimension"] = dim_map.get(qid)
    return ok(out)
