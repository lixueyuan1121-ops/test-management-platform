"""md 任务：把一份提测文档下发到测试机，由执行机上的 agent 操控客户端跑完并回写。

与 exec-queue 的区别：exec-queue 派的是**结构化用例**（gui/api/cli，逐条断言）；
这里派的是一**整份 md**，由 agent 按文档自由执行。两者数据互不相干，
本模块不读写 exec_run，因此不影响执行结果页、质量门禁、失败聚类等任何现有统计。

接口分两组：
- 用户 JWT（页面）：POST 下发 / GET 列表 / GET 详情 / POST 取消 / DELETE / POST 上报缺陷
- runner token（执行机）：GET /queue 拉取 → POST /queue/{id}/claim 认领 →
  POST /queue/{id}/heartbeat 心跳 → PATCH /queue/{id} 回写

沿用全项目约定：{code,msg,data} 信封、手写 _to_out、体外 assert_project_role、
状态 VARCHAR、不建 ENUM。
"""
import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import MdRun, User
from app.schemas.common import ok
from app.services import md_bug_skill
from app.services.md_result import analyze_result
from app.schemas.md_run import MdRunDocument, MdRunBatchCreate, MdRunCreate, MdRunReportIn, MdRunBugsIn

router = APIRouter(prefix="/api/md-runs", tags=["md-runs"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)
_TERMINAL = ("passed", "failed", "blocked", "canceled")


def _dump(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _legacy_failure(verdict, reason, findings, markdown):
    return bool(markdown and verdict == "fail" and (not findings or reason == f"{len(findings)} 条用例失败"))


def _review_findings(r: MdRun) -> tuple[str | None, list]:
    items = r.findings_list
    indexed = list(enumerate(items))
    if r.verdict != "fail" or not r.result_md or (items and not re.fullmatch(r"\d+ 条用例失败", r.reason or "")):
        return r.reason, indexed
    result = analyze_result(r.result_md)
    if not result.failed_cases:
        return r.reason, indexed
    if result.findings == items:
        return result.reason, indexed

    # Keep the raw storage index for claims and receipts. New candidates are appended on first report.
    review = [(None, finding) for finding in result.findings]
    by_title = {f["title"]: i for i, f in enumerate(result.findings)}
    steps = {}
    for i, finding in enumerate(result.findings):
        for step in re.findall(r"^[ \t]*(?:[-*]|\d+[.)])[ \t]+(.+)$", finding["detail"], re.M):
            steps.setdefault(step[:200], set()).add(i)
    legacy_titles = set(result.failed_cases)
    legacy_titles.update(step[:200] for step in re.findall(r"^[ \t]*(?:[-*]|\d+[.)])[ \t]+(.+)$", r.result_md, re.M))
    for index, old in indexed:
        if not isinstance(old, dict):
            continue
        title = str(old.get("title") or "")
        tracked = old.get("external_ref") or old.get("report_state")
        pos = by_title.get(title)
        if pos is None and tracked and len(steps.get(title, ())) == 1:
            pos = next(iter(steps[title]))
        if pos is None:
            if tracked or title not in legacy_titles:
                review.append((index, old))
            continue
        previous_index, current = review[pos]
        if previous_index is not None and (current.get("external_ref") or current.get("report_state")):
            if tracked:
                review.append((index, old))
            continue
        review[pos] = (index, {**old, **result.findings[pos]})
    return result.reason, review


def _to_out(r: MdRun, full: bool = False) -> dict:
    reason, indexed = _review_findings(r)
    out = {
        "id": r.id,
        "run_id": r.id,   # 与 exec_queue/perf 对齐：runner 侧统一用 run_id
        "project_id": r.project_id,
        "task_id": r.task_id,
        "req_no": r.req_no,
        "title": r.title,
        "runner": r.runner,
        "env_group": r.env_group,
        "client_version": r.client_version,
        "source": r.source,
        "status": r.status,
        "verdict": r.verdict,
        "reason": reason,
        "findings": [item for _, item in indexed],
        "duration_ms": r.duration_ms,
        "env_check": json.loads(r.env_check) if r.env_check else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "enqueued_by": r.enqueued_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "can_cancel": r.status in ("pending", "running"),
    }
    if full:
        out["content"] = r.content
        out["result_md"] = r.result_md
    return out


def _resolve_runner(db: Session, ctx: RunnerCtx, runner: str | None) -> str:
    """设备 token 时以设备身份为准（防冒充别的执行机）；共享 token 用 query/body 的 runner。"""
    if ctx.device is not None:
        ctx.device.last_seen_at = datetime.utcnow()
        return ctx.device.runner_id
    from app.services.dispatcher import touch_runner_heartbeat
    rid = (runner or "").strip()
    if not rid:
        raise HTTPException(400, "共享令牌下必须指定 runner")
    touch_runner_heartbeat(db, rid)   # 与 perf/exec 同口径刷心跳，设备看板可见
    return rid


# ==================== runner 侧（必须在 /{run_id} 之前声明）====================

@router.get("/queue")
def queue_pending(
    runner: str | None = Query(None),
    limit: int = Query(3, le=10),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    """执行机拉取待执行任务。拉取即"在线"，设备看板据此判断该机在跑哪类 runner。"""
    rid = _resolve_runner(db, ctx, runner)
    db.commit()
    rows = (
        db.query(MdRun)
        .filter(MdRun.status == "pending", MdRun.runner == rid)
        .order_by(MdRun.id)
        .limit(limit)
        .all()
    )
    return ok([_to_out(r, full=True) for r in rows])


@router.post("/queue/{run_id}/claim")
def queue_claim(
    run_id: int,
    runner: str | None = Query(None),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    rid = _resolve_runner(db, ctx, runner)
    r = db.get(MdRun, run_id)
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.runner != rid:
        raise HTTPException(403, "该任务未派给此执行机")
    now = datetime.utcnow()
    claimed = db.query(MdRun).filter_by(id=run_id, runner=rid, status="pending").update(
        {"status": "running", "started_at": now, "heartbeat_at": now}, synchronize_session=False)
    if not claimed:
        raise HTTPException(409, "该任务已被认领或已结束")
    db.commit()
    db.refresh(r)
    return ok(_to_out(r, full=True))


@router.post("/queue/{run_id}/heartbeat")
def queue_heartbeat(
    run_id: int,
    runner: str | None = Query(None),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    """长任务心跳。返回当前 status，执行机据此感知"已被取消"并自行收尾（还原环境）。"""
    rid = _resolve_runner(db, ctx, runner)
    r = db.get(MdRun, run_id)
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.runner != rid:
        raise HTTPException(403, "该任务未派给此执行机")
    if r.status == "running":
        r.heartbeat_at = datetime.utcnow()
        db.commit()
    return ok({"run_id": r.id, "status": r.status})


@router.patch("/queue/{run_id}")
def queue_report(
    run_id: int,
    body: MdRunReportIn,
    runner: str | None = Query(None),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    rid = _resolve_runner(db, ctx, runner)
    r = db.get(MdRun, run_id)
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.runner != rid:
        raise HTTPException(403, "该任务未派给此执行机")
    # A retried runner callback must not erase references recorded after the first report.
    if r.status in _TERMINAL:
        return ok(_to_out(r, full=True))
    verdict, reason, findings = body.verdict, body.reason, body.findings
    if verdict == "completed" or _legacy_failure(verdict, reason, findings, body.result_md):
        parsed = analyze_result(body.result_md)
        if verdict == "completed" or parsed.verdict == "fail" or parsed.explicit_no_defects:
            verdict, reason, findings = parsed.verdict, parsed.reason, parsed.findings
    r.verdict, r.reason = verdict, reason
    r.status = {"pass": "passed", "fail": "failed", "blocked": "blocked"}[verdict]
    if body.result_md is not None:
        r.result_md = body.result_md
    if findings is not None:
        r.findings = _dump(findings)
    if body.env_check is not None:
        r.env_check = _dump(body.env_check)
    r.duration_ms = body.duration_ms
    r.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    return ok(_to_out(r, full=True))


# ==================== 用户侧 ====================

def _dispatch(body: MdRunDocument, runners: list[str], db: Session, user: User) -> list[dict]:
    if body.project_id is not None:
        assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    rows = [
        MdRun(project_id=body.project_id, task_id=body.task_id,
              req_no=(body.req_no or "").strip() or None, title=body.title.strip(),
              runner=runner, env_group=body.env_group.strip(),
              client_version=(body.client_version or "").strip() or None,
              source=body.source.strip() or "page", content=body.content,
              status="pending", enqueued_by=user.id)
        for runner in dict.fromkeys(runners)
    ]
    db.add_all(rows)
    db.flush()
    created = [_to_out(row) for row in rows]
    db.commit()
    return created


@router.post("")
def create_run(body: MdRunCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return ok(_dispatch(body, [body.runner], db, user)[0])


@router.post("/batch")
def create_runs(body: MdRunBatchCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return ok(_dispatch(body, body.runners, db, user))


@router.get("")
def list_runs(
    project_id: int | None = Query(None),
    status: str | None = Query(None),
    runner: str | None = Query(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(MdRun)
    if project_id is not None:
        assert_project_role(db, user, project_id, tuple(ProjectRole))
        q = q.filter(MdRun.project_id == project_id)
    if status:
        q = q.filter(MdRun.status == status)
    if runner:
        q = q.filter(MdRun.runner == runner)
    rows = q.order_by(MdRun.id.desc()).limit(limit).all()
    return ok([_to_out(r) for r in rows])


@router.get("/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.get(MdRun, run_id)
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.project_id is not None:
        assert_project_role(db, user, r.project_id, tuple(ProjectRole))
    return ok(_to_out(r, full=True))


@router.post("/{run_id}/cancel")
def cancel_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """取消排队中的任务。运行中的任务只置 canceled —— 执行机下一次心跳看到后自行收尾，
    平台不强制杀进程（杀在切过 hosts 的机器上会留下未还原的环境）。"""
    r = db.get(MdRun, run_id)
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.project_id is not None:
        assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    if r.status not in ("pending", "running"):
        raise HTTPException(409, f"任务已结束（{r.status}），无法取消")
    r.status = "canceled"
    r.reason = (r.reason or "") + ("\n" if r.reason else "") + f"[已取消] {user.name}"
    r.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    return ok(_to_out(r))


@router.delete("/{run_id}")
def delete_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.get(MdRun, run_id)
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.project_id is not None:
        assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    if r.status == "running":
        raise HTTPException(409, "任务执行中，不能删除（先取消）")
    db.delete(r)
    db.commit()
    return ok({"deleted": run_id})


def _update_finding(db: Session, run: MdRun, index: int, *, claim=False, outcome=None):
    """Compare-and-swap the JSON column so concurrent reports preserve each other's references."""
    while True:
        db.refresh(run)
        previous = run.findings
        items = run.findings_list
        reason, review = _review_findings(run)
        if index >= len(review):
            return None
        storage_index, item = review[index]
        if not isinstance(item, dict):
            return None
        if claim:
            if item.get("external_ref") or item.get("report_state"):
                return item
            updated = {**item, "report_state": "sending"}
        else:
            updated = {**item, **(outcome or {})}
            if not outcome or "report_state" not in outcome:
                updated.pop("report_state", None)
        if storage_index is None:
            items.append(updated)
        else:
            items[storage_index] = updated
        changed = db.query(MdRun).filter_by(id=run.id, findings=previous).update(
            {"findings": _dump(items), "reason": reason}, synchronize_session=False)
        db.commit()
        if changed:
            return item


@router.post("/{run_id}/report-bugs")
def report_bugs(
    run_id: int,
    body: MdRunBugsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把 findings 上报极库云（人工确认后触发，不自动报）。

    自动上报容易把环境问题、偶发失败灌进缺陷库，所以这里坚持"人点一次"。
    已上报过的条目（有 external_ref）会跳过；单条失败不影响其他条目，返回逐条结果。
    """
    r = db.get(MdRun, run_id)
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.project_id is None:
        raise HTTPException(400, "该任务未绑定项目，无法上报极库云（缺陷要归属到项目）")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)

    if r.status not in _TERMINAL:
        raise HTTPException(409, "任务结束后才能上报缺陷")
    _, indexed = _review_findings(r)
    count = len(indexed)
    targets = range(count) if body.indexes is None else dict.fromkeys(i for i in body.indexes if 0 <= i < count)
    if not targets:
        return ok({"results": [], "reported": 0, "run": _to_out(r, full=True)})

    from app.services import geelib
    if not geelib.is_enabled():
        raise HTTPException(400, "极库云未启用（GEELIB 相关配置缺失）")
    from app.models import Project
    from app.services.geelib_account import get_user_app_token, GeelibAccountError

    proj = db.get(Project, r.project_id)
    sub_id = geelib.resolve_sub_id(proj.code if proj else None, getattr(proj, "geelib_sub_id", None))
    if not sub_id:
        raise HTTPException(400, "该项目未映射极库云项目（project.geelib_sub_id 未配置）")

    try:
        app_token = get_user_app_token(db, user.id)
    except GeelibAccountError as e:
        raise HTTPException(400, f"极库云账号未授权：{e}")

    executor = user.email or settings.GEELIB_DEFAULT_EXECUTOR
    results = []
    for index in targets:
        item = _update_finding(db, r, index, claim=True)
        if item is None:
            continue
        if item.get("external_ref"):
            results.append({"index": index, "ok": True, "skipped": True, "external_ref": item["external_ref"]})
            continue
        if item.get("report_state"):
            results.append({"index": index, "ok": False, "msg": "正在上报或等待核对极库云回执"})
            continue
        title = str(item.get("title") or "").strip() or f"{r.title} 执行失败"
        if r.req_no:
            title = f"[{r.req_no}] {title}"
        try:
            ref = md_bug_skill.create_case(sub_id, title[:200],
                                           str(item.get("detail") or item.get("description") or ""),
                                           item.get("severity"), executor, app_token)
            outcome = {"external_ref": ref}
            results.append({"index": index, "ok": True, **outcome})
        except geelib.GeelibError as error:
            outcome = {"report_state": "uncertain"} if isinstance(error, md_bug_skill.SubmissionUncertain) else {}
            results.append({"index": index, "ok": False, "msg": str(error)})
        _update_finding(db, r, index, outcome=outcome)

    db.refresh(r)
    reported = sum(1 for x in results if x.get("ok") and not x.get("skipped"))
    return ok({"results": results, "reported": reported, "run": _to_out(r, full=True)})
