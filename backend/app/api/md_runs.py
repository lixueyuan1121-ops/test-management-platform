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
import hashlib
import json
import time
import uuid
import re
from urllib.parse import quote
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, File, Form, UploadFile, Response
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import MdRun, User
from app.models.md_run import MdRunEvidence, MdEvidenceDelivery
from app.schemas.common import ok
from app.services import md_bug_skill, md_evidence
from app.services.md_report_db import database_unavailable, is_disconnect, report_db_step
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


def _to_out(r: MdRun, full: bool = False, db: Session | None = None) -> dict:
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
    summary = (out["env_check"] or {}).pop("evidence_summary", None)
    if full:
        out["content"] = r.content
        out["result_md"] = r.result_md
        if db is not None:
            rows = db.query(MdRunEvidence).filter_by(run_id=r.id).order_by(MdRunEvidence.id).all()
            out["evidence"] = [md_evidence.metadata(row) for row in rows]
            out["evidence_summary"] = summary
            deliveries = db.query(MdEvidenceDelivery).join(MdRunEvidence).filter(MdRunEvidence.run_id == r.id).all()
            targets = {(d.evidence_id, d.sub_id, d.matter_id): d for d in deliveries}
            for item in out["findings"]:
                if not isinstance(item, dict) or not item.get("title"):
                    continue
                target = item.get("evidence_target") or {}
                files = []
                for row in md_evidence.related(rows, item, len(indexed)):
                    delivery = targets.get((row.id, target.get("sub_id"), target.get("matter_id")))
                    files.append({**md_evidence.metadata(row), "state": delivery.state if delivery else "pending",
                                  "message": delivery.message if delivery else None,
                                  "remote_id": delivery.remote_id if delivery else None})
                item["evidence"] = files
                if files and item.get("report_receipt"):
                    receipt = item["report_receipt"]
                    receipt["attachments"] = [*receipt.get("attachments", []),
                        *[{"id": f["remote_id"], "file_name": f["name"], "skill": "geelib-attachment"}
                          for f in files if f["state"] == "sent"]]
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


@router.post("/queue/{run_id}/evidence")
def upload_evidence(run_id: int, file: UploadFile = File(...), case_label: str = Form(""),
                    runner: str | None = Query(None), db: Session = Depends(get_db),
                    ctx: RunnerCtx = Depends(require_runner_ctx)):
    rid = _resolve_runner(db, ctx, runner)
    run = db.query(MdRun).filter_by(id=run_id).with_for_update().first()
    if not run:
        raise HTTPException(404, "任务不存在")
    if run.runner != rid:
        raise HTTPException(403, "该任务未派给此执行机")
    if run.status in ("pending", "canceled"):
        raise HTTPException(409, "当前状态不能回传附件")
    row = md_evidence.save(db, run, file.filename or "", case_label.strip(), file.file.read(md_evidence.MAX_FILE + 1))
    return ok(md_evidence.metadata(row))


