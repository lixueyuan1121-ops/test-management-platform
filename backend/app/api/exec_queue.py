"""执行队列路由：勾选用例下发 → runner 拉取/认领/回写 的闭环。

四个接口（详见 tools/qalab-runner/HANDOFF.md）：
- POST /api/exec-queue/enqueue   前端「发送到本地执行」按钮调；用户 JWT + 项目 member/admin。
- GET  /api/exec-queue           runner 拉取 pending；runner token。
- POST /api/exec-queue/{id}/claim runner 认领防重跑；runner token。
- PATCH /api/exec-queue/{id}      runner 回写 pass/fail，并同步 checklist_item.exec_status；runner token。

沿用全项目约定：{code,msg,data} 信封（ok/fail）、手写 _to_out、体外 assert_project_role。
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import ChecklistStatus, ExecKind, ExecStatus, ProjectRole
from app.db.session import get_db
from app.models import ChecklistItem, ExecRun, TestCase, User
from app.schemas.common import ok
from app.schemas.exec_queue import EnqueueExecIn, ExecReportIn

router = APIRouter(prefix="/api/exec-queue", tags=["exec-queue"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)

# test_case.exec_kind（若存在）→ ExecKind；缺省 gui。exec_kind 列由 migrate 补，
# 老库/未设值的用例回落到 gui（GUI 是被测客户端的主要形态）。
def _kind_of(tc: TestCase | None) -> ExecKind:
    raw = getattr(tc, "exec_kind", None) if tc else None
    try:
        return ExecKind(raw) if raw else ExecKind.gui
    except ValueError:
        return ExecKind.gui


def _payload_of(tc: TestCase | None) -> dict:
    """把用例快照成 runner/Claude 要用的 payload（steps/expected/title/params + 结构化 script）。"""
    if not tc:
        return {}
    # script 落库是 JSON 字符串;runner 的 StepExecutor 需要**数组**(Array.isArray 判定)。
    # 解析回对象放进 payload;解析失败/无 script 则给 None(runner 回退 claude 兜底)。
    script = None
    raw = getattr(tc, "script", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed:
                script = parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            script = None
    return {
        "test_case_id": tc.id,
        "title": tc.title,
        "category": tc.category,
        "steps": tc.steps,
        "expected": tc.expected,
        "priority": tc.priority,
        "script": script,
    }


def _to_out(r: ExecRun) -> dict:
    return {
        "run_id": r.id,
        "checklist_item_id": r.checklist_item_id,
        "case_id": r.test_case_id,     # 对齐 runner 端字段名 case_id
        "test_case_id": r.test_case_id,
        "task_id": r.task_id,
        "project_id": r.project_id,
        "runner": r.runner,
        # 防御:kind/status 正常是枚举(有 .value),但历史/脏数据可能是裸字符串;
        # 用 getattr 兼容两者,避免一行坏数据让 runner 的 GET 轮询整个 500(实测踩过)。
        "kind": getattr(r.kind, "value", r.kind),
        "status": getattr(r.status, "value", r.status),
        "payload": json.loads(r.payload or "{}"),
        "verdict": r.verdict,
        "reason": r.reason,
        "evidence_url": r.evidence_url,
        "duration_ms": r.duration_ms,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


# ---- ① 前端「发送到本地执行」：把勾选的清单项入队 ----
@router.post("/enqueue")
def enqueue(
    body: EnqueueExecIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把选中的验收清单项下发到指定 runner。项目 member/admin 可操作。

    整体校验：任一清单项不存在或跨项目 → 400，整批拒绝。
    """
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)

    ids = list(dict.fromkeys(body.checklist_item_ids))  # 去重保序
    items = db.query(ChecklistItem).filter(ChecklistItem.id.in_(ids)).all()
    found = {it.id: it for it in items}
    for cid in ids:
        it = found.get(cid)
        if it is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"清单项 {cid} 不存在")
        if it.project_id != body.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"清单项 {cid} 不属于该项目")

    created = []
    for cid in ids:
        it = found[cid]
        tc = db.get(TestCase, it.test_case_id)
        if _kind_of(tc) == ExecKind.manual:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"清单项 {cid} 对应用例为『人工/不可自动化(manual)』,不能下发到执行机",
            )
        row = ExecRun(
            checklist_item_id=it.id,
            test_case_id=it.test_case_id,
            task_id=it.task_id,
            project_id=it.project_id,
            runner=body.runner,
            kind=_kind_of(tc),
            status=ExecStatus.pending,
            payload=json.dumps(_payload_of(tc), ensure_ascii=False),
            enqueued_by=user.id,
        )
        db.add(row)
        db.flush()
        created.append(row.id)
    db.commit()
    return ok({"run_ids": created})