@router.get("/{run_id}/evidence/{evidence_id}")
def download_evidence(run_id: int, evidence_id: int, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    run = db.get(MdRun, run_id)
    if not run:
        raise HTTPException(404, "任务不存在")
    if run.project_id is not None:
        assert_project_role(db, user, run.project_id, tuple(ProjectRole))
    elif not user.is_platform_admin and run.enqueued_by != user.id:
        raise HTTPException(403, "无权下载此任务附件")
    row = db.query(MdRunEvidence).filter_by(run_id=run_id, id=evidence_id).first()
    if not row:
        raise HTTPException(404, "任务附件不存在")
    return Response(row.content, media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(row.name, safe=""),
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.patch("/queue/{run_id}")
def queue_report(
    run_id: int,
    body: MdRunReportIn,
    runner: str | None = Query(None),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    rid = _resolve_runner(db, ctx, runner)
    r = db.query(MdRun).filter_by(id=run_id).with_for_update().first()
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.runner != rid:
        raise HTTPException(403, "该任务未派给此执行机")
    # A retried runner callback must not erase references recorded after the first report.
    if r.status in _TERMINAL:
        if body.evidence_summary and r.status != "canceled":
            check = json.loads(r.env_check or "{}")
            previous = check.get("evidence_summary")
            if previous and previous["expected"] == body.evidence_summary.expected:
                notes = list(dict.fromkeys([*previous["notes"], *body.evidence_summary.notes]))[-35:]
                if notes != previous["notes"]:
                    check["evidence_summary"] = {**previous, "notes": notes}
                    r.env_check = _dump(check)
                    db.commit()
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
        # Tracker references and attachment destinations are set only by the reporting endpoint.
        r.findings = _dump([{k: v for k, v in f.items() if not k.startswith("report_") and
                             k not in ("external_ref", "evidence_target", "evidence")} for f in findings])
    if body.env_check is not None or body.evidence_summary is not None:
        check = dict(body.env_check or {})
        if body.evidence_summary:
            check["evidence_summary"] = body.evidence_summary.model_dump()
        r.env_check = _dump(check)
    r.duration_ms = body.duration_ms
    r.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    return ok(_to_out(r, full=True))


# ==================== 用户侧 ====================

def _dispatch(body: MdRunDocument, runners: list[str], db: Session, user: User) -> list[dict]:
    if body.project_id is not None:
        assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        MdRun(project_id=body.project_id, task_id=body.task_id,
              req_no=(body.req_no or "").strip() or None, title=body.title.strip(),
              runner=runner, env_group=body.env_group.strip(),
              client_version=(body.client_version or "").strip() or None,
              source=body.source.strip() or "page", content=body.content,
              status="pending", enqueued_by=user.id, created_at=created_at)
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
    can_read_files = r.project_id is not None or user.is_platform_admin or r.enqueued_by == user.id
    return ok(_to_out(r, full=True, db=db if can_read_files else None))


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
    r = db.query(MdRun).filter_by(id=run_id).with_for_update().first()
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.project_id is not None:
        assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    if r.status == "running":
        raise HTTPException(409, "任务执行中，不能删除（先取消）")
    files = db.query(MdRunEvidence.id).filter_by(run_id=r.id)
    db.query(MdEvidenceDelivery).filter(MdEvidenceDelivery.evidence_id.in_(files)).delete(synchronize_session=False)
    db.query(MdRunEvidence).filter_by(run_id=r.id).delete(synchronize_session=False)
    db.delete(r)
    db.commit()
    return ok({"deleted": run_id})


@report_db_step
def _save_finding(db: Session, run_id: int, index: int, expected: dict, updated: dict) -> bool:
    """Retry unrelated JSON changes, but never overwrite a newer attempt on this finding."""
    while True:
        run = db.get(MdRun, run_id, populate_existing=True)
        if run is None:
            raise HTTPException(404, "任务不存在")
        previous = run.findings
        items = run.findings_list
        reason, review = _review_findings(run)
        if index >= len(review):
            return False
        if review[index][1] == updated:
            return True  # 上次提交已生效，仅数据库回执丢失。
        if review[index][1] != expected:
            return False
        storage_index = review[index][0]
        if storage_index is None:
            items.append(updated)
        else:
            items[storage_index] = updated
        changed = db.query(MdRun).filter_by(id=run.id, findings=previous).update(
            {"findings": _dump(items), "reason": reason}, synchronize_session=False)
        db.commit()
        if changed:
            return True


@report_db_step
def _related_evidence(db, run_id, item, count):
    rows = db.query(MdRunEvidence).filter_by(run_id=run_id).order_by(MdRunEvidence.id).all()
    return md_evidence.related(rows, item, count)


def _send_evidence(bind, run, item, sub_id, token, result):
    target = item.get("evidence_target") or {}
    if target.get("sub_id") != sub_id or item.get("external_ref") != f"geelib#{target.get('matter_id')}":
        return result
    files = _related_evidence(bind, run.id, item, len(_review_findings(run)[1]))
    if files:
        states = [md_evidence.deliver(bind, row, sub_id, target["matter_id"], token) for row in files]
        result["attachment_count"] = len(states)
        result["attachments_sent"] = states.count("sent")
    return result


def _report_one(bind, run, index, item, body, sub_id, executor, token):
    from app.services.geelib import GeelibError

    if item.get("external_ref"):
        result = {"ok": True, "skipped": True, "external_ref": item["external_ref"]}
        if body.receipt_action == "attachments":
            return _send_evidence(bind, run, item, sub_id, token, result)
        return result
    if body.receipt_action == "attachments":
        return {"ok": False, "status": "changed", "msg": "请先完成工单上报"}
    changed = {"ok": False, "status": "changed", "msg": "候选状态已更新，请刷新详情"}
    state = item.get("report_state")
    now = time.time()
    if state == "sending":
        started = item.get("report_started_at")
        if started is None:
            # Old workers did not record a start time. Allow one full request window.
            _save_finding(bind, run.id, index, item, {**item, "report_started_at": now})
        if started is None or now - started < 90:
            return {"ok": False, "status": "sending", "msg": "上报请求正在处理，请稍后刷新"}
    title = str(item.get("title") or "").strip() or f"{run.title} 执行失败"
    title = (f"[{run.req_no}] {title}" if run.req_no else title)[:200]
    clean = {k: v for k, v in item.items() if k not in
             ("report_state", "report_started_at", "report_attempt", "report_error", "report_executor")}

    def review(matches, stage, message):
        snapshot = [stage, run.id, index, sub_id, title, item.get("report_attempt"), sorted(m["id"] for m in matches)]
        key = stage + "-" + hashlib.sha256(_dump(snapshot).encode()).hexdigest()[:56]
        if body.receipt_action not in ("link", "retry") or body.review_key != key:
            return {"ok": False, "status": "review", "msg": message, "matches": matches, "review_key": key}
        if body.receipt_action == "link":
            if body.matter_id not in {m["id"] for m in matches}:
                raise HTTPException(409, "请选择当前项目中已核对的缺陷")
            ref = f"geelib#{body.matter_id}"
            linked = {**clean, "external_ref": ref, "evidence_target": {"sub_id": sub_id, "matter_id": body.matter_id}}
            if not _save_finding(bind, run.id, index, item, linked):
                return changed
            return _send_evidence(bind, run, linked, sub_id, token,
                                  {"ok": True, "skipped": True, "status": "linked", "external_ref": ref})
        return None

    skill_review = (body.review_key or "").startswith("skill-")
    receipts = []
    if state:
        try:
            receipts = md_bug_skill.find_title_matches(sub_id, title, token)
        except GeelibError as exc:
            return {"ok": False, "status": "check_failed", "msg": str(exc)}
        if not skill_review:
            decision = review(receipts, "receipt", item.get("report_error") or "请核对极库云工单")
            if decision:
                return decision
    elif body.receipt_action and not skill_review:
        return changed

    facts = {"title": title, "detail": item.get("detail") or item.get("description") or "",
             "severity": item.get("severity"), "environment": run.env_group, "version": run.client_version,
             "test_document": md_bug_skill.case_context(run.content, title)}
    try:
        description, priority, matches = md_bug_skill.prepare_case(sub_id, title, facts, token)
    except GeelibError as exc:
        return {"ok": False, "status": "failed", "msg": str(exc)}
    reviewed = {m["id"] for m in receipts}
    if skill_review or any(m["id"] not in reviewed for m in matches):
        related = {m["id"]: m for m in [*receipts, *matches]}
        decision = review(list(related.values()), "skill", "技能查到可能相同的缺陷，请核对触发条件和实际结果")
        if decision:
            return decision

    claimed = {**clean, "report_state": "sending", "report_attempt": uuid.uuid4().hex,
               "report_started_at": time.time(), "report_executor": executor}
    if not _save_finding(bind, run.id, index, item, claimed):
        return changed
    try:
        report = md_bug_skill.create_case(sub_id, title, description, priority, executor, token)
        updated = {**clean, **report, "evidence_target": {"sub_id": sub_id,
                                                        "matter_id": int(report["external_ref"].split("#")[1])}}
        result = {"ok": True, **report}
    except md_bug_skill.SubmissionUncertain as exc:
        updated = {**claimed, "report_state": "uncertain", "report_error": str(exc)}
        result = {"ok": False, "status": "uncertain", "msg": str(exc)}
    except GeelibError as exc:
        updated = clean
        result = {"ok": False, "status": "failed", "msg": str(exc)}
    if not _save_finding(bind, run.id, index, claimed, updated):
        return changed
    return _send_evidence(bind, run, updated, sub_id, token, result) if result["ok"] else result


@report_db_step
def _report_details(db, run_id):
    run = db.get(MdRun, run_id)
    if run is None:
        raise HTTPException(404, "任务不存在")
    return _to_out(run, full=True, db=db)


def _reportable_run(db, run_id, user):
    r = db.get(MdRun, run_id)
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.project_id is None:
        raise HTTPException(400, "该任务未绑定项目，无法上报极库云（缺陷要归属到项目）")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)

    if r.status not in _TERMINAL:
        raise HTTPException(409, "任务结束后才能上报缺陷")
    return r


def _report_sub_id(db, project_id):
    from app.services import geelib
    if not geelib.is_enabled():
        raise HTTPException(400, "极库云未启用（GEELIB 相关配置缺失）")
    from app.models import Project

    proj = db.get(Project, project_id)
    sub_id = geelib.resolve_sub_id(proj.code if proj else None, getattr(proj, "geelib_sub_id", None))
    if not sub_id:
        raise HTTPException(400, "该项目未映射极库云项目（project.geelib_sub_id 未配置）")
    return sub_id


@report_db_step
def _report_inputs(db, run_id, body, user):
    r = _reportable_run(db, run_id, user)
    if body.receipt_action and (body.indexes is None or len(body.indexes) != 1):
        raise HTTPException(422, "核对回执时须指定一条缺陷候选")
    _, indexed = _review_findings(r)
    count = len(indexed)
    targets = range(count) if body.indexes is None else dict.fromkeys(i for i in body.indexes if 0 <= i < count)
    return r, indexed, targets, _report_sub_id(db, r.project_id) if targets else None


def _report_token(bind, user_id):
    from app.services.geelib_account import get_user_app_token, GeelibAccountError

    try:
        with Session(bind) as auth_db:
            return get_user_app_token(auth_db, user_id)
    except GeelibAccountError as e:
        raise HTTPException(400, f"极库云账号未授权：{e}")
    except DBAPIError as error:
        if is_disconnect(error):
            raise database_unavailable() from error
        raise


@report_db_step
def _executor_project(db, run_id, user):
    return _report_sub_id(db, _reportable_run(db, run_id, user).project_id)


@router.get("/{run_id}/report-executors")
def report_executors(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.services.geelib import GeelibError

    bind = db.get_bind()
    db.close()
    sub_id = _executor_project(bind, run_id, user)
    token = _report_token(bind, user.id)
    try:
        members = md_bug_skill.list_executors(sub_id, token)
    except GeelibError as exc:
        raise HTTPException(502, str(exc)) from exc
    return ok({"members": members, "default_email": user.email or settings.GEELIB_DEFAULT_EXECUTOR})


@router.post("/{run_id}/report-bugs")
def report_bugs(
    run_id: int,
    body: MdRunBugsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """人工上报确认的缺陷，跳过已有工单，逐条返回结果。"""
    executor = body.executor_email or user.email or settings.GEELIB_DEFAULT_EXECUTOR
    bind = db.get_bind()
    db.close()
    r, indexed, targets, sub_id = _report_inputs(bind, run_id, body, user)
    if not targets:
        return ok({"results": [], "reported": 0, "run": _report_details(bind, run_id)})
    app_token = _report_token(bind, user.id)

    results = []
    for index in targets:
        item = indexed[index][1]
        if isinstance(item, dict):
            result = _report_one(bind, r, index, item, body, sub_id, executor, app_token)
            results.append({"index": index, **result})

    reported = sum(1 for x in results if x.get("ok") and not x.get("skipped"))
    return ok({"results": results, "reported": reported, "run": _report_details(bind, run_id)})