# ---- 执行历史查询(用户侧,独立"执行结果"页用)----
# exec_run 每次执行一行、不覆盖;这里按条件查全部历史,支持复测追溯。
@router.get("/history")
def list_history(
    project_id: int = Query(...),
    task_id: int | None = Query(None),
    runner: str | None = Query(None),
    verdict: str | None = Query(None),        # pass / fail
    status_: str | None = Query(None, alias="status"),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    q = db.query(ExecRun).filter(ExecRun.project_id == project_id)
    if task_id is not None:
        q = q.filter(ExecRun.task_id == task_id)
    if runner:
        q = q.filter(ExecRun.runner == runner)
    if verdict:
        q = q.filter(ExecRun.verdict == verdict)
    if status_:
        q = q.filter(ExecRun.status == status_)
    rows = q.order_by(ExecRun.id.desc()).limit(limit).all()

    # 批量补用例标题(payload 里有 title,优先用;缺失再查 test_case),避免 N+1
    out = []
    for r in rows:
        title = None
        try:
            title = (json.loads(r.payload or "{}") or {}).get("title")
        except (json.JSONDecodeError, ValueError):
            title = None
        d = _to_out(r)
        d["title"] = title
        d["enqueued_by"] = r.enqueued_by
        out.append(d)
    return ok(out)


# ---- ② runner 拉取待执行 ----
@router.get("")
def list_pending(
    runner: str = Query("mac-01"),
    limit: int = Query(5, le=20),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    # 设备 token:runner 锁定为该设备的 runner_id(忽略 query,防拿他人 token 冒充别的设备);
    # 共享 token(兜底):沿用 query 的 runner。
    if ctx.device is not None:
        runner = ctx.device.runner_id
        ctx.device.last_seen_at = datetime.utcnow()   # 记录设备活跃
        db.commit()
    rows = (
        db.query(ExecRun)
        .filter(ExecRun.status == ExecStatus.pending, ExecRun.runner == runner)
        .order_by(ExecRun.id)
        .limit(limit)
        .all()
    )
    return ok([_to_out(r) for r in rows])


# ---- ③ runner 认领（防重跑）----
@router.post("/{run_id}/claim")
def claim(
    run_id: int,
    runner: str = Query(...),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    if ctx.device is not None:
        runner = ctx.device.runner_id   # 设备 token:以设备身份为准,防冒充
    r = db.get(ExecRun, run_id)
    if not r or r.status != ExecStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该执行项不可认领")
    # 归属校验：只能认领派给自己的执行项，避免多台 runner 串扰
    # （设备 token 下 runner 已锁定为设备 runner_id;共享 token 下靠 query runner 区分）。
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    r.status = ExecStatus.running
    db.commit()
    db.refresh(r)
    return ok(_to_out(r))


# ---- ④ runner 回写结果，并同步验收清单项状态 ----
@router.patch("/{run_id}")
def report(
    run_id: int,
    body: ExecReportIn,
    runner: str = Query(...),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    if ctx.device is not None:
        runner = ctx.device.runner_id   # 设备 token:以设备身份为准,防冒充
    r = db.get(ExecRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    # 归属校验：只能回写派给自己的执行项（见 claim 说明）。
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")

    is_pass = body.verdict == "pass"
    r.verdict = body.verdict
    r.status = ExecStatus.passed if is_pass else ExecStatus.failed
    r.reason = body.reason
    r.evidence_url = body.evidence_url
    r.duration_ms = body.duration_ms

    # 闭环落点：把结果同步回验收清单项（pass→passed / fail→failed）。
    # 复用现有清单展示、checklist-summary 统计、失败转遗留问题等下游能力。
    if r.checklist_item_id:
        item = db.get(ChecklistItem, r.checklist_item_id)
        if item:
            item.exec_status = ChecklistStatus.passed if is_pass else ChecklistStatus.failed
            item.executed_by = None  # 机器自动执行，无归属用户
            item.executed_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    return ok(_to_out(r))
